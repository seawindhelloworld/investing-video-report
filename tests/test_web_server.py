from __future__ import annotations

import io
import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_opinion_report.web_server import (
    VIDEO_STAGE_DEFINITIONS,
    JobRegistry,
    ReportJob,
    ReportHTTPServer,
    ReportWebApplication,
    WebServerConfig,
    extract_package_archive,
    fast_tier_unavailable,
    list_codex_models,
    list_opencode_models,
    list_opencode_report_models,
    parse_multipart,
    save_package_files,
    summarize_engine_line,
    text_field,
)


class WebServerTests(unittest.TestCase):
    def test_video_ui_exposes_only_the_five_meaning_report_stages(self) -> None:
        self.assertEqual(
            [key for key, _, _ in VIDEO_STAGE_DEFINITIONS],
            ["ingest", "analyze", "render", "html_validate", "complete"],
        )
        script = (
            Path(__file__).resolve().parents[1] / "web" / "app.js"
        ).read_text(encoding="utf-8")
        video_block = script.split("const VIDEO_STAGES = [", 1)[1].split(
            "];", 1
        )[0]
        self.assertIn("原意分析与成稿", video_block)
        self.assertNotIn("research", video_block)
        self.assertNotIn("judgment", video_block)
        self.assertNotIn("fidelity_review", video_block)

    def test_frontend_automatically_follows_new_active_job(self) -> None:
        script = (
            Path(__file__).resolve().parents[1] / "web" / "app.js"
        ).read_text(encoding="utf-8")
        self.assertIn("function renderSubmittingState()", script)
        self.assertIn("active.job_id !== currentJobId", script)
        self.assertIn("await openJob(active.job_id)", script)
        self.assertIn("renderSubmittingState();", script)
        self.assertIn("}, 2000);", script)
        self.assertNotIn('data.append(\n    "regenerate"', script)
        page = (
            Path(__file__).resolve().parents[1] / "web" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn('id="regenerate-report"', page)
        self.assertNotIn('id="open-previous-report"', page)

    def make_zip(self, files: dict[str, bytes]) -> bytes:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, payload in files.items():
                archive.writestr(name, payload)
        return stream.getvalue()

    def test_extracts_package_zip_and_finds_nested_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "upload"
            payload = self.make_zip(
                {
                    "video/package.json": b'{}',
                    "video/transcript.corrected.jsonl": b'{}\n',
                }
            )
            manifest = extract_package_archive(payload, destination)
            self.assertEqual(manifest.relative_to(destination).as_posix(), "files/video/package.json")

    def test_rejects_zip_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = self.make_zip({"../package.json": b"{}"})
            with self.assertRaisesRegex(ValueError, "Unsafe uploaded path"):
                extract_package_archive(payload, Path(directory) / "upload")

    def test_saves_browser_directory_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = save_package_files(
                [
                    ("video/package.json", b"{}"),
                    ("video/corrections.json", b"{}"),
                ],
                Path(directory) / "upload",
            )
            self.assertEqual(manifest.name, "package.json")
            self.assertTrue((manifest.parent / "corrections.json").is_file())

    def test_cancels_a_queued_job_before_engine_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = JobRegistry(root)
            job = ReportJob(
                job_id="job-1",
                content_id="v1",
                report_type="video",
                engine="codex",
                model="gpt-5.6-sol",
                reasoning_effort="high",
                package_manifest=str(root / "package.json"),
                log_path=root / "job.log",
            )
            registry.add(job)
            registry.request_cancel(job.job_id)
            payload = registry.as_dict(job.job_id)
            self.assertEqual(payload["status"], "cancelled")
            self.assertEqual(payload["display_status"], "failed")
            self.assertTrue(payload["cancel_requested"])
            self.assertFalse(registry.has_active_job())

    def test_summarizes_codex_json_events_without_exposing_large_payloads(self) -> None:
        event = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "python -m unittest discover -s tests -v",
                    "aggregated_output": "very large output" * 10_000,
                    "exit_code": 0,
                },
            }
        )
        summary = summarize_engine_line(event, "codex")
        self.assertEqual(
            summary,
            ("command", "命令完成：python -m unittest discover -s tests -v"),
        )
        self.assertIsNone(
            summarize_engine_line(
                "WARN codex_rollout::list: state db discrepancy during lookup",
                "codex",
            )
        )
        self.assertTrue(
            fast_tier_unavailable("service_tier fast is not available for this account")
        )
        self.assertFalse(fast_tier_unavailable("temporary network timeout"))

    def test_streams_compact_events_and_keeps_raw_json_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output" / "report"
            output.mkdir(parents=True)
            application = ReportWebApplication(
                WebServerConfig(
                    project_root=root,
                    output_root=root / "output",
                    codex_binary=sys.executable,
                    opencode_binary=None,
                    default_codex_model="gpt-5.6-sol",
                    default_opencode_model="",
                    default_reasoning_effort="high",
                    sandbox="workspace-write",
                    web_root=Path(__file__).resolve().parents[1] / "web",
                )
            )
            job = ReportJob(
                job_id="job-events",
                content_id="v1",
                report_type="video",
                engine="codex",
                model="gpt-5.6-sol",
                reasoning_effort="high",
                package_manifest=str(root / "package.json"),
                log_path=root / "job.log",
                codex_service_tier="fast",
            )
            application.registry.add(job)

            def fake_automation(config: object, *, run_command: object) -> dict[str, object]:
                del config
                script = (
                    "import json; "
                    "print(json.dumps({'type':'turn.started'})); "
                    "print(json.dumps({'type':'item.completed','item':"
                    "{'type':'agent_message','text':'阶段更新'}})); "
                    "print(json.dumps({'type':'turn.completed','usage':"
                    "{'input_tokens':12,'output_tokens':3}}))"
                )
                completed = run_command(  # type: ignore[operator]
                    [sys.executable, "-c", script],
                    input=None,
                    text=True,
                    cwd=root,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0)
                return {"output_directory": str(output)}

            with patch(
                "video_opinion_report.web_server.run_automation",
                side_effect=fake_automation,
            ):
                application._run_job(job)

            payload = application.registry.as_dict(job.job_id)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["codex_service_tier"], "fast")
            self.assertIn("模型开始分析", payload["log"])
            self.assertIn("阶段更新", payload["log"])
            self.assertNotIn("input_tokens", payload["log"])
            self.assertEqual(payload["token_usage_total"], 15)
            self.assertEqual(payload["stage_token_usage"], {"model": 15})
            self.assertTrue(payload["raw_log_available"])
            self.assertIn("input_tokens", job.raw_log_path.read_text(encoding="utf-8"))

    def test_retries_with_standard_tier_when_fast_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output" / "report"
            output.mkdir(parents=True)
            application = ReportWebApplication(
                WebServerConfig(
                    project_root=root,
                    output_root=root / "output",
                    codex_binary=sys.executable,
                    opencode_binary=None,
                    default_codex_model="gpt-5.6-sol",
                    default_opencode_model="",
                    default_reasoning_effort="high",
                    sandbox="workspace-write",
                    web_root=Path(__file__).resolve().parents[1] / "web",
                )
            )
            job = ReportJob(
                job_id="job-fast-fallback",
                content_id="v1",
                report_type="video",
                engine="codex",
                model="gpt-5.6-sol",
                reasoning_effort="high",
                package_manifest=str(root / "package.json"),
                log_path=root / "job.log",
                codex_service_tier="fast",
            )
            application.registry.add(job)

            def fake_automation(config: object, *, run_command: object) -> dict[str, object]:
                del config
                script = (
                    "import sys; "
                    "fast = sys.argv[1].endswith('fast\\\"'); "
                    "print('service_tier fast is not available' if fast else "
                    "'{\"type\":\"turn.completed\",\"usage\":{}}'); "
                    "raise SystemExit(2 if fast else 0)"
                )
                completed = run_command(  # type: ignore[operator]
                    [sys.executable, "-c", script, 'service_tier="fast"'],
                    input=None,
                    text=True,
                    cwd=root,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0)
                self.assertIn('service_tier="default"', completed.args)
                next_stage = run_command(  # type: ignore[operator]
                    [sys.executable, "-c", script, 'service_tier="fast"'],
                    input=None,
                    text=True,
                    cwd=root,
                    check=False,
                )
                self.assertEqual(next_stage.returncode, 0)
                self.assertIn('service_tier="default"', next_stage.args)
                return {"output_directory": str(output)}

            with patch(
                "video_opinion_report.web_server.run_automation",
                side_effect=fake_automation,
            ):
                application._run_job(job)

            payload = application.registry.as_dict(job.job_id)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["codex_service_tier"], "default")
            self.assertIn("自动回退标准服务层", payload["log"])

    def test_lists_session_jobs_with_three_states_and_run_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = JobRegistry(root)
            jobs = [
                ReportJob(
                    job_id="job-completed",
                    content_id="v-completed",
                    title="已完成报告",
                    report_type="video",
                    engine="codex",
                    model="gpt-5.6-sol",
                    reasoning_effort="xhigh",
                    package_manifest=str(root / "completed" / "package.json"),
                    log_path=root / "completed.log",
                    status="completed",
                ),
                ReportJob(
                    job_id="job-failed",
                    content_id="v-failed",
                    title="失败报告",
                    report_type="video",
                    engine="opencode",
                    model="deepseek/deepseek-v4-flash",
                    reasoning_effort="high",
                    package_manifest=str(root / "failed" / "package.json"),
                    log_path=root / "failed.log",
                    status="failed",
                    error="engine failed",
                ),
                ReportJob(
                    job_id="job-running",
                    content_id="material-running",
                    title="进行中报告",
                    report_type="material",
                    engine="codex",
                    model="gpt-5.6-terra",
                    reasoning_effort="high",
                    package_manifest=str(root / "material-package.json"),
                    log_path=root / "running.log",
                    status="running",
                ),
            ]
            for job in jobs:
                registry.add(job)

            payloads = {job["job_id"]: job for job in registry.list_dicts()}
            self.assertTrue(all("regenerate" not in job for job in payloads.values()))
            self.assertEqual(payloads["job-completed"]["display_status"], "completed")
            self.assertEqual(payloads["job-failed"]["display_status"], "failed")
            self.assertEqual(payloads["job-running"]["display_status"], "running")
            detail = registry.as_dict("job-completed")
            self.assertEqual(detail["title"], "已完成报告")
            self.assertEqual(detail["engine"], "codex")
            self.assertEqual(detail["model"], "gpt-5.6-sol")
            self.assertEqual(detail["reasoning_effort"], "xhigh")
            self.assertNotIn("regenerate", detail)
            self.assertEqual(
                detail["package_manifest"],
                str(root / "completed" / "package.json"),
            )

    def test_times_out_and_terminates_engine_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = ReportWebApplication(
                WebServerConfig(
                    project_root=root,
                    output_root=root / "output",
                    codex_binary="/bin/true",
                    opencode_binary=None,
                    default_codex_model="gpt-5.6-sol",
                    default_opencode_model="",
                    default_reasoning_effort="high",
                    sandbox="workspace-write",
                    web_root=Path(__file__).resolve().parents[1] / "web",
                    job_timeout_seconds=0.05,
                )
            )
            job = ReportJob(
                job_id="job-timeout",
                content_id="v1",
                report_type="video",
                engine="codex",
                model="gpt-5.6-sol",
                reasoning_effort="high",
                package_manifest=str(root / "package.json"),
                log_path=root / "job.log",
            )
            application.registry.add(job)

            def fake_automation(config: object, *, run_command: object) -> dict[str, object]:
                del config
                run_command(  # type: ignore[operator]
                    ["/bin/sh", "-c", "sleep 5"],
                    input=None,
                    text=True,
                    cwd=root,
                    check=False,
                )
                raise AssertionError("timeout should interrupt the engine")

            with patch(
                "video_opinion_report.web_server.run_automation",
                side_effect=fake_automation,
            ):
                application._run_job(job)

            payload = application.registry.as_dict(job.job_id)
            self.assertEqual(payload["status"], "timed_out")
            self.assertIn("已终止", payload["error"])

    def test_parses_multipart_text_and_file_fields(self) -> None:
        boundary = "test-boundary"
        payload = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"engine\"\r\n\r\nopencode\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"package_file\"; filename=\"video/package.json\"\r\n"
            "Content-Type: application/json\r\n\r\n{}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        fields = parse_multipart(f"multipart/form-data; boundary={boundary}", payload)
        self.assertEqual(text_field(fields, "engine"), "opencode")
        self.assertEqual(fields["package_file"][0][0], "video/package.json")

    def test_lists_and_deduplicates_opencode_models(self) -> None:
        completed = subprocess.CompletedProcess(
            ["opencode", "models"],
            0,
            stdout="deepseek/deepseek-reasoner\n\x1b[32mopenai/gpt-5\x1b[0m\ndeepseek/deepseek-reasoner\nnoise\n",
            stderr="",
        )
        with patch("video_opinion_report.web_server.subprocess.run", return_value=completed):
            models = list_opencode_models("opencode")
        self.assertEqual(models, ["deepseek/deepseek-reasoner", "openai/gpt-5"])

    def test_lists_visible_codex_models_with_model_specific_efforts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "models_cache.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "gpt-5.6-sol",
                                "display_name": "GPT-5.6-Sol",
                                "description": "Flagship Codex model",
                                "visibility": "list",
                                "priority": 1,
                                "default_reasoning_level": "low",
                                "supported_reasoning_levels": [
                                    {"effort": "low"},
                                    {"effort": "high"},
                                    {"effort": "max"},
                                    {"effort": "ultra"},
                                ],
                                "input_modalities": ["text", "image"],
                            },
                            {
                                "slug": "codex-auto-review",
                                "display_name": "Codex Auto Review",
                                "visibility": "hide",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            models = list_codex_models(cache_path)

        self.assertEqual([model["id"] for model in models], ["gpt-5.6-sol"])
        self.assertEqual(models[0]["reasoning_efforts"], ["low", "high", "max"])
        self.assertEqual(models[0]["default_reasoning_effort"], "low")
        self.assertTrue(models[0]["vision"])

    def test_limits_opencode_report_models_to_deepseek_v4_flash_and_pro(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "models.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "deepseek": {
                            "models": {
                                "deepseek-v4-flash": {
                                    "name": "DeepSeek V4 Flash",
                                    "attachment": False,
                                    "reasoning_options": [
                                        {
                                            "type": "effort",
                                            "values": ["low", "high", "max"],
                                        }
                                    ],
                                    "modalities": {"input": ["text"]},
                                },
                                "deepseek-v4-pro": {
                                    "name": "DeepSeek V4 Pro",
                                    "attachment": False,
                                    "reasoning_options": [
                                        {"type": "effort", "values": ["high", "max"]}
                                    ],
                                    "modalities": {"input": ["text"]},
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            models = list_opencode_report_models(
                installed_models=[
                    "deepseek/deepseek-v4-flash",
                    "deepseek/deepseek-v4-pro",
                    "deepseek/deepseek-reasoner",
                ],
                cache_path=cache_path,
            )

        self.assertEqual(
            [model["id"] for model in models],
            ["deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro"],
        )
        self.assertEqual(models[0]["reasoning_efforts"], ["low", "high", "max"])
        self.assertEqual(models[1]["reasoning_efforts"], ["high", "max"])
        self.assertFalse(models[0]["vision"])
        self.assertFalse(models[1]["vision"])

    def test_web_ui_exposes_material_mode_and_posts_one_material_zip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            web_root = Path(__file__).resolve().parents[1] / "web"
            output_root = project / "output"
            output_root.mkdir()
            application = ReportWebApplication(
                WebServerConfig(
                    project_root=project,
                    output_root=output_root,
                    codex_binary="/usr/bin/true",
                    opencode_binary=None,
                    default_codex_model="gpt-5.6-sol",
                    default_opencode_model="",
                    default_reasoning_effort="high",
                    sandbox="workspace-write",
                    web_root=web_root,
                )
            )
            server = ReportHTTPServer(("127.0.0.1", 0), application)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = int(server.server_address[1])
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/api/config")
                response = connection.getresponse()
                config = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertIn("material", config["report_types"])
                self.assertIn(".docx", config["material_extensions"])
                self.assertEqual(config["default_codex_service_tier"], "fast")

                connection.request("GET", "/api/jobs")
                response = connection.getresponse()
                jobs = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(jobs["jobs"], [])
                self.assertEqual(
                    jobs["counts"],
                    {"running": 0, "completed": 0, "failed": 0},
                )

                connection.request("GET", "/")
                response = connection.getresponse()
                page = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn('<option value="material">素材报告</option>', page)
                self.assertIn('<select id="model"', page)
                self.assertIn('id="codex-fast-mode"', page)
                self.assertNotIn('id="regenerate-report"', page)
                self.assertIn('id="job-list"', page)
                self.assertIn("完整任务参数", page)

                boundary = "material-boundary"
                archive_payload = self.make_zip(
                    {"brief.txt": "这是一个素材报告输入。".encode()}
                )
                body = b"".join(
                    [
                        f"--{boundary}\r\nContent-Disposition: form-data; name=\"report_type\"\r\n\r\nmaterial\r\n".encode(),
                        f"--{boundary}\r\nContent-Disposition: form-data; name=\"engine\"\r\n\r\ncodex\r\n".encode(),
                        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\ngpt-5.6-sol\r\n".encode(),
                        f"--{boundary}\r\nContent-Disposition: form-data; name=\"reasoning_effort\"\r\n\r\nhigh\r\n".encode(),
                        f"--{boundary}\r\nContent-Disposition: form-data; name=\"package_archive\"; filename=\"brief.zip\"\r\nContent-Type: application/zip\r\n\r\n".encode(),
                        archive_payload,
                        f"\r\n--{boundary}--\r\n".encode(),
                    ]
                )
                fake_job = SimpleNamespace(
                    job_id="job-1",
                    report_type="material",
                    content_id="material-brief-test",
                    status="queued",
                )
                with patch.object(
                    ReportWebApplication, "create_job", return_value=fake_job
                ) as create_job:
                    connection.request(
                        "POST",
                        "/api/jobs",
                        body=body,
                        headers={
                            "Content-Type": f"multipart/form-data; boundary={boundary}",
                            "Content-Length": str(len(body)),
                        },
                    )
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                self.assertEqual(response.status, 202)
                self.assertEqual(payload["report_type"], "material")
                self.assertEqual(payload["material_id"], "material-brief-test")
                arguments = create_job.call_args.kwargs
                self.assertEqual(arguments["report_type"], "material")
                self.assertEqual(arguments["codex_service_tier"], "fast")
                self.assertNotIn("regenerate", arguments)
                self.assertEqual(arguments["package_manifest"].name, "material-package.json")
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

if __name__ == "__main__":
    unittest.main()
