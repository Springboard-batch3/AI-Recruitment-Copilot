from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.interview_blueprint import PolicyVersionReference
from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.phase3_enums import SimulationLifecycle

SIMULATION_SCHEMA_VERSION: str = "1.0.0"


class QuestionResponseRecord(FrozenSchema):
    """Immutable record of candidate's verbal or written response to an interview question."""
    response_id: UUID
    question_id: UUID
    response_text: str = Field(min_length=1, max_length=10000)
    duration_seconds: Optional[int] = Field(default=None, ge=1)
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_invariants(self) -> "QuestionResponseRecord":
        verify_no_prohibited_keys(self.model_dump())
        return self


class ProvisionalRubricEvaluation(FrozenSchema):
    """Provisional structured observation note produced for human reviewer evaluation."""
    evaluation_id: UUID
    question_id: UUID
    provisional_score: Optional[int] = Field(default=None, ge=1, le=5)
    evidence_notes: List[str] = Field(default_factory=list)
    flagged_for_review: bool = True
    ai_assisted: bool = True


class FinalRubricEvaluation(FrozenSchema):
    """Authoritative, human-assigned rating for an interview question."""
    evaluation_id: UUID
    question_id: UUID
    assigned_score: int = Field(ge=1, le=5)
    evaluator_notes: str = Field(min_length=5, max_length=4000)
    evaluator_user_id: UUID
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CandidateSimulationSession(FrozenSchema):
    """Immutable candidate simulation and interview session container."""
    session_id: UUID
    blueprint_id: UUID
    blueprint_version: int = Field(ge=1)
    candidate_profile_id: Optional[UUID] = None
    candidate_profile_version: Optional[int] = Field(default=None, ge=1)
    lifecycle_status: SimulationLifecycle = SimulationLifecycle.NOT_STARTED
    responses: List[QuestionResponseRecord] = Field(default_factory=list)
    provisional_evaluations: List[ProvisionalRubricEvaluation] = Field(default_factory=list)
    final_evaluations: List[FinalRubricEvaluation] = Field(default_factory=list)
    policy_reference: PolicyVersionReference = Field(default_factory=PolicyVersionReference)
    schema_version: str = Field(default=SIMULATION_SCHEMA_VERSION)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_session_invariants(self) -> "CandidateSimulationSession":
        verify_no_prohibited_keys(self.model_dump())
        if self.lifecycle_status == SimulationLifecycle.FINALIZED:
            if not self.final_evaluations:
                raise ValueError("A simulation session cannot be FINALIZED without authoritative human evaluations.")
        return self
