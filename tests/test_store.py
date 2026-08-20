import tempfile
import unittest
from pathlib import Path

from video_opinion_report.models import Stage, StageStatus
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
                Stage.RESEARCH,
                Stage.JUDGMENT,
                Stage.DRAFT,
                Stage.FIDELITY_REVIEW,
                Stage.RENDER,
                Stage.HTML_VALIDATE,
            ):
                manifest.start(stage)
                manifest.complete(stage)
            manifest.restart(Stage.DRAFT)
            self.assertEqual(manifest.stages[Stage.DRAFT.value].status, StageStatus.RUNNING)
            self.assertEqual(
                manifest.stages[Stage.FIDELITY_REVIEW.value].status,
                StageStatus.PENDING,
            )
            self.assertTrue(manifest.is_complete(Stage.RESEARCH))

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
