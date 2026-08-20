from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .ingestion import import_transcript_package
from .models import Stage
from .reporting import (
    build_structured_artifacts,
    render_markdown_report,
    validate_rendered_report,
    validate_report_layers,
)
from .store import ManifestStore, ProcessedReportStore


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
    artifacts = getattr(manifest, "artifacts")
    if key not in artifacts:
        raise FileNotFoundError(f"Manifest artifact is missing: {key}")
    path = (root / artifacts[key]).resolve()
    path.relative_to(root.resolve())
    if not path.exists():
        raise FileNotFoundError(path)
    return path


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



def record_analysis(root: Path, video_id: str, analysis_path: Path, opinions_path: Path) -> None:
    store = ManifestStore(root)
    manifest = store.load(video_id)
    stage = Stage.ANALYZE
    _artifact_path(root, manifest, "transcript_corrected_jsonl")
    analysis_file = analysis_path.expanduser().resolve()
    opinions_file = opinions_path.expanduser().resolve()
    already_complete = manifest.is_complete(stage)
    if already_complete and {"video_analysis", "opinions"} <= set(manifest.artifacts):
        _artifact_path(root, manifest, "video_analysis")
        _artifact_path(root, manifest, "opinions")
        return
    if not already_complete:
        manifest.start(stage)
        store.save(manifest)
    try:
        analysis = _load_object(analysis_file)
        if str(analysis.get("video_id")) != video_id:
            raise ValueError("video-analysis video_id does not match the run")
        opinions = [
            json.loads(line)
            for line in opinions_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not opinions:
            raise ValueError("opinions.jsonl is empty")
        identifiers = [str(item.get("opinion_id") or "") for item in opinions]
        if any(not value for value in identifiers) or len(set(identifiers)) != len(identifiers):
            raise ValueError("Opinion IDs must be present and unique")
        for item in opinions:
            start = float(item["timestamp_start"])
            end = float(item["timestamp_end"])
            if start < 0 or end <= start or not str(item.get("faithful_paraphrase") or "").strip():
                raise ValueError(f"Invalid traceability fields for {item.get('opinion_id')}")
        manifest.artifacts["video_analysis"] = store.relative(analysis_file)
        manifest.artifacts["opinions"] = store.relative(opinions_file)
        manifest.metadata["opinion_count"] = len(opinions)
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
        source_count = 0
        for path in files:
            topic = _load_object(path)
            if not str(topic.get("topic_id") or ""):
                raise ValueError(f"Research topic has no topic_id: {path}")
            assessments = topic.get("assessments")
            sources = topic.get("sources")
            if not isinstance(assessments, list) or not isinstance(sources, list) or not sources:
                raise ValueError(f"Research topic needs assessments and sources: {path}")
            for assessment in assessments:
                if not isinstance(assessment, dict):
                    raise ValueError(f"Invalid assessment in {path}")
                opinion_id = str(assessment.get("opinion_id") or "")
                if opinion_id in covered:
                    raise ValueError(f"Opinion assessed more than once: {opinion_id}")
                if not assessment.get("applicable_conditions"):
                    raise ValueError(f"Assessment lacks applicable conditions: {opinion_id}")
                if "counterevidence" not in assessment:
                    raise ValueError(f"Assessment lacks counterevidence: {opinion_id}")
                covered.add(opinion_id)
            for source in sources:
                if not isinstance(source, dict):
                    raise ValueError(f"Invalid research source: {path}")
                if not str(source.get("url") or "").startswith(("https://", "http://")):
                    raise ValueError(f"Research source has an invalid URL: {path}")
                missing_fields = [
                    field
                    for field in ("title", "publisher", "published_at", "accessed_at")
                    if not source.get(field)
                ]
                if missing_fields:
                    raise ValueError(
                        f"Research source lacks {', '.join(missing_fields)}: {path}"
                    )
            source_count += len(sources)
        if covered != expected:
            missing = sorted(expected - covered)
            extra = sorted(covered - expected)
            raise ValueError(f"Research opinion coverage mismatch; missing={missing}, extra={extra}")
        manifest.artifacts["research_dir"] = store.relative(directory)
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
        if str(judgment.get("video_id") or "") != video_id:
            raise ValueError("Agent judgment video_id does not match the run")
        disclaimer = str(judgment.get("disclaimer") or "")
        if "不代表视频作者观点" not in disclaimer or "不构成投资建议" not in disclaimer:
            raise ValueError("Agent judgment disclaimer must separate authorship and investment advice")
        if not str(judgment.get("source_as_of") or ""):
            raise ValueError("Agent judgment needs source_as_of")
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
                values = topic[field]
                if not isinstance(values, list) or not values:
                    raise ValueError(f"Agent judgment topic {topic_id} needs a non-empty {field} list")
            for url in topic["source_urls"]:
                if not str(url).startswith(("https://", "http://")):
                    raise ValueError(f"Agent judgment topic {topic_id} has an invalid source URL")
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
        manifest.artifacts["agent_judgment"] = store.relative(judgment_file)
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
    markdown_file = markdown_path.expanduser().resolve()
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
        manifest.artifacts["draft_markdown"] = store.relative(markdown_file)
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
        if review.get("external_research_visible_to_reviewer") is True:
            raise ValueError("First fidelity review must not see external research")
        verdict = str(review.get("post_revision_verdict") or review.get("overall_verdict") or "")
        if verdict not in {"passed", "passed_with_asr_caveats"}:
            raise ValueError(f"Fidelity review did not pass: {verdict or '<missing>'}")
        unresolved = review.get("unresolved_transcript_checks")
        if unresolved is None:
            unresolved = review.get("unresolved_audio_checks") or []
        if not isinstance(unresolved, list):
            raise ValueError("unresolved_transcript_checks must be a list")
        manifest.artifacts["fidelity_review"] = store.relative(review_file)
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
        output_file = output_path.expanduser().resolve()
        if not markdown_file.is_file():
            raise FileNotFoundError(markdown_file)
        if not template_file.is_file():
            raise FileNotFoundError(template_file)
        layer_counts = validate_report_layers(markdown_file.read_text(encoding="utf-8"))
        render_markdown_report(markdown_file, template_file, output_file)
        manifest.artifacts["report_markdown"] = store.relative(markdown_file)
        manifest.artifacts["report_html"] = store.relative(output_file)
        manifest.metadata.update(layer_counts)
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
    report_data = arguments.report_data.expanduser().resolve()
    citations = arguments.citations.expanduser().resolve()
    counts = build_structured_artifacts(
        arguments.video_analysis.expanduser().resolve(),
        arguments.opinions.expanduser().resolve(),
        arguments.research_dir.expanduser().resolve(),
        arguments.agent_judgment.expanduser().resolve(),
        arguments.fidelity_review.expanduser().resolve(),
        report_data,
        citations,
    )
    manifest.artifacts["report_data"] = store.relative(report_data)
    manifest.artifacts["citations"] = store.relative(citations)
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
        if str(validation.get("status") or "").lower() != "passed":
            raise ValueError("HTML validation status must be 'passed'")
        if _contains_pending(validation):
            raise ValueError("HTML validation still contains pending checks")
        report_html = _artifact_path(root, manifest, "report_html")
        document = report_html.read_text(encoding="utf-8")
        if len(document) < 200 or "{{REPORT_BODY}}" in document:
            raise ValueError("Rendered HTML is incomplete")
        manifest.metadata.update(validate_rendered_report(report_html, root))
        manifest.artifacts["html_validation"] = store.relative(validation_file)
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
    _artifact_path(root, manifest, "transcript_corrected_jsonl")
    _artifact_path(root, manifest, "transcript_corrections")
    agent_judgment_path = _artifact_path(root, manifest, "agent_judgment")
    agent_judgment = _load_object(agent_judgment_path)
    report_data_path = _artifact_path(root, manifest, "report_data")
    report_data = _load_object(report_data_path)
    try:
        schema_version = int(report_data.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("report-data.json has an invalid schema_version") from exc
    if schema_version < 2:
        raise ValueError("report-data.json must use schema version 2 or newer")
    if report_data.get("agent_judgment") != agent_judgment:
        raise ValueError("report-data.json contains a stale or mismatched Agent judgment")
    report_markdown_path = _artifact_path(root, manifest, "report_markdown")
    validate_report_layers(report_markdown_path.read_text(encoding="utf-8"))
    for key in ("report_markdown", "report_html", "report_data", "citations", "html_validation"):
        _artifact_path(root, manifest, key)
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
