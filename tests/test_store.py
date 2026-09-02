import tempfile
import unittest
from pathlib import Path

from video_opinion_report.models import VIDEO_FULL_PROFILE, VIDEO_MEANING_PROFILE, Stage, StageStatus
from video_opinion_report.store import ManifestStore, ProcessedReportStore


class ManifestStoreTests(unittest.TestCase):
    def test_stage_prerequisites_and_restart_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ManifestStore(root)
            manifest = store.create("video-001", "https://example.com/video")
            with self.assertRaisesRegex(RuntimeError, "ingest"):
                manifest.start(Stage.ANALYZE)

            for stage in (
                Stage.INGEST,
                Stage.ANALYZE,
                Stage.RENDER,
                Stage.HTML_VALIDATE,
            ):
                manifest.start(stage)
                manifest.complete(stage)
            self.assertEqual(manifest.workflow_profile, VIDEO_MEANING_PROFILE)
            manifest.restart(Stage.ANALYZE)
            self.assertEqual(manifest.stages[Stage.ANALYZE.value].status, StageStatus.RUNNING)
            self.assertEqual(
                manifest.stages[Stage.RENDER.value].status,
                StageStatus.PENDING,
            )
            self.assertEqual(
                manifest.stages[Stage.HTML_VALIDATE.value].status,
                StageStatus.PENDING,
            )

    def test_missing_workflow_profile_is_recognized_as_legacy_full(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ManifestStore(Path(directory))
            manifest = store.create("legacy-001", "https://example.com/legacy")
            manifest.metadata.pop("workflow_profile")
            self.assertEqual(manifest.workflow_profile, VIDEO_FULL_PROFILE)
            manifest.start(Stage.INGEST)
            manifest.complete(Stage.INGEST)
            manifest.start(Stage.ANALYZE)
            manifest.complete(Stage.ANALYZE)
            with self.assertRaisesRegex(RuntimeError, "draft"):
                manifest.start(Stage.RENDER)

    def test_processed_report_store_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = ProcessedReportStore(Path(directory))
            entry = {"video_id": "video-001", "source_url": "https://example.com/1"}
            state.add(entry)
            state.add(entry)
            self.assertTrue(state.contains("video-001"))
            self.assertEqual(len(state.load()), 1)


if __name__ == "__main__":
    unittest.main()
