import json
import tempfile
import unittest
from pathlib import Path

from video_opinion_report.cli import record_analysis
from video_opinion_report.content_selection import (
    materialize_content_selection,
    materialize_model_transcript_view,
)
from video_opinion_report.models import Stage
from video_opinion_report.store import ManifestStore


TRANSCRIPT = """{"segment_id":"seg-001","start":0,"end":2,"text":"订阅并购买会员"}
{"segment_id":"seg-002","start":2,"end":4,"text":"正文一"}
{"segment_id":"seg-003","start":4,"end":6,"text":"正文二"}
"""


class ContentSelectionTests(unittest.TestCase):
    def test_materializes_lossless_low_overhead_model_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.report.jsonl"
            transcript.write_text(TRANSCRIPT, encoding="utf-8")
            model_view = root / "transcript.report.model.txt"
            metadata = materialize_model_transcript_view(
                transcript_path=transcript,
                output_path=model_view,
                source_artifact=transcript.name,
            )
            text = model_view.read_text(encoding="utf-8")
            self.assertEqual(metadata["segment_count"], 3)
            self.assertIn(f"source_sha256={metadata['source_sha256']}", text)
            self.assertIn('seg-002\t2.000\t4.000\t"正文一"', text)
            self.assertNotIn('"segment_id":', text)
            self.assertNotIn('"source_chunk":', text)

    def test_removes_blank_segments_without_model_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.corrected.jsonl"
            transcript.write_text(
                '{"segment_id":"seg-001","start":0,"end":1,"text":"正文一"}\n'
                '{"segment_id":"seg-002","start":1,"end":2,"text":"   "}\n'
                '{"segment_id":"seg-003","start":2,"end":3,"text":"正文二"}\n',
                encoding="utf-8",
            )
            selection = materialize_content_selection(
                video_id="v1",
                transcript_path=transcript,
                excluded_ranges=[],
                selection_path=root / "content-selection.json",
                filtered_transcript_path=root / "transcript.report.jsonl",
            )
            self.assertEqual(selection["blank_segment_count"], 1)
            self.assertEqual(selection["excluded_ranges"][0]["category"], "blank")
            self.assertTrue(selection["excluded_ranges"][0]["automatic"])
            self.assertNotIn(
                "seg-002",
                (root / "transcript.report.jsonl").read_text(encoding="utf-8"),
            )

    def test_retains_middle_commercial_source_but_marks_it_non_reportable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.corrected.jsonl"
            transcript.write_text(TRANSCRIPT, encoding="utf-8")
            selection = materialize_content_selection(
                video_id="v1",
                transcript_path=transcript,
                excluded_ranges=[],
                non_reportable_ranges=[
                    {
                        "segment_start": "seg-002",
                        "segment_end": "seg-002",
                        "timestamp_start": 2,
                        "timestamp_end": 4,
                        "category": "advertising",
                        "reason": "确认的中段商业口播",
                        "certainty": "high",
                    }
                ],
                selection_path=root / "content-selection.json",
                filtered_transcript_path=root / "transcript.report.jsonl",
            )
            self.assertEqual(selection["excluded_segment_count"], 0)
            self.assertTrue(selection["non_reportable_ranges"][0]["source_retained"])
            self.assertIn(
                "正文一", (root / "transcript.report.jsonl").read_text(encoding="utf-8")
            )

    def test_materializes_audited_filtered_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.corrected.jsonl"
            transcript.write_text(TRANSCRIPT, encoding="utf-8")
            selection_path = root / "content-selection.json"
            filtered_path = root / "transcript.report.jsonl"

            selection = materialize_content_selection(
                video_id="v1",
                transcript_path=transcript,
                excluded_ranges=[
                    {
                        "segment_start": "seg-001",
                        "segment_end": "seg-001",
                        "timestamp_start": 0,
                        "timestamp_end": 2,
                        "category": "product_promotion",
                        "reason": "付费产品推广",
                        "certainty": "high",
                    }
                ],
                selection_path=selection_path,
                filtered_transcript_path=filtered_path,
            )

            self.assertEqual(selection["segment_count"], 3)
            self.assertEqual(selection["included_segment_count"], 2)
            self.assertEqual(selection["excluded_segment_count"], 1)
            self.assertEqual(len(selection["ranges"]), 2)
            self.assertNotIn("订阅并购买会员", filtered_path.read_text(encoding="utf-8"))
            self.assertEqual(selection["excluded_ranges"][0]["edge_region"], "intro")
            self.assertEqual(
                json.loads(selection_path.read_text(encoding="utf-8"))[
                    "excluded_category_counts"
                ],
                {"product_promotion": 1},
            )

    def test_record_analysis_rejects_opinion_from_excluded_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ManifestStore(root)
            manifest = store.create("v1", "https://example.com/v1")
            transcript = store.run_dir("v1") / "transcript" / "transcript.corrected.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text(TRANSCRIPT, encoding="utf-8")
            manifest.artifacts["transcript_corrected_jsonl"] = store.relative(transcript)
            manifest.metadata["duration_seconds"] = 6
            manifest.start(Stage.INGEST)
            manifest.complete(Stage.INGEST)
            store.save(manifest)
            analysis = root / "analysis.json"
            analysis.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "workflow_profile": "video_meaning_v1",
                        "video_id": "v1",
                        "title": "Test",
                        "creator": "Creator",
                        "source_url": "https://example.com/v1",
                        "published_at": "2026-08-20",
                        "duration_seconds": 6,
                        "summary": "正文摘要",
                        "sections": [
                            {
                                "section_id": "section-1",
                                "title": "正文",
                                "segment_start": "seg-001",
                                "segment_end": "seg-003",
                            }
                        ],
                        "topic_clusters": [],
                        "transcript_risks": [],
                        "non_reportable_ranges": [],
                        "excluded_ranges": [
                            {
                                "segment_start": "seg-001",
                                "segment_end": "seg-001",
                                "timestamp_start": 0,
                                "timestamp_end": 2,
                                "category": "subscription_prompt",
                                "reason": "订阅引导",
                                "certainty": "high",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            opinions = root / "opinions.jsonl"
            opinions.write_text(
                '{"opinion_id":"o1","timestamp_start":0,"timestamp_end":2,'
                '"exact_quote":"订阅并购买会员","faithful_paraphrase":"购买会员",'
                '"opinion_type":"commercial","target":"会员",'
                '"time_horizon":"视频中未明确","stated_basis":[],"qualifiers":[],'
                '"context_before":"","context_after":"",'
                '"research_status":"not_applicable"}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "overlaps excluded"):
                record_analysis(root, "v1", analysis, opinions)

    def test_rejects_commercial_exclusion_in_video_middle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.corrected.jsonl"
            transcript.write_text(TRANSCRIPT, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "middle of the video must be kept"):
                materialize_content_selection(
                    video_id="v1",
                    transcript_path=transcript,
                    excluded_ranges=[
                        {
                            "segment_start": "seg-002",
                            "segment_end": "seg-002",
                            "timestamp_start": 2,
                            "timestamp_end": 4,
                            "category": "advertising",
                            "reason": "中段内容看起来像广告",
                            "certainty": "high",
                        }
                    ],
                    selection_path=root / "content-selection.json",
                    filtered_transcript_path=root / "transcript.report.jsonl",
                )

    def test_rejects_unknown_exclusion_category(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.corrected.jsonl"
            transcript.write_text(TRANSCRIPT, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported category"):
                materialize_content_selection(
                    video_id="v1",
                    transcript_path=transcript,
                    excluded_ranges=[
                        {
                            "segment_start": "seg-002",
                            "segment_end": "seg-002",
                            "timestamp_start": 2,
                            "timestamp_end": 4,
                            "category": "misc",
                            "reason": "不明确",
                            "certainty": "high",
                        }
                    ],
                    selection_path=root / "content-selection.json",
                    filtered_transcript_path=root / "transcript.report.jsonl",
                )

    def test_keeps_uncertain_exclusion_by_rejecting_the_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.corrected.jsonl"
            transcript.write_text(TRANSCRIPT, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "keep uncertain content"):
                materialize_content_selection(
                    video_id="v1",
                    transcript_path=transcript,
                    excluded_ranges=[
                        {
                            "segment_start": "seg-002",
                            "segment_end": "seg-002",
                            "timestamp_start": 2,
                            "timestamp_end": 4,
                            "category": "unrelated_content",
                            "reason": "可能与主题无关",
                            "certainty": "medium",
                        }
                    ],
                    selection_path=root / "content-selection.json",
                    filtered_transcript_path=root / "transcript.report.jsonl",
                )

    def test_rejects_broad_ambiguous_exclusion(self) -> None:
        transcript_content = "".join(
            json.dumps(
                {
                    "segment_id": f"seg-{index:03d}",
                    "start": (index - 1) * 4,
                    "end": index * 4,
                    "text": f"仍可能有内容的片段{index}",
                },
                ensure_ascii=False,
            )
            + "\n"
            for index in range(1, 8)
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.corrected.jsonl"
            transcript.write_text(transcript_content, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ambiguous.*too broad"):
                materialize_content_selection(
                    video_id="v1",
                    transcript_path=transcript,
                    excluded_ranges=[
                        {
                            "segment_start": "seg-001",
                            "segment_end": "seg-006",
                            "timestamp_start": 0,
                            "timestamp_end": 24,
                            "category": "asr_noise",
                            "reason": "疑似噪声",
                            "certainty": "high",
                        }
                    ],
                    selection_path=root / "content-selection.json",
                    filtered_transcript_path=root / "transcript.report.jsonl",
                )

    def test_rejects_adjacent_ambiguous_ranges_as_one_broad_deletion(self) -> None:
        transcript_content = "".join(
            json.dumps(
                {
                    "segment_id": f"seg-{index:03d}",
                    "start": index - 1,
                    "end": index,
                    "text": f"片段{index}",
                },
                ensure_ascii=False,
            )
            + "\n"
            for index in range(1, 101)
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.corrected.jsonl"
            transcript.write_text(transcript_content, encoding="utf-8")
            ranges = []
            for first, last in ((1, 3), (4, 6)):
                ranges.append(
                    {
                        "segment_start": f"seg-{first:03d}",
                        "segment_end": f"seg-{last:03d}",
                        "timestamp_start": first - 1,
                        "timestamp_end": last,
                        "category": "asr_noise",
                        "reason": "疑似连续噪声",
                        "certainty": "high",
                    }
                )
            with self.assertRaisesRegex(ValueError, "Adjacent ambiguous exclusions"):
                materialize_content_selection(
                    video_id="v1",
                    transcript_path=transcript,
                    excluded_ranges=ranges,
                    selection_path=root / "content-selection.json",
                    filtered_transcript_path=root / "transcript.report.jsonl",
                )


if __name__ == "__main__":
    unittest.main()
