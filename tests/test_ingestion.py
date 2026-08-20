import json
import tempfile
import unittest
from pathlib import Path

from video_opinion_report.cli import parser, record_analysis
from video_opinion_report.ingestion import FILE_DESTINATIONS, import_transcript_package
from video_opinion_report.integrity import sha256_file
from video_opinion_report.models import Stage
from video_opinion_report.store import ManifestStore


class TranscriptPackageTests(unittest.TestCase):
    def make_package(
        self,
        root: Path,
        *,
        tamper_hash: bool = False,
        unlogged_change: bool = False,
    ) -> Path:
        package = root / "package"
        package.mkdir()
        transcript = (
            '{"segment_id":"seg-000001","start":0.0,"end":1.5,'
            '"text":"第一句话","source":"asr_corrected"}\n'
        )
        contents = {
            "transcript_jsonl": transcript,
            "transcript_markdown": "[00:00:00.000] 第一段\n",
            "transcript_srt": "1\n00:00:00,000 --> 00:00:01,500\n第一句话\n",
            "transcript_corrected_jsonl": transcript.replace(
                "第一句话", "未经记录的修改"
            )
            if unlogged_change
            else transcript,
            "transcript_corrected_markdown": "[00:00:00.000] 第一句话\n",
            "transcript_corrected_srt": "1\n00:00:00,000 --> 00:00:01,500\n第一句话\n",
            "transcript_corrections": json.dumps(
                {
                    "schema_version": 1,
                    "video_id": "v1",
                    "reviewed_at": "2026-08-20T00:00:00Z",
                    "review_summary": "checked",
                    "unresolved_terms": [],
                    "corrections": [],
                },
                ensure_ascii=False,
            ),
            "transcript_validation": json.dumps({"valid": True}),
        }
        files = {}
        for key, filename in FILE_DESTINATIONS.items():
            path = package / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents[key], encoding="utf-8")
            files[key] = {"path": filename, "sha256": sha256_file(path)}
        if tamper_hash:
            files["transcript_corrected_jsonl"]["sha256"] = "0" * 64
        (package / "package.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "package_type": "video_transcript",
                    "created_at": "2026-08-20T00:00:00Z",
                    "video": {
                        "video_id": "v1",
                        "source_url": "https://example.com/v1",
                        "title": "Example",
                        "creator": "Creator",
                        "published_at": "2026-08-20",
                        "duration_seconds": 1.5,
                    },
                    "files": files,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return package

    def make_current_package(self, root: Path, *, coverage_ratio: float = 0.98) -> Path:
        package = root / "current-package"
        package.mkdir()
        contents = {
            "transcript_corrected_jsonl": (
                '{"segment_id":"seg-000001","start":0.0,"end":1.5,'
                '"text":"第一句话","source":"asr_corrected"}\n'
            ),
            "transcript_corrected_markdown": "[00:00:00.000] 第一句话\n",
            "transcript_corrections": json.dumps(
                {
                    "schema_version": 1,
                    "video_id": "v2",
                    "reviewed_at": "2026-08-20T00:00:00Z",
                    "review_summary": "checked",
                    "unresolved_terms": [],
                    "corrections": [],
                },
                ensure_ascii=False,
            ),
        }
        files = {}
        for key in contents:
            filename = FILE_DESTINATIONS[key]
            path = package / filename
            path.write_text(contents[key], encoding="utf-8")
            files[key] = {"path": filename, "sha256": sha256_file(path)}
        (package / "package.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "package_type": "video_transcript",
                    "created_at": "2026-08-20T00:00:00Z",
                    "video": {
                        "video_id": "v2",
                        "source_url": "https://example.com/v2",
                        "title": "Current export",
                        "creator": "Creator",
                        "published_at": "2026-08-20",
                        "duration_seconds": 1.5,
                    },
                    "quality": {
                        "correction_count": 0,
                        "unresolved_term_count": 0,
                        "coverage_ratio": coverage_ratio,
                        "maximum_gap_seconds": 0.1,
                    },
                    "files": files,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return package

    def test_imports_valid_package_and_opens_analysis_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video_id = import_transcript_package(root, self.make_package(root))
            self.assertEqual(import_transcript_package(root, root / "package"), video_id)
            manifest = ManifestStore(root).load(video_id)
            self.assertTrue(manifest.is_complete(Stage.INGEST))
            self.assertEqual(manifest.metadata["title"], "Example")
            self.assertTrue((root / manifest.artifacts["transcript_corrected_jsonl"]).is_file())

            analysis = root / "analysis.json"
            analysis.write_text('{"video_id":"v1"}', encoding="utf-8")
            opinions = root / "opinions.jsonl"
            opinions.write_text(
                '{"opinion_id":"o1","timestamp_start":0,"timestamp_end":1,'
                '"faithful_paraphrase":"观点"}\n',
                encoding="utf-8",
            )
            record_analysis(root, "v1", analysis, opinions)
            self.assertTrue(ManifestStore(root).load("v1").is_complete(Stage.ANALYZE))

    def test_rejects_checksum_mismatch_before_creating_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                import_transcript_package(root, self.make_package(root, tamper_hash=True))
            self.assertFalse((root / "work").exists())

    def test_rejects_unlogged_transcript_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "missing from the correction log"):
                import_transcript_package(root, self.make_package(root, unlogged_change=True))

    def test_imports_current_four_file_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video_id = import_transcript_package(root, self.make_current_package(root))
            manifest = ManifestStore(root).load(video_id)
            self.assertTrue(manifest.is_complete(Stage.INGEST))
            self.assertEqual(
                manifest.metadata["transcript_package_contract"],
                "current-four-file",
            )
            self.assertNotIn("transcript_jsonl", manifest.artifacts)

    def test_rejects_current_package_with_failed_quality_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "coverage ratio"):
                import_transcript_package(
                    root,
                    self.make_current_package(root, coverage_ratio=0.90),
                )

    def test_cli_exposes_only_report_workflow_entrypoint(self) -> None:
        command_names = parser()._subparsers._group_actions[0].choices
        self.assertIn("import-transcript", command_names)
        self.assertNotIn("acquire", command_names)
        self.assertNotIn("transcribe", command_names)


if __name__ == "__main__":
    unittest.main()
