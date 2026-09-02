from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .content_selection import (
    materialize_content_selection,
    materialize_model_transcript_view,
)
from .ingestion import import_transcript_package
from .models import VIDEO_MEANING_PROFILE, Stage, StageStatus
from .reporting import (
    build_meaning_structured_artifacts,
    build_structured_artifacts,
    render_markdown_report,
    validate_meaning_report,
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

    meaning = commands.add_parser(
        "record-meaning-report",
        help=(
            "Atomically validate and record internal understanding, presentation plan, "
            "source-only analysis, opinions, and report Markdown"
        ),
    )
    meaning.add_argument("--video-id", required=True)
    meaning.add_argument("--understanding-notes", required=True, type=Path)
    meaning.add_argument("--presentation-plan", required=True, type=Path)
    meaning.add_argument("--video-analysis", required=True, type=Path)
    meaning.add_argument("--opinions", required=True, type=Path)
    meaning.add_argument("--markdown", required=True, type=Path)

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


def _start_stage_if_needed(manifest: object, stage: Stage) -> bool:
    record = getattr(manifest, "stages")[stage.value]
    if record.status == StageStatus.RUNNING:
        return False
    getattr(manifest, "start")(stage)
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
        report_transcript = _artifact_path(root, manifest, "transcript_report_jsonl")
        model_view = (
            store.run_dir(video_id) / "transcript" / "transcript.report.model.txt"
        )
        materialize_model_transcript_view(
            transcript_path=report_transcript,
            output_path=model_view,
            source_artifact=report_transcript.name,
        )
        store.set_artifact(manifest, "transcript_report_model", model_view)
        store.save(manifest)
        return
    if not already_complete and _start_stage_if_needed(manifest, stage):
        store.save(manifest)
    try:
        analysis = _load_object(analysis_file)
        analysis_schema = analysis.get("schema_version")
        if analysis_schema not in {1, 2}:
            raise ValueError("video-analysis must use schema_version 1 or 2")
        if manifest.is_meaning_report and analysis.get("workflow_profile") != VIDEO_MEANING_PROFILE:
            raise ValueError(
                "Meaning analysis must declare workflow_profile=video_meaning_v1"
            )
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
        if analysis_schema == 2 and not analysis["sections"]:
            raise ValueError("video-analysis v2 needs at least one reportable section")
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
        section_ranges: dict[str, tuple[int, int]] = {}
        for section in analysis["sections"]:  # type: ignore[index]
            if not isinstance(section, dict):
                raise ValueError("video-analysis sections must contain objects")
            for field in ("section_id", "title", "segment_start", "segment_end"):
                _text(section.get(field), f"video-analysis section {field}")
            section_id = str(section["section_id"])
            if section_id in section_ranges:
                raise ValueError(f"Duplicate video-analysis section_id: {section_id}")
            if analysis_schema == 2:
                _text(section.get("summary"), f"video-analysis section {section_id} summary")
                _text_list(
                    section.get("key_points"),
                    f"video-analysis section {section_id} key_points",
                )
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
            section_ranges[section_id] = (
                transcript_positions[start_id],
                transcript_positions[end_id],
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
            if analysis_schema == 2:
                section_id = _text(
                    item.get("section_id"), f"Opinion {opinion_id} section_id"
                )
                if section_id not in section_ranges:
                    raise ValueError(
                        f"Opinion {opinion_id} refers to an unknown section: {section_id}"
                    )
                speaker = _text(item.get("speaker"), f"Opinion {opinion_id} speaker")
                stance_owner = _text(
                    item.get("stance_owner"), f"Opinion {opinion_id} stance_owner"
                )
                attribution_mode = _text(
                    item.get("attribution_mode"),
                    f"Opinion {opinion_id} attribution_mode",
                )
                allowed_attribution_modes = {
                    "self",
                    "reported",
                    "direct_quote",
                    "uncertain",
                }
                if attribution_mode not in allowed_attribution_modes:
                    raise ValueError(
                        f"Opinion {opinion_id} has unsupported attribution_mode: "
                        f"{attribution_mode}"
                    )
                if speaker.casefold() != stance_owner.casefold() and attribution_mode == "self":
                    raise ValueError(
                        f"Opinion {opinion_id} cannot use self attribution for a different "
                        "stance owner"
                    )
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
            expected_research_status = (
                "not_applicable" if manifest.is_meaning_report else "pending"
            )
            if item.get("research_status") != expected_research_status:
                raise ValueError(
                    f"Opinion {opinion_id} research_status must be "
                    f"{expected_research_status}"
                )
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
            if analysis_schema == 2 and (
                segment_start is None or segment_end is None
            ):
                raise ValueError(
                    f"Opinion {opinion_id} needs a segment range for section coverage"
                )
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
                if analysis_schema == 2:
                    section_start, section_end = section_ranges[str(item["section_id"])]
                    if (
                        transcript_positions[start_id] < section_start
                        or transcript_positions[end_id] > section_end
                    ):
                        raise ValueError(
                            f"Opinion {opinion_id} lies outside its assigned section"
                        )
        store.set_artifact(manifest, "video_analysis", analysis_file)
        store.set_artifact(manifest, "opinions", opinions_file)
        store.set_artifact(manifest, "content_selection", selection_path)
        store.set_artifact(manifest, "transcript_report_jsonl", filtered_transcript_path)
        model_view_path = (
            store.run_dir(video_id) / "transcript" / "transcript.report.model.txt"
        )
        materialize_model_transcript_view(
            transcript_path=filtered_transcript_path,
            output_path=model_view_path,
            source_artifact=filtered_transcript_path.name,
        )
        store.set_artifact(manifest, "transcript_report_model", model_view_path)
        manifest.metadata["opinion_count"] = len(opinions)
        manifest.metadata["video_analysis_schema_version"] = analysis_schema
        manifest.metadata["video_analysis_section_count"] = len(section_ranges)
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


def _validate_understanding_notes(
    payload: dict[str, object],
    *,
    video_id: str,
    transcript_sha256: str,
) -> dict[str, int]:
    if payload.get("schema_version") != 1:
        raise ValueError("understanding-notes.json must use schema_version 1")
    if str(payload.get("video_id") or "") != video_id:
        raise ValueError("understanding-notes.json video_id does not match the run")
    if payload.get("workflow_profile") != VIDEO_MEANING_PROFILE:
        raise ValueError("understanding-notes.json workflow_profile is invalid")
    if payload.get("display_policy") != "internal_only":
        raise ValueError("understanding notes must be marked internal_only")
    if str(payload.get("transcript_sha256") or "") != transcript_sha256:
        raise ValueError("understanding notes do not bind the corrected transcript")
    for field in (
        "term_checks",
        "data_checks",
        "domain_context",
        "uncertainties",
        "web_sources",
    ):
        if not isinstance(payload.get(field), list):
            raise ValueError(f"understanding-notes.json {field} must be a list")
    web_sources = payload["web_sources"]
    for source in web_sources:  # type: ignore[union-attr]
        if not isinstance(source, dict):
            raise ValueError("understanding-notes web_sources must contain objects")
        url = str(source.get("url") or "")
        if urlsplit(url).scheme not in {"http", "https"}:
            raise ValueError("understanding-notes web source needs a direct HTTP URL")
    return {
        "understanding_term_check_count": len(payload["term_checks"]),  # type: ignore[arg-type]
        "understanding_data_check_count": len(payload["data_checks"]),  # type: ignore[arg-type]
        "understanding_web_source_count": len(web_sources),  # type: ignore[arg-type]
    }


def _validate_presentation_plan(
    payload: dict[str, object],
    *,
    video_id: str,
    analysis: dict[str, object],
    transcript_positions: dict[str, int],
) -> dict[str, int]:
    if payload.get("schema_version") != 1:
        raise ValueError("presentation-plan.json must use schema_version 1")
    if str(payload.get("video_id") or "") != video_id:
        raise ValueError("presentation-plan.json video_id does not match the run")
    if payload.get("workflow_profile") != VIDEO_MEANING_PROFILE:
        raise ValueError("presentation-plan.json workflow_profile is invalid")
    _text(payload.get("report_title"), "presentation-plan report_title")
    _text(payload.get("cover_deck"), "presentation-plan cover_deck")
    cards = payload.get("summary_cards")
    if not isinstance(cards, list) or not 2 <= len(cards) <= 3:
        raise ValueError("presentation plan needs 2 to 3 summary cards")
    for card in cards:
        if not isinstance(card, dict):
            raise ValueError("presentation plan summary cards must be objects")
        for field in ("label", "headline", "detail"):
            _text(card.get(field), f"presentation-plan summary card {field}")
    if "报告整理 · 仅据字幕" not in str(cards[0].get("label") or ""):
        raise ValueError("first presentation-plan summary card needs source-only label")

    analysis_sections = analysis.get("sections")
    plan_sections = payload.get("sections")
    if not isinstance(analysis_sections, list) or not isinstance(plan_sections, list):
        raise ValueError("presentation plan and analysis need sections")
    analysis_by_id = {
        str(section.get("section_id") or ""): section
        for section in analysis_sections
        if isinstance(section, dict)
    }
    expected = list(analysis_by_id)
    actual: list[str] = []
    visual_count = 0
    allowed_visuals = {
        "none",
        "kpi",
        "comparison",
        "timeline",
        "mechanism",
        "relationship",
        "news_list",
    }
    for section in plan_sections:
        if not isinstance(section, dict):
            raise ValueError("presentation plan sections must contain objects")
        section_id = _text(section.get("section_id"), "presentation-plan section_id")
        actual.append(section_id)
        _text(section.get("lead"), f"presentation-plan {section_id} lead")
        visual_type = _text(
            section.get("visual_type"), f"presentation-plan {section_id} visual_type"
        )
        if visual_type not in allowed_visuals:
            raise ValueError(f"Unsupported visual_type in {section_id}: {visual_type}")
        _text(
            section.get("visual_reason"),
            f"presentation-plan {section_id} visual_reason",
        )
        segment_ids = _text_list(
            section.get("source_segment_ids"),
            f"presentation-plan {section_id} source_segment_ids",
        )
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError(f"Duplicate source segment IDs in {section_id}")
        analysis_section = analysis_by_id.get(section_id)
        if analysis_section is not None:
            start_id = str(analysis_section.get("segment_start") or "")
            end_id = str(analysis_section.get("segment_end") or "")
            if start_id not in transcript_positions or end_id not in transcript_positions:
                raise ValueError(f"Analysis has an invalid segment range: {section_id}")
            start = transcript_positions[start_id]
            end = transcript_positions[end_id]
            if any(
                segment_id not in transcript_positions
                or not start <= transcript_positions[segment_id] <= end
                for segment_id in segment_ids
            ):
                raise ValueError(
                    f"Presentation visual sources lie outside section {section_id}"
                )
        visual_count += visual_type != "none"
    if actual != expected:
        raise ValueError("presentation plan sections must match analysis order exactly")
    return {
        "presentation_summary_card_count": len(cards),
        "presentation_section_count": len(plan_sections),
        "presentation_visual_count": visual_count,
    }


def record_meaning_report(
    root: Path,
    video_id: str,
    understanding_path: Path,
    presentation_plan_path: Path,
    analysis_path: Path,
    opinions_path: Path,
    markdown_path: Path,
) -> None:
    """Register all model-authored meaning artifacts as one analyze stage."""

    store = ManifestStore(root)
    manifest = store.load(video_id)
    if not manifest.is_meaning_report:
        raise RuntimeError("record-meaning-report only supports video_meaning_v1 runs")
    original_artifacts = dict(manifest.artifacts)
    original_hashes = dict(manifest.artifact_hashes)
    original_metadata = dict(manifest.metadata)
    understanding_file = understanding_path.expanduser().resolve()
    presentation_plan_file = presentation_plan_path.expanduser().resolve()
    analysis_file = analysis_path.expanduser().resolve()
    opinions_file = opinions_path.expanduser().resolve()
    expected_run_dir = store.run_dir(video_id).resolve()
    if understanding_file != expected_run_dir / "understanding-notes.json":
        raise ValueError(f"understanding-notes.json must be written to {expected_run_dir}")
    if presentation_plan_file != expected_run_dir / "presentation-plan.json":
        raise ValueError(f"presentation-plan.json must be written to {expected_run_dir}")
    if analysis_file != expected_run_dir / "video-analysis.json":
        raise ValueError(
            f"video-analysis.json must be written to {expected_run_dir}"
        )
    if opinions_file != expected_run_dir / "opinions.jsonl":
        raise ValueError(f"opinions.jsonl must be written to {expected_run_dir}")
    markdown_file = _require_expected_report_file(
        root, manifest, markdown_path, "report.md"
    )
    if (
        manifest.is_complete(Stage.ANALYZE)
        and "draft_markdown" in manifest.artifacts
    ):
        recorded = _artifact_path(root, manifest, "draft_markdown")
        if recorded != markdown_file:
            raise ValueError("Meaning report Markdown is already recorded elsewhere")
        analysis = _load_object(_artifact_path(root, manifest, "video_analysis"))
        presentation_plan = _load_object(
            _artifact_path(root, manifest, "presentation_plan")
        )
        validate_meaning_report(
            markdown_file.read_text(encoding="utf-8"), analysis, presentation_plan
        )
        return

    try:
        text = markdown_file.read_text(encoding="utf-8")
        if len(text.strip()) < 100:
            raise ValueError("Meaning report Markdown is unexpectedly short")
        analysis = _load_object(analysis_file)
        understanding = _load_object(understanding_file)
        presentation_plan = _load_object(presentation_plan_file)
        transcript_positions = {
            segment.segment_id: index
            for index, segment in enumerate(
                read_jsonl(_artifact_path(root, manifest, "transcript_corrected_jsonl"))
            )
        }
        internal_counts = _validate_understanding_notes(
            understanding,
            video_id=video_id,
            transcript_sha256=manifest.artifact_hashes[
                "transcript_corrected_jsonl"
            ],
        )
        presentation_counts = _validate_presentation_plan(
            presentation_plan,
            video_id=video_id,
            analysis=analysis,
            transcript_positions=transcript_positions,
        )
        counts = validate_meaning_report(text, analysis, presentation_plan)
        record_analysis(root, video_id, analysis_path, opinions_path)
        manifest = store.load(video_id)
        transcript_text = "".join(
            segment.text
            for segment in read_jsonl(
                _artifact_path(root, manifest, "transcript_report_jsonl")
            )
        )
        readability = validate_report_readability(
            text,
            transcript_text=transcript_text,
            topic_count=len(analysis.get("sections") or []),
        )
        store.set_artifact(manifest, "understanding_notes", understanding_file)
        store.set_artifact(manifest, "presentation_plan", presentation_plan_file)
        store.set_artifact(manifest, "draft_markdown", markdown_file)
        manifest.metadata.update(internal_counts)
        manifest.metadata.update(presentation_counts)
        manifest.metadata.update(counts)
        manifest.metadata.update(readability)
        store.save(manifest)
    except Exception as exc:
        manifest = store.load(video_id)
        manifest.artifacts = original_artifacts
        manifest.artifact_hashes = original_hashes
        manifest.metadata = original_metadata
        manifest.fail(Stage.ANALYZE, str(exc), retryable=False)
        store.save(manifest)
        raise


def record_research(root: Path, video_id: str, research_dir: Path) -> None:
    store = ManifestStore(root)
    manifest = store.load(video_id)
    if manifest.is_meaning_report:
        raise RuntimeError("Research is not part of video_meaning_v1")
    stage = Stage.RESEARCH
    _artifact_path(root, manifest, "content_selection")
    _artifact_path(root, manifest, "transcript_report_jsonl")
    directory = research_dir.expanduser().resolve()
    already_complete = manifest.is_complete(stage)
    if already_complete and "research_dir" in manifest.artifacts:
        _artifact_path(root, manifest, "research_dir")
        return
    if not already_complete and _start_stage_if_needed(manifest, stage):
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
    if manifest.is_meaning_report:
        raise RuntimeError("Agent judgment is not part of video_meaning_v1")
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
    elif not already_complete and _start_stage_if_needed(manifest, stage):
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
    if manifest.is_meaning_report:
        raise RuntimeError("A meaning report draft must use record-meaning-report")
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
    elif not already_complete and _start_stage_if_needed(manifest, stage):
        store.save(manifest)
    try:
        text = markdown_file.read_text(encoding="utf-8")
        if len(text.strip()) < 100:
            raise ValueError("Draft Markdown is unexpectedly short")
        layer_counts = validate_report_layers(text)
        if (
            manifest.metadata.get("video_analysis_schema_version") == 2
            and layer_counts["investor_dashboard_count"] != 1
        ):
            raise ValueError(
                "video-analysis v2 reports need exactly one investor dashboard"
            )
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
    if manifest.is_meaning_report:
        raise RuntimeError("Fidelity review is not part of video_meaning_v1")
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
    elif not already_complete and _start_stage_if_needed(manifest, stage):
        store.save(manifest)
    try:
        review = _load_object(review_file)
        review_schema = review.get("schema_version")
        if review_schema not in {1, 2}:
            raise ValueError("Fidelity review must use schema_version 1 or 2")
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
        analysis = _load_object(_artifact_path(root, manifest, "video_analysis"))
        analysis_schema = analysis.get("schema_version")
        if analysis_schema == 2 and review_schema != 2:
            raise ValueError("Fidelity review must use schema_version 2 for analysis v2")
        expected_sections = {
            str(item.get("section_id") or "")
            for item in analysis.get("sections", [])  # type: ignore[union-attr]
            if isinstance(item, dict)
        }
        reviewed_sections = {
            str(item.get("section_id") or "")
            for item in review["section_checks"]  # type: ignore[index]
            if isinstance(item, dict)
        }
        if (
            reviewed_sections != expected_sections
            or len(reviewed_sections) != len(review["section_checks"])  # type: ignore[arg-type]
        ):
            raise ValueError(
                "Fidelity review section coverage does not match recorded sections"
            )
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
        if (
            reviewed_opinions != expected_opinions
            or len(reviewed_opinions) != len(review["opinion_checks"])  # type: ignore[arg-type]
        ):
            raise ValueError(
                "Fidelity review opinion coverage does not match recorded opinions"
            )
        if review_schema == 2:
            for item in review["section_checks"]:  # type: ignore[index]
                if not isinstance(item, dict):
                    raise ValueError("Fidelity review section_checks must contain objects")
                section_id = str(item.get("section_id") or "")
                coverage_status = str(item.get("coverage_status") or "")
                if coverage_status not in {"included", "intentionally_omitted"}:
                    raise ValueError(
                        f"Fidelity review section {section_id} has invalid coverage_status"
                    )
                locations = item.get("report_locations")
                if coverage_status == "included":
                    _text_list(
                        locations,
                        f"Fidelity review section {section_id} report_locations",
                    )
                elif not str(item.get("omission_reason") or "").strip():
                    raise ValueError(
                        f"Fidelity review section {section_id} needs an omission_reason"
                    )
            opinion_records = {
                str(json.loads(line)["opinion_id"]): json.loads(line)
                for line in _artifact_path(root, manifest, "opinions")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            }
            for item in review["opinion_checks"]:  # type: ignore[index]
                if not isinstance(item, dict):
                    raise ValueError("Fidelity review opinion_checks must contain objects")
                opinion_id = str(item.get("opinion_id") or "")
                expected = opinion_records[opinion_id]
                for field in ("speaker", "stance_owner", "attribution_mode"):
                    if str(item.get(field) or "") != str(expected.get(field) or ""):
                        raise ValueError(
                            f"Fidelity review {opinion_id} does not preserve {field}"
                        )
                _text_list(
                    item.get("report_locations"),
                    f"Fidelity review opinion {opinion_id} report_locations",
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
        _start_stage_if_needed(manifest, stage)
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
        if manifest.is_meaning_report:
            layer_counts = validate_meaning_report(
                markdown_text,
                _load_object(_artifact_path(root, manifest, "video_analysis")),
                (
                    _load_object(_artifact_path(root, manifest, "presentation_plan"))
                    if "presentation_plan" in manifest.artifacts
                    else None
                ),
            )
        else:
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


def build_meaning_structured(
    root: Path,
    video_id: str,
    report_data_path: Path,
    citations_path: Path,
) -> None:
    store = ManifestStore(root)
    manifest = store.load(video_id)
    if not manifest.is_meaning_report:
        raise RuntimeError("Meaning structured build requires video_meaning_v1")
    manifest.require_completed(Stage.ANALYZE)
    if manifest.is_complete(Stage.RENDER):
        raise RuntimeError("Structured artifacts cannot be rebuilt after rendering")
    registered_inputs = {
        key: _artifact_path(root, manifest, key)
        for key in (
            "transcript_package",
            "transcript_corrected_jsonl",
            "content_selection",
            "transcript_report_jsonl",
            "video_analysis",
            "opinions",
            "draft_markdown",
        )
    }
    report_data = _require_expected_report_file(
        root, manifest, report_data_path, "report-data.json"
    )
    citations = _require_expected_report_file(
        root, manifest, citations_path, "citations.json"
    )
    source_hashes = {
        key: manifest.artifact_hashes[key] for key in registered_inputs
    }
    if {"report_data", "citations"} <= set(manifest.artifacts):
        if (
            _artifact_path(root, manifest, "report_data") == report_data
            and _artifact_path(root, manifest, "citations") == citations
        ):
            existing = _load_object(report_data)
            if existing.get("source_artifact_hashes") == source_hashes:
                return
        else:
            raise RuntimeError("Structured artifacts are already recorded at other paths")
    counts = build_meaning_structured_artifacts(
        registered_inputs["video_analysis"],
        registered_inputs["opinions"],
        report_data,
        citations,
        source_artifact_hashes=source_hashes,
    )
    store.set_artifact(manifest, "report_data", report_data)
    store.set_artifact(manifest, "citations", citations)
    manifest.metadata.update(counts)
    store.save(manifest)


def build_structured(root: Path, arguments: argparse.Namespace) -> None:
    store = ManifestStore(root)
    manifest = store.load(arguments.video_id)
    if manifest.is_meaning_report:
        raise RuntimeError("build-structured is only available for legacy full reports")
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
    if not manifest.is_meaning_report:
        raise RuntimeError(
            "This branch does not complete or resume legacy video_full_v1 runs"
        )
    manifest.require_completed(
        Stage.INGEST,
        Stage.ANALYZE,
        Stage.RENDER,
        Stage.HTML_VALIDATE,
    )
    _artifact_path(root, manifest, "transcript_corrected_jsonl")
    _artifact_path(root, manifest, "transcript_corrections")
    _artifact_path(root, manifest, "content_selection")
    _artifact_path(root, manifest, "transcript_report_jsonl")
    forbidden_artifacts = {
        "research_dir",
        "agent_judgment",
        "fidelity_review",
    } & set(manifest.artifacts)
    if forbidden_artifacts:
        raise RuntimeError(
            "Meaning run contains legacy artifacts: "
            + ", ".join(sorted(forbidden_artifacts))
        )
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
        "draft_markdown",
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
    if schema_version != 3:
        raise ValueError("Meaning report-data.json must use schema_version 3")
    if report_data.get("workflow_profile") != VIDEO_MEANING_PROFILE:
        raise ValueError("Meaning report-data.json has the wrong workflow profile")
    forbidden_fields = {
        "research_topics",
        "research_status_counts",
        "agent_judgment",
        "fidelity_review",
    } & set(report_data)
    if forbidden_fields:
        raise ValueError(
            "Meaning report-data.json contains legacy fields: "
            + ", ".join(sorted(forbidden_fields))
        )
    expected_source_hashes = {
        key: manifest.artifact_hashes[key]
        for key in (
            "transcript_package",
            "transcript_corrected_jsonl",
            "content_selection",
            "transcript_report_jsonl",
            "video_analysis",
            "opinions",
            "draft_markdown",
        )
    }
    if report_data.get("source_artifact_hashes") != expected_source_hashes:
        raise ValueError("report-data.json is not bound to the recorded source artifacts")
    report_markdown_path = _artifact_path(root, manifest, "report_markdown")
    report_markdown_text = report_markdown_path.read_text(encoding="utf-8")
    analysis = _load_object(_artifact_path(root, manifest, "video_analysis"))
    presentation_plan = (
        _load_object(_artifact_path(root, manifest, "presentation_plan"))
        if "presentation_plan" in manifest.artifacts
        else None
    )
    validate_meaning_report(report_markdown_text, analysis, presentation_plan)
    if report_data.get("analysis") != analysis:
        raise ValueError("report-data.json contains stale analysis")
    citations = _load_object(_artifact_path(root, manifest, "citations"))
    if citations.get("schema_version") != 2:
        raise ValueError("Meaning citations.json must use schema_version 2")
    if citations.get("workflow_profile") != VIDEO_MEANING_PROFILE:
        raise ValueError("Meaning citations.json has the wrong workflow profile")
    if citations.get("external_sources") != []:
        raise ValueError("Meaning citations.json external_sources must be empty")
    transcript_text = "".join(
        segment.text
        for segment in read_jsonl(
            _artifact_path(root, manifest, "transcript_report_jsonl")
        )
    )
    validate_report_readability(
        report_markdown_text,
        transcript_text=transcript_text,
        topic_count=len(analysis.get("sections") or []),
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
            "workflow_profile": manifest.workflow_profile,
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
        elif arguments.command == "record-meaning-report":
            record_meaning_report(
                root,
                arguments.video_id,
                arguments.understanding_notes,
                arguments.presentation_plan,
                arguments.video_analysis,
                arguments.opinions,
                arguments.markdown,
            )
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
