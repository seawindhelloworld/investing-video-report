from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import markdown


class _ReportHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_main = False
        self.has_title = False
        self.ids: set[str] = set()
        self.references: list[tuple[str, str]] = []
        self.image_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        self.has_main = self.has_main or tag == "main"
        self.has_title = self.has_title or tag == "title"
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "img":
            self.image_count += 1
        for attribute in ("href", "src"):
            if values.get(attribute):
                self.references.append((attribute, values[attribute]))


def validate_rendered_report(report_html: Path, project_root: Path) -> dict[str, int]:
    """Validate report structure, anchors, and all local references."""
    root = project_root.resolve()
    report = report_html.resolve()
    report.relative_to(root)
    document = report.read_text(encoding="utf-8")
    if len(document) < 200:
        raise ValueError("Rendered HTML is unexpectedly short")
    if "{{" in document or "}}" in document:
        raise ValueError("Rendered HTML contains an unresolved placeholder")

    parser = _ReportHtmlParser()
    parser.feed(document)
    if not parser.has_title or not parser.has_main:
        raise ValueError("Rendered HTML must contain title and main elements")

    local_count = 0
    anchor_count = 0
    for attribute, raw_reference in parser.references:
        parsed = urlsplit(raw_reference)
        if parsed.scheme in {"http", "https", "mailto", "data"} or raw_reference.startswith("//"):
            continue
        if parsed.scheme:
            raise ValueError(f"Unsupported {attribute} scheme: {raw_reference}")
        if not parsed.path and parsed.fragment:
            anchor_count += 1
            if unquote(parsed.fragment) not in parser.ids:
                raise ValueError(f"Broken internal anchor: {raw_reference}")
            continue
        if not parsed.path:
            continue
        target = (report.parent / unquote(parsed.path)).resolve()
        target.relative_to(root)
        if not target.exists():
            raise FileNotFoundError(f"Broken local {attribute}: {raw_reference}")
        local_count += 1

    return {
        "image_count": parser.image_count,
        "local_reference_count": local_count,
        "internal_anchor_count": anchor_count,
    }


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("Markdown front matter is not closed")
    metadata: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"')
    return metadata, text[end + 5 :]


def validate_report_layers(text: str) -> dict[str, int]:
    """Require ordered, visually separated report layers and a direct creator voice."""
    metadata, _ = parse_front_matter(text)
    external_marker = "本注为基于外部信源形成的独立研判，不代表视频作者观点。"
    lines = text.splitlines()
    creator_headings = [
        index
        for index, line in enumerate(lines)
        if line.startswith("## ")
        and "第一部分" in line
        and ("视频" in line or "作者" in line)
    ]
    external_headings = [
        index
        for index, line in enumerate(lines)
        if line.startswith("## ") and "第二部分" in line and "外部" in line
    ]
    agent_headings = [
        index
        for index, line in enumerate(lines)
        if line.startswith("## ") and "第三部分" in line and "Agent 综合判断" in line
    ]
    missing: list[str] = []
    if not creator_headings:
        missing.append("first creator-content section")
    if not external_headings:
        missing.append("second external-evidence section")
    if not agent_headings:
        missing.append("third Agent judgment section")
    if creator_headings and external_headings and agent_headings:
        creator_start = creator_headings[0]
        external_start = external_headings[0]
        agent_start = agent_headings[0]
        if not creator_start < external_start < agent_start:
            missing.append("ordered three-part structure")
        else:
            creator_content = "\n".join(lines[creator_start + 1 : external_start])
            if (
                'class="layer-intro creator"' not in creator_content
                or "报告说明（非原内容）" not in creator_content
            ):
                missing.append("explicit non-content report boundary")
            host_voice_pattern = re.compile(
                r"(?:视频|作者)(?:先|随后|还|最后|同时|进一步|最终)?"
                r"(?:称|表示|转述|认为|指出|强调|提到|回顾|判断|预测|主张|"
                r"分析|解释|把|用|没有|并未|给出|列出|依据|预告)"
                r"|作者(?:对[^，。；：:]{0,20})?的判断(?:是|为)"
                r"|作者(?:本人|自己|自己的|个人)"
                r"|视频的[^，。；：:]{0,20}(?:部分|章节|段落)"
            )
            attribution_count = len(host_voice_pattern.findall(creator_content))
            if attribution_count:
                missing.append("third-person attribution in creator-content section")
            if external_marker in creator_content or 'class="assessment"' in creator_content:
                missing.append("external assessment leaked into creator-content section")
            speaker_marker = 'class="speaker-opinion-marker"'
            speaker_opinion_marker_count = creator_content.count(speaker_marker)
            total_speaker_opinion_marker_count = text.count(speaker_marker)
            valid_speaker_marker_pattern = re.compile(
                r'<div\s+class="speaker-opinion-marker"\s+data-speaker="[^"]+"[^>]*>'
                r'[^\n]*报告标注 · 视频内个人判断',
            )
            valid_speaker_opinion_marker_count = len(
                valid_speaker_marker_pattern.findall(creator_content)
            )
            if total_speaker_opinion_marker_count != speaker_opinion_marker_count:
                missing.append("speaker-opinion marker outside creator-content section")
            if valid_speaker_opinion_marker_count != speaker_opinion_marker_count:
                missing.append("malformed speaker-opinion marker")
            quick_news_grid_count = creator_content.count('class="quick-news-grid"')
            tech_five_news_heading_pattern = re.compile(
                r"^###\s+(?:[一二三四五六七八九十]+[、.]\s*)?"
                r"科技五大新闻(?:\s+\{[^}]+\})?\s*$",
                re.MULTILINE,
            )
            tech_five_news_heading_count = len(
                tech_five_news_heading_pattern.findall(creator_content)
            )
            legacy_tech_news_name_pattern = re.compile(
                r"片尾五条快讯|片尾科技快讯|五条科技快讯|结尾快讯"
                r"|产品介绍与[^\n]{0,12}(?:科技快讯|科技新闻)"
            )
            legacy_tech_news_name_count = len(
                legacy_tech_news_name_pattern.findall(creator_content)
            )
            promotional_content_pattern = re.compile(
                r"美投\s*Pro|属于产品推广"
                r"|节目(?:先|后段先)[^。\n]{0,40}(?:介绍|推广)[^。\n]{0,24}(?:产品|订阅|研究内容)"
            )
            promotional_content_count = len(
                promotional_content_pattern.findall(creator_content)
            )
            quick_news_blocks = re.findall(
                r'<section\s+class="quick-news-grid"[^>]*>.*?</section>',
                creator_content,
                flags=re.DOTALL,
            )
            visible_video_time_pattern = re.compile(
                r"https://www\.youtube\.com/watch\?[^\s\"')>]*[?&]t=\d+s"
                r"|(?<!\d)\d{1,2}:\d{2}(?:\s*[–-]\s*\d{1,2}:\d{2})?(?!\d)"
            )
            tech_news_visible_timestamp_count = sum(
                len(visible_video_time_pattern.findall(block))
                for block in quick_news_blocks
            )
            if quick_news_grid_count and tech_five_news_heading_count != 1:
                missing.append("fixed 科技五大新闻 chapter heading")
            if legacy_tech_news_name_count:
                missing.append("legacy technology-news chapter naming")
            if promotional_content_count:
                missing.append("promotional content in final report")
            if tech_news_visible_timestamp_count:
                missing.append("visible video timestamps in 科技五大新闻")
            editorial_pattern = re.compile(
                r"不构成(?:本报告|本文)[^。；\n]{0,16}建议"
                r"|ASR"
                r"|未做全面事实核查"
            )
            unlabeled_editorial_note_count = sum(
                1
                for line in creator_content.splitlines()
                if editorial_pattern.search(line) and "非原内容" not in line
            )
            if unlabeled_editorial_note_count:
                missing.append("unlabeled editorial note in creator-content section")
    else:
        attribution_count = 0
        unlabeled_editorial_note_count = 0
        speaker_opinion_marker_count = 0
        tech_five_news_heading_count = 0
        promotional_content_count = 0
        tech_news_visible_timestamp_count = 0
    duration_weight_pattern = re.compile(
        r"(?:按|根据|依照)[^。；\n]{0,24}(?:时长|时间占比|播放占比)"
        r"[^。；\n]{0,24}(?:权重|重要性|篇幅|研究深度)"
        r"|(?:时长|时间占比|播放占比)[^。；\n]{0,24}(?:决定|代表|对应|推导|计算)"
        r"[^。；\n]{0,16}(?:权重|重要性|篇幅|研究深度)"
        r"|(?:相同权重|篇幅权重)"
    )
    duration_weighting_count = len(duration_weight_pattern.findall(text))
    if duration_weighting_count:
        missing.append("duration-derived topic weighting")
    if external_marker not in text:
        missing.append("external evidence assessment disclaimer")
    if "本节为 Agent" not in text or "不构成投资建议" not in text:
        missing.append("Agent judgment disclaimer")
    if metadata.get("description", "").strip():
        missing.append("header description")
    if any(line.strip().startswith("## 视频信息") for line in text.splitlines()):
        missing.append("standalone video information section")
    if "![视频封面" in text:
        missing.append("video cover image")
    if missing:
        raise ValueError(f"Final report is missing required layers: {', '.join(missing)}")
    return {
        "external_assessment_disclaimer_count": text.count(external_marker),
        "agent_judgment_heading_count": len(agent_headings),
        "layer_heading_count": 3,
        "creator_direct_voice_attribution_count": attribution_count,
        "duration_weighting_count": duration_weighting_count,
        "unlabeled_editorial_note_count": unlabeled_editorial_note_count,
        "speaker_opinion_marker_count": speaker_opinion_marker_count,
        "tech_five_news_heading_count": tech_five_news_heading_count,
        "promotional_content_count": promotional_content_count,
        "tech_news_visible_timestamp_count": tech_news_visible_timestamp_count,
    }


def _drop_first_heading(text: str) -> str:
    lines = text.lstrip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines)


def render_markdown_report(markdown_path: Path, template_path: Path, output_path: Path) -> None:
    metadata, body = parse_front_matter(markdown_path.read_text(encoding="utf-8"))
    title = metadata.get("title")
    if not title:
        first = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), "")
        title = first or markdown_path.stem
    body_html = markdown.markdown(
        _drop_first_heading(body),
        extensions=[
            "attr_list",
            "fenced_code",
            "md_in_html",
            "tables",
            "sane_lists",
            "toc",
        ],
        output_format="html5",
    )
    report_meta = " · ".join(
        value
        for value in (
            metadata.get("creator"),
            metadata.get("published_at"),
            f"报告日期 {metadata['report_date']}" if metadata.get("report_date") else None,
        )
        if value
    )
    summary = metadata.get("description", "").strip()
    template = template_path.read_text(encoding="utf-8")
    required = ("{{TITLE}}", "{{REPORT_META}}", "{{SUMMARY}}", "{{REPORT_BODY}}")
    missing = [placeholder for placeholder in required if placeholder not in template]
    if missing:
        raise ValueError(f"HTML template is missing placeholders: {', '.join(missing)}")
    if summary:
        template = template.replace("{{SUMMARY}}", html.escape(summary))
    else:
        template = template.replace("<p>{{SUMMARY}}</p>", "")
        template = template.replace("{{SUMMARY}}", "")
    document = (
        template.replace("{{TITLE}}", html.escape(title))
        .replace("{{REPORT_META}}", html.escape(report_meta))
        .replace("{{REPORT_BODY}}", body_html)
    )
    extra_style = """
    img { display: block; max-width: 100%; height: auto; border-radius: 14px; }
    img[src$=".svg"] { width: 100%; margin: 1rem 0 .35rem; border: 1px solid var(--line); background: #fff; box-shadow: 0 12px 30px rgba(16, 24, 40, .06); }
    .visual-caption { margin: .35rem 0 1.4rem; color: var(--muted); font-size: .86rem; }
    .metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: .75rem; margin: 1.4rem 0 1.8rem; }
    .metric-card { padding: 1rem; border: 1px solid var(--line); border-radius: 14px; background: linear-gradient(145deg, #fff, #f8faff); }
    .metric-card strong { display: block; color: #16213e; font-size: 1.55rem; line-height: 1.15; font-variant-numeric: tabular-nums; }
    .metric-card span { display: block; margin-top: .3rem; color: var(--muted); font-size: .82rem; }
    .summary-dashboard { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .65rem; margin: 1rem 0 1.5rem; }
    .summary-dashboard article { min-height: 120px; padding: .85rem .9rem; border: 1px solid var(--line); border-top: 4px solid #6b83df; border-radius: 13px; background: #fbfcfe; }
    .summary-dashboard span { color: var(--muted); font-size: .76rem; font-weight: 750; text-transform: uppercase; }
    .summary-dashboard strong { display: block; margin-top: .28rem; color: #1f3a8a; font-size: .98rem; }
    .summary-dashboard p { margin: .28rem 0 0; color: #475467; font-size: .8rem; line-height: 1.45; }
    .logic-flow { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .55rem; margin: 1.1rem 0 1.4rem; }
    .logic-step { position: relative; min-height: 92px; padding: .8rem .65rem; border: 1px solid #cdd6f7; border-radius: 12px; background: #f7f9ff; text-align: center; font-size: .84rem; line-height: 1.4; }
    .logic-step strong { display: block; margin-bottom: .25rem; color: #2344a1; font-size: .92rem; }
    .logic-step:not(:last-child)::after { content: "→"; position: absolute; top: 31px; right: -.62rem; z-index: 2; color: #667085; font-weight: 800; }
    .reading-note { margin: 1rem 0 1.4rem; padding: 1rem 1.15rem; border-left: 4px solid #d7a12b; border-radius: 10px; background: #fffaf0; }
    .chapter-lead { margin: 1.1rem 0 1.45rem; padding: 1rem 1.2rem; border-left: 4px solid var(--accent); background: var(--accent-soft); font-size: 1.02rem; }
    .chapter-lead strong { color: #2344a1; }
    .video-data-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: .7rem; margin: 1rem 0 1.45rem; }
    .data-card { padding: .9rem 1rem; border-top: 3px solid #9db0ef; background: #f8faff; }
    .data-card strong { display: block; color: #1f3a8a; font-size: 1.22rem; font-variant-numeric: tabular-nums; }
    .data-card span { display: block; margin-top: .2rem; color: var(--muted); font-size: .82rem; }
    .signal-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .75rem; margin: 1rem 0 1.5rem; }
    .signal-card { padding: 1rem; border: 1px solid var(--line); border-radius: 13px; background: #fbfcfe; }
    .signal-card h4 { margin: 0 0 .35rem; font-size: 1rem; }
    .signal-card p { margin: .25rem 0; font-size: .9rem; }
    .signal-card.positive { border-top: 4px solid #55a47f; }
    .signal-card.caution { border-top: 4px solid #d8a33a; }
    .signal-card.question { border-top: 4px solid #8291ad; }
    .quote-pull { margin: 1.3rem 0 1.6rem; padding: 1.2rem 1.35rem; border-radius: 14px; background: #172447; color: #fff; font-size: 1.1rem; }
    .quote-pull p { margin: 0; max-width: none; }
    .quote-pull a { color: #cfdbff; }
    .number-ribbon { display: flex; flex-wrap: wrap; gap: .55rem; margin: .9rem 0 1.35rem; }
    .number-ribbon span { padding: .42rem .65rem; border-radius: 999px; background: #eef2f7; color: #344054; font-size: .82rem; font-variant-numeric: tabular-nums; }
    .quick-news-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .75rem; margin: 1rem 0 1.4rem; }
    .quick-news-grid article { padding: .95rem 1rem; border: 1px solid var(--line); border-radius: 12px; background: #fbfcfd; }
    .quick-news-grid h3 { margin: 0 0 .35rem; font-size: 1rem; }
    .quick-news-grid p { margin: .3rem 0; font-size: .88rem; }
    .quick-news-grid article:first-child { grid-column: 1 / -1; }
    .coverage-note { margin: 1rem 0; padding: .85rem 1rem; border: 1px dashed #98a2b3; border-radius: 11px; color: #475467; background: #fcfcfd; font-size: .88rem; }
    details.source-group { margin: .75rem 0; padding: .8rem 1rem; border: 1px solid var(--line); border-radius: 11px; background: #fcfcfd; }
    details.source-group summary { cursor: pointer; color: #344054; font-weight: 700; }
    details.source-group[open] summary { margin-bottom: .65rem; }
    .company-takeaways { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .8rem; margin: 1rem 0 1.4rem; }
    .company-takeaways article { padding: 1rem; border: 1px solid var(--line); border-radius: 14px; background: #fbfcfe; font-size: .9rem; }
    .company-takeaways h3 { margin: 0 0 .45rem; }
    .company-takeaways p { margin: .35rem 0; }
    .assessment-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .85rem; margin: 1rem 0 1.4rem; }
    .assessment-grid .assessment { margin: 0; }
    .assessment-grid .assessment p { margin: .45rem 0; }
    .scope-label { color: #7a4b08; font-size: .78rem; line-height: 1.45; }
    .source-row { color: var(--muted); font-size: .8rem; }
    .assessment-depth { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .85rem; margin: 1rem 0 1.6rem; }
    .assessment-depth article { padding: 1rem 1.05rem; border: 1px solid var(--line); border-radius: 14px; background: #fcfcfd; }
    .assessment-depth h3 { margin: 0 0 .55rem; font-size: 1.02rem; }
    .assessment-depth p { margin: .5rem 0; font-size: .86rem; line-height: 1.62; }
    .status-row { display: flex; flex-wrap: wrap; gap: .35rem; margin: .65rem 0; }
    .status-pill { display: inline-flex; padding: .2rem .55rem; border-radius: 999px; background: #eef2ff; color: #3157d5; font-size: .76rem; font-weight: 750; }
    .status-pill.warn { background: #fff3e8; color: #b54708; }
    .status-pill.neutral { background: #f2f4f7; color: #475467; }
    .audit-links { padding: 1rem 1.15rem; border: 1px dashed #98a2b3; border-radius: 12px; background: #fcfcfd; }
    .audit-links ul { margin-bottom: 0; }
    .assessment h3 { margin-top: 0; color: var(--ink); }
    .assessment ul { margin-bottom: .4rem; }
    .layer-intro { margin: 1rem 0 1.6rem; padding: 1rem 1.15rem; border-radius: 12px; background: #f4f7fb; color: #344054; }
    .layer-intro.creator { border-left: 5px solid #3157d5; }
    .layer-intro.external { border-left: 5px solid #d8a33a; }
    .speaker-opinion-marker { display: flex; flex-wrap: wrap; align-items: center; gap: .45rem .65rem; margin: 1.15rem 0 0; padding: .8rem 1rem .72rem; border: 1px solid #e7b85e; border-bottom: 0; border-radius: 14px 14px 0 0; background: #fff4d8; color: #6f3b00; box-shadow: 0 9px 24px rgba(146, 85, 10, .08); }
    .speaker-opinion-marker::before { content: "◆"; color: #c66a08; font-size: .8rem; }
    .speaker-opinion-kicker { padding: .18rem .5rem; border-radius: 999px; background: #b45309; color: #fff; font-size: .72rem; font-weight: 800; letter-spacing: .02em; }
    .speaker-opinion-marker strong { color: #7a3e00; font-size: 1.08rem; }
    .speaker-opinion-topic { color: #8a5a20; font-size: .82rem; }
    .speaker-opinion-marker + blockquote { margin-top: 0; padding: 1rem 1.2rem 1.08rem; border: 1px solid #e7b85e; border-left: 5px solid #d97706; border-radius: 0 0 14px 14px; background: linear-gradient(145deg, #fffdf8, #fff8e8); color: #3f2a12; box-shadow: 0 12px 28px rgba(146, 85, 10, .08); }
    .speaker-opinion-marker + blockquote p { max-width: none; }
    .speaker-opinion-marker + blockquote p:first-child { margin-top: 0; }
    .speaker-opinion-marker + blockquote p:last-child { margin-bottom: 0; }
    .editorial-note { margin: .75rem 0 1.2rem; padding: .8rem 1rem; border: 1px dashed #98a2b3; border-left: 4px solid #667085; border-radius: 10px; background: #f8fafc; color: #475467; font-size: .86rem; }
    .editorial-note strong { color: #344054; }
    .editorial-note.compact { margin: .5rem 0; padding: .65rem .75rem; font-size: .8rem; }
    .annotation-target { margin: -.15rem 0 .85rem; color: #475467; font-size: .88rem; }
    .annotation-target a { font-weight: 700; }
    .status { display: inline-flex; padding: .2rem .55rem; border-radius: 999px; background: #e9efff; color: #2344a1; font-size: .8rem; font-weight: 700; }
    .agent-disclaimer { margin: 1rem 0 1.3rem; padding: 1rem 1.15rem; border: 1px solid #b7c6f8; border-left: 5px solid #3157d5; border-radius: 12px; background: #f5f7ff; color: #263a72; }
    .judgment-overview { display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: .7rem; margin: 1rem 0 1.5rem; }
    .judgment-overview article { padding: .95rem; border: 1px solid var(--line); border-radius: 14px; background: #fbfcfe; }
    .judgment-overview h3 { margin: 0 0 .35rem; font-size: 1rem; }
    .judgment-overview p { margin: .3rem 0; font-size: .86rem; }
    .judgment-overview .verdict { color: #1f3a8a; font-weight: 750; }
    .judgment-card { margin: 1rem 0 1.35rem; padding: 1.15rem 1.25rem; border: 1px solid #cdd6f7; border-radius: 15px; background: linear-gradient(145deg, #fff, #f7f9ff); }
    .judgment-card h3 { margin: 0 0 .55rem; }
    .judgment-card.compact { padding: 1rem 1.1rem; }
    .judgment-card.compact h3 { font-size: 1.08rem; }
    .judgment-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .65rem; margin: .65rem 0; }
    .judgment-facts p { margin: 0; padding: .72rem .78rem; border-radius: 10px; background: #fff; border: 1px solid #e2e7f2; font-size: .84rem; line-height: 1.55; }
    .judgment-rationale { margin: .7rem 0; padding: .72rem .8rem; border-left: 3px solid #8296df; background: #f7f9ff; color: #344054; font-size: .84rem; line-height: 1.58; }
    .next-verification { margin: .72rem 0 0; padding-top: .65rem; border-top: 1px dashed #cdd5e7; color: #344054; font-size: .82rem; }
    .judgment-checklist { display: grid; grid-template-columns: 68px minmax(0, 1fr) 68px minmax(0, 1fr); gap: .45rem .55rem; margin: .7rem 0 0; font-size: .8rem; }
    .judgment-checklist dt { margin: 0; padding: .5rem .35rem; border-radius: 8px; background: #e9efff; color: #2344a1; font-weight: 800; text-align: center; }
    .judgment-checklist dd { margin: 0; padding: .48rem .15rem; color: #475467; line-height: 1.45; }
    .judgment-meta { display: flex; flex-wrap: wrap; gap: .4rem; margin: .55rem 0 .8rem; }
    .judgment-meta span { padding: .22rem .56rem; border-radius: 999px; background: #e9efff; color: #2344a1; font-size: .76rem; font-weight: 700; }
    .judgment-meta span.caution { background: #fff3e8; color: #b54708; }
    .decision-chain { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .55rem; margin: .85rem 0; }
    .decision-chain div { position: relative; padding: .75rem; border-radius: 10px; background: #fff; border: 1px solid #dfe5f4; font-size: .82rem; }
    .decision-chain strong { display: block; margin-bottom: .25rem; color: #344054; }
    .decision-chain div:not(:last-child)::after { content: "→"; position: absolute; top: 50%; right: -.48rem; z-index: 2; color: #667085; font-weight: 800; }
    nav.toc { margin: 2rem 0; padding: 1rem 1.2rem; border: 1px solid var(--line); border-radius: 12px; background: #fbfcfe; }
    @media screen and (min-width: 900px) {
      main { width: min(1080px, calc(100% - 48px)); padding: 56px 64px; }
    }
    @media (max-width: 760px) {
      .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .logic-flow { grid-template-columns: 1fr; }
      .logic-step { min-height: auto; text-align: left; }
      .logic-step:not(:last-child)::after { content: "↓"; top: auto; right: 50%; bottom: -.95rem; }
      .summary-dashboard { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .company-takeaways, .assessment-grid, .assessment-depth, .signal-grid, .quick-news-grid, .judgment-overview, .decision-chain, .judgment-facts { grid-template-columns: 1fr; }
      .judgment-checklist { grid-template-columns: 62px minmax(0, 1fr); }
      .speaker-opinion-marker { align-items: flex-start; }
      .decision-chain div:not(:last-child)::after { content: "↓"; top: auto; right: 50%; bottom: -.8rem; }
      .quick-news-grid article:first-child { grid-column: auto; }
      p:has(> img[src$=".svg"]) { max-width: 100%; overflow-x: auto; padding-bottom: .35rem; }
      p > img[src$=".svg"] { width: 700px; max-width: none; }
    }
    @page { size: A4; margin: 14mm 13mm 16mm; }
    @media print {
      html, body { background: #fff; }
      body {
        color: #172033;
        font-size: 10.5pt;
        line-height: 1.55;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
      }
      main {
        width: auto !important;
        max-width: none;
        margin: 0;
        padding: 0;
        border: 0;
        border-radius: 0;
        box-shadow: none;
      }
      header { margin-bottom: 8mm; }
      h1 { margin: .2em 0 .45em; font-size: 25pt; }
      h2 { margin-top: 10mm; font-size: 17pt; break-after: avoid-page; page-break-after: avoid; }
      h3 { font-size: 13pt; break-after: avoid-page; page-break-after: avoid; }
      p, li { orphans: 3; widows: 3; }
      a { color: inherit; text-decoration-color: #98a2b3; }
      img { max-height: 240mm; object-fit: contain; }
      img[src$=".svg"] { border: .3pt solid #d0d5dd; box-shadow: none; }
      table { display: table; overflow: visible; font-size: 9pt; }
      thead { display: table-header-group; }
      tr, img, blockquote, nav.toc, .metric-grid, .logic-flow, .logic-step,
      .reading-note, .chapter-lead, .data-card, .signal-card, .quote-pull,
      .quick-news-grid article, .company-takeaways article, .assessment, .audit-links,
      .summary-dashboard article, .judgment-overview article, .judgment-card, .decision-chain,
      .speaker-opinion-marker, .speaker-opinion-marker + blockquote {
        break-inside: avoid-page;
        page-break-inside: avoid;
      }
      .speaker-opinion-marker { break-after: avoid-page; page-break-after: avoid; }
      .assessment-grid { display: block; }
      .assessment-grid .assessment { margin: 0 0 5mm; }
      .visual-caption { margin-bottom: 5mm; }
    }
    """
    document = document.replace("</style>", f"{extra_style}\n  </style>", 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")


def build_structured_artifacts(
    video_analysis_path: Path,
    opinions_path: Path,
    research_dir: Path,
    agent_judgment_path: Path,
    fidelity_review_path: Path,
    report_data_path: Path,
    citations_path: Path,
) -> dict[str, int]:
    analysis = json.loads(video_analysis_path.read_text(encoding="utf-8"))
    opinions = [
        json.loads(line)
        for line in opinions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    research = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(research_dir.glob("*.json"))
    ]
    agent_judgment = json.loads(agent_judgment_path.read_text(encoding="utf-8"))
    if str(agent_judgment.get("video_id") or "") != str(analysis["video_id"]):
        raise ValueError("Agent judgment video_id does not match video analysis")
    judgment_topics = agent_judgment.get("topics")
    if not isinstance(judgment_topics, list) or not judgment_topics:
        raise ValueError("Agent judgment has no topic judgments")
    assessment_by_opinion: dict[str, dict[str, object]] = {}
    for topic in research:
        for assessment in topic["assessments"]:
            opinion_id = str(assessment["opinion_id"])
            if opinion_id in assessment_by_opinion:
                raise ValueError(f"Opinion has duplicate external assessments: {opinion_id}")
            assessment_by_opinion[opinion_id] = {
                **assessment,
                "topic_id": topic["topic_id"],
            }
    missing = [item["opinion_id"] for item in opinions if item["opinion_id"] not in assessment_by_opinion]
    if missing:
        raise ValueError(f"Opinions missing external assessments: {', '.join(missing)}")
    joined_opinions = [
        {**item, "research_status": assessment_by_opinion[item["opinion_id"]]["status"], "assessment": assessment_by_opinion[item["opinion_id"]]}
        for item in opinions
    ]
    citation_by_url: dict[str, dict[str, object]] = {}
    for topic in research:
        for source in topic["sources"]:
            url = source["url"]
            if url not in citation_by_url:
                citation_by_url[url] = {
                    **source,
                    "source_ids": [source["source_id"]],
                    "research_topics": [topic["topic_id"]],
                }
            else:
                current = citation_by_url[url]
                current["source_ids"] = sorted(set(current["source_ids"] + [source["source_id"]]))  # type: ignore[operator]
                current["research_topics"] = sorted(set(current["research_topics"] + [topic["topic_id"]]))  # type: ignore[operator]
                current["claims_supported"] = list(
                    dict.fromkeys(current.get("claims_supported", []) + source.get("claims_supported", []))  # type: ignore[operator]
                )
    citations = []
    for index, source in enumerate(citation_by_url.values(), start=1):
        source = dict(source)
        source["citation_id"] = f"citation-{index:03d}"
        citations.append(source)
    generated_at = datetime.now(timezone.utc).isoformat()
    fidelity_review = json.loads(fidelity_review_path.read_text(encoding="utf-8"))
    fidelity_verdict = str(
        fidelity_review.get("post_revision_verdict")
        or fidelity_review.get("overall_verdict")
        or ""
    )
    if fidelity_verdict not in {"passed", "passed_with_asr_caveats"}:
        raise ValueError(f"Fidelity review did not pass: {fidelity_verdict or '<missing>'}")
    status_counts: dict[str, int] = {}
    for opinion in joined_opinions:
        status = opinion["research_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    report_data = {
        "schema_version": 2,
        "generated_at": generated_at,
        "video": {
            "video_id": analysis["video_id"],
            "title": analysis["title"],
            "source_url": analysis["source_url"],
            "creator": analysis["creator"],
            "published_at": analysis["published_at"],
            "duration_seconds": analysis["duration_seconds"],
        },
        "analysis": analysis,
        "opinions": joined_opinions,
        "research_topics": research,
        "research_status_counts": status_counts,
        "agent_judgment": agent_judgment,
        "fidelity_review": fidelity_review,
        "citation_count": len(citations),
    }
    report_data_path.parent.mkdir(parents=True, exist_ok=True)
    citations_path.parent.mkdir(parents=True, exist_ok=True)
    report_data_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    citations_path.write_text(
        json.dumps({"schema_version": 1, "generated_at": generated_at, "citations": citations}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "opinion_count": len(opinions),
        "topic_count": len(research),
        "citation_count": len(citations),
        "agent_judgment_topic_count": len(judgment_topics),
    }
