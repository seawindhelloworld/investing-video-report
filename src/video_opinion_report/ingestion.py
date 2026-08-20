from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .integrity import sha256_file
from .models import Stage
from .store import ManifestStore, ProcessedReportStore, validate_video_id
from .transcript import read_jsonl, validate_segments


PACKAGE_TYPE = "video_transcript"
PACKAGE_SCHEMA_VERSION = 1

FILE_DESTINATIONS = {
    "transcript_jsonl": "transcript.jsonl",
    "transcript_markdown": "transcript.md",
    "transcript_srt": "transcript.srt",
    "transcript_corrected_jsonl": "transcript.corrected.jsonl",
    "transcript_corrected_markdown": "transcript.corrected.md",
    "transcript_corrected_srt": "transcript.corrected.srt",
    "transcript_corrections": "corrections.json",
    "transcript_validation": "validation.json",
}

CURRENT_REQUIRED_FILES = {
    "transcript_corrected_jsonl",
    "transcript_corrected_markdown",
    "transcript_corrections",
}
LEGACY_REQUIRED_FILES = set(FILE_DESTINATIONS)


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _package_file(package_root: Path, specification: object, key: str) -> Path:
    if not isinstance(specification, dict):
        raise ValueError(f"Invalid file entry in transcript package: {key}")
    relative = Path(str(specification.get("path") or ""))
    if not relative.as_posix() or relative.is_absolute():
        raise ValueError(f"Transcript package file must use a relative path: {key}")
    source = (package_root / relative).resolve()
    source.relative_to(package_root)
    if not source.is_file():
        raise FileNotFoundError(source)
    expected_hash = str(specification.get("sha256") or "")
    if len(expected_hash) != 64 or sha256_file(source) != expected_hash:
        raise ValueError(f"Transcript package checksum mismatch: {key}")
    return source


def _validate_correction_chain(
    video_id: str,
    original_path: Path,
    corrected_path: Path,
    corrections_path: Path,
) -> tuple[dict[str, Any], list[object]]:
    original = read_jsonl(original_path)
    corrected = read_jsonl(corrected_path)
    original_errors = validate_segments(original)
    corrected_errors = validate_segments(corrected)
    if original_errors:
        raise ValueError(f"Original transcript is invalid: {original_errors[0]}")
    if corrected_errors:
        raise ValueError(f"Corrected transcript is invalid: {corrected_errors[0]}")
    if len(original) != len(corrected):
        raise ValueError("Corrected transcript must preserve the original segment count")

    differences: dict[str, tuple[str, str]] = {}
    for before, after in zip(original, corrected, strict=True):
        if before.segment_id != after.segment_id:
            raise ValueError("Corrected transcript must preserve segment order and IDs")
        if abs(before.start - after.start) > 0.001 or abs(before.end - after.end) > 0.001:
            raise ValueError(f"Corrected transcript changed timestamps for {before.segment_id}")
        if before.text != after.text:
            differences[before.segment_id] = (before.text, after.text)

    corrections = _load_object(corrections_path)
    if corrections.get("schema_version") != 1 or corrections.get("video_id") != video_id:
        raise ValueError("Transcript correction log does not match the package video")
    entries = corrections.get("corrections")
    if not isinstance(entries, list):
        raise ValueError("Transcript correction log needs a corrections list")
    logged: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid transcript correction entry")
        segment_id = str(entry.get("segment_id") or "")
        if not segment_id or segment_id in logged or segment_id not in differences:
            raise ValueError(f"Correction log has no unique matching change: {segment_id}")
        before, after = differences[segment_id]
        if entry.get("before") != before or entry.get("after") != after:
            raise ValueError(f"Correction log text mismatch: {segment_id}")
        logged.add(segment_id)
    if logged != set(differences):
        raise ValueError("Changed transcript segments are missing from the correction log")

    unresolved = corrections.get("unresolved_terms") or []
    if not isinstance(unresolved, list):
        raise ValueError("Transcript correction log unresolved_terms must be a list")
    segment_ids = {segment.segment_id for segment in original}
    if any(
        not isinstance(item, dict)
        or str(item.get("segment_id") or "") not in segment_ids
        or not str(item.get("term") or "").strip()
        or not str(item.get("reason") or "").strip()
        for item in unresolved
    ):
        raise ValueError("Invalid unresolved transcript term")
    return corrections, unresolved


def _validate_exported_correction(
    video_id: str,
    corrected_path: Path,
    corrections_path: Path,
) -> tuple[dict[str, Any], list[object]]:
    corrected = read_jsonl(corrected_path)
    errors = validate_segments(corrected)
    if errors:
        raise ValueError(f"Corrected transcript is invalid: {errors[0]}")

    corrections = _load_object(corrections_path)
    if corrections.get("schema_version") != 1 or corrections.get("video_id") != video_id:
        raise ValueError("Transcript correction log does not match the package video")
    entries = corrections.get("corrections")
    if not isinstance(entries, list):
        raise ValueError("Transcript correction log needs a corrections list")

    corrected_by_id = {segment.segment_id: segment for segment in corrected}
    logged: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid transcript correction entry")
        segment_id = str(entry.get("segment_id") or "")
        if not segment_id or segment_id in logged or segment_id not in corrected_by_id:
            raise ValueError(f"Correction log has no unique matching segment: {segment_id}")
        if entry.get("after") != corrected_by_id[segment_id].text:
            raise ValueError(f"Correction log text mismatch: {segment_id}")
        if not str(entry.get("before") or "").strip() or not str(entry.get("reason") or "").strip():
            raise ValueError(f"Correction log entry is incomplete: {segment_id}")
        logged.add(segment_id)

    unresolved = corrections.get("unresolved_terms") or []
    if not isinstance(unresolved, list):
        raise ValueError("Transcript correction log unresolved_terms must be a list")
    if any(
        not isinstance(item, dict)
        or str(item.get("segment_id") or "") not in corrected_by_id
        or not str(item.get("term") or "").strip()
        or not str(item.get("reason") or "").strip()
        for item in unresolved
    ):
        raise ValueError("Invalid unresolved transcript term")
    return corrections, unresolved


def _validate_quality_summary(
    payload: dict[str, Any],
    corrections: dict[str, Any],
    unresolved: list[object],
) -> dict[str, Any]:
    quality = payload.get("quality")
    if not isinstance(quality, dict):
        raise ValueError("Transcript package is missing quality validation summary")
    coverage = quality.get("coverage_ratio")
    maximum_gap = quality.get("maximum_gap_seconds")
    if not isinstance(coverage, (int, float)) or coverage < 0.95 or coverage > 1:
        raise ValueError("Transcript package quality validation did not pass: coverage ratio")
    if not isinstance(maximum_gap, (int, float)) or maximum_gap < 0 or maximum_gap > 5:
        raise ValueError("Transcript package quality validation did not pass: maximum gap")
    if quality.get("correction_count") != len(corrections.get("corrections") or []):
        raise ValueError("Transcript package correction count does not match correction log")
    if quality.get("unresolved_term_count") != len(unresolved):
        raise ValueError("Transcript package unresolved term count does not match correction log")
    return quality


def _copy_without_overwriting_different(source: Path, target: Path) -> None:
    if target.exists():
        if not target.is_file() or sha256_file(target) != sha256_file(source):
            raise RuntimeError(f"Refusing to overwrite a different imported transcript: {target}")
        return
    shutil.copy2(source, target)


def import_transcript_package(project_root: Path, package: Path) -> str:
    package_argument = package.expanduser().resolve()
    package_manifest = package_argument / "package.json" if package_argument.is_dir() else package_argument
    package_root = package_manifest.parent.resolve()
    payload = _load_object(package_manifest)
    if payload.get("package_type") != PACKAGE_TYPE:
        raise ValueError(f"Unsupported package_type: {payload.get('package_type')}")
    if payload.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ValueError("Transcript package must use schema_version 1")

    video = payload.get("video")
    if not isinstance(video, dict):
        raise ValueError("Transcript package is missing video metadata")
    video_id = validate_video_id(str(video.get("video_id") or ""))
    source_url = str(video.get("source_url") or "").strip()
    if not source_url:
        raise ValueError("Transcript package is missing source_url")

    files = payload.get("files")
    if not isinstance(files, dict) or not CURRENT_REQUIRED_FILES <= set(files):
        missing = sorted(
            CURRENT_REQUIRED_FILES - set(files)
            if isinstance(files, dict)
            else CURRENT_REQUIRED_FILES
        )
        raise ValueError(f"Transcript package is missing files: {', '.join(missing)}")
    package_files = (
        LEGACY_REQUIRED_FILES
        if LEGACY_REQUIRED_FILES <= set(files)
        else CURRENT_REQUIRED_FILES
    )
    resolved_files = {
        key: _package_file(package_root, files[key], key) for key in package_files
    }

    if package_files == LEGACY_REQUIRED_FILES:
        corrections, unresolved = _validate_correction_chain(
            video_id,
            resolved_files["transcript_jsonl"],
            resolved_files["transcript_corrected_jsonl"],
            resolved_files["transcript_corrections"],
        )
        validation = _load_object(resolved_files["transcript_validation"])
        if validation.get("valid") is not True:
            raise ValueError("Transcript package quality validation did not pass")
        quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
        contract = "legacy-eight-file"
    else:
        corrections, unresolved = _validate_exported_correction(
            video_id,
            resolved_files["transcript_corrected_jsonl"],
            resolved_files["transcript_corrections"],
        )
        quality = _validate_quality_summary(payload, corrections, unresolved)
        contract = "current-four-file"

    store = ManifestStore(project_root)
    if ProcessedReportStore(project_root).contains(video_id):
        raise RuntimeError(f"Report is already processed: {video_id}")
    try:
        manifest = store.load(video_id)
        if manifest.source_url != source_url:
            raise RuntimeError("Existing report run belongs to a different source URL")
        if manifest.is_complete(Stage.INGEST):
            imported = (project_root / manifest.artifacts["transcript_package"]).resolve()
            imported.relative_to(project_root.resolve())
            if not imported.is_file():
                raise FileNotFoundError(imported)
            return video_id
    except FileNotFoundError:
        manifest = store.create(video_id, source_url)
    manifest.start(Stage.INGEST)
    store.save(manifest)
    try:
        destination = store.run_dir(video_id) / "transcript"
        destination.mkdir(parents=True, exist_ok=True)
        for key in package_files:
            filename = FILE_DESTINATIONS[key]
            target = destination / filename
            _copy_without_overwriting_different(resolved_files[key], target)
            manifest.artifacts[key] = store.relative(target)
        package_copy = destination / "package.json"
        _copy_without_overwriting_different(package_manifest, package_copy)
        manifest.artifacts["transcript_package"] = store.relative(package_copy)
        manifest.metadata.update(
            {key: value for key, value in video.items() if key not in {"video_id", "source_url"}}
        )
        manifest.metadata["transcript_package_created_at"] = payload.get("created_at")
        manifest.metadata["transcript_correction_count"] = len(
            corrections.get("corrections") or []
        )
        manifest.metadata["transcript_unresolved_term_count"] = len(
            unresolved
        )
        manifest.metadata["transcript_package_contract"] = contract
        manifest.metadata["transcript_quality"] = quality
        if isinstance(payload.get("provenance"), dict):
            manifest.metadata["transcript_provenance"] = payload["provenance"]
        manifest.complete(Stage.INGEST)
        store.save(manifest)
    except Exception as exc:
        manifest.fail(Stage.INGEST, str(exc), retryable=False)
        store.save(manifest)
        raise
    return video_id
