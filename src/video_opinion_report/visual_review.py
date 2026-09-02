from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import struct
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator, Sequence


DEFAULT_BROWSER_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
VIEWPORTS = (
    ("desktop", 1440, 1000),
    ("mobile", 390, 844),
)


class VisualReviewUnavailableError(RuntimeError):
    """Raised when a real browser render cannot be performed on this computer."""


class VisualReviewFailedError(RuntimeError):
    """Raised when the browser rendered an invalid or effectively blank page."""


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        del format, args


def find_browser_binary(explicit: str | None = None) -> str:
    candidates = ([explicit] if explicit else []) + list(DEFAULT_BROWSER_CANDIDATES)
    for value in candidates:
        if not value:
            continue
        candidate = Path(value).expanduser()
        if candidate.is_file() and candidate.stat().st_mode & 0o111:
            return str(candidate.resolve())
        resolved = shutil.which(value)
        if resolved:
            return resolved
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise VisualReviewUnavailableError(
        "未找到可用于网页验收的 Chrome/Chromium；报告已生成，可稍后只恢复网页验收阶段"
    )


@contextmanager
def _serve_directory(directory: Path) -> Iterator[str]:
    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_address[1])
        yield f"http://127.0.0.1:{port}/index.html"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _png_dimensions(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    if len(payload) < 33 or payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise VisualReviewFailedError(f"浏览器未生成有效 PNG：{path.name}")
    width, height = struct.unpack(">II", payload[16:24])
    return int(width), int(height)


def _capture_screenshot(command: Sequence[str], screenshot: Path) -> None:
    screenshot.unlink(missing_ok=True)
    try:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise VisualReviewUnavailableError(f"浏览器网页验收未能启动：{exc}") from exc

    deadline = time.monotonic() + 30
    previous_size = -1
    stable_observations = 0
    try:
        while time.monotonic() < deadline:
            if screenshot.is_file():
                size = screenshot.stat().st_size
                if size > 0 and size == previous_size:
                    stable_observations += 1
                    if stable_observations >= 3:
                        return
                else:
                    stable_observations = 0
                    previous_size = size
            if process.poll() is not None:
                break
            time.sleep(0.1)
        if screenshot.is_file() and screenshot.stat().st_size > 0:
            return
        raise VisualReviewUnavailableError("浏览器网页验收超时，未生成截图")
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    process.kill()
                process.wait()


def _playwright_runtime() -> tuple[Path, Path] | None:
    dependency_root = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
    )
    node = dependency_root / "node" / "bin" / "node"
    node_modules = dependency_root / "node" / "node_modules"
    if node.is_file() and (node_modules / "playwright").is_dir():
        return node, node_modules
    return None


def _run_playwright_review(
    browser: str,
    url: str,
    destination: Path,
) -> list[dict[str, Any]] | None:
    runtime = _playwright_runtime()
    script = Path(__file__).resolve().parents[2] / "scripts" / "browser_review.cjs"
    if runtime is None or not script.is_file():
        return None
    node, node_modules = runtime
    environment = os.environ.copy()
    environment["NODE_PATH"] = str(node_modules)
    try:
        completed = subprocess.run(
            [str(node), str(script), browser, url, str(destination)],
            text=True,
            capture_output=True,
            check=False,
            timeout=75,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VisualReviewUnavailableError(
            f"Playwright 网页验收未能完成：{exc}"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise VisualReviewUnavailableError(
            "Playwright 网页验收执行失败"
            + (f"：{detail[-500:]}" if detail else "")
        )
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        raw_checks = payload["checks"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise VisualReviewUnavailableError("Playwright 网页验收未返回有效结果") from exc
    if not isinstance(raw_checks, list) or len(raw_checks) != 2:
        raise VisualReviewUnavailableError("Playwright 网页验收结果不完整")

    checks: list[dict[str, Any]] = []
    for raw in raw_checks:
        if not isinstance(raw, dict) or not isinstance(raw.get("metrics"), dict):
            raise VisualReviewUnavailableError("Playwright 视口结果格式无效")
        metrics = raw["metrics"]
        if metrics.get("globalOverflow") is True or metrics.get("offenders"):
            raise VisualReviewFailedError(
                f"{raw.get('viewport')} 视口存在页面横向溢出："
                f"{metrics.get('offenders') or 'document overflow'}"
            )
        if int(metrics.get("textLength") or 0) < 100:
            raise VisualReviewFailedError(
                f"{raw.get('viewport')} 视口没有渲染出完整报告正文"
            )
        screenshot = Path(str(raw.get("screenshot") or "")).resolve()
        rendered_width, rendered_height = _png_dimensions(screenshot)
        requested_size = raw.get("requestedSize")
        if not isinstance(requested_size, list) or len(requested_size) != 2:
            raise VisualReviewUnavailableError("Playwright 视口尺寸结果无效")
        size = screenshot.stat().st_size
        if [rendered_width, rendered_height] != requested_size or size < 4_096:
            raise VisualReviewFailedError(
                f"{raw.get('viewport')} 截图尺寸或内容异常："
                f"{rendered_width}x{rendered_height}，{size} bytes"
            )
        checks.append(
            {
                "viewport": str(raw.get("viewport") or ""),
                "requested_size": requested_size,
                "rendered_size": [rendered_width, rendered_height],
                "document_client_width": int(metrics.get("clientWidth") or 0),
                "document_scroll_width": int(metrics.get("scrollWidth") or 0),
                "screenshot": str(screenshot),
                "screenshot_bytes": size,
                "screenshot_sha256": hashlib.sha256(
                    screenshot.read_bytes()
                ).hexdigest(),
            }
        )
    return checks


def run_headless_visual_review(
    report_html: Path,
    review_directory: Path,
    *,
    browser_binary: str | None = None,
) -> dict[str, Any]:
    """Render desktop and mobile screenshots through a real local browser.

    The review is intentionally deterministic: structural HTML validation remains a
    separate gate, while this gate proves that Chrome can load and paint the page at
    two representative viewports. The screenshots are retained for audit.
    """

    report = report_html.expanduser().resolve()
    if not report.is_file():
        raise FileNotFoundError(report)
    browser = find_browser_binary(browser_binary)
    destination = review_directory.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []

    with _serve_directory(report.parent) as url, tempfile.TemporaryDirectory(
        prefix="report-browser-profile-"
    ) as profile_directory:
        playwright_checks = _run_playwright_review(browser, url, destination)
        if playwright_checks is not None:
            return {
                "visual_review_completed": True,
                "visual_review_method": "playwright_chromium_desktop_and_mobile",
                "browser_binary": browser,
                "checks": playwright_checks,
            }
        for name, width, height in VIEWPORTS:
            screenshot = destination / f"html-review-{name}.png"
            command: Sequence[str] = (
                browser,
                "--headless=new",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-sync",
                "--hide-scrollbars",
                "--metrics-recording-only",
                "--no-default-browser-check",
                "--no-first-run",
                f"--user-data-dir={profile_directory}",
                f"--window-size={width},{height}",
                f"--screenshot={screenshot}",
                "--virtual-time-budget=2500",
                url,
            )
            _capture_screenshot(command, screenshot)
            rendered_width, rendered_height = _png_dimensions(screenshot)
            size = screenshot.stat().st_size
            if rendered_width < width or rendered_height < height or size < 4_096:
                raise VisualReviewFailedError(
                    f"{name} 视口截图尺寸或内容异常："
                    f"{rendered_width}x{rendered_height}，{size} bytes"
                )
            checks.append(
                {
                    "viewport": name,
                    "requested_size": [width, height],
                    "rendered_size": [rendered_width, rendered_height],
                    "screenshot": str(screenshot),
                    "screenshot_bytes": size,
                    "screenshot_sha256": hashlib.sha256(
                        screenshot.read_bytes()
                    ).hexdigest(),
                }
            )

    return {
        "visual_review_completed": True,
        "visual_review_method": "headless_chrome_desktop_and_mobile_smoke",
        "browser_binary": browser,
        "checks": checks,
    }
