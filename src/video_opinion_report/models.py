from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


VIDEO_MEANING_PROFILE = "video_meaning_v1"
VIDEO_FULL_PROFILE = "video_full_v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Stage(str, Enum):
    INGEST = "ingest"
    ANALYZE = "analyze"
    RESEARCH = "research"
    JUDGMENT = "judgment"
    DRAFT = "draft"
    FIDELITY_REVIEW = "fidelity_review"
    RENDER = "render"
    HTML_VALIDATE = "html_validate"
    COMPLETE = "complete"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


COMPLETED_STATUSES = {StageStatus.COMPLETED}


STAGE_PREREQUISITES: dict[Stage, tuple[Stage, ...]] = {
    Stage.INGEST: (),
    Stage.ANALYZE: (Stage.INGEST,),
    Stage.RESEARCH: (Stage.ANALYZE,),
    Stage.JUDGMENT: (Stage.RESEARCH,),
    Stage.DRAFT: (Stage.JUDGMENT,),
    Stage.FIDELITY_REVIEW: (Stage.DRAFT,),
    Stage.RENDER: (Stage.DRAFT, Stage.FIDELITY_REVIEW, Stage.JUDGMENT),
    Stage.HTML_VALIDATE: (Stage.RENDER,),
    Stage.COMPLETE: (Stage.HTML_VALIDATE,),
}

MEANING_STAGE_PREREQUISITES: dict[Stage, tuple[Stage, ...]] = {
    Stage.INGEST: (),
    Stage.ANALYZE: (Stage.INGEST,),
    Stage.RESEARCH: (Stage.ANALYZE,),
    Stage.JUDGMENT: (Stage.RESEARCH,),
    Stage.DRAFT: (Stage.JUDGMENT,),
    Stage.FIDELITY_REVIEW: (Stage.DRAFT,),
    Stage.RENDER: (Stage.ANALYZE,),
    Stage.HTML_VALIDATE: (Stage.RENDER,),
    Stage.COMPLETE: (Stage.HTML_VALIDATE,),
}


def _depends_on(
    stage: Stage,
    ancestor: Stage,
    prerequisites: dict[Stage, tuple[Stage, ...]],
) -> bool:
    direct = prerequisites[stage]
    return ancestor in direct or any(
        _depends_on(item, ancestor, prerequisites) for item in direct
    )


@dataclass(slots=True)
class StageRecord:
    status: str = StageStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    retryable: bool | None = None


@dataclass(slots=True)
class RunManifest:
    video_id: str
    source_url: str
    created_at: str
    updated_at: str
    stages: dict[str, StageRecord] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        video_id: str,
        source_url: str,
        *,
        workflow_profile: str = VIDEO_MEANING_PROFILE,
    ) -> "RunManifest":
        now = utc_now()
        return cls(
            video_id=video_id,
            source_url=source_url,
            created_at=now,
            updated_at=now,
            stages={stage.value: StageRecord() for stage in Stage},
            metadata={"workflow_profile": workflow_profile},
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunManifest":
        stored_stages = value.get("stages", {})
        stages = {name: StageRecord(**record) for name, record in stored_stages.items()}
        for stage in Stage:
            stages.setdefault(stage.value, StageRecord())
        return cls(
            video_id=value["video_id"],
            source_url=value["source_url"],
            created_at=value["created_at"],
            updated_at=value["updated_at"],
            stages=stages,
            artifacts=dict(value.get("artifacts", {})),
            artifact_hashes=dict(value.get("artifact_hashes", {})),
            metadata=dict(value.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_complete(self, stage: Stage) -> bool:
        return self.stages[stage.value].status in COMPLETED_STATUSES

    @property
    def workflow_profile(self) -> str:
        value = str(self.metadata.get("workflow_profile") or "")
        return value or VIDEO_FULL_PROFILE

    @property
    def is_meaning_report(self) -> bool:
        return self.workflow_profile == VIDEO_MEANING_PROFILE

    def stage_prerequisites(self, stage: Stage) -> tuple[Stage, ...]:
        prerequisites = (
            MEANING_STAGE_PREREQUISITES
            if self.is_meaning_report
            else STAGE_PREREQUISITES
        )
        return prerequisites[stage]

    def require_completed(self, *stages: Stage) -> None:
        missing = [stage.value for stage in stages if not self.is_complete(stage)]
        if missing:
            raise RuntimeError(f"Required stages are not complete: {', '.join(missing)}")

    def start(self, stage: Stage) -> None:
        if self.is_complete(stage):
            raise RuntimeError(f"Stage is already complete: {stage.value}")
        self.require_completed(*self.stage_prerequisites(stage))
        record = self.stages[stage.value]
        record.status = StageStatus.RUNNING
        record.started_at = utc_now()
        record.finished_at = None
        record.error = None
        record.retryable = None
        self.updated_at = utc_now()

    def restart(self, stage: Stage) -> None:
        self.require_completed(*self.stage_prerequisites(stage))
        now = utc_now()
        prerequisites = (
            MEANING_STAGE_PREREQUISITES
            if self.is_meaning_report
            else STAGE_PREREQUISITES
        )
        for candidate in Stage:
            if candidate != stage and _depends_on(candidate, stage, prerequisites):
                self.stages[candidate.value] = StageRecord()
        record = self.stages[stage.value]
        record.status = StageStatus.RUNNING
        record.started_at = now
        record.finished_at = None
        record.error = None
        record.retryable = None
        self.updated_at = now

    def complete(self, stage: Stage) -> None:
        record = self.stages[stage.value]
        if record.status != StageStatus.RUNNING:
            raise RuntimeError(f"Stage is not running: {stage.value}")
        record.status = StageStatus.COMPLETED
        record.finished_at = utc_now()
        record.error = None
        record.retryable = None
        self.updated_at = utc_now()

    def fail(self, stage: Stage, error: str, retryable: bool) -> None:
        record = self.stages[stage.value]
        record.status = StageStatus.FAILED
        record.finished_at = utc_now()
        record.error = error
        record.retryable = retryable
        self.updated_at = utc_now()
