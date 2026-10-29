"""Domain service operation for Phase 3C.2: Bounded Recruiter Question Wording Assistance.

Role Boundary Enforcement:
- Admin configures, tests, activates and deactivates provider profiles.
- Recruiter requests wording assistance for one authorized DRAFT interview question.
- The LLM only proposes wording.
- The LLM does NOT select competencies, generate rubrics, score candidates, approve content,
  or make hiring decisions.

Rules & Guarantees:
- Authenticates and authorizes the Recruiter against job ownership / assignment and organization.
- Admin cannot use this recruiter operation merely because they configure providers.
- Authoritatively reloads the question, template/plan, requirement, and provenance through DB.
- Rejects non-DRAFT, approved, in-use, or superseded records.
- For Common Core questions, loads intent, competency label, and type from authoritative records;
  strictly excludes evidence excerpts.
- For Supplemental questions, loads at most one server-selected allowed evidence excerpt only when available.
- Browser-supplied authoritative metadata is completely ignored.
- Ensures the active provider profile is enabled and compatible; falls back safely to deterministic wording on provider failure.
- Returns a CandidateWordingResult for internal evaluation; does NOT persist, approve, or alter lifecycle state.
- Emits a Phase3AuditEvent containing only safe operational hashes, sizes, latency, and codes (no prompts, secrets, or PII).
"""

from datetime import datetime, timezone
import hashlib
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import UserRecord, UserRole
from app.models.candidate import CandidateProfileRecord
from app.models.interview import (
    CandidateInterviewPlanRecord,
    InterviewQuestionRecord,
    InterviewRubricRecord,
    JobInterviewTemplateRecord,
    SupplementalQuestionProvenanceRecord,
)
from app.models.job import JobRequisition, JobRequirement
from app.schemas.phase3_audit import Phase3AuditEvent
from app.schemas.phase3_enums import (
    AuditRetentionClass,
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    ProviderCompatibilityStatus,
    ProviderErrorCode,
    ProviderOperation,
    ProviderType,
    ValidationErrorCode,
    ValidationState,
)
from app.schemas.provider_profile import ProviderProfile
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_ID,
    SAFETY_POLICY_VERSION,
    check_prohibited_topics,
)
from app.schemas.wording_contract import (
    PROMPT_TEMPLATE_VERSION,
    WORDING_REQUEST_SCHEMA_VERSION,
    BoundedWordingRequest,
    BoundedWordingResponse,
    CandidateWordingResult,
    RecruiterWordingAssistRequest,
    StructuredWordingVariant,
    WordingRationaleCode,
)
from app.services.interview_snapshot_service import (
    InterviewSnapshotError,
    InterviewUnauthorizedError,
)
from app.services.provider_registry import ProviderRegistry, get_provider_registry
from app.services.wording_provider import (
    BaseWordingProvider,
    DisabledWordingProvider,
    ProviderTransportError,
)
from app.services.wording_validation_service import (
    SemanticValidationOutcome,
    SemanticWordingValidator,
)

logger = logging.getLogger("ai_hiring.wording_assistance_service")


class QuestionWordingError(Exception):
    """Base exception for question wording assistance failures."""
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class QuestionNotDraftError(QuestionWordingError):
    def __init__(self, message: str = "Wording assistance is only permitted on editable DRAFT questions."):
        super().__init__(code="QUESTION_NOT_IN_DRAFT", message=message, status_code=409)


class QuestionNotFoundError(QuestionWordingError):
    def __init__(self, message: str = "Interview question not found."):
        super().__init__(code="QUESTION_NOT_FOUND", message=message, status_code=404)


class InactiveProviderError(QuestionWordingError):
    def __init__(self, message: str = "No active and compatible provider profile is available."):
        super().__init__(code="NO_ACTIVE_PROVIDER", message=message, status_code=422)


def _verify_recruiter_access(actor: UserRecord, job: JobRequisition) -> None:
    """Strictly verifies that the caller is an assigned Recruiter.
    
    Admins are NOT permitted to use this recruiter wording operation merely because
    they configure providers.
    """
    if actor.role != UserRole.RECRUITER:
        raise InterviewUnauthorizedError("Only authorized Recruiters may request wording assistance.")
    
    # Organization check
    actor_org_id = getattr(actor, "org_id", getattr(actor, "organization_id", None))
    job_org_id = getattr(job, "org_id", getattr(job, "organization_id", None))
    if actor_org_id is not None and job_org_id is not None:
        if actor_org_id != job_org_id:
            raise InterviewUnauthorizedError("Recruiter does not belong to the job's organization.")

    # Assignment check: recruiter must own/be assigned to the job requisition
    if job.created_by_user_id != actor.user_id:
        raise InterviewUnauthorizedError("Recruiter is not assigned to this job requisition.")


def _load_authoritative_question(
    question_id: UUID,
    db: Session,
) -> Tuple[InterviewQuestionRecord, JobRequisition, Optional[JobInterviewTemplateRecord], Optional[CandidateInterviewPlanRecord], List[InterviewQuestionRecord]]:
    """Authoritatively loads the question record, verifying DRAFT status and relationships."""
    stmt = (
        select(InterviewQuestionRecord)
        .options(
            selectinload(InterviewQuestionRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(InterviewQuestionRecord.template).selectinload(JobInterviewTemplateRecord.questions),
            selectinload(InterviewQuestionRecord.rubric),
            selectinload(InterviewQuestionRecord.provenance).selectinload(SupplementalQuestionProvenanceRecord.plan),
            selectinload(InterviewQuestionRecord.provenance).selectinload(SupplementalQuestionProvenanceRecord.source_requirement),
        )
        .where(InterviewQuestionRecord.question_id == question_id)
    )
    question = db.execute(stmt).scalar_one_or_none()
    if question is None:
        raise QuestionNotFoundError(f"Interview question '{question_id}' not found.")

    template = question.template
    if template is not None:
        if template.lifecycle_status != PlanLifecycle.DRAFT:
            raise QuestionNotDraftError(
                f"Question belongs to template '{template.template_id}' with status '{template.lifecycle_status.value}'. Only DRAFT records are editable."
            )
        job = template.requisition
        if job is None:
            stmt_job = select(JobRequisition).where(JobRequisition.job_id == template.job_id)
            job = db.execute(stmt_job).scalar_one_or_none()
        if job is None:
            raise QuestionNotFoundError("Parent job requisition not found.")
        siblings = [q for q in (template.questions or []) if q.question_id != question.question_id]
        return question, job, template, None, siblings

    # Check supplemental question linked to candidate plan via provenance
    prov = question.provenance
    if prov is not None and prov.plan is not None:
        plan = prov.plan
        if plan.lifecycle_status != PlanLifecycle.DRAFT:
            raise QuestionNotDraftError(
                f"Question belongs to candidate plan '{plan.plan_id}' with status '{plan.lifecycle_status.value}'. Only DRAFT records are editable."
            )
        template_stmt = select(JobInterviewTemplateRecord).options(
            selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(JobInterviewTemplateRecord.questions),
        ).where(JobInterviewTemplateRecord.template_id == plan.template_id)
        parent_template = db.execute(template_stmt).scalar_one_or_none()
        if parent_template is None or parent_template.requisition is None:
            raise QuestionNotFoundError("Parent job requisition for candidate plan not found.")
        siblings = [q for q in (parent_template.questions or []) if q.question_id != question.question_id]
        return question, parent_template.requisition, parent_template, plan, siblings

    raise QuestionNotFoundError(f"Question '{question_id}' has no valid parent template or plan.")


def _resolve_permitted_terminology(question: InterviewQuestionRecord, db: Session) -> List[str]:
    """Resolves authoritative permitted terminology keywords from associated requirements."""
    permitted: List[str] = []
    if question.requirement_ids:
        try:
            req_uuids = [UUID(str(rid)) for rid in question.requirement_ids if rid]
            if req_uuids:
                stmt = select(JobRequirement.label).where(JobRequirement.requirement_id.in_(req_uuids))
                labels = db.execute(stmt).scalars().all()
                permitted.extend([str(l) for l in labels if l])
        except Exception as e:
            logger.debug("Failed to resolve requirement terminology: %s", e)
    return sorted(list(set(permitted)))


def _resolve_evidence_excerpt(question: InterviewQuestionRecord, db: Session) -> Optional[str]:
    """For supplemental questions, resolves at most one server-selected allowed evidence excerpt."""
    if question.layer != InterviewLayer.SUPPLEMENTAL_EVIDENCE:
        return None

    prov = question.provenance
    if not prov or not prov.allowlisted_evidence_ids or not prov.plan_id:
        return None

    target_evidence_id = prov.allowlisted_evidence_ids[0]
    stmt = select(CandidateProfileRecord).where(CandidateProfileRecord.profile_id == prov.plan.profile_id)
    profile = db.execute(stmt).scalar_one_or_none()
    if not profile or not profile.evidence_spans:
        return None

    for span in profile.evidence_spans:
        if span.get("evidence_id") == target_evidence_id or str(span.get("id")) == target_evidence_id:
            snippet = span.get("text_snippet") or span.get("snippet") or span.get("text")
            if snippet and isinstance(snippet, str):
                cleaned = snippet.strip()
                return cleaned[:300] if len(cleaned) > 300 else cleaned
    return None


async def request_question_wording_assistance(
    actor: UserRecord,
    question_id: UUID,
    request_dto: RecruiterWordingAssistRequest,
    db: Session,
    provider_registry: Optional[ProviderRegistry] = None,
    custom_provider: Optional[BaseWordingProvider] = None,
) -> CandidateWordingResult:
    """Core domain operation for requesting bounded wording assistance on one question."""
    start_time = time.perf_counter()
    reg = provider_registry or get_provider_registry()

    # 1. Authoritatively load question, verify relationships, and verify Recruiter access
    question, job, template, plan, siblings = _load_authoritative_question(question_id=question_id, db=db)
    _verify_recruiter_access(actor=actor, job=job)

    # 1b. Validate optimistic concurrency if client passed version or source hash
    if request_dto.expected_question_version is not None and request_dto.expected_question_version != question.question_version:
        raise QuestionWordingError(
            code="STALE_QUESTION_VERSION",
            message=f"Question version conflict: expected version {request_dto.expected_question_version}, but current version is {question.question_version}.",
            status_code=409,
        )

    metadata = question.safe_metadata or {}
    expected_det_hash = metadata.get("deterministic_source_hash")
    if not expected_det_hash and question.question_text:
        expected_det_hash = hashlib.sha256(question.question_text.encode("utf-8")).hexdigest()

    if request_dto.source_content_hash is not None and expected_det_hash and request_dto.source_content_hash != expected_det_hash:
        raise QuestionWordingError(
            code="STALE_SOURCE_HASH",
            message="Deterministic source text hash mismatch. Question has changed since requested.",
            status_code=409,
        )

    # 2. Extract authoritative server-owned fields
    competency_label = None
    if question.rubric and question.rubric.competency_definition:
        competency_label = question.rubric.competency_definition[:200]
    elif question.competency_id:
        competency_label = question.competency_id

    permitted_terms = _resolve_permitted_terminology(question=question, db=db)
    evidence_excerpt = _resolve_evidence_excerpt(question=question, db=db)

    # 3. Assemble strict BoundedWordingRequest
    bounded_request = BoundedWordingRequest(
        operation="REWORD_QUESTION",
        request_schema_version=WORDING_REQUEST_SCHEMA_VERSION,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        policy_version=SAFETY_POLICY_VERSION,
        policy_hash=SAFETY_POLICY_HASH,
        source_question_id=question.question_id,
        source_question_version=question.question_version,
        deterministic_question_text=question.question_text,
        question_intent=question.question_intent,
        competency_label=competency_label,
        question_type=question.question_type,
        layer=question.layer,
        permitted_terminology=permitted_terms,
        prohibited_topics=["AGE_AND_DOB", "ETHNICITY_AND_RACE", "RELIGION_AND_BELIEF", "DISABILITY_AND_MEDICAL"],
        output_language="en",
        max_characters=1000,
        max_variants=request_dto.max_variants,
        require_single_primary_question=True,
        allowed_evidence_excerpt=evidence_excerpt,
        allowed_reference_ids=[str(rid) for rid in question.requirement_ids] if question.requirement_ids else [],
    )

    # 4. Resolve Active Provider
    org_id = template.organization_id if template else (plan.organization_id if plan else None)
    active_profile = reg.get_active_profile(organization_id=org_id)
    provider = custom_provider or reg.get_provider_instance(active_profile)

    input_hash = hashlib.sha256(bounded_request.deterministic_question_text.encode("utf-8")).hexdigest()
    input_size = len(bounded_request.deterministic_question_text)

    # 5. Dispatch to Provider with Safe Fallback & Semantic Validation Pipeline
    try:
        response: BoundedWordingResponse = await provider.request_wording_assistance(bounded_request)
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

        # Build raw output hash for provenance
        output_concat = "".join(v.variant_text for v in response.variants)
        output_hash = hashlib.sha256(output_concat.encode("utf-8")).hexdigest()
        output_size = len(output_concat)

        # 6. Execute Phase 3C.3 Semantic Validation Pipeline
        validation_outcome: SemanticValidationOutcome = SemanticWordingValidator.validate_wording_response(
            source_question=question,
            request=bounded_request,
            response=response,
            sibling_questions=siblings,
            allowed_reference_ids=set(bounded_request.allowed_reference_ids),
        )

        if validation_outcome.is_valid:
            # Audit Event for Accepted AI Proposal (MODEL_PROVENANCE, safe metadata only)
            Phase3AuditEvent(
                event_id=uuid.uuid4(),
                actor_user_id=actor.user_id,
                organization_id=org_id,
                event_type="QUESTION_WORDING_VALIDATED",
                resource_type="interview_question",
                resource_id=str(question.question_id),
                resource_version=question.question_version,
                policy_version=SAFETY_POLICY_VERSION,
                policy_hash=SAFETY_POLICY_HASH,
                retention_class=AuditRetentionClass.MODEL_PROVENANCE,
                result_code="VALIDATED",
                safe_metadata={
                    "operation": "REWORD_QUESTION",
                    "request_schema_version": WORDING_REQUEST_SCHEMA_VERSION,
                    "prompt_template_version": PROMPT_TEMPLATE_VERSION,
                    "provider_profile_id": str(active_profile.profile_id) if active_profile else None,
                    "provider_class": active_profile.provider_type.value if active_profile else "DISABLED",
                    "model_identifier": active_profile.model_identifier if active_profile else None,
                    "authoring_origin": AuthoringOrigin.AI_ASSISTED.value,
                    "input_hash": input_hash,
                    "output_hash": output_hash,
                    "input_chars": input_size,
                    "output_chars": output_size,
                    "latency_ms": latency_ms,
                    "variants_count": len(validation_outcome.validated_variants),
                    "validation_status": "PASSED",
                },
            )

            return CandidateWordingResult(
                source_question_id=question.question_id,
                source_question_version=question.question_version,
                operation="REWORD_QUESTION",
                request_schema_version=WORDING_REQUEST_SCHEMA_VERSION,
                prompt_template_version=PROMPT_TEMPLATE_VERSION,
                deterministic_question_text=question.question_text,
                proposed_variants=validation_outcome.validated_variants,
                allowed_references_checked=True,
                is_validated=True,
                validation_codes=[],
                rejection_reasons=[],
                authoring_origin=AuthoringOrigin.AI_ASSISTED,
                provider_fallback_used=False,
                provider_error_code=None,
                safe_error_message=None,
            )
        else:
            # Semantic validation rejected output -> safe deterministic fallback
            Phase3AuditEvent(
                event_id=uuid.uuid4(),
                actor_user_id=actor.user_id,
                organization_id=org_id,
                event_type="QUESTION_WORDING_VALIDATION_FAILED",
                resource_type="interview_question",
                resource_id=str(question.question_id),
                resource_version=question.question_version,
                policy_version=SAFETY_POLICY_VERSION,
                policy_hash=SAFETY_POLICY_HASH,
                retention_class=AuditRetentionClass.MODEL_PROVENANCE,
                result_code="VALIDATION_FAILED",
                safe_metadata={
                    "operation": "REWORD_QUESTION",
                    "provider_profile_id": str(active_profile.profile_id) if active_profile else None,
                    "provider_class": active_profile.provider_type.value if active_profile else "DISABLED",
                    "model_identifier": active_profile.model_identifier if active_profile else None,
                    "input_hash": input_hash,
                    "output_hash": output_hash,
                    "input_chars": input_size,
                    "output_chars": output_size,
                    "latency_ms": latency_ms,
                    "validation_codes": validation_outcome.validation_codes,
                    "validation_status": "BLOCKED",
                },
            )

            return CandidateWordingResult(
                source_question_id=question.question_id,
                source_question_version=question.question_version,
                operation="REWORD_QUESTION",
                request_schema_version=WORDING_REQUEST_SCHEMA_VERSION,
                prompt_template_version=PROMPT_TEMPLATE_VERSION,
                deterministic_question_text=question.question_text,
                proposed_variants=[
                    StructuredWordingVariant(
                        variant_text=question.question_text,
                        rationale_code=WordingRationaleCode.NEUTRAL_PHRASING,
                        referenced_identifiers=[],
                    )
                ],
                allowed_references_checked=True,
                is_validated=False,
                validation_codes=validation_outcome.validation_codes,
                rejection_reasons=validation_outcome.rejection_reasons,
                authoring_origin=AuthoringOrigin.DETERMINISTIC,
                provider_fallback_used=True,
                provider_error_code=validation_outcome.validation_codes[0] if validation_outcome.validation_codes else None,
                safe_error_message="Proposed wording failed semantic validation checks. Deterministic wording retained.",
            )

    except ProviderTransportError as e:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info("Provider transport failure during wording assistance: %s - %s", e.error_code, e.safe_message)

        # Audit Event for Transport Failure
        Phase3AuditEvent(
            event_id=uuid.uuid4(),
            actor_user_id=actor.user_id,
            organization_id=org_id,
            event_type="QUESTION_WORDING_FALLBACK",
            resource_type="interview_question",
            resource_id=str(question.question_id),
            resource_version=question.question_version,
            policy_version=SAFETY_POLICY_VERSION,
            policy_hash=SAFETY_POLICY_HASH,
            retention_class=AuditRetentionClass.OPERATIONAL_TRACE,
            result_code="FALLBACK",
            safe_metadata={
                "operation": "REWORD_QUESTION",
                "provider_error_code": e.error_code.value if hasattr(e.error_code, "value") else str(e.error_code),
                "input_hash": input_hash,
                "input_chars": input_size,
                "latency_ms": latency_ms,
            },
        )

        # Safe fallback: retain deterministic text
        return CandidateWordingResult(
            source_question_id=question.question_id,
            source_question_version=question.question_version,
            operation="REWORD_QUESTION",
            request_schema_version=WORDING_REQUEST_SCHEMA_VERSION,
            prompt_template_version=PROMPT_TEMPLATE_VERSION,
            deterministic_question_text=question.question_text,
            proposed_variants=[
                StructuredWordingVariant(
                    variant_text=question.question_text,
                    rationale_code=WordingRationaleCode.NEUTRAL_PHRASING,
                    referenced_identifiers=[],
                )
            ],
            allowed_references_checked=True,
            is_validated=False,
            validation_codes=[e.error_code.value if hasattr(e.error_code, "value") else str(e.error_code)],
            rejection_reasons=[e.safe_message],
            authoring_origin=AuthoringOrigin.DETERMINISTIC,
            provider_fallback_used=True,
            provider_error_code=e.error_code.value if hasattr(e.error_code, "value") else str(e.error_code),
            safe_error_message=e.safe_message,
        )


def apply_wording_suggestion(
    actor: UserRecord,
    question_id: UUID,
    request: "ApplyWordingSuggestionRequest",
    db: Session,
) -> "QuestionActionResponse":
    """Applies a validated AI-assisted suggestion to a draft question.
    
    Invariants:
    - Only authorized Recruiter for assigned job.
    - Question must be in DRAFT template or candidate plan.
    - Optimistic concurrency: expected_question_version must match current question_version.
    - Preserves deterministic source text in safe_metadata.
    - Marks authoring_origin as AI_ASSISTED.
    - Invalidates previous validation (resets to PENDING, template to DRAFT).
    - Preserves immutable approved versions.
    """
    from datetime import datetime, timezone
    from app.schemas.interview_api import QuestionActionResponse

    question, job, template, plan, _ = _load_authoritative_question(question_id=question_id, db=db)
    _verify_recruiter_access(actor=actor, job=job)

    # Optimistic concurrency check
    if request.expected_question_version != question.question_version:
        raise QuestionWordingError(
            code="STALE_QUESTION_VERSION",
            message=f"Question version mismatch: expected {request.expected_question_version}, but current version is {question.question_version}.",
            status_code=409,
        )

    # Initialize metadata
    metadata = dict(question.safe_metadata or {})
    if "deterministic_source_text" not in metadata:
        metadata["deterministic_source_text"] = question.question_text
        metadata["deterministic_source_hash"] = hashlib.sha256(question.question_text.encode("utf-8")).hexdigest()

    # Verify expected source hash if provided
    if request.expected_source_hash and metadata.get("deterministic_source_hash") != request.expected_source_hash:
        raise QuestionWordingError(
            code="STALE_SOURCE_HASH",
            message="Deterministic source text hash mismatch.",
            status_code=409,
        )

    # Store provenance
    metadata["ai_provenance"] = {
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "applied_by_user_id": str(actor.user_id),
        "rationale_code": request.rationale_code,
    }

    # Apply wording
    question.question_text = request.suggested_text.strip()
    question.authoring_origin = AuthoringOrigin.AI_ASSISTED
    question.question_version += 1
    question.validation_state = ValidationState.PENDING
    question.safe_metadata = metadata

    # Invalidate parent template/plan validation if needed
    if template and template.lifecycle_status == PlanLifecycle.VALIDATED:
        template.lifecycle_status = PlanLifecycle.DRAFT
    if plan and plan.lifecycle_status == PlanLifecycle.VALIDATED:
        plan.lifecycle_status = PlanLifecycle.DRAFT

    org_id = template.organization_id if template else (plan.organization_id if plan else None)
    Phase3AuditEvent(
        event_id=uuid.uuid4(),
        actor_user_id=actor.user_id,
        organization_id=org_id,
        event_type="QUESTION_WORDING_APPLIED",
        resource_type="interview_question",
        resource_id=str(question.question_id),
        resource_version=question.question_version,
        policy_version=SAFETY_POLICY_VERSION,
        policy_hash=SAFETY_POLICY_HASH,
        retention_class=AuditRetentionClass.MODEL_PROVENANCE,
        result_code="SUCCESS",
        safe_metadata={
            "question_version": question.question_version,
            "authoring_origin": AuthoringOrigin.AI_ASSISTED.value,
            "rationale_code": request.rationale_code,
        },
    )
    db.commit()

    return _build_question_action_response(
        question=question,
        metadata=metadata,
        template=template,
        plan=plan,
        message="AI wording applied successfully.",
    )


def _build_question_action_response(
    question: "InterviewQuestionRecord",
    metadata: Dict[str, Any],
    template: Optional["JobInterviewTemplateRecord"] = None,
    plan: Optional["CandidateInterviewPlanRecord"] = None,
    message: Optional[str] = None,
) -> "QuestionActionResponse":
    """Builds a typed QuestionActionResponse with authoritative restore eligibility fields."""
    from app.schemas.interview_api import QuestionActionResponse

    det_text = metadata.get("deterministic_source_text")
    deterministic_source_available = bool(det_text and len(det_text.strip()) > 0)
    has_wording_drift = bool(
        deterministic_source_available
        and question.question_text.strip() != (det_text or "").strip()
    )
    is_template_draft = (template is None or template.lifecycle_status == PlanLifecycle.DRAFT)
    is_plan_draft = (plan is None or plan.lifecycle_status == PlanLifecycle.DRAFT)
    can_restore = bool(deterministic_source_available and has_wording_drift and is_template_draft and is_plan_draft)

    restore_unavailable_reason = None
    if not is_template_draft or not is_plan_draft:
        restore_unavailable_reason = "TEMPLATE_IMMUTABLE"
    elif not deterministic_source_available:
        restore_unavailable_reason = "NO_DETERMINISTIC_SOURCE"
    elif not has_wording_drift:
        restore_unavailable_reason = "ALREADY_RESTORED"

    content_hash = hashlib.sha256(question.question_text.encode("utf-8")).hexdigest()

    return QuestionActionResponse(
        question_id=question.question_id,
        question_version=question.question_version,
        question_text=question.question_text,
        authoring_origin=question.authoring_origin,
        validation_state=question.validation_state,
        deterministic_source_text=det_text,
        deterministic_source_available=deterministic_source_available,
        has_wording_drift=has_wording_drift,
        can_restore=can_restore,
        restore_unavailable_reason=restore_unavailable_reason,
        content_hash=content_hash,
        safe_metadata=question.safe_metadata or {},
        message=message,
    )


def restore_deterministic_wording(
    actor: UserRecord,
    question_id: UUID,
    request: "RestoreDeterministicWordingRequest",
    db: Session,
) -> "QuestionActionResponse":
    """Restores the stored deterministic wording on a question.
    
    Invariants:
    - Only authorized Recruiter for assigned job.
    - Question must be in DRAFT template or candidate plan.
    - Deterministic source must exist (AI fallback without source is rejected).
    - Already-restored requests are idempotent: return existing state without creating duplicate versions.
    - Optimistic concurrency: expected_question_version must match current question_version.
    - Sets authoring_origin back to DETERMINISTIC.
    - Invalidates previous validation.
    """
    from app.schemas.interview_api import QuestionActionResponse

    question, job, template, plan, _ = _load_authoritative_question(question_id=question_id, db=db)
    _verify_recruiter_access(actor=actor, job=job)

    # Immutability check
    if template and template.lifecycle_status != PlanLifecycle.DRAFT:
        raise QuestionWordingError(
            code="TEMPLATE_IMMUTABLE",
            message=f"Cannot restore question in {template.lifecycle_status.value} template. Approved records are immutable.",
            status_code=409,
        )
    if plan and plan.lifecycle_status != PlanLifecycle.DRAFT:
        raise QuestionWordingError(
            code="TEMPLATE_IMMUTABLE",
            message=f"Cannot restore question in {plan.lifecycle_status.value} plan. Approved records are immutable.",
            status_code=409,
        )

    metadata = dict(question.safe_metadata or {})
    det_text = metadata.get("deterministic_source_text")
    if not det_text or not det_text.strip():
        raise QuestionWordingError(
            code="NO_DETERMINISTIC_SOURCE",
            message="This question has no deterministic original.",
            status_code=400,
        )

    # Idempotency check: if already restored, return current state without error
    if question.authoring_origin == AuthoringOrigin.DETERMINISTIC and question.question_text.strip() == det_text.strip():
        return _build_question_action_response(
            question=question,
            metadata=metadata,
            template=template,
            plan=plan,
            message="Question is already in original deterministic wording.",
        )

    # Optimistic concurrency check
    if request.expected_question_version != question.question_version:
        raise QuestionWordingError(
            code="STALE_QUESTION_VERSION",
            message=f"Question version mismatch: expected {request.expected_question_version}, but current version is {question.question_version}.",
            status_code=409,
        )

    question.question_text = det_text.strip()
    question.authoring_origin = AuthoringOrigin.DETERMINISTIC
    question.question_version += 1
    question.validation_state = ValidationState.PENDING
    question.safe_metadata = metadata

    if template and template.lifecycle_status == PlanLifecycle.VALIDATED:
        template.lifecycle_status = PlanLifecycle.DRAFT
    if plan and plan.lifecycle_status == PlanLifecycle.VALIDATED:
        plan.lifecycle_status = PlanLifecycle.DRAFT

    org_id = template.organization_id if template else (plan.organization_id if plan else None)
    Phase3AuditEvent(
        event_id=uuid.uuid4(),
        actor_user_id=actor.user_id,
        organization_id=org_id,
        event_type="QUESTION_WORDING_RESTORED",
        resource_type="interview_question",
        resource_id=str(question.question_id),
        resource_version=question.question_version,
        policy_version=SAFETY_POLICY_VERSION,
        policy_hash=SAFETY_POLICY_HASH,
        retention_class=AuditRetentionClass.OPERATIONAL_TRACE,
        result_code="SUCCESS",
        safe_metadata={
            "question_version": question.question_version,
            "authoring_origin": AuthoringOrigin.DETERMINISTIC.value,
        },
    )
    db.commit()

    return _build_question_action_response(
        question=question,
        metadata=metadata,
        template=template,
        plan=plan,
        message="Original wording restored.",
    )


async def generate_template_ai_wording(
    actor: UserRecord,
    template_id: UUID,
    request: "GenerateTemplateAiWordingRequest",
    db: Session,
    provider_registry: Optional[ProviderRegistry] = None,
    custom_provider: Optional[BaseWordingProvider] = None,
) -> "GenerateTemplateAiWordingResponse":
    """Sequentially generates and validates AI wording for all eligible questions in a DRAFT template.
    
    Invariants & Guarantees:
    - Only authorized Recruiter for assigned job.
    - Template must be in DRAFT lifecycle state.
    - Template version concurrency check.
    - Sequentially processes each question (local concurrency 1).
    - Validates each variant with Phase 3C.3 semantic validation.
    - Persists valid AI draft on each question, retaining deterministic baseline and provenance.
    - One question failure does not fail the entire batch.
    - Emits safe Phase3AuditEvent records.
    """
    from app.schemas.interview_api import (
        GenerateTemplateAiWordingRequest,
        GenerateTemplateAiWordingResponse,
        QuestionBatchWordingResult,
    )

    stmt = (
        select(JobInterviewTemplateRecord)
        .options(
            selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .where(JobInterviewTemplateRecord.template_id == template_id)
    )
    template = db.execute(stmt).scalar_one_or_none()
    if not template:
        raise QuestionNotFoundError(f"Interview template '{template_id}' not found.")

    if template.lifecycle_status != PlanLifecycle.DRAFT:
        raise QuestionNotDraftError(
            f"Template '{template_id}' is in status '{template.lifecycle_status.value}'. Only DRAFT templates can generate AI wording."
        )

    _verify_recruiter_access(actor=actor, job=template.requisition)

    if request.expected_template_version != template.template_version:
        raise QuestionWordingError(
            code="STALE_TEMPLATE_VERSION",
            message=f"Template version mismatch: expected {request.expected_template_version}, current is {template.template_version}.",
            status_code=409,
        )

    questions = sorted(template.questions or [], key=lambda q: q.display_order)
    results: List[QuestionBatchWordingResult] = []
    ai_count = 0
    fallback_count = 0
    failed_count = 0

    reg = provider_registry or get_provider_registry()

    provider_wide_failure_detected = False
    provider_wide_error_message: Optional[str] = None

    for idx, q in enumerate(questions):
        if provider_wide_failure_detected:
            # Retain deterministic wording for remaining questions without calling provider
            fallback_count += 1
            results.append(
                QuestionBatchWordingResult(
                    question_id=q.question_id,
                    display_order=q.display_order,
                    status="DETERMINISTIC_FALLBACK",
                    authoring_origin=q.authoring_origin,
                    question_text=q.question_text,
                    validation_state=q.validation_state,
                    error_message=provider_wide_error_message or "Deterministic fallback retained due to provider incompatibility.",
                )
            )
            continue

        # Request wording assistance for this question
        wording_req = RecruiterWordingAssistRequest(
            expected_question_version=q.question_version,
            source_content_hash=None,
            max_variants=1,
            target_tone="PROFESSIONAL",
        )
        try:
            cand_result = await request_question_wording_assistance(
                actor=actor,
                question_id=q.question_id,
                request_dto=wording_req,
                db=db,
                provider_registry=reg,
                custom_provider=custom_provider,
            )

            if cand_result.is_validated and cand_result.proposed_variants:
                chosen_variant = cand_result.proposed_variants[0]
                # Apply suggested text
                metadata = dict(q.safe_metadata or {})
                if not metadata.get("deterministic_source_text"):
                    metadata["deterministic_source_text"] = cand_result.deterministic_question_text or q.question_text
                if not metadata.get("deterministic_source_hash"):
                    metadata["deterministic_source_hash"] = hashlib.sha256(
                        (metadata["deterministic_source_text"]).encode("utf-8")
                    ).hexdigest()

                metadata["ai_provenance"] = {
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                    "applied_by_user_id": str(actor.user_id),
                    "rationale_code": chosen_variant.rationale_code.value if hasattr(chosen_variant.rationale_code, "value") else str(chosen_variant.rationale_code),
                    "prompt_template_version": cand_result.prompt_template_version,
                }

                q.question_text = chosen_variant.variant_text.strip()
                q.authoring_origin = AuthoringOrigin.AI_ASSISTED
                q.question_version += 1
                q.validation_state = ValidationState.PENDING
                q.safe_metadata = metadata

                ai_count += 1
                results.append(
                    QuestionBatchWordingResult(
                        question_id=q.question_id,
                        display_order=q.display_order,
                        status="AI_READY",
                        authoring_origin=AuthoringOrigin.AI_ASSISTED,
                        question_text=q.question_text,
                        question_version=q.question_version,
                        deterministic_question_text=metadata.get("deterministic_source_text"),
                        rationale_code=chosen_variant.rationale_code.value if hasattr(chosen_variant.rationale_code, "value") else str(chosen_variant.rationale_code),
                        validation_state=q.validation_state,
                    )
                )
            else:
                # Check for provider-wide failure vs question-specific failure
                p_code = cand_result.provider_error_code
                is_provider_wide = p_code in [
                    ProviderErrorCode.RESPONSE_CONTENT_EMPTY.value,
                    ProviderErrorCode.RESPONSE_TRUNCATED.value,
                    ProviderErrorCode.PROFILE_MODE_INCOMPATIBLE.value,
                    ProviderErrorCode.PROVIDER_UNAVAILABLE.value,
                    ProviderErrorCode.PROVIDER_DISABLED.value,
                    ProviderErrorCode.AUTHENTICATION_FAILED.value,
                    ProviderErrorCode.MODEL_NOT_AVAILABLE.value,
                    "RESPONSE_CONTENT_EMPTY",
                    "RESPONSE_TRUNCATED",
                    "PROFILE_MODE_INCOMPATIBLE",
                ]
                if is_provider_wide:
                    provider_wide_failure_detected = True
                    provider_wide_error_message = cand_result.safe_error_message or "Provider mode incompatibility detected."

                fallback_count += 1
                det_txt = q.safe_metadata.get("deterministic_source_text") if q.safe_metadata else None
                results.append(
                    QuestionBatchWordingResult(
                        question_id=q.question_id,
                        display_order=q.display_order,
                        status="DETERMINISTIC_FALLBACK",
                        authoring_origin=q.authoring_origin,
                        question_text=q.question_text,
                        question_version=q.question_version,
                        deterministic_question_text=det_txt,
                        validation_state=q.validation_state,
                        error_message="; ".join(cand_result.rejection_reasons) if cand_result.rejection_reasons else (cand_result.safe_error_message or "Deterministic fallback retained."),
                    )
                )
        except Exception as exc:
            logger.warning("Batch wording failed for question %s: %s", q.question_id, exc)
            failed_count += 1
            det_txt = q.safe_metadata.get("deterministic_source_text") if q.safe_metadata else None
            results.append(
                QuestionBatchWordingResult(
                    question_id=q.question_id,
                    display_order=q.display_order,
                    status="FAILED_RETRYABLE",
                    authoring_origin=q.authoring_origin,
                    question_text=q.question_text,
                    question_version=q.question_version,
                    deterministic_question_text=det_txt,
                    validation_state=q.validation_state,
                    error_message=str(exc),
                )
            )

    if template.lifecycle_status == PlanLifecycle.VALIDATED:
        template.lifecycle_status = PlanLifecycle.DRAFT

    db.commit()

    overall_status = "COMPLETED" if failed_count == 0 and fallback_count == 0 else ("PARTIALLY_COMPLETED" if ai_count > 0 else "FAILED")

    if provider_wide_failure_detected and ai_count == 0:
        safe_msg = "The selected model used its response budget for reasoning and returned no final wording. Select a compatible Instruct model and retry. Your deterministic questions were retained."
    else:
        safe_msg = f"Generated AI wording for {ai_count} of {len(questions)} questions."
        if fallback_count > 0 or failed_count > 0:
            safe_msg += f" {fallback_count + failed_count} retained deterministic version."

    return GenerateTemplateAiWordingResponse(
        template_id=template.template_id,
        template_version=template.template_version,
        lifecycle_status=template.lifecycle_status,
        total_questions=len(questions),
        ai_generated_count=ai_count,
        fallback_count=fallback_count,
        failed_count=failed_count,
        status=overall_status,
        results=results,
        safe_message=safe_msg,
    )

