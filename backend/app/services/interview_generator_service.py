import enum
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import UserRecord, UserRole
from app.models.interview import (
    InterviewQuestionRecord,
    InterviewRubricRecord,
    JobInterviewTemplateRecord,
)
from app.models.job import (
    JobLifecycleStatus,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.schemas.interview_blueprint import (
    BLUEPRINT_SCHEMA_VERSION,
    InterviewBlueprint,
    InterviewQuestion,
    InterviewRubric,
    PolicyVersionReference,
)
from app.schemas.interview_snapshot import (
    DETERMINISTIC_BUILDER_VERSION,
    JobBlueprintInputSnapshot,
    JobRequirementSnapshotItem,
    canonicalize_job_blueprint_snapshot,
    compute_job_blueprint_snapshot_hash,
)
from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.phase3_audit import Phase3AuditEvent
from app.schemas.phase3_enums import (
    AuditRetentionClass,
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    QuestionType,
    ValidationErrorCode,
    ValidationState,
)
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_ID,
    SAFETY_POLICY_SCHEMA_VERSION,
    SAFETY_POLICY_VERSION,
    check_prohibited_topics,
    get_current_safety_policy,
)
from app.services.interview_catalogue import (
    COMPETENCY_KEY_MAPPINGS,
    CURATED_QUESTION_CATALOGUE,
    CURATED_RUBRIC_CATALOGUE,
    QUESTION_CATALOGUE_VERSION,
    RUBRIC_CATALOGUE_VERSION,
    QuestionTemplateEntry,
    RubricTemplateEntry,
    get_catalogue_questions_for_competency,
    get_catalogue_rubric,
)
from app.services.interview_snapshot_service import (
    InterviewJobNotReadyError,
    InterviewJobRevisionStaleError,
    InterviewRequisitionNotFoundError,
    InterviewSnapshotError,
    InterviewUnauthorizedError,
    load_authoritative_job_blueprint_snapshot,
)

logger = logging.getLogger("ai_hiring.services.interview_generator")

# Target configuration bounds
MIN_TARGET_COMPETENCIES: int = 4
MAX_TARGET_COMPETENCIES: int = 6
MIN_TARGET_QUESTIONS: int = 6
MAX_TARGET_QUESTIONS: int = 10
MIN_TOTAL_DURATION_MINUTES: int = 30
MAX_TOTAL_DURATION_MINUTES: int = 60


class InterviewSuitability(str, enum.Enum):
    """Interview assessment suitability classification for job requirements."""
    INTERVIEW_ELIGIBLE = "INTERVIEW_ELIGIBLE"
    DOCUMENT_VERIFICATION = "DOCUMENT_VERIFICATION"
    WORK_SAMPLE_PREFERRED = "WORK_SAMPLE_PREFERRED"
    OUT_OF_INTERVIEW = "OUT_OF_INTERVIEW"


class RequirementClassificationResult(FrozenSchema):
    """Classification of an individual requirement's interview suitability."""
    requirement_id: UUID
    label: str
    category: RequirementCategory
    suitability: InterviewSuitability
    reason: str
    mapped_competency_key: Optional[str] = None


class CompetencySelectionResult(FrozenSchema):
    """Result of deterministic competency selection."""
    competency_key: str
    competency_label: str
    priority_score: float
    selection_reason: str
    covered_requirement_ids: List[UUID]


class BlueprintCoverageSummary(FrozenSchema):
    """Coverage and duration summary for an interview blueprint."""
    total_requirements_count: int
    interview_eligible_requirements_count: int
    covered_requirements_count: int
    selected_competencies: List[CompetencySelectionResult]
    uncovered_eligible_competencies: List[str]
    unsupported_competencies: List[str]
    document_verification_requirements: List[RequirementClassificationResult]
    work_sample_requirements: List[RequirementClassificationResult]
    out_of_interview_requirements: List[RequirementClassificationResult]
    total_estimated_duration_minutes: int
    question_count_by_type: Dict[str, int]
    coverage_percentage: float
    builder_version: str
    question_catalogue_version: str
    rubric_catalogue_version: str


class BlueprintValidationReport(FrozenSchema):
    """Typed validation report produced by InterviewPolicyValidator."""
    is_valid: bool
    blocking_errors: List[str]
    warnings: List[str]
    checked_rules_count: int


class GeneratedDeterministicBlueprint(FrozenSchema):
    """Container for the complete deterministic common-core interview output."""
    blueprint_id: UUID
    blueprint_version: int
    lifecycle_status: PlanLifecycle
    input_snapshot_hash: str
    content_hash: str
    common_core_questions: List[InterviewQuestion]
    coverage_summary: BlueprintCoverageSummary
    validation_report: BlueprintValidationReport
    policy_reference: PolicyVersionReference
    builder_version: str
    question_catalogue_version: str
    rubric_catalogue_version: str


# =========================================================================
# Step 3: Interview Eligibility Rules
# =========================================================================

def evaluate_requirement_interview_suitability(
    req: JobRequirementSnapshotItem,
) -> RequirementClassificationResult:
    """Classifies requirement suitability for interview assessment using explicit deterministic rules."""
    norm_label = req.normalized_label.lower().strip()

    # Rule 1: Credentials, Degrees, Certifications, and Licences -> DOCUMENT_VERIFICATION
    if req.category in {RequirementCategory.EDUCATION, RequirementCategory.CERTIFICATION}:
        return RequirementClassificationResult(
            requirement_id=req.requirement_id,
            label=req.label,
            category=req.category,
            suitability=InterviewSuitability.DOCUMENT_VERIFICATION,
            reason="Formal credentials, degrees, and certifications are evaluated via document verification.",
            mapped_competency_key=None,
        )

    # Rule 2: Languages -> DOCUMENT_VERIFICATION or WORK_SAMPLE unless technical/programming
    if req.category == RequirementCategory.LANGUAGE:
        # Check if it's a programming language mistakenly categorized
        if any(prog in norm_label for prog in ["python", "sql", "golang", "java", "c++", "rust", "typescript", "javascript"]):
            # Reclassify as interview eligible technical skill
            comp_key = _resolve_competency_key(norm_label)
            return RequirementClassificationResult(
                requirement_id=req.requirement_id,
                label=req.label,
                category=req.category,
                suitability=InterviewSuitability.INTERVIEW_ELIGIBLE,
                reason="Programming language technical skill suitable for technical interview evaluation.",
                mapped_competency_key=comp_key,
            )
        return RequirementClassificationResult(
            requirement_id=req.requirement_id,
            label=req.label,
            category=req.category,
            suitability=InterviewSuitability.DOCUMENT_VERIFICATION,
            reason="Natural spoken language fluency is verified via standardized language proficiency documentation or conversation assessment.",
            mapped_competency_key=None,
        )

    # Rule 3: Other administrative items
    if req.category == RequirementCategory.OTHER:
        if any(word in norm_label for word in ["citizenship", "visa", "clearance", "background", "residence", "relocation"]):
            return RequirementClassificationResult(
                requirement_id=req.requirement_id,
                label=req.label,
                category=req.category,
                suitability=InterviewSuitability.OUT_OF_INTERVIEW,
                reason="Legal eligibility and administrative requirements are evaluated out-of-interview.",
                mapped_competency_key=None,
            )

    # Rule 4: Skills, Experiences, and Responsibilities -> INTERVIEW_ELIGIBLE
    comp_key = _resolve_competency_key(norm_label)
    return RequirementClassificationResult(
        requirement_id=req.requirement_id,
        label=req.label,
        category=req.category,
        suitability=InterviewSuitability.INTERVIEW_ELIGIBLE,
        reason="Demonstrated technical, architectural, or behavioral domain competency suitable for structured interview assessment.",
        mapped_competency_key=comp_key,
    )


def _resolve_competency_key(normalized_label: str) -> Optional[str]:
    """Resolves a normalized label to a catalogue competency key using deterministic mapping."""
    norm = normalized_label.lower().strip()
    if norm in COMPETENCY_KEY_MAPPINGS:
        return COMPETENCY_KEY_MAPPINGS[norm]

    # Partial keyword matching
    for kw, ckey in COMPETENCY_KEY_MAPPINGS.items():
        if kw in norm:
            return ckey

    return None


# =========================================================================
# Step 4: Deterministic Competency Selection
# =========================================================================

def _compute_requirement_priority_weight(req: JobRequirementSnapshotItem) -> float:
    """Computes a deterministic priority score using approved job values."""
    score = 0.0

    # Necessity component
    if req.necessity == RequirementNecessity.MUST_HAVE:
        score += 100.0
    else:
        score += 50.0

    # Priority component
    if req.priority == RequirementPriority.CRITICAL:
        score += 40.0
    elif req.priority == RequirementPriority.HIGH:
        score += 30.0
    elif req.priority == RequirementPriority.MEDIUM:
        score += 20.0
    elif req.priority == RequirementPriority.LOW:
        score += 10.0

    # Weight override component
    if req.weight is not None:
        score += req.weight * 5.0

    # Recency / Experience depth
    if req.min_experience_months:
        score += min(req.min_experience_months / 12.0, 5.0)

    return score


def select_deterministic_competencies(
    snapshot: JobBlueprintInputSnapshot,
    classifications: List[RequirementClassificationResult],
) -> Tuple[List[CompetencySelectionResult], List[str], List[str]]:
    """Selects 4 to 6 supported competencies using deterministic priority calculation and tie-breaking."""
    # Group eligible requirements by mapped competency
    eligible_by_comp: Dict[str, List[JobRequirementSnapshotItem]] = {}
    unsupported_labels: Set[str] = set()

    for item, req in zip(classifications, snapshot.approved_requirements):
        if item.suitability == InterviewSuitability.INTERVIEW_ELIGIBLE:
            if item.mapped_competency_key:
                comp_key = item.mapped_competency_key
                eligible_by_comp.setdefault(comp_key, []).append(req)
            else:
                unsupported_labels.add(item.label)

    # Compute aggregate priority per supported competency
    competency_candidates: List[CompetencySelectionResult] = []
    for comp_key, reqs in eligible_by_comp.items():
        # Check that catalogue has active templates
        cat_questions = get_catalogue_questions_for_competency(comp_key)
        if not cat_questions:
            for r in reqs:
                unsupported_labels.add(r.label)
            continue

        total_score = sum(_compute_requirement_priority_weight(r) for r in reqs)
        has_must_have = any(r.necessity == RequirementNecessity.MUST_HAVE for r in reqs)
        max_priority_name = max((r.priority.value for r in reqs), key=lambda p: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(p, 0))

        # Tie-breaker key: (must_have descending, total_score descending, max_priority descending, catalogue order, comp_key)
        first_q = cat_questions[0]
        comp_label = comp_key.replace("_", " ").title()

        competency_candidates.append(
            CompetencySelectionResult(
                competency_key=comp_key,
                competency_label=comp_label,
                priority_score=round(total_score, 4),
                selection_reason=(
                    f"Selected based on {len(reqs)} requirement(s) (Max Priority: {max_priority_name}, "
                    f"Must-Have: {has_must_have}, Aggregate Score: {total_score:.1f})"
                ),
                covered_requirement_ids=[r.requirement_id for r in sorted(reqs, key=lambda r: (r.display_order, str(r.requirement_id)))],
            )
        )

    # Deterministic Sort
    # Tie-breaking sequence:
    # 1. Total score descending
    # 2. Catalogue ordering
    # 3. Stable competency key
    def _comp_sort_key(c: CompetencySelectionResult):
        cat_q = get_catalogue_questions_for_competency(c.competency_key)
        cat_order = cat_q[0].catalogue_order if cat_q else 999
        return (-c.priority_score, cat_order, c.competency_key)

    sorted_candidates = sorted(competency_candidates, key=_comp_sort_key)

    # Select up to MAX_TARGET_COMPETENCIES (6)
    selected = sorted_candidates[:MAX_TARGET_COMPETENCIES]
    uncovered_eligible = [c.competency_key for c in sorted_candidates[MAX_TARGET_COMPETENCIES:]]

    return selected, uncovered_eligible, sorted(list(unsupported_labels))


# =========================================================================
# Step 5 & 6: Common-Question Allocation & Rubric Attachment
# =========================================================================

def allocate_common_core_questions(
    selected_competencies: List[CompetencySelectionResult],
    snapshot: JobBlueprintInputSnapshot,
    blueprint_id: UUID,
) -> List[InterviewQuestion]:
    """Allocates 6 to 10 common-core questions across selected competencies with attached anchored rubrics."""
    allocated_questions: List[InterviewQuestion] = []
    curr_order = 0

    if not selected_competencies:
        return []

    # Round 1: Assign one primary question to each selected competency
    assigned_template_ids: Set[str] = set()

    for comp in selected_competencies:
        cat_questions = get_catalogue_questions_for_competency(comp.competency_key)
        if not cat_questions:
            # Missing deterministic question in catalogue: attempt AI fallback or create manual-required slot
            ai_text = None
            ai_prov = None
            try:
                from app.services.provider_registry import ProviderRegistry
                reg = ProviderRegistry()
                profile = reg.get_active_profile(organization_id=snapshot.organization_id)
                if profile and profile.enabled:
                    from app.services.provider_adapter import BoundedWordingRequest
                    from app.schemas.phase3_enums import ProviderOperation
                    provider = reg.get_provider_instance(profile)
                    req = BoundedWordingRequest(
                        request_id=uuid.uuid4(),
                        organization_id=snapshot.organization_id,
                        operation=ProviderOperation.REWORD_QUESTION,
                        source_question_id=uuid.uuid4(),
                        target_competency=comp.competency_label,
                        deterministic_question_text=f"How do you demonstrate proficiency in {comp.competency_label} in your work?",
                        allowed_reference_ids=[str(r) for r in comp.covered_requirement_ids],
                        max_variants=1,
                    )
                    import anyio
                    resp = anyio.from_thread.run(provider.request_wording_assistance, req)
                    if resp and resp.variants:
                        var = resp.variants[0]
                        if not check_prohibited_topics(var.variant_text):
                            ai_text = var.variant_text.strip()
                            ai_prov = {
                                "provider_class": profile.provider_type.value,
                                "model_name": profile.model_identifier,
                                "applied_at": datetime.now(timezone.utc).isoformat(),
                                "rationale_code": "AI_FALLBACK_MISSING_DETERMINISTIC_QUESTION",
                            }
            except Exception as exc:
                logger.info("AI fallback generation not possible for competency %s: %s", comp.competency_key, exc)

            fallback_q = _build_fallback_question_for_missing_competency(
                competency=comp,
                display_order=curr_order,
                blueprint_id=blueprint_id,
                snapshot=snapshot,
                ai_generated_text=ai_text,
                ai_provenance=ai_prov,
            )
            allocated_questions.append(fallback_q)
            curr_order += 1
            continue

        primary_q_entry = cat_questions[0]
        rubric_entry = get_catalogue_rubric(primary_q_entry.linked_rubric_template_id)
        if not rubric_entry:
            raise ValueError(f"Rubric template '{primary_q_entry.linked_rubric_template_id}' not found for question '{primary_q_entry.template_id}'.")

        q_obj = _build_interview_question_from_catalogue(
            q_entry=primary_q_entry,
            rubric_entry=rubric_entry,
            competency=comp,
            display_order=curr_order,
            blueprint_id=blueprint_id,
            snapshot=snapshot,
        )
        allocated_questions.append(q_obj)
        assigned_template_ids.add(primary_q_entry.template_id)
        curr_order += 1

    # Round 2: If allocated questions < MIN_TARGET_QUESTIONS (6) or budget permits,
    # distribute additional depth questions to highest priority competencies
    for comp in selected_competencies:
        if len(allocated_questions) >= MAX_TARGET_QUESTIONS:
            break

        cat_questions = get_catalogue_questions_for_competency(comp.competency_key)
        # Look for secondary question in catalogue
        for secondary_q in cat_questions[1:]:
            if secondary_q.template_id in assigned_template_ids:
                continue

            # Check duration budget
            total_duration = sum(q.estimated_time_minutes for q in allocated_questions) + secondary_q.estimated_duration_minutes
            if total_duration > MAX_TOTAL_DURATION_MINUTES:
                break

            rubric_entry = get_catalogue_rubric(secondary_q.linked_rubric_template_id)
            if not rubric_entry:
                continue

            q_obj = _build_interview_question_from_catalogue(
                q_entry=secondary_q,
                rubric_entry=rubric_entry,
                competency=comp,
                display_order=curr_order,
                blueprint_id=blueprint_id,
                snapshot=snapshot,
            )
            allocated_questions.append(q_obj)
            assigned_template_ids.add(secondary_q.template_id)
            curr_order += 1

            if len(allocated_questions) >= MAX_TARGET_QUESTIONS:
                break

    return allocated_questions


def _build_interview_question_from_catalogue(
    q_entry: QuestionTemplateEntry,
    rubric_entry: RubricTemplateEntry,
    competency: CompetencySelectionResult,
    display_order: int,
    blueprint_id: UUID,
    snapshot: JobBlueprintInputSnapshot,
) -> InterviewQuestion:
    """Constructs an immutable InterviewQuestion domain object with attached InterviewRubric."""
    q_id = uuid.uuid4()
    rubric_id = uuid.uuid4()

    # Build rubric
    rubric = InterviewRubric(
        rubric_id=rubric_id,
        rubric_version=1,
        competency_definition=rubric_entry.competency_definition,
        question_intent=rubric_entry.question_intent,
        observable_indicators=list(rubric_entry.observable_indicators),
        rating_anchors={int(k): str(v) for k, v in rubric_entry.rating_anchors.items()},
        sufficient_evidence_guidance=rubric_entry.sufficient_evidence_guidance,
        insufficient_evidence_guidance=rubric_entry.insufficient_evidence_guidance,
        permitted_probes=list(q_entry.allowed_probes),
        prohibited_inferences=list(rubric_entry.prohibited_inferences),
        validation_state=ValidationState.PASSED,
        policy_reference=snapshot.policy_reference,
        schema_version=BLUEPRINT_SCHEMA_VERSION,
    )

    return InterviewQuestion(
        question_id=q_id,
        blueprint_id=blueprint_id,
        layer=InterviewLayer.COMMON_CORE,
        question_type=q_entry.question_type,
        competency_id=competency.competency_key,
        requirement_references=list(competency.covered_requirement_ids),
        source_match_result_id=None,
        source_gap_report_id=None,
        deterministic_template_id=q_entry.template_id,
        deterministic_template_version=q_entry.version,
        question_text=q_entry.text_template,
        question_intent=q_entry.question_intent,
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        display_order=display_order,
        estimated_time_minutes=q_entry.estimated_duration_minutes,
        permitted_probes=list(q_entry.allowed_probes),
        validation_state=ValidationState.PASSED,
        included_in_common_score=True,
        rubric=rubric,
        policy_reference=snapshot.policy_reference,
        schema_version=BLUEPRINT_SCHEMA_VERSION,
    )


def _build_fallback_question_for_missing_competency(
    competency: CompetencySelectionResult,
    display_order: int,
    blueprint_id: UUID,
    snapshot: JobBlueprintInputSnapshot,
    ai_generated_text: Optional[str] = None,
    ai_provenance: Optional[Dict[str, Any]] = None,
) -> InterviewQuestion:
    """Constructs a question slot when no catalogue question exists.
    
    If AI wording was successfully produced, origin is AI_ASSISTED.
    Otherwise, marks as unresolved manual-required with validation_state=PENDING.
    """
    q_id = uuid.uuid4()
    rubric_id = uuid.uuid4()

    is_ai = bool(ai_generated_text and len(ai_generated_text.strip()) > 0)
    q_text = ai_generated_text.strip() if is_ai else "[Manual question required]"
    origin = AuthoringOrigin.AI_ASSISTED if is_ai else AuthoringOrigin.DETERMINISTIC
    val_state = ValidationState.PASSED if is_ai else ValidationState.PENDING

    rubric = InterviewRubric(
        rubric_id=rubric_id,
        rubric_version=1,
        competency_definition=f"Demonstrated proficiency in {competency.competency_label}.",
        question_intent=f"Assess practical experience and competence in {competency.competency_label}.",
        observable_indicators=[
            f"Articulates core principles of {competency.competency_label}",
            f"Applies {competency.competency_label} to solve real-world problems",
            f"Demonstrates sound judgement and depth in {competency.competency_label}",
        ],
        rating_anchors={
            1: f"Fails to demonstrate foundational understanding of {competency.competency_label}.",
            3: f"Demonstrates competent standard working knowledge of {competency.competency_label}.",
            5: f"Demonstrates expert advanced mastery and nuanced knowledge of {competency.competency_label}.",
        },
        sufficient_evidence_guidance=f"Candidate provides concrete examples of applying {competency.competency_label}.",
        insufficient_evidence_guidance=f"Candidate provides vague or unsubstantiated claims regarding {competency.competency_label}.",
        permitted_probes=[
            f"Can you walk me through a specific project where you applied {competency.competency_label}?",
            f"What challenges did you face and how did you resolve them?",
        ],
        prohibited_inferences=[],
        validation_state=val_state,
        policy_reference=snapshot.policy_reference,
        schema_version=BLUEPRINT_SCHEMA_VERSION,
    )

    metadata: Dict[str, Any] = {}
    if is_ai:
        metadata = {
            "ai_fallback": True,
            "ai_provenance": ai_provenance or {},
            "reason": "AI_FALLBACK_MISSING_DETERMINISTIC_QUESTION",
        }
    else:
        metadata = {
            "unresolved_manual_required": True,
            "reason": "NO_DETERMINISTIC_QUESTION_AND_AI_UNAVAILABLE",
        }

    return InterviewQuestion(
        question_id=q_id,
        blueprint_id=blueprint_id,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL if "engineering" in competency.competency_key.lower() or "tech" in competency.competency_key.lower() or "python" in competency.competency_key.lower() else QuestionType.BEHAVIORAL,
        competency_id=competency.competency_key,
        requirement_references=list(competency.covered_requirement_ids),
        source_match_result_id=None,
        source_gap_report_id=None,
        deterministic_template_id=None,
        deterministic_template_version=None,
        question_text=q_text,
        question_intent=f"Evaluate candidate depth in {competency.competency_label}",
        authoring_origin=origin,
        display_order=display_order,
        estimated_time_minutes=6,
        permitted_probes=[
            f"Can you walk me through a specific project where you applied {competency.competency_label}?",
            f"What challenges did you face and how did you resolve them?",
        ],
        validation_state=val_state,
        included_in_common_score=True,
        rubric=rubric,
        policy_reference=snapshot.policy_reference,
        schema_version=BLUEPRINT_SCHEMA_VERSION,
    )


# =========================================================================
# Step 7: Calculate Coverage and Duration
# =========================================================================

def calculate_blueprint_coverage(
    snapshot: JobBlueprintInputSnapshot,
    classifications: List[RequirementClassificationResult],
    selected_competencies: List[CompetencySelectionResult],
    uncovered_eligible: List[str],
    unsupported_competencies: List[str],
    questions: List[InterviewQuestion],
) -> BlueprintCoverageSummary:
    """Calculates coverage summary and duration statistics."""
    total_reqs = len(snapshot.approved_requirements)
    eligible_reqs = [c for c in classifications if c.suitability == InterviewSuitability.INTERVIEW_ELIGIBLE]
    doc_reqs = [c for c in classifications if c.suitability == InterviewSuitability.DOCUMENT_VERIFICATION]
    work_sample_reqs = [c for c in classifications if c.suitability == InterviewSuitability.WORK_SAMPLE_PREFERRED]
    out_of_interview_reqs = [c for c in classifications if c.suitability == InterviewSuitability.OUT_OF_INTERVIEW]

    # Covered requirement IDs across selected competencies
    covered_ids: Set[UUID] = set()
    for comp in selected_competencies:
        covered_ids.update(comp.covered_requirement_ids)

    # Type distribution
    type_counts: Dict[str, int] = {}
    for q in questions:
        q_type_str = q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type)
        type_counts[q_type_str] = type_counts.get(q_type_str, 0) + 1

    total_duration = sum(q.estimated_time_minutes for q in questions)
    coverage_pct = (len(covered_ids) / len(eligible_reqs) * 100.0) if eligible_reqs else 0.0

    return BlueprintCoverageSummary(
        total_requirements_count=total_reqs,
        interview_eligible_requirements_count=len(eligible_reqs),
        covered_requirements_count=len(covered_ids),
        selected_competencies=selected_competencies,
        uncovered_eligible_competencies=uncovered_eligible,
        unsupported_competencies=unsupported_competencies,
        document_verification_requirements=doc_reqs,
        work_sample_requirements=work_sample_reqs,
        out_of_interview_requirements=out_of_interview_reqs,
        total_estimated_duration_minutes=total_duration,
        question_count_by_type=type_counts,
        coverage_percentage=round(coverage_pct, 2),
        builder_version=DETERMINISTIC_BUILDER_VERSION,
        question_catalogue_version=QUESTION_CATALOGUE_VERSION,
        rubric_catalogue_version=RUBRIC_CATALOGUE_VERSION,
    )


# =========================================================================
# Step 8: Validate the Blueprint (InterviewPolicyValidator)
# =========================================================================

class InterviewPolicyValidator:
    """Authoritative policy validator for interview blueprints."""

    def validate_blueprint(
        self,
        questions: List[InterviewQuestion],
        snapshot: JobBlueprintInputSnapshot,
        coverage: BlueprintCoverageSummary,
    ) -> BlueprintValidationReport:
        blocking_errors: List[str] = []
        warnings: List[str] = []
        rule_checks = 0

        # Rule 1: Layer and Origin Integrity
        rule_checks += 1
        for q in questions:
            if q.layer != InterviewLayer.COMMON_CORE:
                blocking_errors.append(f"Question '{q.question_id}' has invalid layer '{q.layer}'; must be COMMON_CORE.")
            if q.authoring_origin not in {AuthoringOrigin.DETERMINISTIC, AuthoringOrigin.RECRUITER_EDITED, AuthoringOrigin.AI_ASSISTED}:
                blocking_errors.append(f"Question '{q.question_id}' has invalid origin '{q.authoring_origin}'; must be DETERMINISTIC, RECRUITER_EDITED, or AI_ASSISTED.")
            if not q.included_in_common_score:
                blocking_errors.append(f"Common core question '{q.question_id}' must have included_in_common_score=True.")
            q_meta = getattr(q, "safe_metadata", {}) or {}
            if (
                q_meta.get("unresolved_manual_required")
                or not q.question_text
                or q.question_text.strip() == "[Manual question required]"
            ):
                blocking_errors.append(
                    f"Question {q.display_order} ({q.competency_id}) requires manual recruiter entry before validation."
                )

        # Rule 2: Rubrics and Rating Anchors
        rule_checks += 1
        for q in questions:
            if q.rubric is None:
                blocking_errors.append(f"Question '{q.question_id}' is missing required rubric.")
                continue
            required_anchors = {1, 3, 5}
            actual_anchors = set(q.rubric.rating_anchors.keys())
            if not required_anchors.issubset(actual_anchors):
                blocking_errors.append(f"Rubric for question '{q.question_id}' is missing required scale points {required_anchors - actual_anchors}.")

        # Rule 3: Prohibited Topics and Protected Characteristics
        rule_checks += 1
        for q in questions:
            # Question texts
            for text_val in [q.question_text, q.question_intent] + q.permitted_probes:
                violation = check_prohibited_topics(text_val)
                if violation:
                    blocking_errors.append(f"Prohibited topic '{violation[0]}' (matched '{violation[1]}') in question '{q.question_id}'.")
            # Rubric texts
            if q.rubric:
                for text_val in [
                    q.rubric.competency_definition,
                    q.rubric.question_intent,
                    q.rubric.sufficient_evidence_guidance,
                    q.rubric.insufficient_evidence_guidance,
                ] + list(q.rubric.rating_anchors.values()):
                    violation = check_prohibited_topics(text_val)
                    if violation:
                        blocking_errors.append(f"Prohibited topic '{violation[0]}' (matched '{violation[1]}') in rubric for question '{q.question_id}'.")

        # Rule 4: Probe Limits
        rule_checks += 1
        for q in questions:
            if len(q.permitted_probes) > 2:
                blocking_errors.append(f"Question '{q.question_id}' exceeds maximum of 2 allowed probes (found {len(q.permitted_probes)}).")

        # Rule 5: Unique Question Order and Non-duplicate text
        rule_checks += 1
        display_orders = [q.display_order for q in questions]
        if len(display_orders) != len(set(display_orders)):
            blocking_errors.append("Question display orders contain duplicates.")

        template_ids = [q.deterministic_template_id for q in questions if q.deterministic_template_id]
        if len(template_ids) != len(set(template_ids)):
            blocking_errors.append("Blueprint contains duplicate question template uses.")

        # Rule 6: Competency & Requirement references exist in snapshot
        rule_checks += 1
        valid_req_ids = {r.requirement_id for r in snapshot.approved_requirements}
        for q in questions:
            for rid in q.requirement_references:
                if rid not in valid_req_ids:
                    blocking_errors.append(f"Question '{q.question_id}' references unknown requirement ID '{rid}'.")

        # Rule 7: Target bounds warnings
        rule_checks += 1
        if len(coverage.selected_competencies) < MIN_TARGET_COMPETENCIES:
            warnings.append(
                f"Selected competencies count ({len(coverage.selected_competencies)}) is below target ({MIN_TARGET_COMPETENCIES}). "
                "Available interview-eligible content in job requisition is limited."
            )

        if len(questions) < MIN_TARGET_QUESTIONS:
            warnings.append(
                f"Generated question count ({len(questions)}) is below target ({MIN_TARGET_QUESTIONS}) due to catalogue coverage."
            )

        if coverage.total_estimated_duration_minutes < MIN_TOTAL_DURATION_MINUTES:
            warnings.append(
                f"Total estimated interview duration ({coverage.total_estimated_duration_minutes}m) is below target ({MIN_TOTAL_DURATION_MINUTES}m)."
            )
        elif coverage.total_estimated_duration_minutes > MAX_TOTAL_DURATION_MINUTES:
            warnings.append(
                f"Total estimated interview duration ({coverage.total_estimated_duration_minutes}m) exceeds target ({MAX_TOTAL_DURATION_MINUTES}m)."
            )

        if coverage.unsupported_competencies:
            warnings.append(
                f"Job contains {len(coverage.unsupported_competencies)} unsupported competency label(s) without catalogue coverage."
            )

        return BlueprintValidationReport(
            is_valid=len(blocking_errors) == 0,
            blocking_errors=blocking_errors,
            warnings=warnings,
            checked_rules_count=rule_checks,
        )


# =========================================================================
# Step 9: Canonicalization and Content Hashing
# =========================================================================

def canonicalize_deterministic_blueprint(
    snapshot: JobBlueprintInputSnapshot,
    questions: List[InterviewQuestion],
    coverage: BlueprintCoverageSummary,
) -> Dict[str, Any]:
    """Produces a canonical dictionary representation of the generated deterministic blueprint."""
    sorted_questions = sorted(questions, key=lambda q: (q.display_order, str(q.question_id)))

    q_normalized = []
    for q in sorted_questions:
        rubric_dict = None
        if q.rubric:
            rubric_dict = {
                "competency_definition": q.rubric.competency_definition.strip(),
                "insufficient_evidence_guidance": q.rubric.insufficient_evidence_guidance.strip(),
                "observable_indicators": sorted([oi.strip() for oi in q.rubric.observable_indicators]),
                "permitted_probes": sorted([p.strip() for p in q.rubric.permitted_probes]),
                "prohibited_inferences": sorted([pi.strip() for pi in q.rubric.prohibited_inferences]),
                "rating_anchors": {str(k): v.strip() for k, v in sorted(q.rubric.rating_anchors.items())},
                "rubric_version": q.rubric.rubric_version,
                "sufficient_evidence_guidance": q.rubric.sufficient_evidence_guidance.strip(),
            }

        q_normalized.append({
            "authoring_origin": q.authoring_origin.value if hasattr(q.authoring_origin, "value") else str(q.authoring_origin),
            "competency_id": q.competency_id,
            "deterministic_template_id": q.deterministic_template_id,
            "deterministic_template_version": q.deterministic_template_version,
            "display_order": q.display_order,
            "estimated_time_minutes": q.estimated_time_minutes,
            "included_in_common_score": q.included_in_common_score,
            "layer": q.layer.value if hasattr(q.layer, "value") else str(q.layer),
            "permitted_probes": sorted([p.strip() for p in q.permitted_probes]),
            "question_intent": q.question_intent.strip(),
            "question_text": q.question_text.strip(),
            "question_type": q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type),
            "requirement_references": sorted([str(rid) for rid in q.requirement_references]),
            "rubric": rubric_dict,
        })

    comps_normalized = []
    for c in sorted(coverage.selected_competencies, key=lambda comp: comp.competency_key):
        comps_normalized.append({
            "competency_key": c.competency_key,
            "covered_requirement_ids": sorted([str(rid) for rid in c.covered_requirement_ids]),
            "priority_score": round(float(c.priority_score), 4),
        })

    return {
        "builder_version": coverage.builder_version,
        "input_snapshot_hash": compute_job_blueprint_snapshot_hash(snapshot),
        "job_id": str(snapshot.job_id),
        "job_revision": snapshot.revision,
        "job_version_id": str(snapshot.job_version_id),
        "policy_reference": {
            "policy_hash": snapshot.policy_reference.policy_hash,
            "policy_id": snapshot.policy_reference.policy_id,
            "policy_version": snapshot.policy_reference.policy_version,
            "schema_version": snapshot.policy_reference.schema_version,
        },
        "question_catalogue_version": coverage.question_catalogue_version,
        "questions": q_normalized,
        "rubric_catalogue_version": coverage.rubric_catalogue_version,
        "selected_competencies": comps_normalized,
        "total_estimated_duration_minutes": coverage.total_estimated_duration_minutes,
    }


def compute_deterministic_blueprint_content_hash(
    snapshot: JobBlueprintInputSnapshot,
    questions: List[InterviewQuestion],
    coverage: BlueprintCoverageSummary,
) -> str:
    """Computes deterministic SHA-256 hash of the generated interview blueprint content."""
    canonical_dict = canonicalize_deterministic_blueprint(snapshot, questions, coverage)
    serialized = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()


# =========================================================================
# Step 4, 10 & 11: InterviewBlueprintBuilder & Service Orchestration
# =========================================================================

class InterviewBlueprintBuilder:
    """Core domain builder that constructs deterministic common-core interview blueprints."""

    def __init__(self):
        self.validator = InterviewPolicyValidator()

    def generate_blueprint_preview(
        self,
        snapshot: JobBlueprintInputSnapshot,
    ) -> GeneratedDeterministicBlueprint:
        """Pure domain generation that converts authoritative job snapshot into a validated deterministic blueprint."""
        blueprint_id = uuid.uuid4()
        input_hash = compute_job_blueprint_snapshot_hash(snapshot)

        # 1. Suitability classification
        classifications = [
            evaluate_requirement_interview_suitability(req)
            for req in snapshot.approved_requirements
        ]

        # 2. Competency Selection
        selected_comps, uncovered, unsupported = select_deterministic_competencies(
            snapshot=snapshot,
            classifications=classifications,
        )

        # 3. Question Allocation & Rubric Attachment
        questions = allocate_common_core_questions(
            selected_competencies=selected_comps,
            snapshot=snapshot,
            blueprint_id=blueprint_id,
        )

        # 4. Coverage and Duration Calculation
        coverage = calculate_blueprint_coverage(
            snapshot=snapshot,
            classifications=classifications,
            selected_competencies=selected_comps,
            uncovered_eligible=uncovered,
            unsupported_competencies=unsupported,
            questions=questions,
        )

        # 5. Policy Validation
        val_report = self.validator.validate_blueprint(
            questions=questions,
            snapshot=snapshot,
            coverage=coverage,
        )

        # 6. Content Hashing
        content_hash = compute_deterministic_blueprint_content_hash(
            snapshot=snapshot,
            questions=questions,
            coverage=coverage,
        )

        return GeneratedDeterministicBlueprint(
            blueprint_id=blueprint_id,
            blueprint_version=1,
            lifecycle_status=PlanLifecycle.DRAFT,
            input_snapshot_hash=input_hash,
            content_hash=content_hash,
            common_core_questions=questions,
            coverage_summary=coverage,
            validation_report=val_report,
            policy_reference=snapshot.policy_reference,
            builder_version=DETERMINISTIC_BUILDER_VERSION,
            question_catalogue_version=QUESTION_CATALOGUE_VERSION,
            rubric_catalogue_version=RUBRIC_CATALOGUE_VERSION,
        )

    def persist_blueprint_draft(
        self,
        actor: UserRecord,
        snapshot: JobBlueprintInputSnapshot,
        blueprint: GeneratedDeterministicBlueprint,
        db: Session,
    ) -> JobInterviewTemplateRecord:
        """Persists the generated blueprint as a JobInterviewTemplateRecord DRAFT."""
        if not blueprint.validation_report.is_valid:
            raise InterviewSnapshotError(
                code="INTERVIEW_BLUEPRINT_INVALID",
                message=f"Cannot persist invalid interview blueprint: {', '.join(blueprint.validation_report.blocking_errors)}",
                status_code=422,
            )

        # Idempotency check: look for existing DRAFT template with identical input_snapshot_hash and content_hash
        stmt_existing = (
            select(JobInterviewTemplateRecord)
            .options(
                selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric)
            )
            .where(
                JobInterviewTemplateRecord.job_id == snapshot.job_id,
                JobInterviewTemplateRecord.job_version_id == snapshot.job_version_id,
                JobInterviewTemplateRecord.input_snapshot_hash == blueprint.input_snapshot_hash,
                JobInterviewTemplateRecord.content_hash == blueprint.content_hash,
                JobInterviewTemplateRecord.lifecycle_status == PlanLifecycle.DRAFT,
            )
        )
        existing_template = db.execute(stmt_existing).scalar_one_or_none()
        if existing_template is not None:
            return existing_template

        # Determine next template version for this job
        stmt_max_ver = (
            select(JobInterviewTemplateRecord.template_version)
            .where(JobInterviewTemplateRecord.job_id == snapshot.job_id)
            .order_by(JobInterviewTemplateRecord.template_version.desc())
        )
        max_ver = db.execute(stmt_max_ver).scalars().first()
        next_template_version = (max_ver or 0) + 1

        template_record = JobInterviewTemplateRecord(
            template_id=blueprint.blueprint_id,
            organization_id=snapshot.organization_id,
            job_id=snapshot.job_id,
            job_version_id=snapshot.job_version_id,
            revision=snapshot.revision,
            template_version=next_template_version,
            lifecycle_status=PlanLifecycle.DRAFT,
            guardrail_policy_version=snapshot.policy_reference.policy_version,
            guardrail_policy_hash=snapshot.policy_reference.policy_hash,
            deterministic_builder_version=blueprint.builder_version,
            question_catalogue_version=blueprint.question_catalogue_version,
            rubric_catalogue_version=blueprint.rubric_catalogue_version,
            input_snapshot_hash=blueprint.input_snapshot_hash,
            content_hash=blueprint.content_hash,
            created_by_user_id=actor.user_id,
            record_version=1,
        )
        db.add(template_record)
        db.flush()

        # Persist questions and rubrics
        for q in blueprint.common_core_questions:
            q_rec = InterviewQuestionRecord(
                question_id=q.question_id,
                template_id=template_record.template_id,
                question_version=1,
                layer=q.layer,
                question_type=q.question_type,
                authoring_origin=q.authoring_origin,
                display_order=q.display_order,
                competency_id=q.competency_id,
                requirement_ids=[str(rid) for rid in q.requirement_references],
                deterministic_template_id=q.deterministic_template_id,
                deterministic_template_version=q.deterministic_template_version,
                question_text=q.question_text,
                question_intent=q.question_intent,
                estimated_time_minutes=q.estimated_time_minutes,
                included_in_common_score=q.included_in_common_score,
                validation_state=q.validation_state,
                permitted_probes=list(q.permitted_probes),
                safe_metadata={},
            )
            db.add(q_rec)
            db.flush()

            if q.rubric:
                rubric_rec = InterviewRubricRecord(
                    rubric_id=q.rubric.rubric_id,
                    question_id=q_rec.question_id,
                    rubric_version=q.rubric.rubric_version,
                    competency_definition=q.rubric.competency_definition,
                    question_intent=q.rubric.question_intent,
                    observable_indicators=list(q.rubric.observable_indicators),
                    rating_anchors={str(k): str(v) for k, v in q.rubric.rating_anchors.items()},
                    sufficient_evidence_guidance=q.rubric.sufficient_evidence_guidance,
                    insufficient_evidence_guidance=q.rubric.insufficient_evidence_guidance,
                    incomplete_answer_guidance=None,
                    permitted_probes=list(q.rubric.permitted_probes),
                    prohibited_inferences=list(q.rubric.prohibited_inferences),
                    subject_matter_review_status="REVIEWED",
                    validation_state=q.rubric.validation_state,
                )
                db.add(rubric_rec)

        db.commit()
        db.refresh(template_record)
        return template_record


# Exported Service Functions

def preview_deterministic_interview_template(
    actor: UserRecord,
    job_id: UUID,
    expected_revision: Optional[int],
    db: Session,
    organization_id: Optional[UUID] = None,
) -> GeneratedDeterministicBlueprint:
    """Loads authoritative job snapshot and generates a deterministic blueprint preview."""
    snapshot, _ = load_authoritative_job_blueprint_snapshot(
        actor=actor,
        job_id=job_id,
        expected_revision=expected_revision,
        db=db,
        organization_id=organization_id,
    )
    builder = InterviewBlueprintBuilder()
    return builder.generate_blueprint_preview(snapshot)


def create_deterministic_interview_template_draft(
    actor: UserRecord,
    job_id: UUID,
    expected_revision: Optional[int],
    db: Session,
    organization_id: Optional[UUID] = None,
) -> Tuple[JobInterviewTemplateRecord, GeneratedDeterministicBlueprint]:
    """Loads authoritative job snapshot, generates blueprint, and persists a DRAFT template record."""
    snapshot, _ = load_authoritative_job_blueprint_snapshot(
        actor=actor,
        job_id=job_id,
        expected_revision=expected_revision,
        db=db,
        organization_id=organization_id,
    )
    builder = InterviewBlueprintBuilder()
    blueprint = builder.generate_blueprint_preview(snapshot)
    template_record = builder.persist_blueprint_draft(
        actor=actor,
        snapshot=snapshot,
        blueprint=blueprint,
        db=db,
    )
    return template_record, blueprint
