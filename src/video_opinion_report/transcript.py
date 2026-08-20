from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(slots=True)
class TranscriptSegment:
    segment_id: str
    start: float
    end: float
    text: str
    speaker: str | None = None
    confidence: float | None = None
    source: str = "asr"
    source_chunk: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TranscriptSegment":
        return cls(
            segment_id=str(value["segment_id"]),
            start=float(value["start"]),
            end=float(value["end"]),
            text=str(value["text"]),
            speaker=value.get("speaker"),
            confidence=value.get("confidence"),
            source=str(value.get("source", "asr")),
            source_chunk=value.get("source_chunk"),
        )


def validate_segments(segments: Iterable[TranscriptSegment]) -> list[str]:
    items = list(segments)
    errors: list[str] = []
    if not items:
        return ["Transcript is empty"]

    previous_start = -1.0
    identifiers: set[str] = set()
    for index, segment in enumerate(items):
        label = segment.segment_id or f"index {index}"
        if label in identifiers:
            errors.append(f"{label}: segment ID is duplicated")
        identifiers.add(label)
        if not math.isfinite(segment.start) or not math.isfinite(segment.end):
            errors.append(f"{label}: timestamps must be finite")
            continue
        if segment.start < 0:
            errors.append(f"{label}: start must be non-negative")
        if segment.end <= segment.start:
            errors.append(f"{label}: end must be greater than start")
        if segment.start < previous_start:
            errors.append(f"{label}: timestamps are not monotonic")
        if not segment.text.strip():
            errors.append(f"{label}: text is empty")
        if segment.confidence is not None and (
            not math.isfinite(segment.confidence) or not 0 <= segment.confidence <= 1
        ):
            errors.append(f"{label}: confidence must be between 0 and 1")
        if segment.end - segment.start > 180:
            errors.append(f"{label}: segment is longer than 180 seconds")
        previous_start = segment.start
    return errors


def _normalized_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def transcript_metrics(
    segments: Iterable[TranscriptSegment],
    *,
    media_duration: float,
    min_coverage_ratio: float = 0.95,
    max_gap_seconds: float = 5.0,
    max_repeated_segments: int = 2,
    low_confidence_threshold: float = 0.75,
) -> dict[str, Any]:
    items = list(segments)
    errors = validate_segments(items)
    warnings: list[str] = []
    if media_duration <= 0 or not math.isfinite(media_duration):
        errors.append("Media duration must be a positive finite number")
    if not items or media_duration <= 0:
        return {
            "valid": False,
            "segment_count": len(items),
            "media_duration": media_duration,
            "covered_seconds": 0.0,
            "coverage_ratio": 0.0,
            "maximum_gap_seconds": media_duration if media_duration > 0 else None,
            "average_confidence": None,
            "low_confidence_segments": [],
            "risk_segments": [],
            "errors": errors,
            "warnings": warnings,
        }

    intervals = sorted(
        (max(0.0, item.start), min(media_duration, item.end))
        for item in items
        if item.end > 0 and item.start < media_duration
    )
    covered = 0.0
    cursor = 0.0
    gaps: list[tuple[float, float]] = []
    for start, end in intervals:
        if end <= start:
            continue
        if start > cursor:
            gaps.append((cursor, start))
        covered += max(0.0, end - max(start, cursor))
        cursor = max(cursor, end)
    if cursor < media_duration:
        gaps.append((cursor, media_duration))
    coverage_ratio = min(1.0, covered / media_duration)
    maximum_gap = max((end - start for start, end in gaps), default=0.0)
    if coverage_ratio < min_coverage_ratio:
        errors.append(
            f"Transcript coverage {coverage_ratio:.3%} is below {min_coverage_ratio:.3%}"
        )
    if maximum_gap > max_gap_seconds:
        errors.append(
            f"Maximum transcript gap {maximum_gap:.2f}s exceeds {max_gap_seconds:.2f}s"
        )

    repeated_runs: list[tuple[int, int, str]] = []
    run_start = 0
    for index in range(1, len(items) + 1):
        same = (
            index < len(items)
            and _normalized_text(items[index].text)
            and _normalized_text(items[index].text) == _normalized_text(items[index - 1].text)
        )
        if same:
            continue
        run_length = index - run_start
        if run_length > max_repeated_segments:
            repeated_runs.append((run_start, index - 1, items[run_start].text))
        run_start = index
    if repeated_runs:
        errors.append(
            f"Transcript contains {len(repeated_runs)} repeated segment run(s)"
        )

    confidences = [item.confidence for item in items if item.confidence is not None]
    average_confidence = sum(confidences) / len(confidences) if confidences else None
    low_confidence = [
        item.segment_id
        for item in items
        if item.confidence is not None and item.confidence < low_confidence_threshold
    ]
    if low_confidence:
        warnings.append(
            f"{len(low_confidence)} segment(s) are below confidence {low_confidence_threshold:.2f}"
        )

    low_confidence_ids = set(low_confidence)
    risk_segments: list[dict[str, Any]] = [
        {"start": start, "end": end, "reason": "transcript_gap"}
        for start, end in gaps
        if end - start > min(1.0, max_gap_seconds)
    ]
    risk_segments.extend(
        {
            "start": item.start,
            "end": item.end,
            "segment_id": item.segment_id,
            "reason": "low_confidence",
        }
        for item in items
        if item.segment_id in low_confidence_ids
    )
    return {
        "valid": not errors,
        "segment_count": len(items),
        "media_duration": media_duration,
        "start": items[0].start,
        "end": items[-1].end,
        "covered_seconds": covered,
        "coverage_ratio": coverage_ratio,
        "maximum_gap_seconds": maximum_gap,
        "average_confidence": average_confidence,
        "low_confidence_segments": low_confidence,
        "repeated_runs": [
            {"start_index": start, "end_index": end, "text": text}
            for start, end, text in repeated_runs
        ],
        "risk_segments": risk_segments,
        "errors": errors,
        "warnings": warnings,
    }


def read_jsonl(path: Path) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            segments.append(TranscriptSegment.from_dict(json.loads(line)))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid transcript line {line_number}: {exc}") from exc
    return segments


def write_jsonl(path: Path, segments: Iterable[TranscriptSegment]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(asdict(segment), ensure_ascii=False) + "\n" for segment in segments
    )
    path.write_text(content, encoding="utf-8")


def format_clock(seconds: float, *, srt: bool = False) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    separator = "," if srt else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def write_markdown(path: Path, segments: Iterable[TranscriptSegment]) -> None:
    lines = ["# 视频转录", ""]
    for segment in segments:
        speaker = f" **{segment.speaker}**" if segment.speaker else ""
        lines.append(
            f"- `{format_clock(segment.start)}–{format_clock(segment.end)}`{speaker}  "
        )
        lines.append(f"  {segment.text.strip()}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_srt(path: Path, segments: Iterable[TranscriptSegment]) -> None:
    blocks: list[str] = []
    for index, segment in enumerate(segments, 1):
        text = segment.text.strip()
        if segment.speaker:
            text = f"{segment.speaker}: {text}"
        blocks.append(
            f"{index}\n{format_clock(segment.start, srt=True)} --> "
            f"{format_clock(segment.end, srt=True)}\n{text}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
