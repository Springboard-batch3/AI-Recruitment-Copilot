from typing import Optional
from app.schemas.phase3_enums import (
    ATSExportLifecycle,
    PlanLifecycle,
    SimulationLifecycle,
)


class InvalidLifecycleTransitionError(ValueError):
    """Raised when an illegal lifecycle state transition is attempted."""
    def __init__(self, from_state: str, to_state: str, entity_type: str, reason: Optional[str] = None):
        msg = f"Invalid {entity_type} lifecycle transition from '{from_state}' to '{to_state}'."
        if reason:
            msg += f" Reason: {reason}"
        super().__init__(msg)
        self.from_state = from_state
        self.to_state = to_state
        self.entity_type = entity_type


# 1. Plan / Interview Blueprint Lifecycle Transitions
ALLOWED_PLAN_TRANSITIONS: dict[PlanLifecycle, set[PlanLifecycle]] = {
    PlanLifecycle.DRAFT: {PlanLifecycle.VALIDATED},
    PlanLifecycle.VALIDATED: {PlanLifecycle.DRAFT, PlanLifecycle.APPROVED},
    PlanLifecycle.APPROVED: {PlanLifecycle.IN_USE, PlanLifecycle.SUPERSEDED},
    PlanLifecycle.IN_USE: {PlanLifecycle.SUPERSEDED},
    PlanLifecycle.SUPERSEDED: set(),  # Terminal state
}


def transition_plan_lifecycle(current: PlanLifecycle, target: PlanLifecycle) -> PlanLifecycle:
    """Validates and applies an interview plan lifecycle transition."""
    allowed = ALLOWED_PLAN_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidLifecycleTransitionError(
            from_state=current.value,
            to_state=target.value,
            entity_type="PlanLifecycle",
            reason=f"Approved or in-use plans cannot be directly modified or skipped. Allowed targets: {[s.value for s in allowed]}"
        )
    return target


# 2. Simulation Lifecycle Transitions
ALLOWED_SIMULATION_TRANSITIONS: dict[SimulationLifecycle, set[SimulationLifecycle]] = {
    SimulationLifecycle.NOT_STARTED: {SimulationLifecycle.IN_PROGRESS},
    SimulationLifecycle.IN_PROGRESS: {SimulationLifecycle.SUBMITTED},
    SimulationLifecycle.SUBMITTED: {SimulationLifecycle.PROVISIONALLY_ASSESSED},
    SimulationLifecycle.PROVISIONALLY_ASSESSED: {SimulationLifecycle.HUMAN_REVIEWED},
    SimulationLifecycle.HUMAN_REVIEWED: {SimulationLifecycle.FINALIZED},
    SimulationLifecycle.FINALIZED: set(),  # Terminal state
}


def transition_simulation_lifecycle(current: SimulationLifecycle, target: SimulationLifecycle) -> SimulationLifecycle:
    """Validates and applies a candidate simulation lifecycle transition."""
    allowed = ALLOWED_SIMULATION_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidLifecycleTransitionError(
            from_state=current.value,
            to_state=target.value,
            entity_type="SimulationLifecycle",
            reason=f"Candidate simulation must follow sequential human-review gate. Allowed targets: {[s.value for s in allowed]}"
        )
    return target


# 3. ATS Export Lifecycle Transitions
ALLOWED_ATS_TRANSITIONS: dict[ATSExportLifecycle, set[ATSExportLifecycle]] = {
    ATSExportLifecycle.DRAFT: {ATSExportLifecycle.PREVIEWED},
    ATSExportLifecycle.PREVIEWED: {ATSExportLifecycle.CONFIRMED, ATSExportLifecycle.DRAFT},
    ATSExportLifecycle.CONFIRMED: {ATSExportLifecycle.SENDING},
    ATSExportLifecycle.SENDING: {
        ATSExportLifecycle.SENT,
        ATSExportLifecycle.RETRYABLE_FAILURE,
        ATSExportLifecycle.PERMANENT_FAILURE,
        ATSExportLifecycle.RECONCILIATION_REQUIRED,
    },
    ATSExportLifecycle.RETRYABLE_FAILURE: {ATSExportLifecycle.SENDING, ATSExportLifecycle.PERMANENT_FAILURE},
    ATSExportLifecycle.PERMANENT_FAILURE: set(),  # Terminal state
    ATSExportLifecycle.SENT: set(),  # Terminal state
    ATSExportLifecycle.RECONCILIATION_REQUIRED: {ATSExportLifecycle.SENT, ATSExportLifecycle.PERMANENT_FAILURE},
}


def transition_ats_export_lifecycle(current: ATSExportLifecycle, target: ATSExportLifecycle) -> ATSExportLifecycle:
    """Validates and applies an ATS export job lifecycle transition."""
    allowed = ALLOWED_ATS_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidLifecycleTransitionError(
            from_state=current.value,
            to_state=target.value,
            entity_type="ATSExportLifecycle",
            reason=f"ATS export requires recruiter preview and confirmation before transmission. Allowed targets: {[s.value for s in allowed]}"
        )
    return target
