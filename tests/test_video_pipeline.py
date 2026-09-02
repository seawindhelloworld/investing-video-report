from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_opinion_report.cli import (
    build_meaning_structured,
    complete_run,
    record_meaning_report,
    render_html,
    validate_html,
)
from video_opinion_report.ingestion import FILE_DESTINATIONS, import_transcript_package
from video_opinion_report.integrity import sha256_file
from video_opinion_report.models import VIDEO_MEANING_PROFILE, Stage, StageStatus
from video_opinion_report.store import ManifestStore, ProcessedReportStore


REPORT_MARKDOWN = """---
title: "Synthetic report"
video_id: "video-1"
source_url: "https://example.com/video-1"
creator: "Creator"
published_at: "2026-08-20"
report_date: "2026-08-24"
---

# 市场改善仍是带条件的判断

> 视频围绕市场是否改善展开，并始终保留“可能”这一限定。

<section class="hero-card">
<strong>视频内容导读</strong>
<p>核心信息是市场存在改善可能，但这不是确定性结论。</p>
</section>

<section class="summary-dashboard">
<article><span>报告整理 · 仅据字幕</span><strong>市场可能改善</strong><p>不是确定性结论。</p></article>
<article><span>关键限定</span><strong>保留可能性</strong><p>视频没有给出确定承诺。</p></article>
</section>

## 视频 / 作者内容

<div class="layer-intro creator">市场改善是可能性判断，不能改写为已经发生。</div>

<section id="section-1" class="video-section" data-section-id="section-1">

### 市场判断

<div class="section-lead">市场改善仍是一项带条件的判断。</div>

市场可能改善，但表达的重点是方向判断及其不确定性，而不是一项已经得到外部证据确认的事实。

<div class="speaker-opinion-marker creator-view-card" data-speaker="Creator" data-stance-owner="Creator" data-attribution-mode="self"><span class="speaker-opinion-kicker">CREATOR TAKE</span><strong>Creator</strong></div>

<div class="view-summary"><p>讲者认为市场可能改善，同时保留判断空间。</p></div>

</section>
"""


class VideoPipelineTests(unittest.TestCase):
    def _make_package(self, root: Path) -> Path:
        package = root / "package"
        package.mkdir()
        contents = {
            "transcript_corrected_jsonl": (
                '{"segment_id":"seg-000001","start":0.0,"end":3.0,'
                '"text":"我认为市场可能改善","speaker":"Creator",'
                '"source":"asr_corrected"}\n'
            ),
            "transcript_corrected_markdown": "[00:00:00.000] 我认为市场可能改善\n",
            "transcript_corrections": json.dumps(
                {
                    "schema_version": 1,
                    "video_id": "video-1",
                    "unresolved_terms": [],
                    "corrections": [],
                },
                ensure_ascii=False,
            ),
        }
        files = {}
        for key, content in contents.items():
            path = package / FILE_DESTINATIONS[key]
            path.write_text(content, encoding="utf-8")
            files[key] = {"path": path.name, "sha256": sha256_file(path)}
        (package / "package.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "package_type": "video_transcript",
                    "created_at": "2026-08-20T00:00:00Z",
                    "video": {
                        "video_id": "video-1",
                        "source_url": "https://example.com/video-1",
                        "title": "Synthetic report",
                        "creator": "Creator",
                        "published_at": "2026-08-20",
                        "duration_seconds": 3.0,
                    },
                    "quality": {
                        "correction_count": 0,
                        "unresolved_term_count": 0,
                        "coverage_ratio": 1.0,
                        "maximum_gap_seconds": 0.0,
                    },
                    "files": files,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return package

    def test_runs_the_hash_bound_video_meaning_report_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "assets"
            assets.mkdir()
            template = assets / "report-template.html"
            template.write_text(
                "<!doctype html><html><head><title>{{TITLE}}</title>"
                "<style>body{font-family:sans-serif;line-height:1.5}</style></head>"
                "<body><header><p>{{REPORT_META}}</p><p>{{SUMMARY}}</p></header>"
                "<main>{{REPORT_BODY}}</main></body></html>",
                encoding="utf-8",
            )
            video_id = import_transcript_package(root, self._make_package(root))
            store = ManifestStore(root)
            run_dir = store.run_dir(video_id)
            report_dir = root / "reports" / "2026-08-20-video-1"
            report_dir.mkdir(parents=True)

            analysis = run_dir / "video-analysis.json"
            analysis.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "workflow_profile": VIDEO_MEANING_PROFILE,
                        "video_id": video_id,
                        "title": "Synthetic report",
                        "creator": "Creator",
                        "source_url": "https://example.com/video-1",
                        "published_at": "2026-08-20",
                        "duration_seconds": 3.0,
                        "summary": "市场改善判断及其限定。",
                        "sections": [
                            {
                                "section_id": "section-1",
                                "title": "市场判断",
                                "segment_start": "seg-000001",
                                "segment_end": "seg-000001",
                                "summary": "市场可能改善，但判断带有明确限定。",
                                "key_points": ["改善是可能性判断，不是确定结论。"],
                            }
                        ],
                        "topic_clusters": [],
                        "excluded_ranges": [],
                        "non_reportable_ranges": [],
                        "transcript_risks": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            opinions = run_dir / "opinions.jsonl"
            opinions.write_text(
                json.dumps(
                    {
                        "opinion_id": "opinion-001",
                        "section_id": "section-1",
                        "timestamp_start": 0.0,
                        "timestamp_end": 3.0,
                        "segment_start": "seg-000001",
                        "segment_end": "seg-000001",
                        "exact_quote": "我认为市场可能改善",
                        "faithful_paraphrase": "市场可能改善",
                        "speaker": "Creator",
                        "stance_owner": "Creator",
                        "attribution_mode": "self",
                        "opinion_type": "market_judgment",
                        "target": "市场",
                        "time_horizon": "视频中未明确",
                        "stated_basis": [],
                        "qualifiers": ["我认为", "可能"],
                        "context_before": "",
                        "context_after": "",
                        "research_status": "not_applicable",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            understanding = run_dir / "understanding-notes.json"
            understanding.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "video_id": video_id,
                        "workflow_profile": VIDEO_MEANING_PROFILE,
                        "display_policy": "internal_only",
                        "transcript_sha256": store.load(video_id).artifact_hashes[
                            "transcript_corrected_jsonl"
                        ],
                        "term_checks": [],
                        "data_checks": [],
                        "domain_context": [],
                        "uncertainties": [],
                        "web_sources": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            presentation_plan = run_dir / "presentation-plan.json"
            presentation_plan.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "video_id": video_id,
                        "workflow_profile": VIDEO_MEANING_PROFILE,
                        "report_title": "市场改善仍是带条件的判断",
                        "cover_deck": "视频围绕市场是否改善展开。",
                        "summary_cards": [
                            {
                                "label": "报告整理 · 仅据字幕",
                                "headline": "市场可能改善",
                                "detail": "不是确定性结论。",
                            },
                            {
                                "label": "关键限定",
                                "headline": "保留可能性",
                                "detail": "视频没有给出确定承诺。",
                            },
                        ],
                        "sections": [
                            {
                                "section_id": "section-1",
                                "lead": "市场改善仍是一项带条件的判断。",
                                "visual_type": "none",
                                "visual_reason": "没有足够的可比较数据。",
                                "source_segment_ids": ["seg-000001"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            draft = report_dir / "report.md"
            draft.write_text(
                REPORT_MARKDOWN + "\n## 外部证据研判\n不应进入原意报告。\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "external-research"):
                record_meaning_report(
                    root,
                    video_id,
                    understanding,
                    presentation_plan,
                    analysis,
                    opinions,
                    draft,
                )
            failed = store.load(video_id)
            self.assertEqual(failed.stages[Stage.ANALYZE.value].status, StageStatus.FAILED)
            self.assertNotIn("video_analysis", failed.artifacts)
            self.assertNotIn("draft_markdown", failed.artifacts)

            draft.write_text(REPORT_MARKDOWN, encoding="utf-8")
            record_meaning_report(
                root,
                video_id,
                understanding,
                presentation_plan,
                analysis,
                opinions,
                draft,
            )
            build_meaning_structured(
                root,
                video_id,
                report_dir / "report-data.json",
                report_dir / "citations.json",
            )
            render_html(root, video_id, draft, template, report_dir / "index.html")

            validation = run_dir / "html-validation.json"
            validation.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "video_id": video_id,
                        "status": "passed",
                        "visual_review_completed": True,
                        "report_html_sha256": sha256_file(report_dir / "index.html"),
                        "report_markdown_sha256": sha256_file(draft),
                        "report_data_sha256": sha256_file(report_dir / "report-data.json"),
                        "citations_sha256": sha256_file(report_dir / "citations.json"),
                        "checks": {"desktop": "passed", "mobile": "passed"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            validate_html(root, video_id, validation)
            complete_run(root, video_id)

            manifest = store.load(video_id)
            self.assertEqual(manifest.workflow_profile, VIDEO_MEANING_PROFILE)
            self.assertTrue(manifest.is_complete(Stage.COMPLETE))
            self.assertIn("understanding_notes", manifest.artifacts)
            self.assertIn("presentation_plan", manifest.artifacts)
            self.assertEqual(
                {
                    stage: manifest.stages[stage.value].status
                    for stage in (Stage.RESEARCH, Stage.JUDGMENT, Stage.DRAFT, Stage.FIDELITY_REVIEW)
                },
                {
                    Stage.RESEARCH: StageStatus.PENDING,
                    Stage.JUDGMENT: StageStatus.PENDING,
                    Stage.DRAFT: StageStatus.PENDING,
                    Stage.FIDELITY_REVIEW: StageStatus.PENDING,
                },
            )
            self.assertTrue(ProcessedReportStore(root).contains(video_id))
            report_data = json.loads(
                (report_dir / "report-data.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report_data["schema_version"], 3)
            self.assertEqual(report_data["workflow_profile"], VIDEO_MEANING_PROFILE)
            self.assertEqual(report_data["source_coverage"][0]["report_anchor"], "#section-1")
            self.assertNotIn("research_topics", report_data)
            self.assertNotIn("agent_judgment", report_data)
            self.assertNotIn("understanding_notes", report_data)
            self.assertNotIn("presentation_plan", report_data)
            citations = json.loads(
                (report_dir / "citations.json").read_text(encoding="utf-8")
            )
            self.assertEqual(citations["external_sources"], [])
            html = (report_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn('class="reading-paths video meaning"', html)


if __name__ == "__main__":
    unittest.main()
