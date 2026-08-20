import tempfile
import unittest
from pathlib import Path

from video_opinion_report.transcript import (
    TranscriptSegment,
    format_clock,
    read_jsonl,
    validate_segments,
    transcript_metrics,
    write_jsonl,
    write_srt,
)


class TranscriptTests(unittest.TestCase):
    def sample(self) -> list[TranscriptSegment]:
        return [
            TranscriptSegment("seg-000001", 0.0, 1.25, "第一句话"),
            TranscriptSegment("seg-000002", 1.25, 3.5, "第二句话", speaker="UP主"),
        ]

    def test_round_trip_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transcript.jsonl"
            write_jsonl(path, self.sample())
            loaded = read_jsonl(path)
            self.assertEqual([item.text for item in loaded], ["第一句话", "第二句话"])
            self.assertEqual(validate_segments(loaded), [])

    def test_validation_detects_bad_segments(self) -> None:
        segments = [
            TranscriptSegment("a", 3.0, 2.0, "错误区间"),
            TranscriptSegment("b", 1.0, 1.5, "时间倒序"),
        ]
        errors = validate_segments(segments)
        self.assertTrue(any("end must be greater" in error for error in errors))
        self.assertTrue(any("not monotonic" in error for error in errors))

    def test_srt_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transcript.srt"
            write_srt(path, self.sample())
            content = path.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:01,250", content)
            self.assertIn("UP主: 第二句话", content)

    def test_clock_format(self) -> None:
        self.assertEqual(format_clock(3661.234), "01:01:01.234")

    def test_quality_metrics_reject_low_coverage_and_large_gap(self) -> None:
        segments = [
            TranscriptSegment("a", 0.0, 2.0, "开头", confidence=0.9),
            TranscriptSegment("b", 8.0, 10.0, "结尾", confidence=0.5),
        ]
        result = transcript_metrics(
            segments,
            media_duration=10.0,
            min_coverage_ratio=0.9,
            max_gap_seconds=2.0,
        )
        self.assertFalse(result["valid"])
        self.assertAlmostEqual(result["coverage_ratio"], 0.4)
        self.assertAlmostEqual(result["maximum_gap_seconds"], 6.0)
        self.assertEqual(result["low_confidence_segments"], ["b"])


if __name__ == "__main__":
    unittest.main()
