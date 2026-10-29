import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import Field

from app.schemas.matching import FrozenSchema
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    QuestionType,
    ValidationState,
)


class InterviewRubricAnchorResponse(FrozenSchema):
    point: int
    description: str


class InterviewRubricResponse(FrozenSchema):
    rubric_id: uuid.UUID
    rubric_version: int
    competency_definition: str
    question_intent: str
    observable_indicators: List[str]
    rating_anchors: Dict[int, str]
    sufficient_evidence_guidance: str
    insufficient_evidence_guidance: str
    incomplete_answer_guidance: Optional[str] = None
    permitted_probes: List[str]
    prohibited_inferences: List[str]
    subject_matter_review_status: str
    validation_state: ValidationState


class InterviewQuestionResponse(FrozenSchema):
    question_id: uuid.UUID
    question_version: int = 1
    layer: InterviewLayer
    question_type: QuestionType
    competency_id: Optional[str] = None
    requirement_references: List[uuid.UUID]
    deterministic_template_id: Optional[str] = None
    deterministic_template_version: Optional[str] = None
    question_text: str
    question_intent: str
    authoring_origin: AuthoringOrigin
    display_order: int
    estimated_time_minutes: int
    permitted_probes: List[str]
    validation_state: ValidationState
    included_in_common_score: bool
    content_hash: Optional[str] = None
    deterministic_source_text: Optional[str] = None
    deterministic_source_available: bool = False
    has_wording_drift: bool = False
    can_restore: bool = False
    restore_unavailable_reason: Optional[str] = None
    ai_suggestion_text: Optional[str] = None
    ai_suggestion_available: bool = False
    ai_suggestion_review_state: Optional[str] = None
    safe_fallback_reason: Optional[str] = None
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)
    rubric: Optional[InterviewRubricResponse] = None



class RequirementClassificationResponse(FrozenSchema):
    requirement_id: uuid.UUID
    label: str
    category: str
    suitability: str
    reason: str
    mapped_competency_key: Optional[str] = None


class CompetencySelectionResponse(FrozenSchema):
    competency_key: str
    competency_label: str
    priority_score: float
    selection_reason: str
    covered_requirement_ids: List[uuid.UUID]


class BlueprintCoverageResponse(FrozenSchema):
    total_requirements_count: int
    interview_eligible_requirements_count: int
    covered_requirements_count: int
    selected_competencies: List[CompetencySelectionResponse]
    uncovered_eligible_competencies: List[str]
    unsupported_competencies: List[str]
    document_verification_requirements: List[RequirementClassificationResponse]
    work_sample_requirements: List[RequirementClassificationResponse]
    out_of_interview_requirements: List[RequirementClassificationResponse]
    total_estimated_duration_minutes: int
    question_count_by_type: Dict[str, int]
    coverage_percentage: float
    builder_version: str
    question_catalogue_version: str
    rubric_catalogue_version: str


class BlueprintValidationReportResponse(FrozenSchema):
    is_valid: bool
    blocking_errors: List[str]
    warnings: List[str]
    checked_rules_count: int


class InterviewTemplatePreviewResponse(FrozenSchema):
    """Response DTO for deterministic interview template preview."""
    job_id: uuid.UUID
    job_version_id: uuid.UUID
    job_revision: int
    lifecycle_status: PlanLifecycle
    input_snapshot_hash: str
    content_hash: str
    common_core_questions: List[InterviewQuestionResponse]
    coverage_summary: BlueprintCoverageResponse
    validation_report: BlueprintValidationReportResponse
    builder_version: str
    question_catalogue_version: str
    rubric_catalogue_version: str
    policy_id: str
    policy_version: str
    policy_hash: str


class InterviewTemplateDraftCreateResponse(FrozenSchema):
    """Response DTO for created interview template draft."""
    template_id: uuid.UUID
    organization_id: uuid.UUID
    job_id: uuid.UUID
    job_version_id: uuid.UUID
    job_revision: int
    template_version: int
    lifecycle_status: PlanLifecycle
    input_snapshot_hash: str
    content_hash: str
    common_core_questions: List[InterviewQuestionResponse]
    coverage_summary: BlueprintCoverageResponse
    validation_report: BlueprintValidationReportResponse
    builder_version: str
    question_catalogue_version: str
    rubric_catalogue_version: str
    created_by_user_id: uuid.UUID
    created_at: datetime


# =========================================================================
# Candidate Interview Plan Schemas (Phase 3B.3)
# =========================================================================

class SupplementalQuestionGuidanceResponse(FrozenSchema):
    """Safe, non-scored guidance for an evidence clarification question."""
    clarification_purpose: str
    facts_to_confirm: List[str]
    prohibited_inferences: List[str]


class SupplementalQuestionProvenanceResponse(FrozenSchema):
    """Provenance references binding a supplemental question to authoritative match results."""
    source_requirement_id: uuid.UUID
    source_match_result_id: uuid.UUID
    source_match_state: str
    allowlisted_evidence_ids: List[str]
    excluded_from_common_score: bool = True


class CandidateSupplementalQuestionResponse(FrozenSchema):
    """Supplemental evidence clarification question DTO."""
    question_id: uuid.UUID
    layer: InterviewLayer = InterviewLayer.SUPPLEMENTAL_EVIDENCE
    question_type: QuestionType = QuestionType.EVIDENCE_CLARIFICATION
    authoring_origin: AuthoringOrigin = AuthoringOrigin.DETERMINISTIC
    display_order: int
    requirement_references: List[uuid.UUID]
    deterministic_template_id: str
    deterministic_template_version: str
    question_text: str
    question_intent: str
    estimated_time_minutes: int
    included_in_common_score: bool = False
    validation_state: ValidationState
    guidance: SupplementalQuestionGuidanceResponse
    provenance: SupplementalQuestionProvenanceResponse


class CandidateInterviewPlanSummaryResponse(FrozenSchema):
    """Safe summary of the bound common-core template."""
    template_id: uuid.UUID
    template_version: int
    template_content_hash: str
    job_id: uuid.UUID
    job_revision: int
    common_core_questions_count: int
    common_core_duration_minutes: int


class CandidatePlanCoverageSummaryResponse(FrozenSchema):
    """Coverage summary for candidate-specific clarification questions."""
    selected_target_count: int
    target_selection_reasons: Dict[str, str]
    match_states_clarified: List[str]
    total_supplemental_duration_minutes: int


class CandidateInterviewPlanPreviewResponse(FrozenSchema):
    """Response DTO for candidate interview plan preview."""
    plan_id: uuid.UUID
    job_id: uuid.UUID
    job_revision: int
    template_id: uuid.UUID
    template_version: int
    candidate_id: uuid.UUID
    profile_id: uuid.UUID
    profile_version: int
    match_result_id: uuid.UUID
    match_result_fingerprint: str
    lifecycle_status: PlanLifecycle
    scoring_separation_policy: str
    input_snapshot_hash: str
    content_hash: str
    common_template_summary: CandidateInterviewPlanSummaryResponse
    supplemental_questions: List[CandidateSupplementalQuestionResponse]
    coverage_summary: CandidatePlanCoverageSummaryResponse
    validation_report: BlueprintValidationReportResponse
    supplemental_catalogue_version: str
    policy_version: str


class CandidateInterviewPlanDraftCreateResponse(FrozenSchema):
    """Response DTO for created candidate interview plan draft."""
    plan_id: uuid.UUID
    organization_id: uuid.UUID
    job_id: uuid.UUID
    job_revision: int
    template_id: uuid.UUID
    template_version: int
    candidate_id: uuid.UUID
    profile_id: uuid.UUID
    profile_version: int
    match_result_id: uuid.UUID
    match_result_fingerprint: str
    plan_version: int
    lifecycle_status: PlanLifecycle
    scoring_separation_policy: str
    input_snapshot_hash: str
    content_hash: str
    common_template_summary: CandidateInterviewPlanSummaryResponse
    supplemental_questions: List[CandidateSupplementalQuestionResponse]
    coverage_summary: CandidatePlanCoverageSummaryResponse
    validation_report: BlueprintValidationReportResponse
    supplemental_catalogue_version: str
    policy_version: str
    created_by_user_id: uuid.UUID
    created_at: datetime


# =========================================================================
# Phase 3B.4: Review, Safe Editing, Validation, Versioning and Approval
# =========================================================================

class QuestionEditItem(FrozenSchema):
    """Specific allowed question-level edit payload."""
    question_id: uuid.UUID
    question_text: str = Field(min_length=5, max_length=2000)
    display_order: int = Field(ge=0)
    estimated_time_minutes: int = Field(ge=1, le=60)
    permitted_probes: List[str] = Field(default_factory=list)


class InterviewTemplateEditRequest(FrozenSchema):
    """Request payload for safe editing of a template draft."""
    expected_record_version: int = Field(ge=1)
    expected_content_hash: str = Field(min_length=64, max_length=64)
    edit_reason: Optional[str] = Field(default=None, max_length=500)
    questions: List[QuestionEditItem] = Field(min_length=1)


class CandidateSupplementalQuestionEditItem(FrozenSchema):
    """Specific allowed candidate supplemental question edit payload."""
    question_id: uuid.UUID
    question_text: str = Field(min_length=5, max_length=2000)
    display_order: int = Field(ge=0)
    estimated_time_minutes: int = Field(ge=1, le=15)
    clarification_purpose: Optional[str] = Field(default=None, max_length=1000)


class CandidatePlanEditRequest(FrozenSchema):
    """Request payload for safe editing of a candidate plan draft."""
    expected_record_version: int = Field(ge=1)
    expected_content_hash: str = Field(min_length=64, max_length=64)
    edit_reason: Optional[str] = Field(default=None, max_length=500)
    supplemental_questions: List[CandidateSupplementalQuestionEditItem] = Field(default_factory=list)


class InterviewApprovalRequest(FrozenSchema):
    """Request payload for approving a validated template or plan."""
    expected_content_hash: str = Field(min_length=64, max_length=64)
    approval_confirmation: bool = Field(default=True)
    approval_notes: Optional[str] = Field(default=None, max_length=500)


class SuccessorDraftCreateRequest(FrozenSchema):
    """Request payload for creating a successor draft from an approved version."""
    edit_reason: str = Field(min_length=3, max_length=500)


class ValidationSummaryResponse(FrozenSchema):
    """Result of an explicit validation operation."""
    record_id: uuid.UUID
    record_version: int
    lifecycle_status: PlanLifecycle
    is_valid: bool
    content_hash: str
    validated_content_hash: Optional[str] = None
    blocking_errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    checked_rules_count: int
    validator_version: str
    policy_version: str
    validation_timestamp: datetime


class ApprovalSummaryResponse(FrozenSchema):
    """Result of an explicit approval operation."""
    record_id: uuid.UUID
    record_version: int
    lifecycle_status: PlanLifecycle
    approved_by_user_id: uuid.UUID
    approved_at: datetime
    content_hash: str
    is_immutable: bool = True
    parent_record_id: Optional[uuid.UUID] = None
    superseded_parent_id: Optional[uuid.UUID] = None


class VersionSummaryItem(FrozenSchema):
    """Summary of a single template or plan version in history."""
    version_id: uuid.UUID
    version_number: int
    lifecycle_status: PlanLifecycle
    content_hash: str
    created_by_user_id: uuid.UUID
    created_at: datetime
    approved_by_user_id: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    parent_id: Optional[uuid.UUID] = None
    policy_version: str
    questions_count: int
    total_duration_minutes: int
    edit_reason: Optional[str] = None


class VersionHistoryResponse(FrozenSchema):
    """Version history list for a job template or candidate plan."""
    resource_type: str
    resource_id: uuid.UUID
    current_active_version: int
    versions: List[VersionSummaryItem]


class QuestionDiffItem(FrozenSchema):
    """Field-level difference between two versions of a question."""
    question_id: str
    display_order_v1: Optional[int] = None
    display_order_v2: Optional[int] = None
    text_changed: bool = False
    text_v1: Optional[str] = None
    text_v2: Optional[str] = None
    time_changed: bool = False
    time_v1: Optional[int] = None
    time_v2: Optional[int] = None
    probes_changed: bool = False
    probes_v1: List[str] = Field(default_factory=list)
    probes_v2: List[str] = Field(default_factory=list)
    change_type: str  # "UNCHANGED", "MODIFIED", "ADDED", "REMOVED"


class VersionDiffResponse(FrozenSchema):
    """Safe, structured comparison of two versions."""
    resource_type: str
    resource_id: uuid.UUID
    version_from: int
    version_to: int
    lifecycle_status_from: PlanLifecycle
    lifecycle_status_to: PlanLifecycle
    content_hash_from: str
    content_hash_to: str
    total_duration_minutes_from: int
    total_duration_minutes_to: int
    changed_questions_count: int
    question_diffs: List[QuestionDiffItem]


# =========================================================================
# Phase 3C.4: Recruiter AI Wording Application & Restoration Schemas
# =========================================================================

class ApplyWordingSuggestionRequest(FrozenSchema):
    """Request payload to apply a validated AI-assisted wording suggestion to a draft question."""
    expected_question_version: int = Field(ge=1, description="Optimistic concurrency: expected question version.")
    expected_source_hash: Optional[str] = Field(default=None, max_length=64, description="Expected content hash of deterministic source text.")
    suggested_text: str = Field(min_length=5, max_length=2000, description="The validated suggestion text to apply.")
    rationale_code: Optional[str] = Field(default=None, max_length=100)


class RestoreDeterministicWordingRequest(FrozenSchema):
    """Request payload to restore the original deterministic wording on a question."""
    expected_question_version: int = Field(ge=1, description="Optimistic concurrency: expected question version.")


class QuestionActionResponse(FrozenSchema):
    """Response payload returned when a question is updated or restored."""
    question_id: uuid.UUID
    question_version: int
    question_text: str
    authoring_origin: AuthoringOrigin
    validation_state: ValidationState
    deterministic_source_text: Optional[str] = None
    deterministic_source_available: bool = False
    has_wording_drift: bool = False
    can_restore: bool = False
    restore_unavailable_reason: Optional[str] = None
    content_hash: Optional[str] = None
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


class GenerateTemplateAiWordingRequest(FrozenSchema):
    """Request payload to initiate AI wording generation for an entire draft template."""
    expected_template_version: int = Field(ge=1, description="Expected template version.")
    expected_content_hash: Optional[str] = Field(default=None, max_length=64)


class QuestionBatchWordingResult(FrozenSchema):
    """Result status for an individual question in a batch generation operation."""
    question_id: uuid.UUID
    display_order: int
    status: str  # "AI_READY", "DETERMINISTIC_FALLBACK", "FAILED_RETRYABLE"
    authoring_origin: AuthoringOrigin
    question_text: str
    question_version: Optional[int] = None
    deterministic_question_text: Optional[str] = None
    rationale_code: Optional[str] = None
    validation_state: ValidationState
    error_message: Optional[str] = None


class GenerateTemplateAiWordingResponse(FrozenSchema):
    """Response payload returned after batch AI wording generation completes."""
    template_id: uuid.UUID
    template_version: int
    lifecycle_status: PlanLifecycle
    total_questions: int
    ai_generated_count: int
    fallback_count: int
    failed_count: int
    status: str  # "COMPLETED", "PARTIALLY_COMPLETED", "FAILED"
    results: List[QuestionBatchWordingResult]
    safe_message: str


# =========================================================================
# Phase 3D.1: Recruiter Text Simulation Schemas
# =========================================================================

class SimulationAnswerItemResponse(FrozenSchema):
    """Authoritative answer schema for a single simulated question."""
    answer_id: uuid.UUID
    question_id: uuid.UUID
    question_version: int
    display_order: int
    layer: str
    answer_text: str
    answer_version: int
    is_submitted: bool
    content_hash: str
    updated_at: str


class SimulationQuestionSnapshotResponse(FrozenSchema):
    """Frozen snapshot of an interview question during a simulation attempt."""
    question_id: uuid.UUID
    question_version: int
    display_order: int
    layer: InterviewLayer
    question_type: QuestionType
    competency_name: str
    question_text: str
    estimated_time_minutes: int
    permitted_probes: List[str]
    rubric: Optional[Dict[str, Any]] = None


class SimulationAttemptResponse(FrozenSchema):
    """Full representation of a simulation attempt including snapshots and answers."""
    attempt_id: uuid.UUID
    organization_id: uuid.UUID
    recruiter_user_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_name: Optional[str] = None
    job_id: uuid.UUID
    job_title: Optional[str] = None
    job_version_id: uuid.UUID
    job_revision: int
    candidate_profile_version: int
    match_result_id: Optional[uuid.UUID] = None
    plan_id: uuid.UUID
    template_id: uuid.UUID
    plan_version: int
    record_version: int = 1
    authoritative_content_hash: str
    lifecycle_status: str  # "NOT_STARTED", "IN_PROGRESS", "SUBMITTED"
    guardrail_policy_version: str
    total_questions_count: int
    answered_questions_count: int
    started_at: str
    submitted_at: Optional[str] = None
    questions: List[SimulationQuestionSnapshotResponse]
    answers: Dict[str, SimulationAnswerItemResponse]  # keyed by question_id string
    assessments: Dict[str, Any] = Field(default_factory=dict)  # keyed by question_id string (ProvisionalAssessmentItemResponse dicts)



class SimulationAnswerSaveRequest(FrozenSchema):
    """Payload to save or update one draft simulation answer."""
    expected_answer_version: int = Field(ge=1, description="Optimistic concurrency: expected answer version.")
    answer_text: str = Field(description="Recruiter simulated answer text.")


class SimulationAnswerSaveResponse(FrozenSchema):
    """Response returned after saving a draft simulation answer."""
    answer: SimulationAnswerItemResponse
    answered_questions_count: int
    total_questions_count: int
    attempt_status: str


class SimulationSubmitRequest(FrozenSchema):
    """Payload to finalize and submit a simulation attempt."""
    expected_record_version: Optional[int] = Field(default=None, ge=1)


class SimulationSubmitResponse(FrozenSchema):
    """Response returned upon successful submission of a simulation attempt."""
    attempt_id: uuid.UUID
    lifecycle_status: str
    submitted_at: str
    total_questions_count: int
    answered_questions_count: int
    authoritative_content_hash: str
    message: str



