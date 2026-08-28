from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from video_opinion_report.cli import (
    build_structured,
    complete_run,
    record_agent_judgment,
    record_analysis,
    record_draft,
    record_fidelity_review,
    record_research,
    render_html,
    validate_html,
)
from video_opinion_report.ingestion import FILE_DESTINATIONS, import_transcript_package
from video_opinion_report.integrity import sha256_file
from video_opinion_report.models import Stage
from video_opinion_report.store import ManifestStore, ProcessedReportStore


REPORT_MARKDOWN = """---
title: "Synthetic report"
video_id: "video-1"
creator: "Creator"
published_at: "2026-08-20"
report_date: "2026-08-24"
description: ""
---

# Synthetic report

<section id="investor-dashboard" class="investor-dashboard" markdown="1">
<header class="investor-dashboard-header"><strong>投资决策总览</strong><small>报告综合 · 非视频原内容</small></header>
<article class="investor-topic" data-status="mixed">市场改善仍需等待下一期数据验证。</article>
</section>

## 第一部分｜视频 / 作者内容

<div class="layer-intro creator"><strong>报告说明（非原内容）：</strong>以下为忠实整理。</div>

市场可能改善，但结论仍取决于后续数据。这一限定与原句保持一致，并保留对应的不确定性。

## 第二部分｜外部证据研判

本注为基于外部信源形成的独立研判，不代表视频作者观点。

公开资料支持方向，但样本仍有限，结论只在给定条件内成立。

## 第三部分｜Agent 综合判断

本节为 Agent 基于视频内容、既有外部研判和注明日期的公开资料形成的综合判断，不代表视频作者观点，也不构成投资建议。

当前姿态是等待下一次数据验证，不输出买卖指令。
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

    def test_runs_the_hash_bound_video_report_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "assets"
            assets.mkdir()
            template = assets / "report-template.html"
            template.write_text(
                "<!doctype html><html><head><title>{{TITLE}}</title>"
                "<style>body{font-family:sans-serif}</style></head><body><main>"
                "<header><p>{{REPORT_META}}</p><p>{{SUMMARY}}</p></header>"
                "{{REPORT_BODY}}</main></body></html>",
                encoding="utf-8",
            )
            video_id = import_transcript_package(root, self._make_package(root))
            run_dir = ManifestStore(root).run_dir(video_id)

            analysis = run_dir / "video-analysis.json"
            analysis.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
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
                        "research_status": "pending",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            record_analysis(root, video_id, analysis, opinions)

            research_dir = run_dir / "research"
            research_dir.mkdir()
            source_url = "https://example.com/official-evidence"
            (research_dir / "market.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "video_id": video_id,
                        "topic_id": "market",
                        "theme": "市场改善",
                        "researched_at": "2026-08-24",
                        "disclaimer": "独立研判，不代表视频作者观点。",
                        "topic_summary": "方向可能成立，但依赖后续数据。",
                        "assessments": [
                            {
                                "opinion_id": "opinion-001",
                                "status": "conditional",
                                "conclusion": "结论有条件成立。",
                                "supporting_evidence": ["官方数据出现改善。"],
                                "counterevidence": ["样本期仍然较短。"],
                                "applicable_conditions": ["后续数据继续改善。"],
                                "time_horizon": "未来一个季度",
                                "priced_in": "无法判断是否已充分计价。",
                                "uncertainties": ["下一期数据尚未发布。"],
                            }
                        ],
                        "sources": [
                            {
                                "source_id": "source-001",
                                "title": "Official evidence",
                                "publisher": "Official publisher",
                                "author": "Official author",
                                "published_at": "2026-08-23",
                                "accessed_at": "2026-08-24",
                                "url": source_url,
                                "evidence_summary": "提供带日期的市场数据。",
                                "scope": "最近一个季度",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            record_research(root, video_id, research_dir)

            judgment = run_dir / "agent-judgment.json"
            judgment.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "video_id": video_id,
                        "source_as_of": "2026-08-24",
                        "disclaimer": (
                            "本节不代表视频作者观点，也不构成投资建议。"
                        ),
                        "cross_topic_summary": "等待数据确认。",
                        "topics": [
                            {
                                "topic_id": "market",
                                "theme": "市场改善",
                                "conclusion": "改善尚需验证。",
                                "evidence_layers": {
                                    "facts": ["官方数据已出现初步改善。"],
                                    "management_claims": [],
                                    "inference": "若改善持续，方向判断更可信。",
                                    "agent_judgment": "当前只适合等待验证。",
                                },
                                "confidence": "中等",
                                "time_horizon": "未来一个季度",
                                "priced_in": "无法判断是否已充分计价。",
                                "what_must_be_true": ["下一期数据继续改善。"],
                                "disconfirmers": ["30日内指标回落超过10%。"],
                                "downside_mechanism": {
                                    "shock": "数据恶化",
                                    "transmission": "预期下修",
                                    "constraint": "证据期较短",
                                    "outcome": "判断不成立",
                                },
                                "action_posture": "wait_for_proof",
                                "missing_evidence": "下一期官方数据",
                                "next_verification": "2026-09-24复核",
                                "source_urls": [source_url],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            record_agent_judgment(root, video_id, judgment)

            report_dir = root / "reports" / "2026-08-20-video-1"
            report_dir.mkdir(parents=True)
            draft = report_dir / "report.md"
            draft.write_text(REPORT_MARKDOWN, encoding="utf-8")
            record_draft(root, video_id, draft)

            report_transcript = ManifestStore(root).artifact_path(
                ManifestStore(root).load(video_id), "transcript_report_jsonl"
            )
            review = run_dir / "fidelity-review.json"
            review.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "video_id": video_id,
                        "external_research_visible_to_reviewer": False,
                        "overall_verdict": "passed",
                        "post_revision_verdict": "passed",
                        "draft_sha256": sha256_file(draft),
                        "transcript_sha256": sha256_file(report_transcript),
                        "section_checks": [
                            {
                                "section_id": "section-1",
                                "status": "passed",
                                "coverage_status": "included",
                                "report_locations": ["第一部分 / 市场判断"],
                                "omission_reason": "",
                            }
                        ],
                        "opinion_checks": [
                            {
                                "opinion_id": "opinion-001",
                                "status": "passed",
                                "speaker": "Creator",
                                "stance_owner": "Creator",
                                "attribution_mode": "self",
                                "report_locations": ["第一部分 / 市场判断"],
                            }
                        ],
                        "exclusion_checks": [],
                        "unresolved_transcript_checks": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            record_fidelity_review(root, video_id, review)

            build_structured(
                root,
                argparse.Namespace(
                    video_id=video_id,
                    video_analysis=analysis,
                    opinions=opinions,
                    research_dir=research_dir,
                    agent_judgment=judgment,
                    fidelity_review=review,
                    report_data=report_dir / "report-data.json",
                    citations=report_dir / "citations.json",
                ),
            )
            render_html(
                root,
                video_id,
                draft,
                template,
                report_dir / "index.html",
            )

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
                        "report_data_sha256": sha256_file(
                            report_dir / "report-data.json"
                        ),
                        "citations_sha256": sha256_file(
                            report_dir / "citations.json"
                        ),
                        "checks": {"desktop": "passed", "mobile": "passed"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            validate_html(root, video_id, validation)
            complete_run(root, video_id)

            manifest = ManifestStore(root).load(video_id)
            self.assertTrue(manifest.is_complete(Stage.COMPLETE))
            self.assertTrue(ProcessedReportStore(root).contains(video_id))
            self.assertIn("report_html", manifest.artifact_hashes)
            self.assertIn("default_visible_main_cjk_count", manifest.metadata)
            self.assertIn("creator_visible_compression_ratio", manifest.metadata)
            self.assertIn(
                'class="reading-paths video"',
                (report_dir / "index.html").read_text(encoding="utf-8"),
            )
            self.assertIn("source_artifact_hashes", json.loads(
                (report_dir / "report-data.json").read_text(encoding="utf-8")
            ))


if __name__ == "__main__":
    unittest.main()
