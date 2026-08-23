from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import traceback
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Sequence
from urllib.parse import parse_qs, unquote, urlsplit

from .automation import (
    AutomationConfig,
    CODEX_REASONING_EFFORTS,
    CODEX_SERVICE_TIERS,
    ENGINES,
    OPENCODE_VARIANTS,
    REASONING_EFFORTS,
    REPORT_TYPES,
    SANDBOX_MODES,
    load_material_metadata,
    load_package_metadata,
    resolve_engine_binary,
    run_automation,
)
from .materials import (
    MATERIAL_STAGE_DEFINITIONS,
    MaterialManifestStore,
    SUPPORTED_MATERIAL_EXTENSIONS,
    extract_material_archive,
)
from .store import ManifestStore


MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 4 * 1024 * 1024
MAX_UPLOAD_FILES = 1_000
MAX_LOG_BYTES = 64 * 1024
MAX_RECENT_EVENTS = 80
ACTIVE_JOB_STATUSES = {"queued", "running"}
JOB_STATUSES = ACTIVE_JOB_STATUSES | {"completed", "failed", "cancelled", "timed_out"}
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MODEL_ID = re.compile(r"^[A-Za-z0-9_.-]+/[^\s]+$")
CODEX_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
PREFERRED_OPENCODE_MODELS = (
    (
        "deepseek/deepseek-v4-flash",
        "DeepSeek V4 Flash",
        ("low", "high", "max"),
    ),
    (
        "deepseek/deepseek-v4-pro",
        "DeepSeek V4 Pro",
        ("high", "max"),
    ),
)
FALLBACK_CODEX_MODELS = (
    (
        "gpt-5.6-sol",
        "GPT-5.6 Sol",
        "旗舰模型，适合复杂研究与高难度报告。",
        ("low", "medium", "high", "xhigh", "max"),
        "low",
    ),
    (
        "gpt-5.6-terra",
        "GPT-5.6 Terra",
        "能力、速度与成本更均衡。",
        ("low", "medium", "high", "xhigh", "max"),
        "medium",
    ),
    (
        "gpt-5.6-luna",
        "GPT-5.6 Luna",
        "更快、更经济，适合高频任务。",
        ("low", "medium", "high", "xhigh", "max"),
        "medium",
    ),
)
VIDEO_STAGE_DEFINITIONS = (
    ("ingest", "导入校验", "字幕契约与质量门"),
    ("analyze", "原意分析", "章节、观点与限定"),
    ("research", "外部研判", "证据、反方与条件"),
    ("judgment", "综合判断", "定价、反证与风险"),
    ("draft", "报告起草", "三层内容结构"),
    ("fidelity_review", "原意审查", "逐段忠实性核对"),
    ("render", "页面渲染", "Markdown 与 HTML"),
    ("html_validate", "网页验收", "链接、布局与映射"),
    ("complete", "完成交付", "产物一致性确认"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _one_line(value: object, limit: int = 220) -> str:
    text = ANSI_ESCAPE.sub("", str(value or ""))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_codex_event(payload: dict[str, Any]) -> tuple[str, str] | None:
    event_type = str(payload.get("type") or "")
    if event_type == "thread.started":
        return "session", "Codex 会话已建立"
    if event_type == "turn.started":
        return "thinking", "模型开始分析"
    if event_type == "turn.completed":
        usage = payload.get("usage")
        total = 0
        if isinstance(usage, dict):
            total = sum(
                int(usage.get(key) or 0)
                for key in ("input_tokens", "output_tokens", "reasoning_output_tokens")
            )
        suffix = f" · {total:,} tokens" if total else ""
        return "turn_completed", "模型阶段完成" + suffix
    if event_type == "turn.failed":
        error = payload.get("error")
        if isinstance(error, dict):
            error = error.get("message") or error.get("code")
        return "error", "模型运行失败：" + _one_line(error or "未知错误")
    if event_type not in {"item.started", "item.updated", "item.completed"}:
        return None

    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    item_type = str(item.get("type") or "")
    completed = event_type == "item.completed"
    if item_type == "reasoning":
        return "thinking", "正在分析与规划" if not completed else "完成一段分析"
    if item_type == "agent_message":
        message = _one_line(item.get("text"), 260)
        return "message", message or "模型更新了任务说明"
    if item_type == "command_execution":
        command = _one_line(item.get("command") or item.get("command_line"), 180)
        exit_code = item.get("exit_code")
        if completed and exit_code not in (None, 0):
            return "command_failed", f"命令失败（{exit_code}）：{command}"
        action = "命令完成" if completed else "正在执行命令"
        return "command", f"{action}：{command or '项目命令'}"
    if item_type == "file_change":
        changes = item.get("changes")
        paths = []
        if isinstance(changes, list):
            paths = [
                _one_line(change.get("path"), 80)
                for change in changes
                if isinstance(change, dict) and change.get("path")
            ]
        suffix = "、".join(paths[:3])
        if len(paths) > 3:
            suffix += f" 等 {len(paths)} 个文件"
        action = "已写入文件" if completed else "正在写入文件"
        return "file", action + (f"：{suffix}" if suffix else "")
    if item_type == "mcp_tool_call":
        tool = _one_line(item.get("tool") or item.get("name"), 120)
        action = "工具调用完成" if completed else "正在调用工具"
        return "tool", f"{action}：{tool or '外部工具'}"
    if item_type == "web_search":
        query = _one_line(item.get("query"), 160)
        action = "检索完成" if completed else "正在检索外部证据"
        return "search", action + (f"：{query}" if query else "")
    if item_type == "error":
        return "error", "运行提示：" + _one_line(item.get("message") or "未知错误")
    return None


def summarize_engine_line(line: str, engine: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped:
        return None
    if engine == "codex":
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            lowered = stripped.lower()
            noisy = (
                "state db discrepancy",
                "skill descriptions were shortened",
                "ignoring interface.icon_",
            )
            if any(marker in lowered for marker in noisy):
                return None
            return "runtime", _one_line(stripped)
        if isinstance(payload, dict):
            return summarize_codex_event(payload)
        return None
    return "runtime", _one_line(stripped)


def fast_tier_unavailable(output: str) -> bool:
    lowered = output.lower()
    tier_marker = any(
        marker in lowered
        for marker in ("service_tier", "service tier", "fast mode", "fast tier")
    )
    rejection_marker = any(
        marker in lowered
        for marker in (
            "not available",
            "not enabled",
            "not allowed",
            "unsupported",
            "invalid value",
            "unknown value",
        )
    )
    return tier_marker and rejection_marker


def _safe_relative_path(value: str) -> Path:
    normalized = value.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ValueError(f"Unsafe uploaded path: {value}")
    return Path(*relative.parts)


def find_package_manifest(root: Path) -> Path:
    candidates = [
        path
        for path in root.rglob("package.json")
        if "__MACOSX" not in path.parts and path.is_file()
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"Upload must contain exactly one package.json; found {len(candidates)}"
        )
    return candidates[0]


def extract_package_archive(payload: bytes, destination: Path) -> Path:
    archive_path = destination / "package.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(payload)
    extracted = destination / "files"
    extracted.mkdir()
    total_size = 0
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_UPLOAD_FILES:
            raise ValueError("Archive contains too many files")
        for member in members:
            relative = _safe_relative_path(member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"Archive symlinks are not allowed: {member.filename}")
            total_size += member.file_size
            if total_size > MAX_UPLOAD_BYTES:
                raise ValueError("Archive expands beyond the upload size limit")
            target = (extracted / relative).resolve()
            target.relative_to(extracted.resolve())
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    return find_package_manifest(extracted)


def save_package_files(
    files: list[tuple[str, bytes]],
    destination: Path,
) -> Path:
    if not files or len(files) > MAX_UPLOAD_FILES:
        raise ValueError("Uploaded package directory has an invalid file count")
    root = destination / "files"
    root.mkdir(parents=True, exist_ok=True)
    total_size = 0
    seen: set[Path] = set()
    for filename, payload in files:
        relative = _safe_relative_path(filename)
        if relative in seen:
            raise ValueError(f"Duplicate uploaded path: {filename}")
        seen.add(relative)
        total_size += len(payload)
        if total_size > MAX_UPLOAD_BYTES:
            raise ValueError("Uploaded package exceeds the size limit")
        target = (root / relative).resolve()
        target.relative_to(root.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return find_package_manifest(root)


def parse_multipart(
    content_type: str,
    payload: bytes,
) -> dict[str, list[tuple[str | None, bytes]]]:
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: "
        + content_type.encode("ascii", "strict")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + payload
    )
    if not message.is_multipart():
        raise ValueError("Expected multipart/form-data")
    fields: dict[str, list[tuple[str | None, bytes]]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        content = part.get_payload(decode=True) or b""
        fields.setdefault(str(name), []).append((filename, content))
    return fields


def list_opencode_models(binary: str) -> list[str]:
    result = subprocess.run(
        [binary, "models"],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to list OpenCode models")
    models = []
    for raw_line in result.stdout.splitlines():
        line = ANSI_ESCAPE.sub("", raw_line).strip()
        if MODEL_ID.fullmatch(line):
            models.append(line)
    return list(dict.fromkeys(models))


def _codex_models_cache_path() -> Path:
    configured = os.environ.get("CODEX_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".codex"
    return base / "models_cache.json"


def _opencode_models_cache_path() -> Path:
    configured = os.environ.get("XDG_CACHE_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".cache"
    return base / "opencode" / "models.json"


def list_codex_models(cache_path: Path | None = None) -> list[dict[str, Any]]:
    path = (cache_path or _codex_models_cache_path()).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_models = payload.get("models") if isinstance(payload, dict) else None
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        raw_models = None

    models: list[dict[str, Any]] = []
    if isinstance(raw_models, list):
        for raw in raw_models:
            if not isinstance(raw, dict) or raw.get("visibility") != "list":
                continue
            model_id = str(raw.get("slug") or "")
            if not CODEX_MODEL_ID.fullmatch(model_id):
                continue
            raw_levels = raw.get("supported_reasoning_levels")
            levels = (
                [
                    str(item.get("effort"))
                    for item in raw_levels
                    if isinstance(item, dict)
                    and item.get("effort") in CODEX_REASONING_EFFORTS
                ]
                if isinstance(raw_levels, list)
                else []
            )
            if not levels:
                levels = list(CODEX_REASONING_EFFORTS)
            default_effort = str(raw.get("default_reasoning_level") or "")
            if default_effort not in levels:
                default_effort = "medium" if "medium" in levels else levels[0]
            modalities = raw.get("input_modalities")
            models.append(
                {
                    "id": model_id,
                    "label": str(raw.get("display_name") or model_id),
                    "description": str(raw.get("description") or ""),
                    "reasoning_efforts": levels,
                    "default_reasoning_effort": default_effort,
                    "vision": isinstance(modalities, list) and "image" in modalities,
                    "priority": (
                        raw.get("priority")
                        if isinstance(raw.get("priority"), int)
                        else 999
                    ),
                }
            )
    if not models:
        models = [
            {
                "id": model_id,
                "label": label,
                "description": description,
                "reasoning_efforts": list(efforts),
                "default_reasoning_effort": default_effort,
                "vision": True,
                "priority": index,
            }
            for index, (model_id, label, description, efforts, default_effort) in enumerate(
                FALLBACK_CODEX_MODELS, 1
            )
        ]
    return sorted(models, key=lambda item: (item["priority"], item["label"]))


def list_opencode_report_models(
    binary: str | None = None,
    *,
    installed_models: Sequence[str] | None = None,
    cache_path: Path | None = None,
) -> list[dict[str, Any]]:
    installed = set(
        installed_models
        if installed_models is not None
        else list_opencode_models(binary or "opencode")
    )
    metadata: dict[str, Any] = {}
    try:
        payload = json.loads(
            (cache_path or _opencode_models_cache_path()).read_text(encoding="utf-8")
        )
        provider = payload.get("deepseek") if isinstance(payload, dict) else None
        if isinstance(provider, dict) and isinstance(provider.get("models"), dict):
            metadata = provider["models"]
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass

    result: list[dict[str, Any]] = []
    for model_id, fallback_label, fallback_efforts in PREFERRED_OPENCODE_MODELS:
        if model_id not in installed:
            continue
        local_id = model_id.split("/", 1)[1]
        raw = metadata.get(local_id)
        raw = raw if isinstance(raw, dict) else {}
        discovered_efforts: list[str] = []
        reasoning_options = raw.get("reasoning_options")
        if isinstance(reasoning_options, list):
            for option in reasoning_options:
                if not isinstance(option, dict) or option.get("type") != "effort":
                    continue
                values = option.get("values")
                if isinstance(values, list):
                    discovered_efforts.extend(
                        str(value) for value in values if value in OPENCODE_VARIANTS
                    )
        efforts = list(dict.fromkeys(discovered_efforts)) or list(fallback_efforts)
        modalities = raw.get("modalities")
        inputs = modalities.get("input") if isinstance(modalities, dict) else []
        result.append(
            {
                "id": model_id,
                "label": str(raw.get("name") or fallback_label),
                "description": str(raw.get("description") or ""),
                "reasoning_efforts": efforts,
                "default_reasoning_effort": "high" if "high" in efforts else efforts[0],
                "vision": bool(raw.get("attachment")) or (
                    isinstance(inputs, list) and "image" in inputs
                ),
            }
        )
    return result


def text_field(
    fields: dict[str, list[tuple[str | None, bytes]]],
    name: str,
) -> str:
    values = fields.get(name) or []
    if not values:
        return ""
    if len(values) != 1 or values[0][0] is not None:
        raise ValueError(f"Invalid form field: {name}")
    value = values[0][1].decode("utf-8").strip()
    if len(value) > 2_048:
        raise ValueError(f"Form field is too long: {name}")
    return value


@dataclass(slots=True)
class ReportJob:
    job_id: str
    content_id: str
    report_type: str
    engine: str
    model: str
    reasoning_effort: str
    package_manifest: str
    log_path: Path
    codex_service_tier: str = "default"
    title: str = ""
    status: str = "queued"
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    cancel_requested: bool = False
    activity: str = "等待启动"
    activity_kind: str = "queued"
    last_event_at: str | None = None
    heartbeat_at: str | None = None
    current_stage: str | None = None
    stage_started_at: str | None = None
    raw_log_path: Path | None = None
    recent_events: list[dict[str, str]] = field(default_factory=list)
    process: subprocess.Popen[str] | None = field(default=None, repr=False)


class JobCancelledError(RuntimeError):
    pass


class JobTimeoutError(RuntimeError):
    pass


def _terminate_process(process: subprocess.Popen[str], *, force: bool = False) -> None:
    if process.poll() is not None:
        return
    termination_signal = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(process.pid, termination_signal)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill() if force else process.terminate()
        except ProcessLookupError:
            pass


class JobRegistry:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self._jobs: dict[str, ReportJob] = {}
        self._lock = threading.Lock()

    @staticmethod
    def display_status(status: str) -> str:
        if status in ACTIVE_JOB_STATUSES:
            return "running"
        if status == "completed":
            return "completed"
        return "failed"

    def has_active_job(self) -> bool:
        with self._lock:
            return any(job.status in ACTIVE_JOB_STATUSES for job in self._jobs.values())

    def add(self, job: ReportJob) -> None:
        with self._lock:
            if any(item.status in ACTIVE_JOB_STATUSES for item in self._jobs.values()):
                raise RuntimeError("Another report job is already running")
            if job.status not in JOB_STATUSES:
                raise ValueError(f"Invalid report job status: {job.status}")
            self._jobs[job.job_id] = job

    def get(self, job_id: str) -> ReportJob:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise KeyError(f"Unknown report job: {job_id}") from exc

    def update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in values.items():
                if key == "status" and value not in JOB_STATUSES:
                    raise ValueError(f"Invalid report job status: {value}")
                setattr(job, key, value)

    def record_event(self, job_id: str, kind: str, message: str) -> None:
        now = utc_now()
        with self._lock:
            job = self._jobs[job_id]
            job.activity_kind = kind
            job.activity = message
            job.last_event_at = now
            job.heartbeat_at = now
            job.recent_events.append({"at": now, "kind": kind, "message": message})
            if len(job.recent_events) > MAX_RECENT_EVENTS:
                del job.recent_events[:-MAX_RECENT_EVENTS]

    def heartbeat(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.status == "running":
                job.heartbeat_at = utc_now()

    def begin(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs[job_id]
            if job.cancel_requested or job.status == "cancelled":
                return False
            job.status = "running"
            job.started_at = utc_now()
            job.last_event_at = job.started_at
            job.heartbeat_at = job.started_at
            job.activity = "正在准备报告流水线"
            job.activity_kind = "starting"
            return True

    def attach_process(self, job_id: str, process: subprocess.Popen[str]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.process = process
            cancel_requested = job.cancel_requested
        if cancel_requested:
            _terminate_process(process)

    def clear_process(self, job_id: str, process: subprocess.Popen[str]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.process is process:
                job.process = None

    def request_cancel(self, job_id: str) -> ReportJob:
        with self._lock:
            try:
                job = self._jobs[job_id]
            except KeyError as exc:
                raise KeyError(f"Unknown report job: {job_id}") from exc
            if job.status not in ACTIVE_JOB_STATUSES:
                return job
            job.cancel_requested = True
            process = job.process
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = utc_now()
                job.error = "任务已由用户取消"
        if process is not None:
            _terminate_process(process)
        return job

    def stage_statuses(self, report_type: str, content_id: str) -> dict[str, str]:
        return {
            name: str(record["status"])
            for name, record in self.stage_details(report_type, content_id).items()
        }

    def stage_details(
        self, report_type: str, content_id: str
    ) -> dict[str, dict[str, Any]]:
        try:
            if report_type == "material":
                manifest = MaterialManifestStore(self.project_root).load(content_id)
                return {
                    name: {
                        "status": str(record.get("status") or "pending"),
                        "started_at": record.get("started_at"),
                        "finished_at": record.get("finished_at"),
                        "error": record.get("error"),
                        "retryable": None,
                    }
                    for name, record in manifest["stages"].items()
                }
            manifest = ManifestStore(self.project_root).load(content_id)
        except (FileNotFoundError, ValueError):
            return {}
        return {
            name: {
                "status": (
                    record.status.value
                    if hasattr(record.status, "value")
                    else str(record.status)
                ),
                "started_at": record.started_at,
                "finished_at": record.finished_at,
                "error": record.error,
                "retryable": record.retryable,
            }
            for name, record in manifest.stages.items()
        }

    def as_dict(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        definitions = (
            MATERIAL_STAGE_DEFINITIONS
            if job.report_type == "material"
            else VIDEO_STAGE_DEFINITIONS
        )
        stage_details = self.stage_details(job.report_type, job.content_id)
        stage_statuses = {
            name: str(record["status"]) for name, record in stage_details.items()
        }
        if self.display_status(job.status) == "failed":
            stage_statuses = {
                name: "failed" if status == "running" else status
                for name, status in stage_statuses.items()
            }
        current_stage = next(
            (key for key, _, _ in definitions if stage_statuses.get(key) == "running"),
            None,
        )
        if current_stage is None:
            current_stage = next(
                (key for key, _, _ in definitions if stage_statuses.get(key) == "failed"),
                None,
            )
        if current_stage is None and job.status in ACTIVE_JOB_STATUSES:
            current_stage = next(
                (key for key, _, _ in definitions if stage_statuses.get(key) != "completed"),
                None,
            )
        with self._lock:
            live_job = self._jobs[job_id]
            if current_stage and current_stage != live_job.current_stage:
                live_job.current_stage = current_stage
                live_job.stage_started_at = (
                    stage_details.get(current_stage, {}).get("started_at") or utc_now()
                )
            process_alive = bool(
                live_job.process is not None and live_job.process.poll() is None
            )
            activity = live_job.activity
            activity_kind = live_job.activity_kind
            last_event_at = live_job.last_event_at
            heartbeat_at = live_job.heartbeat_at
            stage_started_at = live_job.stage_started_at
            recent_events = list(live_job.recent_events)
            raw_log_path = live_job.raw_log_path
        completed_stage_count = sum(
            stage_statuses.get(key) == "completed" for key, _, _ in definitions
        )
        current_stage_label = next(
            (label for key, label, _ in definitions if key == current_stage), None
        )
        current_stage_detail = stage_details.get(current_stage or "", {})
        retrying = bool(
            job.status in ACTIVE_JOB_STATUSES
            and current_stage_detail.get("status") == "failed"
            and current_stage_detail.get("retryable") is not False
        )
        return {
            "job_id": job.job_id,
            "report_type": job.report_type,
            "content_id": job.content_id,
            "title": job.title or job.content_id,
            "video_id": job.content_id if job.report_type == "video" else None,
            "material_id": job.content_id if job.report_type == "material" else None,
            "engine": job.engine,
            "model": job.model,
            "reasoning_effort": job.reasoning_effort,
            "codex_service_tier": job.codex_service_tier,
            "status": job.status,
            "display_status": self.display_status(job.status),
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "error": job.error,
            "cancel_requested": job.cancel_requested,
            "result": job.result,
            "package_manifest": job.package_manifest,
            "stage_statuses": stage_statuses,
            "stage_details": stage_details,
            "current_stage": current_stage,
            "current_stage_label": current_stage_label,
            "stage_started_at": stage_started_at,
            "completed_stage_count": completed_stage_count,
            "total_stage_count": len(definitions),
            "progress_percent": round(
                completed_stage_count * 100 / len(definitions)
            ),
            "retrying": retrying,
            "current_stage_error": current_stage_detail.get("error"),
            "current_stage_retryable": current_stage_detail.get("retryable"),
            "activity": activity,
            "activity_kind": activity_kind,
            "last_event_at": last_event_at,
            "heartbeat_at": heartbeat_at,
            "process_alive": process_alive,
            "recent_events": recent_events,
            "raw_log_available": bool(raw_log_path and raw_log_path.is_file()),
            "stage_definitions": [
                {"key": key, "label": label, "description": description}
                for key, label, description in definitions
            ],
            "log": read_log_tail(job.log_path),
        }

    def list_dicts(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda job: (job.created_at, job.job_id),
                reverse=True,
            )
            return [
                {
                    "job_id": job.job_id,
                    "report_type": job.report_type,
                    "content_id": job.content_id,
                    "title": job.title or job.content_id,
                    "engine": job.engine,
                    "model": job.model,
                    "reasoning_effort": job.reasoning_effort,
                    "codex_service_tier": job.codex_service_tier,
                    "activity": job.activity,
                    "heartbeat_at": job.heartbeat_at,
                    "status": job.status,
                    "display_status": self.display_status(job.status),
                    "created_at": job.created_at,
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "error": job.error,
                    "report_url": (job.result or {}).get("report_url"),
                }
                for job in jobs
            ]


def read_log_tail(path: Path) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        start = max(0, size - MAX_LOG_BYTES)
        stream.seek(start)
        payload = stream.read()
        if start and b"\n" in payload:
            payload = payload.split(b"\n", 1)[1]
        return payload.decode("utf-8", "replace")


@dataclass(frozen=True, slots=True)
class WebServerConfig:
    project_root: Path
    output_root: Path
    codex_binary: str | None
    opencode_binary: str | None
    default_codex_model: str
    default_opencode_model: str
    default_reasoning_effort: str
    sandbox: str
    web_root: Path
    job_timeout_seconds: float = 7200.0
    default_codex_service_tier: str = "fast"


class ReportWebApplication:
    def __init__(self, config: WebServerConfig):
        self.config = config
        self.registry = JobRegistry(config.project_root)
        self.upload_root = config.project_root / "work" / "_automation_uploads"

    def create_job(
        self,
        *,
        package_manifest: Path,
        report_type: str,
        engine: str,
        model: str,
        reasoning_effort: str,
        codex_service_tier: str = "default",
    ) -> ReportJob:
        if engine not in ENGINES:
            raise ValueError("Unsupported automation engine")
        if report_type not in REPORT_TYPES:
            raise ValueError("Unsupported report type")
        if not model or any(character in model for character in "\r\n\0"):
            raise ValueError("A valid model is required")
        if engine == "codex" and not self.config.codex_binary:
            raise RuntimeError("Codex CLI is not available on this computer")
        if engine == "opencode" and not self.config.opencode_binary:
            raise RuntimeError("OpenCode is not available on this computer")
        model_catalog = (
            list_codex_models()
            if engine == "codex"
            else list_opencode_report_models(self.config.opencode_binary)
        )
        selected_model = next(
            (item for item in model_catalog if item["id"] == model), None
        )
        if selected_model is None:
            raise ValueError("请选择当前执行引擎提供的模型")
        if reasoning_effort not in selected_model["reasoning_efforts"]:
            raise ValueError("所选模型不支持这个推理强度")
        if codex_service_tier not in CODEX_SERVICE_TIERS:
            raise ValueError("请选择有效的 Codex 服务层")
        if engine != "codex":
            codex_service_tier = "default"
        if report_type == "video":
            metadata = load_package_metadata(package_manifest)
            content_id = metadata["video_id"]
        else:
            metadata = load_material_metadata(package_manifest)
            content_id = metadata["material_id"]
        job_id = uuid.uuid4().hex
        log_path = self.config.project_root / "work" / "_automation_jobs" / f"{job_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        job = ReportJob(
            job_id=job_id,
            content_id=content_id,
            report_type=report_type,
            engine=engine,
            model=model,
            reasoning_effort=reasoning_effort,
            package_manifest=str(package_manifest.resolve()),
            log_path=log_path,
            codex_service_tier=codex_service_tier,
            title=str(metadata.get("title") or content_id),
        )
        self.registry.add(job)
        thread = threading.Thread(
            target=self._run_job,
            args=(job,),
            name=f"report-job-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job

    def _run_job(self, job: ReportJob) -> None:
        if not self.registry.begin(job.job_id):
            return
        job_heartbeat_stop = threading.Event()

        def maintain_job_heartbeat() -> None:
            while not job_heartbeat_stop.wait(2):
                self.registry.heartbeat(job.job_id)

        job_heartbeat_thread = threading.Thread(
            target=maintain_job_heartbeat,
            name=f"report-job-heartbeat-{job.job_id[:8]}",
            daemon=True,
        )
        job_heartbeat_thread.start()
        config = AutomationConfig(
            project_root=self.config.project_root,
            package=Path(job.package_manifest),
            model=job.model,
            reasoning_effort=job.reasoning_effort,
            output_root=self.config.output_root,
            engine=job.engine,
            codex_binary=self.config.codex_binary or "codex",
            opencode_binary=self.config.opencode_binary or "opencode",
            sandbox=self.config.sandbox,
            report_type=job.report_type,
            codex_service_tier=job.codex_service_tier,
        )

        def logged_runner(
            command: list[str],
            *,
            input: str | None,
            text: bool,
            cwd: Path,
            check: bool,
        ) -> subprocess.CompletedProcess[str]:
            del text, check
            raw_log_path = job.log_path.with_suffix(".raw.jsonl")
            raw_tail: list[str] = []
            self.registry.update(job.job_id, raw_log_path=raw_log_path)
            with (
                job.log_path.open("a", encoding="utf-8", buffering=1) as log,
                raw_log_path.open("a", encoding="utf-8", buffering=1) as raw_log,
            ):
                started_message = (
                    f"启动 {job.engine} {job.report_type} 自动化"
                    + (
                        " · Fast 模式"
                        if job.engine == "codex" and job.codex_service_tier == "fast"
                        else ""
                    )
                )
                log.write(f"[{utc_now()}] {started_message}\n")
                self.registry.record_event(job.job_id, "starting", started_message)
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=cwd,
                    start_new_session=True,
                )
                self.registry.attach_process(job.job_id, process)
                reader_errors: list[BaseException] = []

                def consume_output() -> None:
                    try:
                        if process.stdout is None:
                            return
                        for raw_line in process.stdout:
                            raw_log.write(raw_line)
                            raw_tail.append(_one_line(raw_line, 1_000))
                            if len(raw_tail) > 80:
                                del raw_tail[:-80]
                            summary = summarize_engine_line(raw_line, job.engine)
                            if summary is None:
                                continue
                            kind, message = summary
                            log.write(f"[{utc_now()}] {message}\n")
                            self.registry.record_event(job.job_id, kind, message)
                    except BaseException as exc:  # surfaced on the job thread below
                        reader_errors.append(exc)

                reader_thread = threading.Thread(
                    target=consume_output,
                    name=f"report-events-{job.job_id[:8]}",
                    daemon=True,
                )
                reader_thread.start()
                try:
                    if process.stdin is not None:
                        try:
                            if input:
                                process.stdin.write(input)
                            process.stdin.close()
                        except BrokenPipeError:
                            pass
                    process.wait(timeout=self.config.job_timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    _terminate_process(process)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        _terminate_process(process, force=True)
                        process.wait()
                    raise JobTimeoutError(
                        f"报告任务超过 {self.config.job_timeout_seconds:g} 秒，已终止"
                    ) from exc
                finally:
                    reader_thread.join(timeout=5)
                    if process.stdout is not None:
                        process.stdout.close()
                    self.registry.clear_process(job.job_id, process)
                if reader_errors:
                    raise RuntimeError(f"读取引擎事件失败：{reader_errors[0]}")
                if job.cancel_requested:
                    raise JobCancelledError("任务已由用户取消")
                exit_message = f"模型进程退出（状态 {process.returncode}）"
                log.write(f"[{utc_now()}] {exit_message}\n")
                self.registry.record_event(job.job_id, "engine_exit", exit_message)
                if process.returncode == 0:
                    validation_message = "模型阶段结束，正在进行浏览器验收与产物封板"
                    log.write(f"[{utc_now()}] {validation_message}\n")
                    self.registry.record_event(
                        job.job_id, "validating", validation_message
                    )
            if (
                process.returncode != 0
                and job.engine == "codex"
                and job.codex_service_tier == "fast"
                and fast_tier_unavailable("\n".join(raw_tail))
            ):
                fallback_command = [
                    'service_tier="default"'
                    if argument == 'service_tier="fast"'
                    else argument
                    for argument in command
                ]
                if fallback_command != command:
                    fallback_message = "Fast 模式当前不可用，自动回退标准服务层重试"
                    with job.log_path.open("a", encoding="utf-8") as fallback_log:
                        fallback_log.write(f"[{utc_now()}] {fallback_message}\n")
                    self.registry.update(job.job_id, codex_service_tier="default")
                    self.registry.record_event(
                        job.job_id, "fallback", fallback_message
                    )
                    return logged_runner(
                        fallback_command,
                        input=input,
                        text=True,
                        cwd=cwd,
                        check=False,
                    )
            return subprocess.CompletedProcess(command, process.returncode)

        try:
            self.registry.record_event(
                job.job_id, "pipeline", "正在检查已有产物并从首个未完成阶段恢复"
            )
            result = run_automation(config, run_command=logged_runner)
            if job.cancel_requested:
                raise JobCancelledError("任务已由用户取消")
            output_directory = Path(str(result["output_directory"]))
            relative = output_directory.relative_to(self.config.output_root)
            result["report_url"] = "/outputs/" + relative.as_posix() + "/index.html"
            result["markdown_url"] = "/outputs/" + relative.as_posix() + "/report.md"
            self.registry.update(
                job.job_id,
                status="completed",
                finished_at=utc_now(),
                result=result,
            )
            self.registry.record_event(job.job_id, "completed", "报告已完成并通过验收")
        except Exception as exc:
            with job.log_path.open("a", encoding="utf-8") as log:
                log.write("\n" + traceback.format_exc() + "\n")
            self.registry.update(
                job.job_id,
                status=(
                    "cancelled"
                    if isinstance(exc, JobCancelledError)
                    else "timed_out"
                    if isinstance(exc, JobTimeoutError)
                    else "failed"
                ),
                finished_at=utc_now(),
                error=str(exc),
            )
            self.registry.record_event(job.job_id, "error", f"任务停止：{exc}")
        finally:
            job_heartbeat_stop.set()
            job_heartbeat_thread.join(timeout=3)


class ReportRequestHandler(BaseHTTPRequestHandler):
    server_version = "ContentReportUI/1.0"

    @property
    def application(self) -> ReportWebApplication:
        return self.server.application  # type: ignore[attr-defined,no-any-return]

    def log_message(self, format: str, *args: object) -> None:
        sys_message = f"{self.address_string()} - {format % args}"
        print(sys_message)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str, *, report: bool = False) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if report:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' https: data:; font-src 'self' data:; "
                "script-src 'none'; connect-src 'none'; frame-src 'none'; "
                "object-src 'none'; base-uri 'none'; form-action 'none'",
            )
        else:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path == "/":
            self._send_file(self.application.config.web_root / "index.html", "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self._send_file(self.application.config.web_root / "app.js", "text/javascript; charset=utf-8")
            return
        if path == "/styles.css":
            self._send_file(self.application.config.web_root / "styles.css", "text/css; charset=utf-8")
            return
        if path == "/api/config":
            self._send_json(
                HTTPStatus.OK,
                {
                    "engines": {
                        "codex": {"available": bool(self.application.config.codex_binary)},
                        "opencode": {"available": bool(self.application.config.opencode_binary)},
                    },
                    "default_models": {
                        "codex": self.application.config.default_codex_model,
                        "opencode": self.application.config.default_opencode_model,
                    },
                    "default_reasoning_effort": self.application.config.default_reasoning_effort,
                    "default_codex_service_tier": self.application.config.default_codex_service_tier,
                    "codex_service_tiers": list(CODEX_SERVICE_TIERS),
                    "reasoning_efforts": {
                        "codex": list(CODEX_REASONING_EFFORTS),
                        "opencode": list(OPENCODE_VARIANTS),
                    },
                    "report_types": {
                        "video": {
                            "label": "视频报告",
                            "description": "从已验证的标准字幕包生成忠实于视频内容的三层报告。",
                        },
                        "material": {
                            "label": "素材报告",
                            "description": "从一个 ZIP 内的 Word、文本、HTML 和文字图片生成综合报告。",
                        },
                    },
                    "material_extensions": list(SUPPORTED_MATERIAL_EXTENSIONS),
                    "material_images_require_vision_model": True,
                    "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
                },
            )
            return
        if path == "/api/models":
            engine = (parse_qs(parsed.query).get("engine") or [""])[0]
            if engine not in ENGINES:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Unsupported automation engine"})
                return
            binary = (
                self.application.config.codex_binary
                if engine == "codex"
                else self.application.config.opencode_binary
            )
            if not binary:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": f"{engine} is not available"},
                )
                return
            try:
                models = (
                    list_codex_models()
                    if engine == "codex"
                    else list_opencode_report_models(binary)
                )
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"models": models})
            return
        if path == "/api/jobs":
            jobs = self.application.registry.list_dicts()
            counts = {"running": 0, "completed": 0, "failed": 0}
            for job in jobs:
                counts[str(job["display_status"])] += 1
            self._send_json(
                HTTPStatus.OK,
                {"jobs": jobs, "counts": counts, "total": len(jobs)},
            )
            return
        if path.startswith("/api/jobs/") and path.endswith("/raw-log"):
            job_id = path.removeprefix("/api/jobs/").removesuffix("/raw-log")
            try:
                job = self.application.registry.get(job_id)
            except KeyError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if job.raw_log_path is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_file(job.raw_log_path, "application/x-ndjson; charset=utf-8")
            return
        if path.startswith("/api/jobs/"):
            job_id = path.removeprefix("/api/jobs/")
            try:
                payload = self.application.registry.as_dict(job_id)
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, payload)
            return
        if path.startswith("/outputs/"):
            try:
                relative = _safe_relative_path(path.removeprefix("/outputs/"))
                target = (self.application.config.output_root / relative).resolve()
                target.relative_to(self.application.config.output_root)
            except ValueError:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            suffix_types = {
                ".html": "text/html; charset=utf-8",
                ".md": "text/markdown; charset=utf-8",
                ".json": "application/json; charset=utf-8",
            }
            self._send_file(
                target,
                suffix_types.get(target.suffix.lower(), "application/octet-stream"),
                report=target.suffix.lower() == ".html",
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/api/jobs":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if self.application.registry.has_active_job():
            self._send_json(
                HTTPStatus.CONFLICT,
                {"error": "当前已有报告正在生成，请等待完成后再提交。"},
            )
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("Request body is empty or exceeds the upload size limit")
            content_type = self.headers.get("Content-Type") or ""
            if not content_type.lower().startswith("multipart/form-data"):
                raise ValueError("Expected multipart/form-data")
            fields = parse_multipart(content_type, self.rfile.read(length))
            report_type = text_field(fields, "report_type") or "video"
            if report_type not in REPORT_TYPES:
                raise ValueError("请选择有效的报告类型")
            engine = text_field(fields, "engine") or "codex"
            default_models = {
                "codex": self.application.config.default_codex_model,
                "opencode": self.application.config.default_opencode_model,
            }
            model = text_field(fields, "model") or default_models.get(engine, "")
            reasoning_effort = (
                text_field(fields, "reasoning_effort")
                or self.application.config.default_reasoning_effort
            )
            codex_service_tier = (
                text_field(fields, "codex_service_tier")
                or self.application.config.default_codex_service_tier
            )
            package_path = text_field(fields, "package_path")
            upload_id = uuid.uuid4().hex
            upload_directory = self.application.upload_root / upload_id
            archive_values = fields.get("package_archive") or []
            directory_values = fields.get("package_file") or []
            supplied_modes = sum(
                bool(value) for value in (package_path, archive_values, directory_values)
            )
            if supplied_modes != 1:
                raise ValueError("请选择一种导入方式：目录、ZIP或服务器路径")
            if report_type == "material":
                if package_path or directory_values:
                    raise ValueError("素材报告当前只接受一个 ZIP 文件")
                if len(archive_values) != 1:
                    raise ValueError("素材报告每次只能上传一个 ZIP 文件")
                filename, archive_payload = archive_values[0]
                if not filename or not filename.lower().endswith(".zip"):
                    raise ValueError("素材报告必须上传 ZIP 文件")
                package_manifest = extract_material_archive(
                    archive_payload,
                    upload_directory,
                    filename,
                    textutil_binary=shutil.which("textutil"),
                )
            elif package_path:
                package_manifest = Path(package_path).expanduser().resolve()
                if package_manifest.is_dir():
                    package_manifest = package_manifest / "package.json"
            elif archive_values:
                if len(archive_values) != 1:
                    raise ValueError("Only one ZIP archive may be uploaded")
                filename, archive_payload = archive_values[0]
                if not filename or not filename.lower().endswith(".zip"):
                    raise ValueError("Transcript archive must be a .zip file")
                package_manifest = extract_package_archive(archive_payload, upload_directory)
            else:
                files = [
                    (filename, payload)
                    for filename, payload in directory_values
                    if filename is not None
                ]
                package_manifest = save_package_files(files, upload_directory)
            job = self.application.create_job(
                package_manifest=package_manifest,
                report_type=report_type,
                engine=engine,
                model=model,
                reasoning_effort=reasoning_effort,
                codex_service_tier=codex_service_tier,
            )
            self._send_json(
                HTTPStatus.ACCEPTED,
                {
                    "job_id": job.job_id,
                    "report_type": job.report_type,
                    "content_id": job.content_id,
                    "video_id": job.content_id if job.report_type == "video" else None,
                    "material_id": job.content_id if job.report_type == "material" else None,
                    "status": job.status,
                },
            )
        except (ValueError, FileNotFoundError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except RuntimeError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_DELETE(self) -> None:  # noqa: N802
        path = unquote(urlsplit(self.path).path)
        if not path.startswith("/api/jobs/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        job_id = path.removeprefix("/api/jobs/")
        if not job_id or "/" in job_id:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid job ID"})
            return
        try:
            self.application.registry.request_cancel(job_id)
            self._send_json(
                HTTPStatus.ACCEPTED,
                self.application.registry.as_dict(job_id),
            )
        except KeyError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})


class ReportHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], application: ReportWebApplication):
        super().__init__(address, ReportRequestHandler)
        self.application = application


def parser() -> argparse.ArgumentParser:
    project_root = Path(__file__).resolve().parents[2]
    result = argparse.ArgumentParser(description="Serve the local content report control panel.")
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--port", type=int, default=8765)
    result.add_argument("--project-root", type=Path, default=project_root)
    result.add_argument("--output-root", type=Path, default=Path("output"))
    result.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    result.add_argument("--opencode-bin", default=os.environ.get("OPENCODE_BIN", "opencode"))
    result.add_argument("--default-codex-model", default=os.environ.get("REPORT_CODEX_MODEL", ""))
    result.add_argument("--default-opencode-model", default=os.environ.get("REPORT_OPENCODE_MODEL", ""))
    result.add_argument(
        "--default-reasoning-effort",
        choices=REASONING_EFFORTS,
        default=os.environ.get("REPORT_CODEX_REASONING_EFFORT", "high"),
    )
    result.add_argument(
        "--default-codex-service-tier",
        choices=CODEX_SERVICE_TIERS,
        default=os.environ.get("REPORT_CODEX_SERVICE_TIER", "fast"),
        help="Default Codex service tier shown in the web form",
    )
    result.add_argument("--sandbox", choices=SANDBOX_MODES, default="workspace-write")
    result.add_argument(
        "--job-timeout-seconds",
        type=float,
        default=float(os.environ.get("REPORT_JOB_TIMEOUT_SECONDS", "7200")),
        help="Terminate a report engine process after this many seconds",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        project_root = arguments.project_root.expanduser().resolve()
        if not (project_root / "pyproject.toml").is_file():
            raise ValueError(f"Not a report project root: {project_root}")
        output_root = arguments.output_root.expanduser()
        if not output_root.is_absolute():
            output_root = project_root / output_root
        output_root = output_root.resolve()
        output_root.relative_to(project_root)
        try:
            codex_binary = resolve_engine_binary(arguments.codex_bin)
        except FileNotFoundError:
            codex_binary = None
        try:
            opencode_binary = resolve_engine_binary(arguments.opencode_bin)
        except FileNotFoundError:
            opencode_binary = None
        if not codex_binary and not opencode_binary:
            raise FileNotFoundError("Neither Codex CLI nor OpenCode is available")
        if (
            not math.isfinite(arguments.job_timeout_seconds)
            or arguments.job_timeout_seconds <= 0
        ):
            raise ValueError("job timeout must be positive")
        web_root = project_root / "web"
        for filename in ("index.html", "app.js", "styles.css"):
            if not (web_root / filename).is_file():
                raise FileNotFoundError(web_root / filename)
        application = ReportWebApplication(
            WebServerConfig(
                project_root=project_root,
                output_root=output_root,
                codex_binary=codex_binary,
                opencode_binary=opencode_binary,
                default_codex_model=arguments.default_codex_model,
                default_opencode_model=arguments.default_opencode_model,
                default_reasoning_effort=arguments.default_reasoning_effort,
                sandbox=arguments.sandbox,
                web_root=web_root,
                job_timeout_seconds=arguments.job_timeout_seconds,
                default_codex_service_tier=arguments.default_codex_service_tier,
            )
        )
        server = ReportHTTPServer((arguments.host, arguments.port), application)
        host, port = server.server_address[:2]
        print(f"Content report control panel: http://{host}:{port}")
        print("Press Ctrl+C to stop.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
