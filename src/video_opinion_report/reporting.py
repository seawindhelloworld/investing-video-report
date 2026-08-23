from __future__ import annotations

import html
import json
import os
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
        self.has_strict_csp = False
        self.forbidden_elements: list[str] = []
        self.dangerous_attributes: list[str] = []
        self.reading_path_count = 0
        self.report_detail_count = 0
        self.open_report_detail_count = 0
        self.claim_component_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        classes = set(values.get("class", "").split())
        if "reading-paths" in classes:
            self.reading_path_count += 1
        if "report-detail" in classes:
            self.report_detail_count += 1
            if any(name == "open" for name, _ in attrs):
                self.open_report_detail_count += 1
        if classes.intersection(_CLAIM_COMPONENT_CLASSES):
            self.claim_component_count += 1
        if tag in {
            "script",
            "iframe",
            "frame",
            "frameset",
            "object",
            "embed",
            "form",
            "input",
            "button",
            "textarea",
            "select",
            "base",
            "link",
        }:
            self.forbidden_elements.append(tag)
        for name, _ in attrs:
            if name.startswith("on") or name in {"srcdoc", "style", "formaction"}:
                self.dangerous_attributes.append(f"{tag}[{name}]")
        if tag == "meta" and values.get("http-equiv", "").casefold() == "content-security-policy":
            policy = values.get("content", "").casefold()
            self.has_strict_csp = (
                "default-src 'none'" in policy and "script-src 'none'" in policy
            )
        self.has_main = self.has_main or tag == "main"
        self.has_title = self.has_title or tag == "title"
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "img":
            self.image_count += 1
        for attribute in ("href", "src"):
            if values.get(attribute):
                self.references.append((attribute, values[attribute]))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)


class _ReportFragmentParser(_ReportHtmlParser):
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        if tag in {
            "html",
            "head",
            "body",
            "title",
            "meta",
            "style",
            "svg",
            "math",
        }:
            self.forbidden_elements.append(tag)


_READABILITY_BLOCK_TAGS = {
    "blockquote",
    "dd",
    "li",
    "p",
    "td",
    "th",
}
_CLAIM_COMPONENT_CLASSES = (
    "topic-brief",
    "evidence-delta",
    "decision-brief",
)


class _VisibleReportTextParser(HTMLParser):
    """Collect text that is visible before a reader opens disclosure widgets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.blocks: list[str] = []
        self.quote_parts: list[str] = []
        self.collapsed_detail_count = 0
        self._detail_hidden: list[bool] = []
        self._hidden_depth = 0
        self._block_depth = 0
        self._block_parts: list[str] = []
        self._quote_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value for name, value in attrs}
        if tag == "details":
            hidden = "open" not in values
            self._detail_hidden.append(hidden)
            if hidden:
                self._hidden_depth += 1
                self.collapsed_detail_count += 1
            return
        if self._hidden_depth:
            return
        if tag == "blockquote":
            self._quote_depth += 1
        if tag in _READABILITY_BLOCK_TAGS:
            if self._block_depth == 0:
                self._block_parts = []
            self._block_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "details":
            if self._detail_hidden:
                hidden = self._detail_hidden.pop()
                if hidden:
                    self._hidden_depth -= 1
            return
        if self._hidden_depth:
            return
        if tag in _READABILITY_BLOCK_TAGS and self._block_depth:
            self._block_depth -= 1
            if self._block_depth == 0:
                block = "".join(self._block_parts).strip()
                if block:
                    self.blocks.append(block)
                self._block_parts = []
        if tag == "blockquote" and self._quote_depth:
            self._quote_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._hidden_depth or not data.strip():
            return
        self.text_parts.append(data)
        if self._block_depth:
            self._block_parts.append(data)
        if self._quote_depth:
            self.quote_parts.append(data)


class _ClaimComponentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.claim_ids: dict[str, list[str]] = {
            class_name: [] for class_name in _CLAIM_COMPONENT_CLASSES
        }
        self.component_texts: dict[str, list[tuple[str, str]]] = {
            class_name: [] for class_name in _CLAIM_COMPONENT_CLASSES
        }
        self.report_detail_count = 0
        self.open_report_detail_count = 0
        self.summary_dashboard_count = 0
        self._component_stack: list[tuple[str, str, str, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        classes = set(values.get("class", "").split())
        if "report-detail" in classes:
            self.report_detail_count += 1
            if "open" in values:
                self.open_report_detail_count += 1
        if "summary-dashboard" in classes:
            self.summary_dashboard_count += 1
        for class_name in _CLAIM_COMPONENT_CLASSES:
            if class_name in classes:
                claim_id = values.get("data-claim-id", "")
                self.claim_ids[class_name].append(claim_id)
                self._component_stack.append((tag, class_name, claim_id, []))

    def handle_endtag(self, tag: str) -> None:
        if self._component_stack and self._component_stack[-1][0] == tag:
            _, class_name, claim_id, parts = self._component_stack.pop()
            self.component_texts[class_name].append(
                (claim_id, "".join(parts).strip())
            )

    def handle_data(self, data: str) -> None:
        if self._component_stack:
            self._component_stack[-1][3].append(data)


def _validate_safe_report_fragment(fragment: str) -> None:
    parser = _ReportFragmentParser()
    parser.feed(fragment)
    if parser.forbidden_elements:
        raise ValueError(
            "Report Markdown contains forbidden HTML elements: "
            + ", ".join(sorted(set(parser.forbidden_elements)))
        )
    if parser.dangerous_attributes:
        raise ValueError(
            "Report Markdown contains dangerous HTML attributes: "
            + ", ".join(sorted(set(parser.dangerous_attributes)))
        )


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
    if not parser.has_strict_csp:
        raise ValueError("Rendered HTML must include a strict Content Security Policy")
    if parser.forbidden_elements:
        raise ValueError(
            "Rendered HTML contains forbidden elements: "
            + ", ".join(sorted(set(parser.forbidden_elements)))
        )
    if parser.dangerous_attributes:
        raise ValueError(
            "Rendered HTML contains dangerous attributes: "
            + ", ".join(sorted(set(parser.dangerous_attributes)))
        )
    if parser.claim_component_count and parser.reading_path_count != 1:
        raise ValueError("Rendered progressive report must contain one reading path navigator")
    if parser.open_report_detail_count:
        raise ValueError("Rendered report-detail disclosures must be closed by default")

    local_count = 0
    anchor_count = 0
    for attribute, raw_reference in parser.references:
        parsed = urlsplit(raw_reference)
        if parsed.scheme in {"http", "https"} or raw_reference.startswith("//"):
            continue
        if parsed.scheme == "mailto" and attribute == "href":
            continue
        if parsed.scheme == "data" and attribute == "src":
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
        "reading_path_count": parser.reading_path_count,
        "rendered_report_detail_count": parser.report_detail_count,
        "rendered_claim_component_count": parser.claim_component_count,
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


def _markdown_fragment(text: str) -> str:
    return markdown.markdown(
        text,
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


def _report_layer_markdown(text: str) -> dict[str, str]:
    _, body = parse_front_matter(text)
    lines = body.splitlines(keepends=True)
    layer_starts: dict[str, int] = {}
    all_h2_starts: list[int] = []
    offset = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            all_h2_starts.append(offset)
            if "第一部分" in stripped and ("视频" in stripped or "作者" in stripped):
                layer_starts.setdefault("creator", offset)
            elif "第二部分" in stripped and "外部" in stripped:
                layer_starts.setdefault("external", offset)
            elif "第三部分" in stripped and "Agent 综合判断" in stripped:
                layer_starts.setdefault("agent", offset)
        offset += len(line)
    result: dict[str, str] = {}
    for name, start in layer_starts.items():
        end = next((candidate for candidate in all_h2_starts if candidate > start), len(body))
        result[name] = body[start:end]
    return result


def _cjk_count(text: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]", text))


def _visible_metrics(markdown_text: str) -> dict[str, object]:
    fragment = _markdown_fragment(markdown_text)
    parser = _VisibleReportTextParser()
    parser.feed(fragment)
    blocks = [block for block in parser.blocks if _cjk_count(block) >= 20]
    return {
        "cjk_count": _cjk_count("".join(parser.text_parts)),
        "quote_cjk_count": _cjk_count("".join(parser.quote_parts)),
        "collapsed_detail_count": parser.collapsed_detail_count,
        "block_cjk_counts": [_cjk_count(block) for block in blocks],
        "normalized_blocks": [
            re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "", block).casefold()
            for block in blocks
        ],
    }


def validate_report_readability(
    text: str,
    *,
    transcript_text: str = "",
    topic_count: int = 0,
) -> dict[str, int | float]:
    """Validate default-visible density while keeping audit detail in the same report."""
    layers = _report_layer_markdown(text)
    layer_metrics = {
        name: _visible_metrics(layers.get(name, ""))
        for name in ("creator", "external", "agent")
    }
    _, body = parse_front_matter(text)
    body_fragment = _markdown_fragment(_drop_first_heading(body))
    claim_parser = _ClaimComponentParser()
    claim_parser.feed(body_fragment)
    layer_claim_parsers: dict[str, _ClaimComponentParser] = {}
    for name in ("creator", "external", "agent"):
        parser = _ClaimComponentParser()
        parser.feed(_markdown_fragment(layers.get(name, "")))
        layer_claim_parsers[name] = parser

    visible_counts = {
        name: int(metrics["cjk_count"])
        for name, metrics in layer_metrics.items()
    }
    visible_main = sum(visible_counts.values())
    transcript_cjk = _cjk_count(transcript_text)
    normalized_topic_count = max(0, int(topic_count))
    visible_limit = max(3200, 1200 + max(1, normalized_topic_count) * 750)
    creator_ratio = (
        visible_counts["creator"] / transcript_cjk if transcript_cjk else 0.0
    )
    block_sizes = [
        int(size)
        for metrics in layer_metrics.values()
        for size in metrics["block_cjk_counts"]  # type: ignore[index]
    ]
    collapsed_detail_count = int(
        sum(
            int(metrics["collapsed_detail_count"])
            for metrics in layer_metrics.values()
        )
    )
    visible_quote_cjk = sum(
        int(metrics["quote_cjk_count"])
        for metrics in layer_metrics.values()
    )

    claim_sets: dict[str, set[str]] = {}
    violations: list[str] = []
    claim_id_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    expected_layer = {
        "topic-brief": "creator",
        "evidence-delta": "external",
        "decision-brief": "agent",
    }
    any_claim_components = any(
        parser.claim_ids[class_name]
        for parser in layer_claim_parsers.values()
        for class_name in _CLAIM_COMPONENT_CLASSES
    )
    claim_brief_sizes: list[int] = []
    for class_name, layer_name in expected_layer.items():
        identifiers = layer_claim_parsers[layer_name].claim_ids[class_name]
        all_identifiers = [
            claim_id
            for parser in layer_claim_parsers.values()
            for claim_id in parser.claim_ids[class_name]
        ]
        if any(not claim_id_pattern.fullmatch(identifier) for identifier in all_identifiers):
            violations.append(f"{class_name} has a missing or invalid data-claim-id")
        if len(identifiers) != len(set(identifiers)):
            violations.append(f"{class_name} repeats a claim in the same layer")
        wrong_layer_count = len(all_identifiers) - len(identifiers)
        if wrong_layer_count:
            violations.append(f"{class_name} appears outside the {layer_name} layer")
        claim_brief_sizes.extend(
            _cjk_count(component_text)
            for _, component_text in layer_claim_parsers[layer_name].component_texts[
                class_name
            ]
        )
        claim_sets[class_name] = set(identifiers)
    if any_claim_components and len({frozenset(values) for values in claim_sets.values()}) != 1:
        violations.append("topic/evidence/decision claim IDs do not map one-to-one")

    long_report = transcript_cjk >= 2500 and normalized_topic_count >= 1
    if long_report:
        if not all(claim_sets.values()):
            violations.append(
                "long reports need topic-brief, evidence-delta, and decision-brief claim mapping"
            )
        if len(claim_sets["topic-brief"]) != normalized_topic_count:
            violations.append(
                "claim mapping count must match the recorded research topic count"
            )
        if claim_parser.report_detail_count < max(
            1, len(claim_sets["topic-brief"]) * 2
        ):
            violations.append("long reports need same-page report-detail disclosures")
        if claim_parser.open_report_detail_count:
            violations.append("report-detail disclosures must be closed by default")
        if claim_parser.summary_dashboard_count != 1:
            violations.append("long reports need a default-visible summary-dashboard")
        if claim_brief_sizes and max(claim_brief_sizes) > 260:
            violations.append("a default-visible claim brief exceeds 260 CJK characters")
        if creator_ratio > 0.42:
            violations.append(
                f"creator layer default-visible compression ratio is {creator_ratio:.1%}, above 42%"
            )
        if visible_main > visible_limit:
            violations.append(
                f"default-visible main content has {visible_main} CJK characters, above {visible_limit}"
            )

    if block_sizes and max(block_sizes) > 320:
        violations.append("a default-visible paragraph exceeds 320 CJK characters")

    normalized_by_layer = {
        name: {
            block
            for block in metrics["normalized_blocks"]  # type: ignore[index]
            if len(block) >= 60
        }
        for name, metrics in layer_metrics.items()
    }
    duplicate_blocks = set()
    layer_names = tuple(normalized_by_layer)
    for index, name in enumerate(layer_names):
        for other_name in layer_names[index + 1 :]:
            duplicate_blocks.update(
                normalized_by_layer[name] & normalized_by_layer[other_name]
            )
    if duplicate_blocks:
        violations.append("a long paragraph is duplicated across report layers")

    if violations:
        raise ValueError(
            "Report readability validation did not pass: " + "; ".join(violations)
        )

    return {
        "default_visible_creator_cjk_count": visible_counts["creator"],
        "default_visible_external_cjk_count": visible_counts["external"],
        "default_visible_agent_cjk_count": visible_counts["agent"],
        "default_visible_main_cjk_count": visible_main,
        "default_visible_cjk_limit": visible_limit,
        "creator_visible_compression_ratio": round(creator_ratio, 4),
        "collapsed_report_detail_count": collapsed_detail_count,
        "report_detail_count": claim_parser.report_detail_count,
        "open_report_detail_count": claim_parser.open_report_detail_count,
        "claim_map_count": len(claim_sets["topic-brief"]),
        "max_claim_brief_cjk_count": max(claim_brief_sizes, default=0),
        "visible_quote_cjk_count": visible_quote_cjk,
        "long_visible_block_count": sum(size > 180 for size in block_sizes),
        "max_visible_block_cjk_count": max(block_sizes, default=0),
        "cross_layer_duplicate_block_count": len(duplicate_blocks),
    }


def _add_reading_paths(body_html: str) -> str:
    if 'class="reading-paths"' in body_html:
        return body_html
    report_modes = (
        (
            (
                ("第一部分｜视频 / 作者内容", "creator-content", "视频内容"),
                ("第二部分｜外部证据研判", "external-evidence", "外部证据"),
                ("第三部分｜Agent 综合判断", "agent-judgment", "直接看 Agent 判断"),
            ),
            "video",
        ),
        (
            (
                ("第一部分｜素材内容整理", "material-content", "素材内容"),
                ("第二部分｜跨素材分析与主题归纳", "material-analysis", "跨素材分析"),
                (
                    "第三部分｜Agent 综合判断与待核实事项",
                    "material-judgment",
                    "直接看 Agent 判断",
                ),
            ),
            "material",
        ),
    )
    for items, mode in report_modes:
        updated = body_html
        links: list[str] = []
        for heading, anchor, label in items:
            pattern = re.compile(
                rf'<h2\s+id="[^"]*">{re.escape(heading)}</h2>'
            )
            updated, count = pattern.subn(
                f'<h2 id="{anchor}">{heading}</h2>', updated, count=1
            )
            if not count:
                break
            links.append(f'<a href="#{anchor}">{label}</a>')
        else:
            nav = (
                f'<nav class="reading-paths {mode}" aria-label="阅读路径">'
                '<strong>阅读路径</strong>'
                + "".join(links)
                + '<span>详细依据可在各主题内展开</span></nav>\n'
            )
            return nav + updated
    return body_html


def render_markdown_report(markdown_path: Path, template_path: Path, output_path: Path) -> None:
    metadata, body = parse_front_matter(markdown_path.read_text(encoding="utf-8"))
    title = metadata.get("title")
    if not title:
        first = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), "")
        title = first or markdown_path.stem
    body_html = _markdown_fragment(_drop_first_heading(body))
    _validate_safe_report_fragment(body_html)
    body_html = _add_reading_paths(body_html)
    _validate_safe_report_fragment(body_html)
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
    if not re.search(
        r'<meta\s+[^>]*http-equiv=["\']Content-Security-Policy["\']',
        template,
        flags=re.IGNORECASE,
    ):
        if "<head>" not in template:
            raise ValueError("HTML template must contain a head element")
        csp = (
            '<meta http-equiv="Content-Security-Policy" '
            'content="default-src \'none\'; style-src \'unsafe-inline\'; '
            "img-src 'self' https: data:; font-src 'self' data:; "
            "script-src 'none'; connect-src 'none'; frame-src 'none'; "
            "object-src 'none'; base-uri 'none'; form-action 'none'\">"
        )
        template = template.replace("<head>", "<head>\n  " + csp, 1)
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
    .reading-paths { position: sticky; top: .55rem; z-index: 10; display: flex; flex-wrap: wrap; align-items: center; gap: .45rem; margin: 0 0 1.5rem; padding: .65rem .75rem; border: 1px solid #d6deee; border-radius: 12px; background: rgba(255, 255, 255, .96); box-shadow: 0 8px 24px rgba(16, 24, 40, .08); backdrop-filter: blur(10px); }
    .reading-paths strong { margin-right: .15rem; color: #344054; font-size: .8rem; }
    .reading-paths a { padding: .32rem .58rem; border-radius: 999px; background: #eef3ff; color: #2344a1; font-size: .78rem; font-weight: 750; text-decoration: none; }
    .reading-paths a:hover { background: #dfe8ff; }
    .reading-paths span { margin-left: auto; color: var(--muted); font-size: .75rem; }
    h2 { scroll-margin-top: 5.2rem; }
    .topic-brief, .evidence-delta, .decision-brief { margin: .85rem 0 1rem; padding: 1rem 1.05rem; border: 1px solid #dce3ef; border-radius: 14px; background: #fbfcfe; }
    .topic-brief { border-left: 5px solid #6b83df; }
    .evidence-delta { border-left: 5px solid #d8a33a; background: #fffdf8; }
    .decision-brief { border-left: 5px solid #55a47f; background: #f8fcfa; }
    .topic-brief > :first-child, .evidence-delta > :first-child, .decision-brief > :first-child { margin-top: 0; }
    .topic-brief > :last-child, .evidence-delta > :last-child, .decision-brief > :last-child { margin-bottom: 0; }
    .topic-brief p, .evidence-delta p, .decision-brief p { margin: .38rem 0; }
    .topic-brief ul, .evidence-delta ul, .decision-brief ul { margin: .55rem 0 0; padding-left: 1.15rem; }
    details.report-detail { margin: .65rem 0 1.3rem; padding: .75rem .9rem; border: 1px solid #e0e5ed; border-radius: 11px; background: #fcfcfd; }
    details.report-detail summary { cursor: pointer; color: #475467; font-size: .84rem; font-weight: 750; }
    details.report-detail[open] summary { margin-bottom: .8rem; color: #344054; }
    details.report-detail > :last-child { margin-bottom: 0; }
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
      .reading-paths { top: 0; margin-right: -8px; margin-left: -8px; border-radius: 0 0 12px 12px; overflow-x: auto; flex-wrap: nowrap; }
      .reading-paths strong, .reading-paths a { flex: 0 0 auto; }
      .reading-paths span { display: none; }
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
      .reading-paths { display: none; }
      details.report-detail > :not(summary) { display: block !important; }
      details.report-detail summary { display: none; }
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
    *,
    source_artifact_hashes: dict[str, str] | None = None,
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

    def add_citation(
        source: dict[str, object],
        *,
        topic_id: str,
        source_layer: str,
    ) -> None:
        url = str(source.get("url") or "")
        if not url:
            raise ValueError(f"Source in {topic_id} has no URL")
        if url not in citation_by_url:
            citation_by_url[url] = {
                **source,
                "source_ids": [source.get("source_id")],
                "research_topics": [topic_id],
                "source_layers": [source_layer],
            }
            return
        current = citation_by_url[url]
        current["source_ids"] = sorted(
            {
                str(item)
                for item in current.get("source_ids", [])  # type: ignore[arg-type]
                + [source.get("source_id")]  # type: ignore[operator]
                if item
            }
        )
        current["research_topics"] = sorted(
            set(current.get("research_topics", []) + [topic_id])  # type: ignore[operator]
        )
        current["source_layers"] = sorted(
            set(current.get("source_layers", []) + [source_layer])  # type: ignore[operator]
        )
        current["claims_supported"] = list(
            dict.fromkeys(
                current.get("claims_supported", [])  # type: ignore[arg-type]
                + source.get("claims_supported", [])  # type: ignore[operator]
            )
        )

    for topic in research:
        for source in topic["sources"]:
            add_citation(
                source,
                topic_id=str(topic["topic_id"]),
                source_layer="external_research",
            )
    for source in agent_judgment.get("sources", []):
        add_citation(
            source,
            topic_id="agent-judgment",
            source_layer="agent_judgment",
        )
    for topic in judgment_topics:
        for source in topic.get("sources", []):
            add_citation(
                source,
                topic_id=str(topic["topic_id"]),
                source_layer="agent_judgment",
            )
        for url in topic.get("source_urls", []):
            if str(url) not in citation_by_url:
                raise ValueError(
                    f"Agent judgment source URL lacks citation metadata: {url}"
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
        "source_artifact_hashes": dict(sorted((source_artifact_hashes or {}).items())),
    }
    report_data_path.parent.mkdir(parents=True, exist_ok=True)
    citations_path.parent.mkdir(parents=True, exist_ok=True)

    def write_json_atomic(path: Path, value: object) -> None:
        content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)

    write_json_atomic(report_data_path, report_data)
    write_json_atomic(
        citations_path,
        {
            "schema_version": 1,
            "video_id": analysis["video_id"],
            "generated_at": generated_at,
            "citations": citations,
        },
    )
    return {
        "opinion_count": len(opinions),
        "topic_count": len(research),
        "citation_count": len(citations),
        "agent_judgment_topic_count": len(judgment_topics),
    }
