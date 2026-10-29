import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from app.models.matching import MatchRunLifecycleStatus
from app.schemas.candidate import StrictSchema
from app.schemas.matching import verify_no_prohibited_keys


class CandidateProfileSelection(StrictSchema):
    profile_id: uuid.UUID
    profile_version: int = Field(ge=1)


class MatchRunCreateRequest(StrictSchema):
    job_id: uuid.UUID
    expected_revision: int = Field(ge=1)
    candidates: List[CandidateProfileSelection] = Field(min_length=1)
    evaluation_date: Optional[datetime] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_request(self) -> "MatchRunCreateRequest":
        # Check for duplicate candidate profiles within the request
        seen = set()
        for cand in self.candidates:
            key = (cand.profile_id, cand.profile_version)
            if key in seen:
                raise ValueError(
                    f"Duplicate candidate profile selection ({cand.profile_id}, v{cand.profile_version}) in request."
                )
            seen.add(key)
        return self


class MatchRunSummaryResponse(StrictSchema):
    run_id: uuid.UUID
    lifecycle_status: MatchRunLifecycleStatus
    job_id: uuid.UUID
    job_version_id: uuid.UUID
    revision: int
    candidate_count: int
    matcher_algorithm_version: str
    matching_rule_version: str
    scoring_policy_version: str
    match_input_schema_version: str
    input_fingerprint: str
    candidate_input_fingerprint: str
    reused: bool = False
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    results_url: str

    @model_validator(mode="after")
    def validate_privacy(self) -> "MatchRunSummaryResponse":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class MatchRunDetailResponse(StrictSchema):
    run_id: uuid.UUID
    lifecycle_status: MatchRunLifecycleStatus
    job_id: uuid.UUID
    job_version_id: uuid.UUID
    revision: int
    candidate_count: int
    matcher_algorithm_version: str
    matching_rule_version: str
    scoring_policy_version: str
    match_input_schema_version: str
    input_fingerprint: str
    candidate_input_fingerprint: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    results_url: str

    @model_validator(mode="after")
    def validate_privacy(self) -> "MatchRunDetailResponse":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class MatchResultRankingItemResponse(StrictSchema):
    result_id: uuid.UUID
    run_id: uuid.UUID
    profile_id: uuid.UUID
    profile_version: int
    rank: int
    is_tied: bool
    tie_group_id: Optional[str] = None
    raw_compatibility_score: float
    display_compatibility_score: float
    confidence_tier: str
    warning_count: int
    must_have_warning_count: int
    must_have_total_count: int = 0
    must_have_evidenced_count: int = 0
    not_evidenced_count: int = 0
    needs_confirmation_count: int = 0
    evidence_summary: Optional[Any] = None
    component_scores: Dict[str, Any]
    confidence_diagnostics: Dict[str, Any]
    must_have_warnings: List[Dict[str, Any]]
    general_warnings: List[Dict[str, Any]]
    requirement_contributions: Optional[List[Dict[str, Any]]] = None
    detail_url: str

    @model_validator(mode="after")
    def validate_privacy(self) -> "MatchResultRankingItemResponse":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class PaginatedMatchResultsResponse(StrictSchema):
    items: List[MatchResultRankingItemResponse]
    total: int
    limit: int
    offset: int
    run_id: uuid.UUID
    job_id: uuid.UUID
    revision: int

    @model_validator(mode="after")
    def validate_privacy(self) -> "PaginatedMatchResultsResponse":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class MatchResultDetailResponse(StrictSchema):
    result_id: uuid.UUID
    run_id: uuid.UUID
    profile_id: uuid.UUID
    profile_version: int
    rank: int
    is_tied: bool
    tie_group_id: Optional[str] = None
    raw_compatibility_score: float
    display_compatibility_score: float
    confidence_tier: str
    warning_count: int
    must_have_warning_count: int
    evidence_summary: Optional[Any] = None
    component_scores: Dict[str, Any]
    confidence_diagnostics: Dict[str, Any]
    must_have_warnings: List[Dict[str, Any]]
    general_warnings: List[Dict[str, Any]]
    score_decomposition: Dict[str, Any]
    created_at: datetime

    @model_validator(mode="after")
    def validate_privacy(self) -> "MatchResultDetailResponse":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self
