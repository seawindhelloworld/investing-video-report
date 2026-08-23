from __future__ import annotations

import json
import math
import os
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .integrity import sha256_file
from .transcript import TranscriptSegment, read_jsonl, validate_segments


EXCLUDED_CONTENT_CATEGORIES = {
    "advertising",
    "product_promotion",
    "subscription_prompt",
    "sales_language",
    "unrelated_content",
    "boilerplate_outro",
    "asr_noise",
    "blank",
}
UNCERTAIN_EXCLUSION_CATEGORIES = {
    "unrelated_content",
    "boilerplate_outro",
    "asr_noise",
}
EDGE_ONLY_EXCLUSION_CATEGORIES = {
    "advertising",
    "product_promotion",
    "subscription_prompt",
    "sales_language",
    "unrelated_content",
    "boilerplate_outro",
}
NON_REPORTABLE_CATEGORIES = {
    "advertising",
    "product_promotion",
    "subscription_prompt",
    "sales_language",
}
MAX_EDGE_EXCLUSION_SECONDS = 120.0
MAX_CONTIGUOUS_EXCLUSION_SEGMENTS = 20
MAX_CONTIGUOUS_EXCLUSION_SECONDS = 60.0
MAX_UNCERTAIN_CATEGORY_SEGMENTS = 5
MAX_UNCERTAIN_CATEGORY_SECONDS = 20.0
MAX_TOTAL_EXCLUSION_FRACTION = 0.15
MIN_TOTAL_EXCLUSION_ALLOWANCE = 1


def _timestamp(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Content exclusion {label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Content exclusion {label} must be finite and non-negative")
    return result


def _write_analysis_artifact(path: Path, content: bytes) -> None:
    if path.exists():
        if not path.is_file():
            raise RuntimeError(f"Analysis artifact path is not a file: {path}")
        if path.read_bytes() == content:
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _serialize_segments(segments: list[TranscriptSegment]) -> bytes:
    return "".join(
        json.dumps(asdict(segment), ensure_ascii=False) + "\n" for segment in segments
    ).encode("utf-8")


def materialize_content_selection(
    *,
    video_id: str,
    transcript_path: Path,
    excluded_ranges: object,
    non_reportable_ranges: object | None = None,
    selection_path: Path,
    filtered_transcript_path: Path,
    source_artifact: str = "transcript.corrected.jsonl",
) -> dict[str, Any]:
    segments = read_jsonl(transcript_path)
    errors = validate_segments(segments, allow_blank_text=True)
    if errors:
        raise ValueError(f"Corrected transcript is invalid: {errors[0]}")
    if not isinstance(excluded_ranges, list):
        raise ValueError("video-analysis excluded_ranges must be a list")
    if non_reportable_ranges is None:
        non_reportable_ranges = []
    if not isinstance(non_reportable_ranges, list):
        raise ValueError("video-analysis non_reportable_ranges must be a list")

    transcript_start = segments[0].start
    transcript_end = segments[-1].end
    transcript_duration = transcript_end - transcript_start
    edge_window_seconds = min(
        MAX_EDGE_EXCLUSION_SECONDS,
        transcript_duration / 3.0,
    )
    intro_end = transcript_start + edge_window_seconds
    outro_start = transcript_end - edge_window_seconds
    positions = {segment.segment_id: index for index, segment in enumerate(segments)}
    assignments: list[int | None] = [None] * len(segments)
    normalized_exclusions: list[dict[str, Any]] = []

    # Empty ASR rows carry no author content and are safe to remove without spending
    # model tokens. Keep them in the audit trail as deterministic exclusions.
    cursor = 0
    while cursor < len(segments):
        if segments[cursor].text.strip():
            cursor += 1
            continue
        end = cursor
        while end + 1 < len(segments) and not segments[end + 1].text.strip():
            end += 1
        normalized_index = len(normalized_exclusions)
        normalized_exclusions.append(
            {
                "exclusion_id": f"exclusion-{normalized_index + 1:03d}",
                "segment_start": segments[cursor].segment_id,
                "segment_end": segments[end].segment_id,
                "timestamp_start": segments[cursor].start,
                "timestamp_end": segments[end].end,
                "category": "blank",
                "reason": "程序识别的空白字幕",
                "certainty": "high",
                "automatic": True,
            }
        )
        for position in range(cursor, end + 1):
            assignments[position] = normalized_index
        cursor = end + 1

    for raw_index, raw in enumerate(excluded_ranges, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid content exclusion #{raw_index}")
        segment_start = str(raw.get("segment_start") or "")
        segment_end = str(raw.get("segment_end") or "")
        if segment_start not in positions or segment_end not in positions:
            raise ValueError(
                f"Content exclusion references an unknown segment: "
                f"{segment_start}–{segment_end}"
            )
        start_index = positions[segment_start]
        end_index = positions[segment_end]
        if end_index < start_index:
            raise ValueError(
                f"Content exclusion has reversed segment order: "
                f"{segment_start}–{segment_end}"
            )
        category = str(raw.get("category") or "").strip()
        if category not in EXCLUDED_CONTENT_CATEGORIES:
            raise ValueError(
                f"Content exclusion has unsupported category: {category or '<empty>'}"
            )
        if category == "blank" and any(
            segments[position].text.strip()
            for position in range(start_index, end_index + 1)
        ):
            raise ValueError("Only actually empty subtitle segments may use category blank")
        reason = str(raw.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"Content exclusion lacks a reason: {segment_start}")
        certainty = str(raw.get("certainty") or "").strip().casefold()
        if certainty != "high":
            raise ValueError(
                f"Content exclusion must be high certainty; keep uncertain content: "
                f"{segment_start}–{segment_end}"
            )
        timestamp_start = _timestamp(raw.get("timestamp_start"), "timestamp_start")
        timestamp_end = _timestamp(raw.get("timestamp_end"), "timestamp_end")
        expected_start = segments[start_index].start
        expected_end = segments[end_index].end
        if (
            timestamp_end <= timestamp_start
            or abs(timestamp_start - expected_start) > 0.1
            or abs(timestamp_end - expected_end) > 0.1
        ):
            raise ValueError(
                f"Content exclusion timestamps do not match its segments: "
                f"{segment_start}–{segment_end}"
            )
        edge_region: str | None = None
        if category in EDGE_ONLY_EXCLUSION_CATEGORIES:
            if expected_end <= intro_end + 0.1:
                edge_region = "intro"
            elif expected_start >= outro_start - 0.1:
                edge_region = "outro"
            else:
                raise ValueError(
                    "Commercial, promotional, or potentially unrelated content in "
                    "the middle of the video must be kept to avoid false deletion: "
                    f"{segment_start}–{segment_end}"
                )
        segment_span = end_index - start_index + 1
        duration = expected_end - expected_start
        if (
            segment_span > MAX_CONTIGUOUS_EXCLUSION_SEGMENTS
            or duration > MAX_CONTIGUOUS_EXCLUSION_SECONDS
        ):
            raise ValueError(
                "Content exclusion is too broad; keep the passage and exclude only "
                f"a smaller clearly identified range: {segment_start}–{segment_end}"
            )
        if category in UNCERTAIN_EXCLUSION_CATEGORIES and (
            segment_span > MAX_UNCERTAIN_CATEGORY_SEGMENTS
            or duration > MAX_UNCERTAIN_CATEGORY_SECONDS
        ):
            raise ValueError(
                "Potentially ambiguous content exclusion is too broad; keep it by "
                f"default: {segment_start}–{segment_end}"
            )
        normalized_index = len(normalized_exclusions)
        for position in range(start_index, end_index + 1):
            if assignments[position] is not None:
                raise ValueError(
                    f"Content exclusions overlap at {segments[position].segment_id}"
                )
            assignments[position] = normalized_index
        normalized_exclusion = {
            "exclusion_id": f"exclusion-{normalized_index + 1:03d}",
            "segment_start": segment_start,
            "segment_end": segment_end,
            "timestamp_start": expected_start,
            "timestamp_end": expected_end,
            "category": category,
            "reason": reason,
            "certainty": "high",
        }
        if edge_region is not None:
            normalized_exclusion["edge_region"] = edge_region
        normalized_exclusions.append(normalized_exclusion)

    normalized_non_reportable: list[dict[str, Any]] = []
    non_reportable_positions: set[int] = set()
    for raw_index, raw in enumerate(non_reportable_ranges, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid non-reportable range #{raw_index}")
        segment_start = str(raw.get("segment_start") or "")
        segment_end = str(raw.get("segment_end") or "")
        if segment_start not in positions or segment_end not in positions:
            raise ValueError(
                "Non-reportable range references an unknown segment: "
                f"{segment_start}–{segment_end}"
            )
        start_index = positions[segment_start]
        end_index = positions[segment_end]
        if end_index < start_index:
            raise ValueError(
                f"Non-reportable range has reversed segment order: {segment_start}–{segment_end}"
            )
        category = str(raw.get("category") or "").strip()
        if category not in NON_REPORTABLE_CATEGORIES:
            raise ValueError(
                f"Non-reportable range has unsupported category: {category or '<empty>'}"
            )
        reason = str(raw.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"Non-reportable range lacks a reason: {segment_start}")
        if str(raw.get("certainty") or "").strip().casefold() != "high":
            raise ValueError(
                "Non-reportable range must be high certainty; keep uncertain content: "
                f"{segment_start}–{segment_end}"
            )
        expected_start = segments[start_index].start
        expected_end = segments[end_index].end
        timestamp_start = _timestamp(raw.get("timestamp_start"), "timestamp_start")
        timestamp_end = _timestamp(raw.get("timestamp_end"), "timestamp_end")
        if (
            timestamp_end <= timestamp_start
            or abs(timestamp_start - expected_start) > 0.1
            or abs(timestamp_end - expected_end) > 0.1
        ):
            raise ValueError(
                "Non-reportable range timestamps do not match its segments: "
                f"{segment_start}–{segment_end}"
            )
        segment_span = end_index - start_index + 1
        if (
            segment_span > MAX_CONTIGUOUS_EXCLUSION_SEGMENTS
            or expected_end - expected_start > MAX_CONTIGUOUS_EXCLUSION_SECONDS
        ):
            raise ValueError(
                "Non-reportable range is too broad; keep uncertain content: "
                f"{segment_start}–{segment_end}"
            )
        for position in range(start_index, end_index + 1):
            if assignments[position] is not None:
                raise ValueError(
                    "Non-reportable range duplicates excluded content at "
                    f"{segments[position].segment_id}"
                )
            if position in non_reportable_positions:
                raise ValueError(
                    f"Non-reportable ranges overlap at {segments[position].segment_id}"
                )
            non_reportable_positions.add(position)
        normalized_non_reportable.append(
            {
                "range_id": f"non-reportable-{raw_index:03d}",
                "segment_start": segment_start,
                "segment_end": segment_end,
                "timestamp_start": expected_start,
                "timestamp_end": expected_end,
                "category": category,
                "reason": reason,
                "certainty": "high",
                "source_retained": True,
            }
        )

    excluded_count = sum(
        assignment is not None and bool(segments[index].text.strip())
        for index, assignment in enumerate(assignments)
    )
    nonblank_segment_count = sum(bool(segment.text.strip()) for segment in segments)
    total_exclusion_allowance = max(
        MIN_TOTAL_EXCLUSION_ALLOWANCE,
        math.ceil(nonblank_segment_count * MAX_TOTAL_EXCLUSION_FRACTION),
    )
    if excluded_count > total_exclusion_allowance:
        raise ValueError(
            "Content selection excludes too much of the transcript; keep uncertain "
            "content and reduce exclusions to minimal clear passages"
        )
    if len(non_reportable_positions) > total_exclusion_allowance:
        raise ValueError(
            "Non-reportable selection covers too much of the transcript; retain "
            "uncertain middle content in the report"
        )
    nonblank_total_duration = sum(
        segment.end - segment.start for segment in segments if segment.text.strip()
    )

    def validate_total_duration(positions_to_check: set[int], label: str) -> None:
        durations = [
            segments[position].end - segments[position].start
            for position in positions_to_check
            if segments[position].text.strip()
        ]
        if not durations:
            return
        duration_allowance = max(
            max(durations),
            nonblank_total_duration * MAX_TOTAL_EXCLUSION_FRACTION,
        )
        if sum(durations) > duration_allowance + 0.1:
            raise ValueError(
                f"{label} covers too much transcript duration; keep uncertain content"
            )

    validate_total_duration(
        {
            index
            for index, assignment in enumerate(assignments)
            if assignment is not None
        },
        "Content selection",
    )
    validate_total_duration(non_reportable_positions, "Non-reportable selection")
    cursor = 0
    while cursor < len(segments):
        if assignments[cursor] is None:
            cursor += 1
            continue
        end = cursor
        while end + 1 < len(segments) and assignments[end + 1] is not None:
            end += 1
        nonblank_positions = [
            position
            for position in range(cursor, end + 1)
            if segments[position].text.strip()
        ]
        if nonblank_positions and (
            len(nonblank_positions) > MAX_CONTIGUOUS_EXCLUSION_SEGMENTS
            or segments[nonblank_positions[-1]].end
            - segments[nonblank_positions[0]].start
            > MAX_CONTIGUOUS_EXCLUSION_SECONDS
        ):
            raise ValueError(
                "Adjacent content exclusions form an overly broad deletion; keep the "
                f"passage by default: {segments[cursor].segment_id}–"
                f"{segments[end].segment_id}"
            )
        uncertain_positions = [
            position
            for position in range(cursor, end + 1)
            if normalized_exclusions[assignments[position]]["category"]
            in UNCERTAIN_EXCLUSION_CATEGORIES
        ]
        if uncertain_positions and (
            len(uncertain_positions) > MAX_UNCERTAIN_CATEGORY_SEGMENTS
            or segments[uncertain_positions[-1]].end
            - segments[uncertain_positions[0]].start
            > MAX_UNCERTAIN_CATEGORY_SECONDS
        ):
            raise ValueError(
                "Adjacent ambiguous exclusions form an overly broad deletion; keep "
                "the passage by default: "
                f"{segments[cursor].segment_id}–{segments[end].segment_id}"
            )
        cursor = end + 1

    ranges: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(segments):
        assignment = assignments[cursor]
        end = cursor
        while end + 1 < len(segments) and assignments[end + 1] == assignment:
            end += 1
        if assignment is None:
            decision = "included"
            category = "creator_content"
            reason = "校订字幕中的可报告作者内容"
        else:
            decision = "excluded"
            category = normalized_exclusions[assignment]["category"]
            reason = normalized_exclusions[assignment]["reason"]
        ranges.append(
            {
                "range_id": f"selection-{len(ranges) + 1:03d}",
                "segment_start": segments[cursor].segment_id,
                "segment_end": segments[end].segment_id,
                "timestamp_start": segments[cursor].start,
                "timestamp_end": segments[end].end,
                "decision": decision,
                "category": category,
                "reason": reason,
            }
        )
        cursor = end + 1

    included = [
        segment for index, segment in enumerate(segments) if assignments[index] is None
    ]
    if not included:
        raise ValueError("Content selection excluded the entire corrected transcript")
    filtered_content = _serialize_segments(included)
    _write_analysis_artifact(filtered_transcript_path, filtered_content)
    category_counts = Counter(
        normalized_exclusions[assignment]["category"]
        for assignment in assignments
        if assignment is not None
    )
    selection: dict[str, Any] = {
        "schema_version": 1,
        "video_id": video_id,
        "source_artifact": source_artifact,
        "source_sha256": sha256_file(transcript_path),
        "filtered_artifact": "transcript.report.jsonl",
        "filtered_sha256": sha256_file(filtered_transcript_path),
        "segment_count": len(segments),
        "included_segment_count": len(included),
        "excluded_segment_count": len(segments) - len(included),
        "blank_segment_count": sum(not segment.text.strip() for segment in segments),
        "excluded_category_counts": dict(sorted(category_counts.items())),
        "selection_policy": {
            "default_decision_when_uncertain": "included",
            "required_exclusion_certainty": "high",
            "middle_of_video_default": "included",
            "edge_only_exclusion_categories": sorted(
                EDGE_ONLY_EXCLUSION_CATEGORIES
            ),
            "edge_window_seconds_per_side": edge_window_seconds,
            "maximum_edge_window_seconds_per_side": MAX_EDGE_EXCLUSION_SECONDS,
            "maximum_edge_window_fraction_per_side": 1 / 3,
            "maximum_contiguous_exclusion_segments": (
                MAX_CONTIGUOUS_EXCLUSION_SEGMENTS
            ),
            "maximum_contiguous_exclusion_seconds": (
                MAX_CONTIGUOUS_EXCLUSION_SECONDS
            ),
            "maximum_total_exclusion_fraction": MAX_TOTAL_EXCLUSION_FRACTION,
            "maximum_total_exclusion_duration_fraction": (
                MAX_TOTAL_EXCLUSION_FRACTION
            ),
        },
        "excluded_ranges": normalized_exclusions,
        "non_reportable_ranges": normalized_non_reportable,
        "ranges": ranges,
    }
    selection_content = (
        json.dumps(selection, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    _write_analysis_artifact(selection_path, selection_content)
    return selection
