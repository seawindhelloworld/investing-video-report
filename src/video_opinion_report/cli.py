from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .content_selection import materialize_content_selection
from .ingestion import import_transcript_package
from .models import Stage
from .reporting import (
    build_structured_artifacts,
    render_markdown_report,
    validate_rendered_report,
    validate_report_layers,
    validate_report_readability,
)
from .integrity import sha256_file
from .store import ManifestStore, ProcessedReportStore
from .transcript import read_jsonl, validate_segments


def project_root(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not (path / "pyproject.toml").exists():
        raise argparse.ArgumentTypeError(f"Not a project root: {path}")
    return path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="video-opinion-report")
    result.add_argument("--project-root", type=project_root, default=Path.cwd().resolve())
    commands = result.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser(
        "import-transcript",
        help="Create a report run from a validated transcript package",
    )
    ingest.add_argument("--package", required=True, type=Path)

    render = commands.add_parser("render-html", help="Render a Markdown report with the project HTML template")
    render.add_argument("--video-id", required=True)
    render.add_argument("--markdown", required=True, type=Path)
    render.add_argument("--template", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)
    render.add_argument("--force", action="store_true", help="Re-render and invalidate HTML validation")

    structured = commands.add_parser(
        "build-structured",
        help="Join creator analysis, external research, Agent judgment, and review artifacts",
    )
    structured.add_argument("--video-id", required=True)
    structured.add_argument("--video-analysis", required=True, type=Path)
    structured.add_argument("--opinions", required=True, type=Path)
    structured.add_argument("--research-dir", required=True, type=Path)
    structured.add_argument("--agent-judgment", required=True, type=Path)
    structured.add_argument("--fidelity-review", required=True, type=Path)
    structured.add_argument("--report-data", required=True, type=Path)
    structured.add_argument("--citations", required=True, type=Path)

    analysis = commands.add_parser("record-analysis", help="Validate and record semantic analysis artifacts")
    analysis.add_argument("--video-id", required=True)
    analysis.add_argument("--video-analysis", required=True, type=Path)
    analysis.add_argument("--opinions", required=True, type=Path)

    research = commands.add_parser("record-research", help="Validate and record research artifacts")
    research.add_argument("--video-id", required=True)
    research.add_argument("--research-dir", required=True, type=Path)

    judgment = commands.add_parser(
        "record-judgment",
        help="Validate and record the source-backed Agent judgment artifact",
    )
    judgment.add_argument("--video-id", required=True)
    judgment.add_argument("--judgment", required=True, type=Path)
    judgment.add_argument(
        "--force",
        action="store_true",
        help="Replace a completed judgment and reopen render/HTML validation",
    )

    draft = commands.add_parser("record-draft", help="Record the transcript-grounded report draft")
    draft.add_argument("--video-id", required=True)
    draft.add_argument("--markdown", required=True, type=Path)
    draft.add_argument("--force", action="store_true", help="Replace a completed draft and reopen downstream gates")

    fidelity = commands.add_parser("record-fidelity-review", help="Validate and record fidelity review")
    fidelity.add_argument("--video-id", required=True)
    fidelity.add_argument("--review", required=True, type=Path)
    fidelity.add_argument("--force", action="store_true", help="Replace a completed review and reopen downstream gates")

    html_validation = commands.add_parser("validate-html", help="Record a passed HTML validation result")
    html_validation.add_argument("--video-id", required=True)
    html_validation.add_argument("--validation", required=True, type=Path)

    complete = commands.add_parser("complete-run", help="Mark a fully validated run as processed")
    complete.add_argument("--video-id", required=True)

    status = commands.add_parser("status", help="Print the current run manifest")
    status.add_argument("--video-id", required=True)
    return result


def _artifact_path(root: Path, manifest: object, key: str) -> Path:
    return ManifestStore(root).artifact_path(manifest, key)  # type: ignore[arg-type]


def _completed_with_artifacts(root: Path, manifest: object, stage: Stage, *keys: str) -> bool:
    if not getattr(manifest, "is_complete")(stage):
        return False
    for key in keys:
        _artifact_path(root, manifest, key)
    return True



def _load_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _text_list(value: object, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise ValueError(f"{label} must be {qualifier}")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} must contain only non-empty strings")
    return [item.strip() for item in value]


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _iso_date(value: object, label: str) -> str:
    text = _text(value, label)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date or datetime") from exc
    return text


def _http_url(value: object, label: str) -> str:
    text = _text(value, label)
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be a direct HTTP(S) URL")
    return text


def _normalized_quote(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _report_date(manifest: object) -> str:
    raw = str(getattr(manifest, "metadata").get("published_at") or "").strip()
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise ValueError("Package published_at must identify a report date") from exc


def _expected_report_file(
    root: Path,
    manifest: object,
    filename: str,
) -> Path:
    video_id = str(getattr(manifest, "video_id"))
    return (
        root.resolve()
        / "reports"
        / f"{_report_date(manifest)}-{video_id}"
        / filename
    ).resolve()


def _require_expected_report_file(
    root: Path,
    manifest: object,
    path: Path,
    filename: str,
) -> Path:
    resolved = path.expanduser().resolve()
    expected = _expected_report_file(root, manifest, filename)
    if resolved != expected:
        raise ValueError(f"{filename} must be written to {expected}")
    return resolved



def record_analysis(root: Path, video_id: str, analysis_path: Path, opinions_path: Path) -> None:
    store = ManifestStore(root)
    manifest = store.load(video_id)
    stage = Stage.ANALYZE
    _artifact_path(root, manifest, "transcript_corrected_jsonl")
    analysis_file = analysis_path.expanduser().resolve()
    opinions_file = opinions_path.expanduser().resolve()
    already_complete = manifest.is_complete(stage)
    if already_complete and {
        "video_analysis",
        "opinions",
        "content_selection",
        "transcript_report_jsonl",
    } <= set(manifest.artifacts):
        _artifact_path(root, manifest, "video_analysis")
        _artifact_path(root, manifest, "opinions")
        _artifact_path(root, manifest, "content_selection")
        _artifact_path(root, manifest, "transcript_report_jsonl")
        return
    if not already_complete:
        manifest.start(stage)
        store.save(manifest)
    try:
        analysis = _load_object(analysis_file)
        if analysis.get("schema_version") != 1:
            raise ValueError("video-analysis must use schema_version 1")
        if str(analysis.get("video_id")) != video_id:
            raise ValueError("video-analysis video_id does not match the run")
        if "targeted_corrections" in analysis:
            raise ValueError(
                "video-analysis must not contain a separate subtitle-correction stage"
            )
        for field in ("title", "creator", "source_url", "published_at", "summary"):
            _text(analysis.get(field), f"video-analysis {field}")
        _http_url(analysis.get("source_url"), "video-analysis source_url")
        if str(analysis.get("source_url")) != manifest.source_url:
            raise ValueError("video-analysis source_url does not match the run")
        duration = _finite_number(
            analysis.get("duration_seconds"), "video-analysis duration_seconds"
        )
        if duration <= 0:
            raise ValueError("video-analysis duration_seconds must be positive")
        for field in ("sections", "topic_clusters", "transcript_risks"):
            if not isinstance(analysis.get(field), list):
                raise ValueError(f"video-analysis {field} must be a list")
        for field in ("excluded_ranges", "non_reportable_ranges"):
            if not isinstance(analysis.get(field), list):
                raise ValueError(f"video-analysis {field} must be a list")
        for field in ("title", "creator", "source_url", "published_at"):
            expected = manifest.metadata.get(field)
            if expected is not None and str(analysis.get(field)) != str(expected):
                raise ValueError(f"video-analysis {field} does not match the package")
        expected_duration = manifest.metadata.get("duration_seconds")
        if isinstance(expected_duration, (int, float)) and abs(
            duration - float(expected_duration)
        ) > 0.1:
            raise ValueError("video-analysis duration does not match the package")
        transcript_path = _artifact_path(root, manifest, "transcript_corrected_jsonl")
        transcript = read_jsonl(transcript_path)
        transcript_errors = validate_segments(transcript, allow_blank_text=True)
        if transcript_errors:
            raise ValueError(f"Corrected transcript is invalid: {transcript_errors[0]}")
        transcript_positions = {
            segment.segment_id: index for index, segment in enumerate(transcript)
        }
        for section in analysis["sections"]:  # type: ignore[index]
            if not isinstance(section, dict):
                raise ValueError("video-analysis sections must contain objects")
            for field in ("section_id", "title", "segment_start", "segment_end"):
                _text(section.get(field), f"video-analysis section {field}")
            start_id = str(section["segment_start"])
            end_id = str(section["segment_end"])
            if (
                start_id not in transcript_positions
                or end_id not in transcript_positions
                or transcript_positions[end_id] < transcript_positions[start_id]
            ):
                raise ValueError(
                    f"video-analysis section has an invalid transcript range: {start_id}–{end_id}"
                )
        selection_path = store.run_dir(video_id) / "content-selection.json"
        filtered_transcript_path = (
            store.run_dir(video_id) / "transcript" / "transcript.report.jsonl"
        )
        selection = materialize_content_selection(
            video_id=video_id,
            transcript_path=transcript_path,
            excluded_ranges=analysis.get("excluded_ranges"),
            non_reportable_ranges=analysis.get("non_reportable_ranges"),
            selection_path=selection_path,
            filtered_transcript_path=filtered_transcript_path,
            source_artifact="transcript.corrected.jsonl",
        )
        opinions: list[dict[str, object]] = []
        for line_number, line in enumerate(
            opinions_file.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid opinions.jsonl line {line_number}: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"Opinion line {line_number} must be an object")
            opinions.append(item)
        if not opinions:
            raise ValueError("opinions.jsonl is empty")
        identifiers = [str(item.get("opinion_id") or "") for item in opinions]
        if any(not value for value in identifiers) or len(set(identifiers)) != len(identifiers):
            raise ValueError("Opinion IDs must be present and unique")
        for item in opinions:
            opinion_id = str(item["opinion_id"])
            start = _finite_number(
                item.get("timestamp_start"), f"Opinion {opinion_id} timestamp_start"
            )
            end = _finite_number(
                item.get("timestamp_end"), f"Opinion {opinion_id} timestamp_end"
            )
            if start < transcript[0].start or end <= start or end > transcript[-1].end:
                raise ValueError(f"Opinion timestamp is outside the transcript: {opinion_id}")
            exact_quote = _text(item.get("exact_quote"), f"Opinion {opinion_id} exact_quote")
            for field in (
                "faithful_paraphrase",
                "opinion_type",
                "target",
                "time_horizon",
            ):
                _text(item.get(field), f"Opinion {opinion_id} {field}")
            _text_list(
                item.get("stated_basis"),
                f"Opinion {opinion_id} stated_basis",
                allow_empty=True,
            )
            _text_list(
                item.get("qualifiers"),
                f"Opinion {opinion_id} qualifiers",
                allow_empty=True,
            )
            for field in ("context_before", "context_after"):
                if not isinstance(item.get(field), str):
                    raise ValueError(f"Opinion {opinion_id} {field} must be a string")
            if item.get("research_status") != "pending":
                raise ValueError(f"Opinion {opinion_id} research_status must be pending")
            if any(
                start < float(exclusion["timestamp_end"])
                and end > float(exclusion["timestamp_start"])
                for exclusion in selection["excluded_ranges"]
            ):
                raise ValueError(
                    f"Opinion overlaps excluded transcript content: "
                    f"{item.get('opinion_id')}"
                )
            if any(
                start < float(item_range["timestamp_end"])
                and end > float(item_range["timestamp_start"])
                for item_range in selection["non_reportable_ranges"]
            ):
                raise ValueError(
                    "Opinion overlaps retained advertising or promotional content: "
                    f"{item.get('opinion_id')}"
                )
            overlapping = [
                segment
                for segment in transcript
                if start < segment.end and end > segment.start
            ]
            if not overlapping:
                raise ValueError(f"Opinion has no supporting transcript segment: {opinion_id}")
            source_text = "".join(segment.text for segment in overlapping)
            if _normalized_quote(exact_quote) not in _normalized_quote(source_text):
                raise ValueError(
                    f"Opinion exact_quote is not present in its transcript range: {opinion_id}"
                )
            segment_start = item.get("segment_start")
            segment_end = item.get("segment_end")
            if segment_start is not None or segment_end is not None:
                start_id = str(segment_start or "")
                end_id = str(segment_end or "")
                if (
                    start_id not in transcript_positions
                    or end_id not in transcript_positions
                    or transcript_positions[end_id] < transcript_positions[start_id]
                    or transcript[transcript_positions[start_id]].start > start + 0.1
                    or transcript[transcript_positions[end_id]].end < end - 0.1
                ):
                    raise ValueError(
                        f"Opinion segment range does not cover its timestamps: {opinion_id}"
                    )
        store.set_artifact(manifest, "video_analysis", analysis_file)
        store.set_artifact(manifest, "opinions", opinions_file)
        store.set_artifact(manifest, "content_selection", selection_path)
        store.set_artifact(manifest, "transcript_report_jsonl", filtered_transcript_path)
        manifest.metadata["opinion_count"] = len(opinions)
        manifest.metadata["transcript_included_segment_count"] = selection[
            "included_segment_count"
        ]
        manifest.metadata["transcript_excluded_segment_count"] = selection[
            "excluded_segment_count"
        ]
        manifest.metadata["transcript_excluded_category_counts"] = selection[
            "excluded_category_counts"
        ]
        manifest.metadata["transcript_non_reportable_range_count"] = len(
            selection["non_reportable_ranges"]
        )
        if not already_complete:
            manifest.complete(stage)
        store.save(manifest)
    except Exception as exc:
        if not already_complete:
            manifest.fail(stage, str(exc), retryable=False)
            store.save(manifest)
        raise


def record_research(root: Path, video_id: str, research_dir: Path) -> None:
    store = ManifestStore(root)
    manifest = store.load(video_id)
    stage = Stage.RESEARCH
    _artifact_path(root, manifest, "content_selection")
    _artifact_path(root, manifest, "transcript_report_jsonl")
    directory = research_dir.expanduser().resolve()
    already_complete = manifest.is_complete(stage)
    if already_complete and "research_dir" in manifest.artifacts:
        _artifact_path(root, manifest, "research_dir")
        return
    if not already_complete:
        manifest.start(stage)
        store.save(manifest)
    try:
        opinion_lines = _artifact_path(root, manifest, "opinions").read_text(
            encoding="utf-8"
        ).splitlines()
        expected = {
            str(json.loads(line)["opinion_id"])
            for line in opinion_lines
            if line.strip()
        }
        files = sorted(directory.glob("*.json"))
        if not files:
            raise ValueError("Research directory has no JSON artifacts")
        covered: set[str] = set()
        topic_identifiers: set[str] = set()
        source_identifiers: set[str] = set()
        source_count = 0
        allowed_statuses = {
            "supported",
            "partially_supported",
            "mixed",
            "not_supported",
            "insufficient",
            "conditional",
        }
        for path in files:
            topic = _load_object(path)
            if topic.get("schema_version") != 1:
                raise ValueError(f"Research topic must use schema_version 1: {path}")
            if str(topic.get("video_id") or "") != video_id:
                raise ValueError(f"Research topic video_id does not match the run: {path}")
            topic_id = _text(topic.get("topic_id"), f"Research topic_id in {path}")
            if topic_id in topic_identifiers:
                raise ValueError(f"Duplicate research topic_id: {topic_id}")
            topic_identifiers.add(topic_id)
            for field in ("theme", "topic_summary"):
                _text(topic.get(field), f"Research topic {topic_id} {field}")
            _iso_date(topic.get("researched_at"), f"Research topic {topic_id} researched_at")
            disclaimer = _text(
                topic.get("disclaimer"), f"Research topic {topic_id} disclaimer"
            )
            if "不代表视频作者观点" not in disclaimer:
                raise ValueError(
                    f"Research topic {topic_id} disclaimer does not separate authorship"
                )
            assessments = topic.get("assessments")
            sources = topic.get("sources")
            if not isinstance(assessments, list) or not isinstance(sources, list) or not sources:
                raise ValueError(f"Research topic needs assessments and sources: {path}")
            if not assessments:
                raise ValueError(f"Research topic has no assessments: {path}")
            for assessment in assessments:
                if not isinstance(assessment, dict):
                    raise ValueError(f"Invalid assessment in {path}")
                opinion_id = _text(
                    assessment.get("opinion_id"), f"Assessment opinion_id in {path}"
                )
                if opinion_id in covered:
                    raise ValueError(f"Opinion assessed more than once: {opinion_id}")
                status = _text(
                    assessment.get("status"), f"Assessment {opinion_id} status"
                )
                if status not in allowed_statuses:
                    raise ValueError(f"Assessment {opinion_id} has unsupported status: {status}")
                for field in ("conclusion", "time_horizon", "priced_in"):
                    _text(
                        assessment.get(field), f"Assessment {opinion_id} {field}"
                    )
                for field in (
                    "supporting_evidence",
                    "counterevidence",
                    "applicable_conditions",
                    "uncertainties",
                ):
                    _text_list(
                        assessment.get(field), f"Assessment {opinion_id} {field}"
                    )
                covered.add(opinion_id)
            for source in sources:
                if not isinstance(source, dict):
                    raise ValueError(f"Invalid research source: {path}")
                source_id = _text(
                    source.get("source_id"), f"Research source_id in {path}"
                )
                if source_id in source_identifiers:
                    raise ValueError(f"Duplicate research source_id: {source_id}")
                source_identifiers.add(source_id)
                for field in (
                    "title",
                    "publisher",
                    "author",
                    "evidence_summary",
                    "scope",
                ):
                    _text(source.get(field), f"Research source {source_id} {field}")
                _iso_date(
                    source.get("published_at"),
                    f"Research source {source_id} published_at",
                )
                _iso_date(
                    source.get("accessed_at"),
                    f"Research source {source_id} accessed_at",
                )
                _http_url(source.get("url"), f"Research source {source_id} url")
            source_count += len(sources)
        if covered != expected:
            missing = sorted(expected - covered)
            extra = sorted(covered - expected)
            raise ValueError(f"Research opinion coverage mismatch; missing={missing}, extra={extra}")
        store.set_artifact(manifest, "research_dir", directory)
        manifest.metadata["topic_count"] = len(files)
        manifest.metadata["research_source_count"] = source_count
        if not already_complete:
            manifest.complete(stage)
        store.save(manifest)
    except Exception as exc:
        if not already_complete:
            manifest.fail(stage, str(exc), retryable=False)
            store.save(manifest)
        raise


def record_agent_judgment(
    root: Path,
    video_id: str,
    judgment_path: Path,
    *,
    force: bool = False,
) -> None:
    store = ManifestStore(root)
    manifest = store.load(video_id)
    stage = Stage.JUDGMENT
    judgment_file = judgment_path.expanduser().resolve()
    already_complete = manifest.is_complete(stage)
    if already_complete and not force and "agent_judgment" in manifest.artifacts:
        _artifact_path(root, manifest, "agent_judgment")
        return
    if force:
        manifest.restart(stage)
        already_complete = False
        store.save(manifest)
    elif not already_complete:
        manifest.start(stage)
        store.save(manifest)
    try:
        judgment = _load_object(judgment_file)
        if judgment.get("schema_version") != 1:
            raise ValueError("Agent judgment must use schema_version 1")
        if str(judgment.get("video_id") or "") != video_id:
            raise ValueError("Agent judgment video_id does not match the run")
        disclaimer = str(judgment.get("disclaimer") or "")
        if "不代表视频作者观点" not in disclaimer or "不构成投资建议" not in disclaimer:
            raise ValueError("Agent judgment disclaimer must separate authorship and investment advice")
        _iso_date(judgment.get("source_as_of"), "Agent judgment source_as_of")
        if not str(judgment.get("cross_topic_summary") or "").strip():
            raise ValueError("Agent judgment needs cross_topic_summary")
        topics = judgment.get("topics")
        if not isinstance(topics, list) or not topics:
            raise ValueError("Agent judgment needs at least one topic judgment")
        identifiers: set[str] = set()
        allowed_postures = {
            "watchlist",
            "wait_for_proof",
            "re_underwrite",
            "avoid_for_now",
            "research_only",
        }
        research_directory = _artifact_path(root, manifest, "research_dir")
        research_topic_ids: set[str] = set()
        known_source_urls: set[str] = set()
        known_source_ids: set[str] = set()
        for path in sorted(research_directory.glob("*.json")):
            research_topic = _load_object(path)
            research_topic_ids.add(str(research_topic.get("topic_id") or ""))
            for source in research_topic.get("sources") or []:
                if isinstance(source, dict):
                    known_source_urls.add(str(source.get("url") or ""))
                    known_source_ids.add(str(source.get("source_id") or ""))

        judgment_sources: list[object] = []
        root_sources = judgment.get("sources", [])
        if not isinstance(root_sources, list):
            raise ValueError("Agent judgment sources must be a list")
        judgment_sources.extend(root_sources)
        for topic in topics:
            if isinstance(topic, dict):
                topic_sources = topic.get("sources", [])
                if not isinstance(topic_sources, list):
                    raise ValueError("Agent judgment topic sources must be a list")
                judgment_sources.extend(topic_sources)
        judgment_source_ids: set[str] = set()
        for source in judgment_sources:
            if not isinstance(source, dict):
                raise ValueError("Agent judgment sources must contain objects")
            source_id = _text(source.get("source_id"), "Agent judgment source_id")
            if source_id in judgment_source_ids or source_id in known_source_ids:
                raise ValueError(f"Duplicate Agent judgment source_id: {source_id}")
            judgment_source_ids.add(source_id)
            for field in (
                "title",
                "publisher",
                "author",
                "evidence_summary",
                "scope",
            ):
                _text(source.get(field), f"Agent judgment source {source_id} {field}")
            _iso_date(
                source.get("published_at"),
                f"Agent judgment source {source_id} published_at",
            )
            _iso_date(
                source.get("accessed_at"),
                f"Agent judgment source {source_id} accessed_at",
            )
            known_source_urls.add(
                _http_url(source.get("url"), f"Agent judgment source {source_id} url")
            )
        for topic in topics:
            if not isinstance(topic, dict):
                raise ValueError("Invalid Agent judgment topic")
            topic_id = str(topic.get("topic_id") or "")
            if not topic_id or topic_id in identifiers:
                raise ValueError("Agent judgment topic IDs must be present and unique")
            identifiers.add(topic_id)
            missing = [
                key
                for key in (
                    "theme",
                    "conclusion",
                    "evidence_layers",
                    "confidence",
                    "time_horizon",
                    "priced_in",
                    "what_must_be_true",
                    "disconfirmers",
                    "downside_mechanism",
                    "action_posture",
                    "missing_evidence",
                    "next_verification",
                    "source_urls",
                )
                if not topic.get(key)
            ]
            if missing:
                raise ValueError(f"Agent judgment topic {topic_id} lacks {', '.join(missing)}")
            if str(topic["action_posture"]) not in allowed_postures:
                raise ValueError(f"Unsupported Agent judgment posture: {topic['action_posture']}")
            for field in ("what_must_be_true", "disconfirmers", "source_urls"):
                _text_list(topic[field], f"Agent judgment topic {topic_id} {field}")
            if any(not re.search(r"\d", item) for item in topic["disconfirmers"]):
                raise ValueError(
                    f"Agent judgment topic {topic_id} disconfirmers must be quantified"
                )
            for url in topic["source_urls"]:
                normalized_url = _http_url(
                    url, f"Agent judgment topic {topic_id} source URL"
                )
                if normalized_url not in known_source_urls:
                    raise ValueError(
                        f"Agent judgment topic {topic_id} source URL lacks source metadata"
                    )
            evidence_layers = topic["evidence_layers"]
            if not isinstance(evidence_layers, dict):
                raise ValueError(f"Agent judgment topic {topic_id} has invalid evidence_layers")
            facts = evidence_layers.get("facts")
            management_claims = evidence_layers.get("management_claims")
            if not isinstance(facts, list) or not facts:
                raise ValueError(f"Agent judgment topic {topic_id} needs non-empty facts")
            if not isinstance(management_claims, list):
                raise ValueError(
                    f"Agent judgment topic {topic_id} needs a management_claims list"
                )
            if any(
                not str(evidence_layers.get(key) or "").strip()
                for key in ("inference", "agent_judgment")
            ):
                raise ValueError(
                    f"Agent judgment topic {topic_id} needs inference and agent_judgment layers"
                )
            mechanism = topic["downside_mechanism"]
            if not isinstance(mechanism, dict) or any(
                not str(mechanism.get(key) or "").strip()
                for key in ("shock", "transmission", "constraint", "outcome")
            ):
                raise ValueError(
                    f"Agent judgment topic {topic_id} needs shock/transmission/constraint/outcome"
                )
        if identifiers != research_topic_ids:
            raise ValueError(
                "Agent judgment topic coverage does not match research topics; "
                f"missing={sorted(research_topic_ids - identifiers)}, "
                f"extra={sorted(identifiers - research_topic_ids)}"
            )
        store.set_artifact(manifest, "agent_judgment", judgment_file)
        manifest.metadata["agent_judgment_topic_count"] = len(topics)
        manifest.metadata["agent_judgment_source_as_of"] = judgment["source_as_of"]
        if not already_complete:
            manifest.complete(stage)
        store.save(manifest)
    except Exception as exc:
        if not already_complete:
            manifest.fail(stage, str(exc), retryable=False)
            store.save(manifest)
        raise


def record_draft(root: Path, video_id: str, markdown_path: Path, *, force: bool = False) -> None:
    store = ManifestStore(root)
    manifest = store.load(video_id)
    stage = Stage.DRAFT
    _artifact_path(root, manifest, "agent_judgment")
    markdown_file = _require_expected_report_file(
        root, manifest, markdown_path, "report.md"
    )
    already_complete = manifest.is_complete(stage)
    if already_complete and not force and "draft_markdown" in manifest.artifacts:
        _artifact_path(root, manifest, "draft_markdown")
        return
    if force:
        manifest.restart(stage)
        already_complete = False
        store.save(manifest)
    elif not already_complete:
        manifest.start(stage)
        store.save(manifest)
    try:
        text = markdown_file.read_text(encoding="utf-8")
        if len(text.strip()) < 100:
            raise ValueError("Draft Markdown is unexpectedly short")
        layer_counts = validate_report_layers(text)
        transcript_text = "".join(
            segment.text
            for segment in read_jsonl(
                _artifact_path(root, manifest, "transcript_report_jsonl")
            )
        )
        readability_counts = validate_report_readability(
            text,
            transcript_text=transcript_text,
            topic_count=int(manifest.metadata.get("topic_count") or 0),
        )
        store.set_artifact(manifest, "draft_markdown", markdown_file)
        manifest.metadata.update(layer_counts)
        manifest.metadata.update(readability_counts)
        if not already_complete:
            manifest.complete(stage)
        store.save(manifest)
    except Exception as exc:
        if not already_complete:
            manifest.fail(stage, str(exc), retryable=False)
            store.save(manifest)
        raise


def record_fidelity_review(root: Path, video_id: str, review_path: Path, *, force: bool = False) -> None:
    store = ManifestStore(root)
    manifest = store.load(video_id)
    _artifact_path(root, manifest, "transcript_report_jsonl")
    stage = Stage.FIDELITY_REVIEW
    review_file = review_path.expanduser().resolve()
    already_complete = manifest.is_complete(stage)
    if already_complete and not force and "fidelity_review" in manifest.artifacts:
        _artifact_path(root, manifest, "fidelity_review")
        return
    if force:
        manifest.restart(stage)
        already_complete = False
        store.save(manifest)
    elif not already_complete:
        manifest.start(stage)
        store.save(manifest)
    try:
        review = _load_object(review_file)
        if review.get("schema_version") != 1:
            raise ValueError("Fidelity review must use schema_version 1")
        if str(review.get("video_id") or "") != video_id:
            raise ValueError("Fidelity review video_id does not match the run")
        if review.get("external_research_visible_to_reviewer") is not False:
            raise ValueError("First fidelity review must not see external research")
        draft_path = _artifact_path(root, manifest, "draft_markdown")
        transcript_path = _artifact_path(root, manifest, "transcript_report_jsonl")
        if review.get("draft_sha256") != sha256_file(draft_path):
            raise ValueError("Fidelity review is not bound to the recorded draft")
        if review.get("transcript_sha256") != sha256_file(transcript_path):
            raise ValueError("Fidelity review is not bound to the report transcript")
        verdict = str(review.get("post_revision_verdict") or review.get("overall_verdict") or "")
        if verdict not in {"passed", "passed_with_asr_caveats"}:
            raise ValueError(f"Fidelity review did not pass: {verdict or '<missing>'}")
        unresolved = review.get("unresolved_transcript_checks")
        if unresolved is None:
            unresolved = review.get("unresolved_audio_checks") or []
        if not isinstance(unresolved, list):
            raise ValueError("unresolved_transcript_checks must be a list")
        for field in ("section_checks", "opinion_checks", "exclusion_checks"):
            if not isinstance(review.get(field), list):
                raise ValueError(f"Fidelity review {field} must be a list")
        expected_opinions = {
            str(json.loads(line)["opinion_id"])
            for line in _artifact_path(root, manifest, "opinions")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        }
        reviewed_opinions = {
            str(item.get("opinion_id") or "")
            for item in review["opinion_checks"]  # type: ignore[index]
            if isinstance(item, dict)
        }
        if reviewed_opinions != expected_opinions:
            raise ValueError(
                "Fidelity review opinion coverage does not match recorded opinions"
            )
        store.set_artifact(manifest, "fidelity_review", review_file)
        manifest.metadata["fidelity_review_verdict"] = verdict
        manifest.metadata["unresolved_transcript_check_count"] = len(unresolved)
        if not already_complete:
            manifest.complete(stage)
        store.save(manifest)
    except Exception as exc:
        if not already_complete:
            manifest.fail(stage, str(exc), retryable=False)
            store.save(manifest)
        raise


def render_html(
    root: Path,
    video_id: str,
    markdown_path: Path,
    template_path: Path,
    output_path: Path,
    *,
    force: bool = False,
) -> None:
    store = ManifestStore(root)
    manifest = store.load(video_id)
    stage = Stage.RENDER
    if not force and _completed_with_artifacts(root, manifest, stage, "report_markdown", "report_html"):
        return
    if force:
        manifest.restart(stage)
    else:
        manifest.start(stage)
    store.save(manifest)
    try:
        markdown_file = markdown_path.expanduser().resolve()
        template_file = template_path.expanduser().resolve()
        output_file = _require_expected_report_file(
            root, manifest, output_path, "index.html"
        )
        if not markdown_file.is_file():
            raise FileNotFoundError(markdown_file)
        if not template_file.is_file():
            raise FileNotFoundError(template_file)
        recorded_draft = _artifact_path(root, manifest, "draft_markdown")
        if markdown_file != recorded_draft:
            raise ValueError("render-html must render the recorded draft Markdown")
        expected_template = (root / "assets" / "report-template.html").resolve()
        if template_file != expected_template:
            raise ValueError(f"render-html must use the project template: {expected_template}")
        report_data = _artifact_path(root, manifest, "report_data")
        citations = _artifact_path(root, manifest, "citations")
        if report_data.parent != output_file.parent or citations.parent != output_file.parent:
            raise ValueError("Structured artifacts and HTML must share the report directory")
        markdown_text = markdown_file.read_text(encoding="utf-8")
        layer_counts = validate_report_layers(markdown_text)
        transcript_text = "".join(
            segment.text
            for segment in read_jsonl(
                _artifact_path(root, manifest, "transcript_report_jsonl")
            )
        )
        readability_counts = validate_report_readability(
            markdown_text,
            transcript_text=transcript_text,
            topic_count=int(manifest.metadata.get("topic_count") or 0),
        )
        render_markdown_report(markdown_file, template_file, output_file)
        manifest.metadata.update(validate_rendered_report(output_file, root))
        store.set_artifact(manifest, "report_markdown", markdown_file)
        store.set_artifact(manifest, "report_html", output_file)
        manifest.metadata.update(layer_counts)
        manifest.metadata.update(readability_counts)
        manifest.complete(stage)
        store.save(manifest)
    except Exception as exc:
        manifest.fail(
            stage,
            str(exc),
            retryable=not isinstance(exc, (FileNotFoundError, ValueError)),
        )
        store.save(manifest)
        raise


def build_structured(root: Path, arguments: argparse.Namespace) -> None:
    store = ManifestStore(root)
    manifest = store.load(arguments.video_id)
    manifest.require_completed(Stage.RESEARCH, Stage.JUDGMENT, Stage.FIDELITY_REVIEW)
    if manifest.is_complete(Stage.RENDER):
        raise RuntimeError("Structured artifacts cannot be rebuilt after rendering")
    registered_inputs = {
        "video_analysis": _artifact_path(root, manifest, "video_analysis"),
        "opinions": _artifact_path(root, manifest, "opinions"),
        "research_dir": _artifact_path(root, manifest, "research_dir"),
        "agent_judgment": _artifact_path(root, manifest, "agent_judgment"),
        "fidelity_review": _artifact_path(root, manifest, "fidelity_review"),
    }
    argument_inputs = {
        "video_analysis": arguments.video_analysis.expanduser().resolve(),
        "opinions": arguments.opinions.expanduser().resolve(),
        "research_dir": arguments.research_dir.expanduser().resolve(),
        "agent_judgment": arguments.agent_judgment.expanduser().resolve(),
        "fidelity_review": arguments.fidelity_review.expanduser().resolve(),
    }
    mismatched = [
        key for key in registered_inputs if registered_inputs[key] != argument_inputs[key]
    ]
    if mismatched:
        raise ValueError(
            "build-structured inputs must match recorded artifacts: "
            + ", ".join(mismatched)
        )
    report_data = _require_expected_report_file(
        root, manifest, arguments.report_data, "report-data.json"
    )
    citations = _require_expected_report_file(
        root, manifest, arguments.citations, "citations.json"
    )
    missing_hashes = [
        key for key in registered_inputs if key not in manifest.artifact_hashes
    ]
    if missing_hashes:
        raise RuntimeError(
            "Recorded artifacts predate integrity binding and must be re-recorded: "
            + ", ".join(missing_hashes)
        )
    source_hashes = {
        key: manifest.artifact_hashes[key] for key in registered_inputs
    }
    if {"report_data", "citations"} <= set(manifest.artifacts):
        if (
            _artifact_path(root, manifest, "report_data") == report_data
            and _artifact_path(root, manifest, "citations") == citations
        ):
            existing_report_data = _load_object(report_data)
            if existing_report_data.get("source_artifact_hashes") == source_hashes:
                return
        else:
            raise RuntimeError("Structured artifacts are already recorded at other paths")
    counts = build_structured_artifacts(
        registered_inputs["video_analysis"],
        registered_inputs["opinions"],
        registered_inputs["research_dir"],
        registered_inputs["agent_judgment"],
        registered_inputs["fidelity_review"],
        report_data,
        citations,
        source_artifact_hashes=source_hashes,
    )
    store.set_artifact(manifest, "report_data", report_data)
    store.set_artifact(manifest, "citations", citations)
    manifest.metadata.update(counts)
    store.save(manifest)


def validate_html(root: Path, video_id: str, validation_path: Path) -> None:
    store = ManifestStore(root)
    manifest = store.load(video_id)
    stage = Stage.HTML_VALIDATE
    validation_file = validation_path.expanduser().resolve()
    if manifest.is_complete(stage):
        _artifact_path(root, manifest, "html_validation")
        return
    manifest.start(stage)
    store.save(manifest)
    try:
        validation = _load_object(validation_file)
        if validation.get("schema_version") != 1:
            raise ValueError("HTML validation must use schema_version 1")
        if str(validation.get("video_id") or "") != video_id:
            raise ValueError("HTML validation video_id does not match the run")
        if str(validation.get("status") or "").lower() != "passed":
            raise ValueError("HTML validation status must be 'passed'")
        if validation.get("visual_review_completed") is not True:
            raise ValueError("HTML validation must include a completed visual review")
        if _contains_pending(validation):
            raise ValueError("HTML validation still contains pending checks")
        report_html = _artifact_path(root, manifest, "report_html")
        bound_artifacts = {
            "report_html_sha256": report_html,
            "report_markdown_sha256": _artifact_path(
                root, manifest, "report_markdown"
            ),
            "report_data_sha256": _artifact_path(root, manifest, "report_data"),
            "citations_sha256": _artifact_path(root, manifest, "citations"),
        }
        for field, artifact in bound_artifacts.items():
            if validation.get(field) != sha256_file(artifact):
                raise ValueError(f"HTML validation is not bound to {artifact.name}")
        document = report_html.read_text(encoding="utf-8")
        if len(document) < 200 or "{{REPORT_BODY}}" in document:
            raise ValueError("Rendered HTML is incomplete")
        manifest.metadata.update(validate_rendered_report(report_html, root))
        store.set_artifact(manifest, "html_validation", validation_file)
        manifest.complete(stage)
        store.save(manifest)
    except Exception as exc:
        manifest.fail(stage, str(exc), retryable=True)
        store.save(manifest)
        raise


def _contains_pending(value: object) -> bool:
    if isinstance(value, dict):
        return any(_contains_pending(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_pending(item) for item in value)
    return isinstance(value, str) and value.strip().lower() == "pending"



def complete_run(root: Path, video_id: str) -> None:
    store = ManifestStore(root)
    processed = ProcessedReportStore(root)
    manifest = store.load(video_id)
    manifest.require_completed(
        Stage.INGEST,
        Stage.ANALYZE,
        Stage.RESEARCH,
        Stage.JUDGMENT,
        Stage.DRAFT,
        Stage.FIDELITY_REVIEW,
        Stage.RENDER,
        Stage.HTML_VALIDATE,
    )
    _artifact_path(root, manifest, "transcript_corrected_jsonl")
    _artifact_path(root, manifest, "transcript_corrections")
    _artifact_path(root, manifest, "content_selection")
    _artifact_path(root, manifest, "transcript_report_jsonl")
    agent_judgment_path = _artifact_path(root, manifest, "agent_judgment")
    agent_judgment = _load_object(agent_judgment_path)
    report_data_path = _artifact_path(root, manifest, "report_data")
    report_data = _load_object(report_data_path)
    required_hashes = {
        "transcript_package",
        "transcript_corrected_jsonl",
        "transcript_corrections",
        "content_selection",
        "transcript_report_jsonl",
        "video_analysis",
        "opinions",
        "research_dir",
        "agent_judgment",
        "draft_markdown",
        "fidelity_review",
        "report_data",
        "citations",
        "report_markdown",
        "report_html",
        "html_validation",
    }
    missing_hashes = sorted(required_hashes - set(manifest.artifact_hashes))
    if missing_hashes:
        raise RuntimeError(
            "Run artifacts predate integrity binding: " + ", ".join(missing_hashes)
        )
    try:
        schema_version = int(report_data.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("report-data.json has an invalid schema_version") from exc
    if schema_version < 2:
        raise ValueError("report-data.json must use schema version 2 or newer")
    if report_data.get("agent_judgment") != agent_judgment:
        raise ValueError("report-data.json contains a stale or mismatched Agent judgment")
    expected_source_hashes = {
        key: manifest.artifact_hashes[key]
        for key in (
            "video_analysis",
            "opinions",
            "research_dir",
            "agent_judgment",
            "fidelity_review",
        )
    }
    if report_data.get("source_artifact_hashes") != expected_source_hashes:
        raise ValueError("report-data.json is not bound to the recorded source artifacts")
    report_markdown_path = _artifact_path(root, manifest, "report_markdown")
    report_markdown_text = report_markdown_path.read_text(encoding="utf-8")
    validate_report_layers(report_markdown_text)
    transcript_text = "".join(
        segment.text
        for segment in read_jsonl(
            _artifact_path(root, manifest, "transcript_report_jsonl")
        )
    )
    validate_report_readability(
        report_markdown_text,
        transcript_text=transcript_text,
        topic_count=int(manifest.metadata.get("topic_count") or 0),
    )
    for key in ("report_markdown", "report_html", "report_data", "citations", "html_validation"):
        _artifact_path(root, manifest, key)
    validate_rendered_report(_artifact_path(root, manifest, "report_html"), root)
    if not manifest.is_complete(Stage.COMPLETE):
        manifest.start(Stage.COMPLETE)
        manifest.complete(Stage.COMPLETE)
        store.save(manifest)
    completed_at = manifest.stages[Stage.COMPLETE.value].finished_at or datetime.now(
        timezone.utc
    ).isoformat()
    processed.add(
        {
            "video_id": manifest.video_id,
            "source_url": manifest.source_url,
            "title": manifest.metadata.get("title"),
            "published_at": manifest.metadata.get("published_at"),
            "completed_at": completed_at,
            "report_markdown": manifest.artifacts["report_markdown"],
            "report_html": manifest.artifacts["report_html"],
        }
    )


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    root = Path(arguments.project_root).resolve()
    store = ManifestStore(root)
    try:
        if arguments.command == "import-transcript":
            video_id = import_transcript_package(root, arguments.package)
            print(store.manifest_path(video_id))
        elif arguments.command == "render-html":
            render_html(
                root,
                arguments.video_id,
                arguments.markdown,
                arguments.template,
                arguments.output,
                force=arguments.force,
            )
        elif arguments.command == "build-structured":
            build_structured(root, arguments)
        elif arguments.command == "record-analysis":
            record_analysis(
                root,
                arguments.video_id,
                arguments.video_analysis,
                arguments.opinions,
            )
        elif arguments.command == "record-research":
            record_research(root, arguments.video_id, arguments.research_dir)
        elif arguments.command == "record-judgment":
            record_agent_judgment(
                root,
                arguments.video_id,
                arguments.judgment,
                force=arguments.force,
            )
        elif arguments.command == "record-draft":
            record_draft(root, arguments.video_id, arguments.markdown, force=arguments.force)
        elif arguments.command == "record-fidelity-review":
            record_fidelity_review(root, arguments.video_id, arguments.review, force=arguments.force)
        elif arguments.command == "validate-html":
            validate_html(root, arguments.video_id, arguments.validation)
        elif arguments.command == "complete-run":
            complete_run(root, arguments.video_id)
        elif arguments.command == "status":
            print(json.dumps(store.load(arguments.video_id).to_dict(), ensure_ascii=False, indent=2))
        else:
            raise AssertionError(arguments.command)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
