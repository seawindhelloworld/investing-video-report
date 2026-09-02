import tempfile
import unittest
from pathlib import Path

from video_opinion_report.reporting import (
    build_structured_artifacts,
    parse_front_matter,
    render_markdown_report,
    validate_rendered_report,
    validate_report_layers,
    validate_report_readability,
)


class ReportingTests(unittest.TestCase):
    def _progressive_report(self) -> str:
        creator = []
        evidence = []
        decisions = []
        for index in (1, 2):
            claim = f"claim-{index:03d}"
            creator.append(
                f'<section class="topic-brief" data-claim-id="{claim}" markdown="1">\n\n'
                f"**结论：**主题 {index} 的方向成立，但仍有明确限定。\n\n"
                "- **关键依据：**一项可回溯事实。\n"
                "- **核心限定：**下一期数据可能改变判断。\n\n"
                "</section>\n\n"
                f'<details class="report-detail" data-claim-id="{claim}" markdown="1">\n'
                "<summary>展开作者依据与原话</summary>\n\n"
                + ("完整背景、推理、限定和时间戳仍保留。" * 45)
                + "\n\n</details>"
            )
            evidence.append(
                f'<section class="evidence-delta" data-claim-id="{claim}" markdown="1">\n\n'
                "**证据结论：**外部证据只部分支持。\n\n"
                "- **一致视角：**方向一致。\n"
                "- **不同视角：**样本有限。\n"
                "- **关键条件：**数据延续。\n\n"
                "</section>\n\n"
                f'<details class="report-detail" data-claim-id="{claim}" markdown="1">\n'
                "<summary>展开证据边界</summary>\n\n完整来源说明。\n\n</details>"
            )
            decisions.append(
                f'<section class="decision-brief" data-claim-id="{claim}" markdown="1">\n\n'
                "- **Agent 判断：**等待证明。\n"
                "- **反证 / 下一验证：**下一期官方数据。\n\n"
                "</section>\n\n"
                f'<details class="report-detail" data-claim-id="{claim}" markdown="1">\n'
                "<summary>展开完整判断</summary>\n\n成立条件、反证和下行机制。\n\n</details>"
            )
        return "\n\n".join(
            [
                "# R",
                "## 第一部分｜视频 / 作者内容",
                '<div class="layer-intro creator"><strong>报告说明（非原内容）：</strong>以下为忠实整理。</div>',
                '<section class="summary-dashboard"><article>主题一</article><article>主题二</article></section>',
                *creator,
                "## 第二部分｜外部证据研判",
                "本注为基于外部信源形成的独立研判，不代表视频作者观点。",
                *evidence,
                "## 第三部分｜Agent 综合判断",
                "本节为 Agent 基于视频、研判和注明日期的资料形成，不代表视频作者观点，也不构成投资建议。",
                *decisions,
            ]
        )

    def test_render_markdown_report_parses_markdown_in_opted_in_html_container(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown_path = root / "report.md"
            template_path = root / "template.html"
            output_path = root / "report.html"
            markdown_path.write_text(
                "---\ntitle: Container test\n---\n\n"
                "# Container test\n\n"
                '<section class="card" markdown="1">\n\n'
                "**Rendered** body.\n\n"
                "</section>\n",
                encoding="utf-8",
            )
            template_path.write_text(
                "<html><head><title>{{TITLE}}</title></head>"
                "<body>{{REPORT_META}}{{SUMMARY}}{{REPORT_BODY}}</body></html>",
                encoding="utf-8",
            )

            render_markdown_report(markdown_path, template_path, output_path)

            rendered = output_path.read_text(encoding="utf-8")
            self.assertIn('<section class="card">', rendered)
            self.assertIn("<strong>Rendered</strong> body.", rendered)
            self.assertNotIn("**Rendered**", rendered)

    def test_parse_front_matter(self) -> None:
        metadata, body = parse_front_matter('---\ntitle: "Example"\ncreator: Me\n---\n# Example\n')
        self.assertEqual(metadata["title"], "Example")
        self.assertEqual(metadata["creator"], "Me")
        self.assertTrue(body.startswith("# Example"))

    def test_render_report_replaces_template_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "report.md"
            template = root / "template.html"
            output = root / "index.html"
            source.write_text(
                '---\ntitle: "示例"\ncreator: 作者\nreport_date: "2026-08-01"\n---\n# 示例\n\n## 表格 {#table}\n\n| A | B |\n|---|---|\n| 1 | 2 |\n',
                encoding="utf-8",
            )
            template.write_text(
                "<html><head><style></style><title>{{TITLE}}</title></head>"
                "<body>{{REPORT_META}}|{{SUMMARY}}|{{REPORT_BODY}}</body></html>",
                encoding="utf-8",
            )
            render_markdown_report(source, template, output)
            html = output.read_text(encoding="utf-8")
            self.assertIn("<title>示例</title>", html)
            self.assertIn("<table>", html)
            self.assertIn('id="table"', html)
            self.assertIn("@page { size: A4;", html)
            self.assertIn("@media print", html)
            self.assertNotIn("{{REPORT_BODY}}", html)

    def test_render_report_can_suppress_unlabeled_header_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "report.md"
            template = root / "template.html"
            output = root / "index.html"
            source.write_text(
                '---\ntitle: "Report"\ndescription: ""\n---\n\n# Report\n\n## Executive Summary\n',
                encoding="utf-8",
            )
            template.write_text(
                "<html><head><style></style></head><body><h1>{{TITLE}}</h1>"
                "<p>{{SUMMARY}}</p>{{REPORT_BODY}}<span>{{REPORT_META}}</span></body></html>",
                encoding="utf-8",
            )

            render_markdown_report(source, template, output)

            document = output.read_text(encoding="utf-8")
            self.assertNotIn("<p></p>", document)
            self.assertIn(">Executive Summary</h2>", document)

    def test_project_template_renders_editorial_cover_and_deck(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "report.md"
            output = root / "index.html"
            source.write_text(
                '---\ntitle: "大债务周期下的贬值交易"\n'
                'creator: "美投侃新闻"\n'
                'description: "长期资本正在变贵。"\n---\n\n'
                "# 大债务周期下的贬值交易\n\n正文。\n",
                encoding="utf-8",
            )

            render_markdown_report(
                source,
                Path(__file__).resolve().parents[1] / "assets" / "report-template.html",
                output,
            )

            document = output.read_text(encoding="utf-8")
            self.assertIn('class="report-cover"', document)
            self.assertIn("Market Intelligence", document)
            self.assertIn('class="cover-deck">长期资本正在变贵。</p>', document)
            self.assertIn("color-scheme: light", document)
            self.assertIn("--canvas: #f4f1ea", document)
            self.assertIn("--ink: #182230", document)

    def test_render_report_injects_script_free_reading_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "report.md"
            template = root / "template.html"
            output = root / "index.html"
            source.write_text(self._progressive_report(), encoding="utf-8")
            template.write_text(
                "<html><head><style></style><title>{{TITLE}}</title></head>"
                "<body><main>{{REPORT_META}}{{SUMMARY}}{{REPORT_BODY}}</main></body></html>",
                encoding="utf-8",
            )

            render_markdown_report(source, template, output)

            document = output.read_text(encoding="utf-8")
            self.assertIn('class="reading-paths video"', document)
            self.assertIn('href="#agent-judgment"', document)
            self.assertIn('<h2 id="creator-content">', document)
            self.assertIn('class="report-detail"', document)
            self.assertNotIn("<script", document)
            counts = validate_rendered_report(output, root)
            self.assertEqual(counts["reading_path_count"], 1)
            self.assertEqual(counts["rendered_report_detail_count"], 6)
            self.assertEqual(counts["rendered_claim_component_count"], 6)

    def test_readability_gate_counts_only_default_visible_content(self) -> None:
        report = self._progressive_report()
        metrics = validate_report_readability(
            report,
            transcript_text="市场可能改善但仍需验证" * 420,
            topic_count=2,
        )

        self.assertEqual(metrics["claim_map_count"], 2)
        self.assertEqual(metrics["report_detail_count"], 6)
        self.assertEqual(metrics["open_report_detail_count"], 0)
        self.assertEqual(metrics["collapsed_report_detail_count"], 6)
        self.assertLess(metrics["max_claim_brief_cjk_count"], 260)
        self.assertLess(metrics["creator_visible_compression_ratio"], 0.42)
        self.assertEqual(metrics["cross_layer_duplicate_block_count"], 0)

    def test_readability_gate_allows_editorial_components_without_claim_mapping(self) -> None:
        report = self._progressive_report().replace(
            'class="topic-brief" data-claim-id="claim-001"',
            'class="plain-topic" data-claim-id="claim-001"',
        )
        metrics = validate_report_readability(
            report,
            transcript_text="市场可能改善但仍需验证" * 420,
            topic_count=2,
        )
        self.assertEqual(metrics["claim_map_count"], 1)

    def test_readability_gate_rejects_verbose_default_layer(self) -> None:
        report = self._progressive_report().replace(
            "**结论：**主题 1 的方向成立，但仍有明确限定。",
            "默认可见的长篇转述。" * 420,
        )
        with self.assertRaisesRegex(ValueError, "compression ratio|default-visible"):
            validate_report_readability(
                report,
                transcript_text="市场可能改善但仍需验证" * 420,
                topic_count=2,
            )

    def test_readability_gate_allows_editorial_details_to_be_open(self) -> None:
        report = self._progressive_report().replace(
            "完整背景、推理、限定和时间戳仍保留。" * 45,
            "完整背景、推理和限定仍保留。",
            1,
        ).replace(
            '<details class="report-detail"',
            '<details open class="report-detail"',
            1,
        )
        metrics = validate_report_readability(
            report,
            transcript_text="市场可能改善但仍需验证" * 420,
            topic_count=2,
        )
        self.assertEqual(metrics["open_report_detail_count"], 1)

    def test_render_report_rejects_executable_raw_html(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "report.md"
            template = root / "template.html"
            output = root / "index.html"
            source.write_text(
                "# Unsafe\n\n<script src=\"https://example.com/payload.js\"></script>\n",
                encoding="utf-8",
            )
            template.write_text(
                "<html><head><style></style><title>{{TITLE}}</title></head>"
                "<body><main>{{REPORT_META}}{{SUMMARY}}{{REPORT_BODY}}</main></body></html>",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "forbidden HTML elements"):
                render_markdown_report(source, template, output)

    def test_validate_rendered_report_checks_local_assets_and_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_dir = root / "reports" / "example"
            report_dir.mkdir(parents=True)
            (report_dir / "chart.svg").write_text("<svg/>", encoding="utf-8")
            report = report_dir / "index.html"
            report.write_text(
                "<html><head><title>R</title>"
                '<meta http-equiv="Content-Security-Policy" '
                'content="default-src \'none\'; script-src \'none\'">'
                "</head><body><main>"
                '<a href="#topic">Topic</a><h2 id="topic">Topic</h2>'
                '<img src="chart.svg" alt="Chart">'
                + ("content " * 40)
                + "</main></body></html>",
                encoding="utf-8",
            )

            counts = validate_rendered_report(report, root)

            self.assertEqual(counts["image_count"], 1)
            self.assertEqual(counts["local_reference_count"], 1)
            self.assertEqual(counts["internal_anchor_count"], 1)

            report.write_text(report.read_text(encoding="utf-8").replace("#topic", "#missing"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Broken internal anchor"):
                validate_rendered_report(report, root)

    def test_validate_report_layers_requires_external_and_agent_sections(self) -> None:
        text = """# R

## 第一部分｜视频 / 作者内容

<div class="layer-intro creator"><strong>报告说明（非原内容）：</strong>以下为忠实整理。</div>

市场修复来自盈利改善，但利率压力仍未解除。

## 第二部分｜外部证据研判

**观点研判注 1**
本注为基于外部信源形成的独立研判，不代表视频作者观点。

## 第三部分｜Agent 综合判断

本节为 Agent 基于视频、研判和注明日期的资料形成，不代表视频作者观点，也不构成投资建议。
"""
        counts = validate_report_layers(text)
        self.assertEqual(counts["external_assessment_disclaimer_count"], 1)
        self.assertEqual(counts["agent_judgment_heading_count"], 1)
        self.assertEqual(counts["layer_heading_count"], 3)
        self.assertEqual(counts["creator_direct_voice_attribution_count"], 0)
        self.assertEqual(counts["duration_weighting_count"], 0)
        self.assertEqual(counts["unlabeled_editorial_note_count"], 0)
        self.assertEqual(counts["speaker_opinion_marker_count"], 0)
        self.assertEqual(counts["tech_five_news_heading_count"], 0)
        self.assertEqual(counts["promotional_content_count"], 0)
        self.assertEqual(counts["tech_news_visible_timestamp_count"], 0)
        with self.assertRaisesRegex(ValueError, "third Agent judgment"):
            validate_report_layers(text.replace("## 第三部分｜Agent 综合判断", "## 总结"))
        with self.assertRaisesRegex(ValueError, "video information"):
            validate_report_layers(text + "\n## 视频信息\n")
        with self.assertRaisesRegex(ValueError, "video cover"):
            validate_report_layers(text + "\n![视频封面](cover.jpg)\n")
        described = text.replace(
            "# R",
            '---\ndescription: "长期资本正在变贵，市场需要重新给久期定价。"\n---\n# R',
        )
        counts = validate_report_layers(described)
        self.assertEqual(counts["layer_heading_count"], 3)

    def test_validate_report_layers_rejects_host_voice_only_in_creator_section(self) -> None:
        text = """# R

## 第一部分｜视频 / 作者内容

<div class="layer-intro creator"><strong>报告说明（非原内容）：</strong>以下为忠实整理。</div>

视频称市场已经修复。

## 第二部分｜外部证据研判

本注为基于外部信源形成的独立研判，不代表视频作者观点。

## 第三部分｜Agent 综合判断

本节为 Agent 基于视频、研判和注明日期的资料形成，不代表视频作者观点，也不构成投资建议。
"""
        with self.assertRaisesRegex(ValueError, "third-person attribution"):
            validate_report_layers(text)

        external_only = text.replace(
            "视频称市场已经修复。",
            "市场已经修复。",
        ).replace(
            "本注为基于外部信源形成的独立研判，不代表视频作者观点。",
            "视频称市场已经修复。\n\n本注为基于外部信源形成的独立研判，不代表视频作者观点。",
        )
        counts = validate_report_layers(external_only)
        self.assertEqual(counts["creator_direct_voice_attribution_count"], 0)

        topic_word_only = external_only.replace(
            "市场已经修复。",
            "生成式视频模型的成本可能继续下降。",
            1,
        )
        counts = validate_report_layers(topic_word_only)
        self.assertEqual(counts["creator_direct_voice_attribution_count"], 0)

    def test_validate_report_layers_rejects_duration_derived_importance(self) -> None:
        text = """# R

## 第一部分｜视频 / 作者内容

<div class="layer-intro creator"><strong>报告说明（非原内容）：</strong>以下为忠实整理。</div>

市场修复来自盈利改善。因为微软部分更长，报告按章节时长分配篇幅权重。

## 第二部分｜外部证据研判

本注为基于外部信源形成的独立研判，不代表视频作者观点。

## 第三部分｜Agent 综合判断

本节为 Agent 基于视频、研判和注明日期的资料形成，不代表视频作者观点，也不构成投资建议。
"""
        with self.assertRaisesRegex(ValueError, "duration-derived topic weighting"):
            validate_report_layers(text)

    def test_validate_report_layers_rejects_unlabeled_editorial_notes(self) -> None:
        text = """# R

## 第一部分｜视频 / 作者内容

<div class="layer-intro creator"><strong>报告说明（非原内容）：</strong>以下为忠实整理。</div>

已经小幅补仓。这是个人行动，不构成本报告的投资建议。

## 第二部分｜外部证据研判

本注为基于外部信源形成的独立研判，不代表视频作者观点。

## 第三部分｜Agent 综合判断

本节为 Agent 基于视频、研判和注明日期的资料形成，不代表视频作者观点，也不构成投资建议。
"""
        with self.assertRaisesRegex(ValueError, "unlabeled editorial note"):
            validate_report_layers(text)

        labeled = text.replace(
            "已经小幅补仓。这是个人行动，不构成本报告的投资建议。",
            "已经小幅补仓。\n\n<aside class=\"editorial-note\"><strong>报告说明（非原内容）：</strong>上述行动不构成本报告的投资建议。</aside>",
        )
        counts = validate_report_layers(labeled)
        self.assertEqual(counts["unlabeled_editorial_note_count"], 0)

    def test_validate_report_layers_counts_and_scopes_speaker_opinion_markers(self) -> None:
        text = """# R

## 第一部分｜视频 / 作者内容

<div class="layer-intro creator"><strong>报告说明（非原内容）：</strong>以下为忠实整理。</div>

<div class="speaker-opinion-marker" data-speaker="Jason"><span class="speaker-opinion-kicker">报告标注 · 视频内个人判断</span><strong>Jason</strong><span class="speaker-opinion-topic">市场</span></div>

> 这更像财报驱动的修复。

## 第二部分｜外部证据研判

本注为基于外部信源形成的独立研判，不代表视频作者观点。

## 第三部分｜Agent 综合判断

本节为 Agent 基于视频、研判和注明日期的资料形成，不代表视频作者观点，也不构成投资建议。
"""
        counts = validate_report_layers(text)
        self.assertEqual(counts["speaker_opinion_marker_count"], 1)

        malformed = text.replace(' data-speaker="Jason"', "")
        with self.assertRaisesRegex(ValueError, "malformed speaker-opinion marker"):
            validate_report_layers(malformed)

        outside = text.replace(
            "## 第三部分｜Agent 综合判断",
            '<div class="speaker-opinion-marker" data-speaker="X"><span>报告标注 · 视频内个人判断</span></div>\n\n## 第三部分｜Agent 综合判断',
        )
        with self.assertRaisesRegex(ValueError, "outside creator-content"):
            validate_report_layers(outside)

    def test_investor_dashboard_and_attribution_cards_keep_layer_boundaries(self) -> None:
        text = """# R

<section id="investor-dashboard" class="investor-dashboard" data-as-of="2026-08-28" markdown="1">
<header class="investor-dashboard-header"><span class="dashboard-kicker">INVESTOR DASHBOARD</span><strong>投资决策总览</strong><small>报告综合 · 非视频原内容</small></header>
<div class="investor-dashboard-grid">
<article class="investor-topic" data-status="mixed"><div class="investor-topic-head"><span class="asset-tags">CRM</span><span class="status-pill">证据：部分支持</span></div><strong>软件需求仍有韧性</strong><p class="video-core"><b>视频核心观点</b>行业进入分化。</p><dl><dt>Agent 姿态</dt><dd>等待证明</dd><dt>期限</dt><dd>两个季度</dd><dt>下一催化</dt><dd>下一次财报</dd><dt>关键反证</dt><dd>cRPO 连续两季低于 10%</dd></dl></article>
</div>
</section>

## 第一部分｜视频 / 作者内容

<div class="layer-intro creator"><strong>报告说明（非原内容）：</strong>以下为忠实整理。</div>

<div class="speaker-opinion-marker creator-view-card" data-speaker="Jason" data-stance-owner="Jason" data-attribution-mode="self"><span class="speaker-opinion-kicker">JASON TAKE</span><strong>Jason</strong><span class="speaker-opinion-topic">软件</span></div>

> 软件行业将继续分化。

<div class="speaker-opinion-marker reported-view-card" data-speaker="Jason" data-stance-owner="CNBC" data-attribution-mode="reported"><span class="speaker-opinion-kicker">CNBC VIEW</span><strong>CNBC</strong><span class="speaker-opinion-topic">由 Jason 转述</span></div>

> 合作提供了继续乐观的理由。

## 第二部分｜外部证据研判

本注为基于外部信源形成的独立研判，不代表视频作者观点。

## 第三部分｜Agent 综合判断

本节为 Agent 基于视频、研判和注明日期的资料形成，不代表视频作者观点，也不构成投资建议。
"""
        counts = validate_report_layers(text)
        self.assertEqual(counts["investor_dashboard_count"], 1)
        self.assertEqual(counts["creator_view_card_count"], 1)
        self.assertEqual(counts["reported_view_card_count"], 1)

        misplaced = text.replace(
            "## 第一部分｜视频 / 作者内容",
            "## 第一部分｜视频 / 作者内容\n\n<section id=\"investor-dashboard-copy\" class=\"investor-dashboard\">报告综合 · 非视频原内容</section>",
        )
        with self.assertRaisesRegex(ValueError, "single investor dashboard"):
            validate_report_layers(misplaced)

        missing_label = text.replace("报告综合 · 非视频原内容", "综合摘要")
        with self.assertRaisesRegex(ValueError, "non-video boundary label"):
            validate_report_layers(missing_label)

        misattributed = text.replace(
            'data-stance-owner="Jason" data-attribution-mode="self"',
            'data-stance-owner="CNBC" data-attribution-mode="self"',
        )
        with self.assertRaisesRegex(ValueError, "malformed speaker-opinion marker"):
            validate_report_layers(misattributed)

    def test_rendered_dashboard_is_linked_from_reading_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "report.md"
            output = root / "index.html"
            report = self._progressive_report().replace(
                "## 第一部分｜视频 / 作者内容",
                '<section id="investor-dashboard" class="investor-dashboard" markdown="1">\n\n'
                '<small>报告综合 · 非视频原内容</small>\n\n'
                '<article class="investor-topic">等待证明</article>\n\n'
                '</section>\n\n## 第一部分｜视频 / 作者内容',
            )
            source.write_text(report, encoding="utf-8")
            render_markdown_report(
                source,
                Path(__file__).resolve().parents[1] / "assets" / "report-template.html",
                output,
            )
            document = output.read_text(encoding="utf-8")
            self.assertIn('href="#investor-dashboard">投资总览</a>', document)
            self.assertIn('class="investor-dashboard"', document)
            self.assertIn('class="investor-topic"', document)

    def test_validate_report_layers_requires_fixed_tech_news_name_and_rejects_ads(self) -> None:
        text = """# R

## 第一部分｜视频 / 作者内容

<div class="layer-intro creator"><strong>报告说明（非原内容）：</strong>以下为忠实整理。</div>

### 六、科技五大新闻 {#tech-five-news}

<section class="quick-news-grid"><article>新闻一</article></section>

## 第二部分｜外部证据研判

本注为基于外部信源形成的独立研判，不代表视频作者观点。

## 第三部分｜Agent 综合判断

本节为 Agent 基于视频、研判和注明日期的资料形成，不代表视频作者观点，也不构成投资建议。
"""
        counts = validate_report_layers(text)
        self.assertEqual(counts["tech_five_news_heading_count"], 1)
        self.assertEqual(counts["promotional_content_count"], 0)
        self.assertEqual(counts["tech_news_visible_timestamp_count"], 0)

        legacy = text.replace("科技五大新闻", "片尾五条快讯", 1)
        with self.assertRaisesRegex(ValueError, "科技五大新闻"):
            validate_report_layers(legacy)

        promotional = text.replace(
            '<section class="quick-news-grid">',
            "节目先介绍美投Pro的研究内容。\n\n<section class=\"quick-news-grid\">",
        )
        with self.assertRaisesRegex(ValueError, "promotional content"):
            validate_report_layers(promotional)

        timestamped = text.replace(
            "新闻一</article>",
            '新闻一 <a href="https://www.youtube.com/watch?v=v1&t=10s">00:10</a></article>',
        )
        with self.assertRaisesRegex(ValueError, "visible video timestamps"):
            validate_report_layers(timestamped)

    def test_build_structured_artifacts_joins_assessments_and_deduplicates_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            research = root / "research"
            research.mkdir()
            (root / "analysis.json").write_text(
                '{"video_id":"v1","title":"T","source_url":"https://video","creator":"C","published_at":"2026-01-01","duration_seconds":10}',
                encoding="utf-8",
            )
            (root / "opinions.jsonl").write_text(
                '{"opinion_id":"o1","research_status":"pending"}\n', encoding="utf-8"
            )
            (root / "review.json").write_text('{"post_revision_verdict":"passed"}', encoding="utf-8")
            (root / "judgment.json").write_text(
                '{"video_id":"v1","source_as_of":"2026-08-01",'
                '"sources":[{"source_id":"agent-s1","title":"Agent source",'
                '"publisher":"Official","author":"Issuer",'
                '"published_at":"2026-08-01","accessed_at":"2026-08-02",'
                '"url":"https://agent-source","evidence_summary":"e",'
                '"scope":"agent"}],'
                '"topics":[{"topic_id":"j1","source_urls":["https://agent-source"]}]}',
                encoding="utf-8",
            )
            (research / "topic.json").write_text(
                '{"topic_id":"t1","assessments":[{"opinion_id":"o1","status":"supported"}],"sources":[{"source_id":"s1","title":"S","publisher":"P","published_at":"2026","accessed_at":"2026","url":"https://source","source_type":"primary","claims_supported":["x"]}]}',
                encoding="utf-8",
            )
            counts = build_structured_artifacts(
                root / "analysis.json",
                root / "opinions.jsonl",
                research,
                root / "judgment.json",
                root / "review.json",
                root / "report-data.json",
                root / "citations.json",
            )
            self.assertEqual(
                counts,
                {
                    "opinion_count": 1,
                    "topic_count": 1,
                    "citation_count": 2,
                    "agent_judgment_topic_count": 1,
                },
            )
            report_data = __import__("json").loads((root / "report-data.json").read_text(encoding="utf-8"))
            self.assertEqual(report_data["opinions"][0]["research_status"], "supported")
            self.assertEqual(report_data["schema_version"], 2)
            self.assertEqual(report_data["agent_judgment"]["topics"][0]["topic_id"], "j1")
            citations = __import__("json").loads(
                (root / "citations.json").read_text(encoding="utf-8")
            )["citations"]
            self.assertIn(
                "https://agent-source", {item["url"] for item in citations}
            )


if __name__ == "__main__":
    unittest.main()
