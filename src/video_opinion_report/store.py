from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .integrity import sha256_file
from .models import RunManifest


VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def validate_video_id(video_id: str) -> str:
    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise ValueError(
            "Invalid video ID; expected 1-128 ASCII letters, digits, underscores, or hyphens"
        )
    return video_id


def sha256_artifact(path: Path) -> str:
    if path.is_symlink():
        raise ValueError(f"Artifact symlinks are not allowed: {path}")
    resolved = path.resolve()
    if resolved.is_file():
        return sha256_file(resolved)
    if not resolved.is_dir():
        raise FileNotFoundError(resolved)
    digest = hashlib.sha256()
    children = list(resolved.rglob("*"))
    symlinks = [child for child in children if child.is_symlink()]
    if symlinks:
        raise ValueError(f"Artifact symlinks are not allowed: {symlinks[0]}")
    for child in sorted(item for item in children if item.is_file()):
        relative = child.relative_to(resolved).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        file_hash = sha256_file(child).encode("ascii")
        digest.update(file_hash)
    return digest.hexdigest()


class ManifestStore:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()

    def run_dir(self, video_id: str) -> Path:
        return self.project_root / "work" / validate_video_id(video_id)

    def manifest_path(self, video_id: str) -> Path:
        return self.run_dir(video_id) / "manifest.json"

    def create(self, video_id: str, source_url: str) -> RunManifest:
        path = self.manifest_path(video_id)
        if path.exists():
            raise FileExistsError(f"Run already exists: {path}")
        manifest = RunManifest.create(video_id, source_url)
        self.save(manifest)
        return manifest

    def load(self, video_id: str) -> RunManifest:
        path = self.manifest_path(video_id)
        if not path.exists():
            raise FileNotFoundError(f"Run does not exist: {path}")
        return RunManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, manifest: RunManifest) -> None:
        validate_video_id(manifest.video_id)
        path = self.manifest_path(manifest.video_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.project_root))

    def set_artifact(self, manifest: RunManifest, key: str, path: Path) -> None:
        resolved = path.resolve()
        manifest.artifacts[key] = self.relative(resolved)
        manifest.artifact_hashes[key] = sha256_artifact(resolved)

    def artifact_path(self, manifest: RunManifest, key: str) -> Path:
        relative = manifest.artifacts.get(key)
        if not relative:
            raise FileNotFoundError(f"Manifest artifact is missing: {key}")
        path = (self.project_root / relative).resolve()
        path.relative_to(self.project_root)
        if not path.exists():
            raise FileNotFoundError(path)
        expected_hash = manifest.artifact_hashes.get(key)
        if expected_hash and sha256_artifact(path) != expected_hash:
            raise RuntimeError(f"Manifest artifact changed after validation: {key}")
        return path


class ProcessedReportStore:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.path = self.project_root / "state" / "processed-reports.json"

    def load(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            entries = payload
        else:
            entries = payload.get("reports", [])
        return {
            validate_video_id(str(item["video_id"])): dict(item)
            for item in entries
        }

    def contains(self, video_id: str) -> bool:
        return validate_video_id(video_id) in self.load()

    def add(self, entry: dict[str, object]) -> None:
        video_id = validate_video_id(str(entry["video_id"]))
        reports = self.load()
        reports[video_id] = dict(entry)
        self._save(reports)

    def remove(self, video_id: str) -> None:
        reports = self.load()
        if reports.pop(validate_video_id(video_id), None) is None:
            return
        self._save(reports)

    def _save(self, reports: dict[str, dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "reports": sorted(reports.values(), key=lambda item: str(item["video_id"])),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
