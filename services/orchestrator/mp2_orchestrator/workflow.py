"""AnalyzeMediaWorkflow.

The activity boundaries are fixed now, including the ones deferred past M4, so that later
milestones extend the pipeline rather than reshape it. Workflow inputs carry IDs and
version references only; media bytes never enter Temporal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@dataclass
class AnalyzeMediaInput:
    analysis_run_id: str
    source_asset_id: str
    contract_version: int


# (activity name, task queue, start-to-close timeout minutes)
ACTIVITIES: tuple[tuple[str, str, int], ...] = (
    ("ValidateSource", "mp2-io", 5),
    ("ProbeAndHash", "mp2-io", 15),
    ("NormalizeAsset", "mp2-cpu", 30),
    ("SegmentShots", "mp2-cpu", 60),
    ("ExtractVisual", "mp2-cpu", 120),
    ("ExtractAudio", "mp2-cpu", 60),
    ("Transcribe", "mp2-cpu", 120),
    ("BuildScenePackets", "mp2-cpu", 30),
    ("InterpretSemantics", "mp2-semantic", 120),
    ("AssembleEvidence", "mp2-cpu", 15),
    # --- M0-M4 stops at evidence readiness. Boundaries below are declared, not implemented.
    ("CalculateGenome", "mp2-cpu", 30),
    ("BuildProjections", "mp2-cpu", 30),
    ("ValidateAnalysis", "mp2-cpu", 15),
    ("PublishVersion", "mp2-review", 15),
)

EVIDENCE_READY_BOUNDARY = 10

RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=5,
)


@workflow.defn
class AnalyzeMediaWorkflow:
    def __init__(self) -> None:
        self._state: dict[str, Any] = {}

    @workflow.query(name="progress")
    def progress(self) -> dict[str, Any]:
        """Queryable progress, so a long analysis can be inspected without polling the DB."""
        return {
            "completed": self._state.get("completed", []),
            "warnings": self._state.get("warnings", []),
            "segment_count": len(self._state.get("segment_ids", [])),
        }

    @workflow.run
    async def run(self, value: AnalyzeMediaInput) -> dict[str, Any]:
        self._state = {
            "analysis_run_id": value.analysis_run_id,
            "source_asset_id": value.source_asset_id,
            "contract_version": value.contract_version,
        }

        try:
            for name, queue, timeout_minutes in ACTIVITIES[:EVIDENCE_READY_BOUNDARY]:
                self._state = await workflow.execute_activity(
                    name, self._state, task_queue=queue,
                    start_to_close_timeout=timedelta(minutes=timeout_minutes),
                    retry_policy=RETRY,
                )
        except Exception as exc:
            # Record terminal failure against the run so the API reflects reality.
            await workflow.execute_activity(
                "MarkRunFailed",
                {"analysis_run_id": value.analysis_run_id, "error": repr(exc)[:500]},
                task_queue="mp2-io",
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            raise

        return {
            **self._state,
            "status": "evidence_ready",
            "deferred_activities": [a[0] for a in ACTIVITIES[EVIDENCE_READY_BOUNDARY:]],
        }
