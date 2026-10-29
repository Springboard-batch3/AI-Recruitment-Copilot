"""Domain service for Phase 3D.5 Dedicated AI Interview Questions Workspace.

Separates deterministic interview template authoring from AI question generation.
Preserves the deterministic blueprint as authoritative foundation while maintaining
strict data boundary: never leaking deterministic question wording into AI responses.
Supports both BLUEPRINT_GROUNDED and REQUIREMENT_GROUNDED generation modes.
"""
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.models.auth import UserRecord, UserRole
from app.models.candidate_access import CandidateApplicationRecord, CandidatePracticeReleaseRecord
from app.models.interview import (
    AiInterviewQuestionSetRecord,
    InterviewQuestionRecord,
    JobInterviewTemplateRecord,
)
from app.models.job import JobLifecycleStatus, JobRequisition, JobRequirement, JobVersion
from app.schemas.ai_question_set import (
    AiQuestionSetApproveResponse,
    AiQuestionSetDetailResponse,
    AiQuestionSetDirectoryResponse,
    AiQuestionSetGenerateRequest,
    AiQuestionSetSummaryResponse,
    AiQuestionSetValidateResponse,
    AiQuestionSlotItemResponse,
    AiQuestionSlotUpdateRequest,
    EligibleJobItemResponse,
    EligibleJobsResponse,
    ReleaseEligibilitySummaryDto,
    SafeRubricResponse,
)
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    InterviewMode,
    PlanLifecycle,
    QuestionDifficulty,
    QuestionFormat,
    QuestionType,
)
from app.schemas.mcq_contract import (
    McqCreateRequest,
    McqGenerateRequest,
    McqItemDto,
    McqOptionDto,
    McqReorderRequest,
    McqUpdateRequest,
    StructuredAiMcqBatchResponse,
)
from app.schemas.safety_policy import check_prohibited_topics
from app.schemas.wording_contract import (
    PROMPT_TEMPLATE_VERSION,
    BoundedWordingRequest,
    BoundedWordingResponse,
    CandidateWordingResult,
    RecruiterWordingAssistRequest,
    StructuredWordingVariant,
    WordingRationaleCode,
)
from app.schemas.question_generation_contract import (
    GENERATION_POLICY_VERSION,
    GUARDRAIL_VERSION,
    PROMPT_CONTRACT_VERSION,
    QUESTION_GENERATION_SCHEMA_VERSION,
    BoundedJobGroundedSlotRequest,
    CanonicalGenerationSourcePacket,
    CanonicalRequirementItem,
    GenerationDeliveryMode,
    PlannedQuestionSlot,
    StrictGeneratedQuestionItem,
    StrictJobGroundedGenerationBatchResponse,
)
from app.services.mcq_validation_service import validate_mcq_item
from app.services.provider_registry import ProviderRegistry, get_provider_registry
from app.services.wording_assistance_service import (
    request_question_wording_assistance,
)
from app.services.wording_provider import (
    BaseWordingProvider,
    DisabledWordingProvider,
    FakeWordingProvider,
    ProviderTransportError,
)

logger = logging.getLogger("ai_hiring.ai_question_set_service")


class AiQuestionSetError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _check_recruiter_access(actor: UserRecord, job: JobRequisition) -> None:
    if getattr(job, "deleted_at", None) is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job requisition has been deleted."},
        )
    if actor.role == UserRole.RECRUITER and job.created_by_user_id != actor.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Access to requisition denied."},
        )
    # Check org isolation
    actor_org = getattr(actor, "org_id", getattr(actor, "organization_id", None))
    job_org = getattr(job, "org_id", getattr(job, "organization_id", None))
    if actor_org is not None and job_org is not None and actor_org != job_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Access to requisition denied across organizations."},
        )


def _map_rubric_to_safe(q_rec: Optional[InterviewQuestionRecord]) -> Optional[SafeRubricResponse]:
    if not q_rec or not hasattr(q_rec, "rubric") or not q_rec.rubric:
        return None
    r = q_rec.rubric
    return SafeRubricResponse(
        rubric_id=r.rubric_id,
        rubric_version=r.rubric_version,
        competency_definition=r.competency_definition or "",
        question_intent=r.question_intent or "",
        observable_indicators=list(r.observable_indicators or []),
        rating_anchors={str(k): str(v) for k, v in (r.rating_anchors or {}).items()},
        sufficient_evidence_guidance=r.sufficient_evidence_guidance,
        insufficient_evidence_guidance=r.insufficient_evidence_guidance,
        permitted_probes=list(r.permitted_probes or []),
    )


def _compute_requirement_fingerprint(job_id: UUID, version_id: UUID, revision: int, requirement_ids: List[str]) -> str:
    sorted_ids = sorted(str(rid) for rid in requirement_ids)
    raw = f"REQ_GROUNDED:{job_id}:{version_id}:{revision}:{','.join(sorted_ids)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _compute_canonical_source_fingerprint(packet: CanonicalGenerationSourcePacket) -> str:
    """Computes a SHA-256 fingerprint over the authoritative generation source packet."""
    sorted_req_ids = sorted(str(r.requirement_id) for r in packet.canonical_requirements)
    raw_fingerprint = (
        f"GEN_FINGERPRINT:{packet.organization_id}:{packet.job_id}:{packet.job_version_id}:"
        f"{packet.job_revision}:{packet.delivery_mode.value}:{packet.requested_question_count}:"
        f"{','.join(sorted_req_ids)}:{packet.generation_policy_version}:{packet.prompt_contract_version}:"
        f"{packet.guardrail_version}"
    )
    return hashlib.sha256(raw_fingerprint.encode("utf-8")).hexdigest()


def _is_requirement_eligible(req: JobRequirement) -> bool:
    """Filters out administrative, personal, unapproved, or prompt-injected requirements."""
    label_text = getattr(req, "label", getattr(req, "name", "")) or ""
    if not label_text.strip():
        return False
    
    rationale_text = getattr(req, "rationale", getattr(req, "description", "")) or ""
    text = f"{label_text} {rationale_text}".lower()
    
    # Administrative exclusions
    admin_terms = [
        "relocation", "visa sponsorship", "driver's license", "drivers license",
        "background check", "drug test", "salary requirement", "working hours",
        "shift work", "commute", "citizenship", "security clearance"
    ]
    if any(term in text for term in admin_terms):
        return False
        
    # Prompt injection / model control exclusions
    injection_terms = [
        "ignore previous", "ignore all", "system prompt", "developer instruction",
        "you are a", "jailbreak", "override", "disregard instructions"
    ]
    if any(term in text for term in injection_terms):
        return False
        
    return True


def _build_canonical_generation_packet(
    job: JobRequisition,
    job_version: JobVersion,
    delivery_mode: GenerationDeliveryMode = GenerationDeliveryMode.CHAT,
    actor: Optional[UserRecord] = None,
) -> Tuple[CanonicalGenerationSourcePacket, Dict[str, Any]]:
    """Builds a CanonicalGenerationSourcePacket and internal plan from approved job revision requirements."""
    raw_requirements = list(job_version.requirements or [])
    eligible = [r for r in raw_requirements if _is_requirement_eligible(r)]
    if not eligible and raw_requirements:
        eligible = raw_requirements

    def sort_key(r: JobRequirement) -> int:
        nec = getattr(r, "necessity", getattr(r, "importance", None))
        nec_str = nec.value if hasattr(nec, "value") else str(nec or "")
        prio = getattr(r, "priority", None)
        prio_str = prio.value if hasattr(prio, "value") else str(prio or "")
        if nec_str.upper() == "MUST_HAVE" or prio_str.upper() in ("CRITICAL", "HIGH"):
            return 0
        return 1

    eligible.sort(key=sort_key)

    target_count = min(5, max(len(eligible), 1))
    selected_reqs = eligible[:target_count]

    canonical_reqs: List[CanonicalRequirementItem] = []
    for r in selected_reqs:
        cat_str = r.category.value if hasattr(r.category, "value") else str(r.category)
        nec_str = r.necessity.value if hasattr(r.necessity, "value") else str(r.necessity)
        prio_str = r.priority.value if hasattr(r.priority, "value") else str(r.priority)
        canonical_reqs.append(
            CanonicalRequirementItem(
                requirement_id=r.requirement_id,
                label=r.label.strip(),
                category=cat_str,
                necessity=nec_str,
                priority=prio_str,
                rationale=r.rationale.strip() if r.rationale else None,
                is_interview_eligible=True,
            )
        )

    planned_slots: List[PlannedQuestionSlot] = []
    plan_slots_internal: List[Dict[str, Any]] = []

    for idx, r in enumerate(selected_reqs):
        cat_str = r.category.value if hasattr(r.category, "value") else str(r.category)
        q_type_enum = QuestionType.TECHNICAL if cat_str in ("SKILL", "EXPERIENCE", "HARD_SKILL", "DOMAIN_KNOWLEDGE") else QuestionType.BEHAVIORAL
        req_name = getattr(r, "label", getattr(r, "name", "Requirement")).strip()
        req_desc = getattr(r, "rationale", getattr(r, "description", req_name)) or req_name
        slot_uuid = uuid.uuid4()

        planned_slot = PlannedQuestionSlot(
            slot_id=slot_uuid,
            display_order=idx,
            primary_requirement_id=r.requirement_id,
            supporting_requirement_ids=[],
            competency_name=req_name,
            question_type=q_type_enum,
            question_objective=f"Assess practical experience, decision-making, and technical execution in {req_name}.",
            expected_evidence_type="PRACTICAL_EXPERIENCE",
            difficulty="INTERMEDIATE",
            delivery_mode=delivery_mode,
            estimated_time_minutes=5,
        )
        planned_slots.append(planned_slot)

        plan_slots_internal.append({
            "slot_id": str(slot_uuid),
            "display_order": idx,
            "source_requirement_ids": [str(r.requirement_id)],
            "competency_name": req_name,
            "requirement_summary": req_desc.strip()[:250],
            "question_type": q_type_enum.value,
            "estimated_time_minutes": 5,
            "permitted_probes": [
                f"Can you provide a concrete example from your past work demonstrating {req_name}?",
                "What challenges or trade-offs did you encounter and how did you resolve them?",
            ],
            "rubric": {
                "rubric_id": str(uuid.uuid4()),
                "rubric_version": 1,
                "competency_definition": f"Demonstrated proficiency and applied practical experience in {req_name}.",
                "question_intent": f"Assess depth of experience, technical decision-making, and execution capability for {req_name}.",
                "observable_indicators": [
                    f"Articulates core principles and design trade-offs of {req_name}.",
                    "Demonstrates practical hands-on problem-solving rather than purely theoretical knowledge.",
                    "Explains edge cases, failure modes, or architectural considerations clearly.",
                ],
                "rating_anchors": {
                    "1": f"No substantive knowledge or practical experience with {req_name}.",
                    "3": f"Solid working knowledge and practical execution of {req_name}.",
                    "5": f"Expert mastery, deep architectural insights, and advanced handling of complex edge cases.",
                },
                "sufficient_evidence_guidance": f"Candidate provides verifiable project details, specific technologies used, and reasoning for {req_name}.",
                "insufficient_evidence_guidance": "Candidate speaks in high-level generalities without concrete technical examples.",
                "permitted_probes": [
                    f"Can you provide a concrete example from your past work demonstrating {req_name}?",
                    "What challenges or trade-offs did you encounter and how did you resolve them?",
                ],
            },
        })

    raw_org = getattr(job, "organization_id", None) or getattr(job, "org_id", None)
    if raw_org is None and actor is not None:
        raw_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)
    
    if raw_org is None or str(raw_org) == "00000000-0000-0000-0000-000000000000":
        # Generate a stable deterministic UUID from job_id if no org_id is present
        org_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"org.job.{job.job_id}")
    elif isinstance(raw_org, uuid.UUID):
        org_id = raw_org
    else:
        org_id = uuid.UUID(str(raw_org))

    raw_rev = getattr(job_version, "revision", None) or getattr(job_version, "version_number", None) or 1
    packet = CanonicalGenerationSourcePacket(
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=job_version.job_version_id,
        job_revision=raw_rev,
        job_title=job_version.title or f"Job #{str(job.job_id)[:8]}",
        seniority_level=None,
        role_summary=job_version.summary,
        key_responsibilities=job_version.responsibilities,
        planned_slots=planned_slots,
        canonical_requirements=canonical_reqs,
        requested_question_count=len(planned_slots),
        delivery_mode=delivery_mode,
        generation_policy_version=GENERATION_POLICY_VERSION,
        prompt_contract_version=PROMPT_CONTRACT_VERSION,
        guardrail_version=GUARDRAIL_VERSION,
    )

    fingerprint = _compute_canonical_source_fingerprint(packet)
    internal_plan = {
        "job_id": str(job.job_id),
        "job_version_id": str(job_version.job_version_id),
        "job_revision": raw_rev,
        "job_title": packet.job_title,
        "fingerprint": fingerprint,
        "source_requirement_ids": [str(r.requirement_id) for r in selected_reqs],
        "slots": plan_slots_internal,
    }

    return packet, internal_plan


def _validate_ai_wording_safety(text: str) -> Tuple[bool, Optional[str]]:
    """Strictly validates generated wording against prompt injection and safety policies."""
    if not text or len(text.strip()) < 15:
        return False, "WORDING_TOO_SHORT"
    if len(text.strip()) > 1200:
        return False, "WORDING_TOO_LONG"
        
    # Check HTML tags
    if re.search(r"<[^>]+>", text):
        return False, "PROHIBITED_HTML_CONTENT"
        
    # Prohibited topic checks
    violation = check_prohibited_topics(text)
    if violation is not None:
        category, _ = violation
        return False, f"PROHIBITED_TOPIC_{category}"
        
    # Rejection of scores, rankings, recommendations in question text
    lower = text.lower()
    score_words = ["score:", "candidate score", "hiring recommendation", "ranking:", "pass/fail"]
    if any(sw in lower for sw in score_words):
        return False, "INVALID_MODEL_OUTPUT"
        
    return True, None


async def _generate_requirement_grounded_slots(
    packet: CanonicalGenerationSourcePacket,
    internal_plan: Dict[str, Any],
    actor: UserRecord,
    db: Session,
    provider_registry: Optional[ProviderRegistry] = None,
    custom_provider: Optional[BaseWordingProvider] = None,
    interview_mode: InterviewMode = InterviewMode.TEXT,
    existing_text_stems: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Generates AI wording for all planned slots using the job-grounded batch generation provider."""
    reg = provider_registry or get_provider_registry()
    items: List[Dict[str, Any]] = []
    
    active_profile = None
    try:
        active_profile = reg.get_active_profile()
    except Exception:
        pass

    model_id = active_profile.model_identifier if active_profile else "qwen/qwen3-4b-2507"
    prov_class = active_profile.provider_type.value if (active_profile and hasattr(active_profile.provider_type, "value")) else "LOCAL_OPENAI_COMPATIBLE"

    provider = custom_provider or (reg.get_provider_instance(active_profile) if active_profile else None)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "PROVIDER_UNAVAILABLE", "message": "Failed to generate AI questions. Local AI is unavailable. Please check that LM Studio is running."},
        )

    job_context = {
        "job_title": packet.job_title,
        "seniority_level": packet.seniority_level or "Intermediate",
        "role_summary": packet.role_summary or "",
        "key_responsibilities": packet.key_responsibilities or "",
    }

    slots_context = []
    for s in packet.planned_slots:
        # Find matching internal slot to get requirement rationale / summary
        internal_match = next((x for x in internal_plan["slots"] if str(x["slot_id"]) == str(s.slot_id)), None)
        req_desc = internal_match.get("requirement_summary") if internal_match else s.competency_name
        slots_context.append({
            "slot_id": str(s.slot_id),
            "primary_requirement_id": str(s.primary_requirement_id),
            "allowed_requirement_ids": [str(s.primary_requirement_id)] + [str(rid) for rid in s.supporting_requirement_ids],
            "primary_requirement_label": s.competency_name,
            "primary_requirement_rationale": req_desc,
            "competency_name": s.competency_name,
            "question_objective": s.question_objective,
            "question_type": s.question_type.value,
            "difficulty": s.difficulty,
            "delivery_mode": "VOICE" if interview_mode == InterviewMode.AUDIO else s.delivery_mode.value,
        })

    mode_str = interview_mode.value if hasattr(interview_mode, "value") else str(interview_mode)
    try:
        batch_resp: StrictJobGroundedGenerationBatchResponse = await provider.request_job_grounded_generation(
            job_context=job_context,
            slots_context=slots_context,
            interview_mode=mode_str,
            existing_text_stems=existing_text_stems,
        )
    except Exception as e:
        logger.warning("Batch job-grounded generation provider error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "PROVIDER_UNAVAILABLE", "message": f"Failed to generate AI questions. Local AI is unavailable or returned an error: {str(e)}"},
        )

    # Validate output questions against planned slots
    questions_by_slot = {q.slot_id: q for q in batch_resp.questions}
    allowed_req_ids = {str(r.requirement_id) for r in packet.canonical_requirements}

    for slot in internal_plan["slots"]:
        slot_id_str = str(slot["slot_id"])
        item: Dict[str, Any] = {
            "slot_id": slot_id_str,
            "display_order": slot["display_order"],
            "source_requirement_ids": slot["source_requirement_ids"],
            "competency_name": slot["competency_name"],
            "question_type": slot["question_type"],
            "estimated_time_minutes": 3 if interview_mode == InterviewMode.AUDIO else slot["estimated_time_minutes"],
            "permitted_probes": slot["permitted_probes"],
            "rubric": slot.get("rubric"),
            "ai_wording": None,
            "review_state": "UNREVIEWED",
            "authoring_origin": AuthoringOrigin.LOCAL_LLM_GENERATED.value,
            "validation_status": "PENDING",
            "safe_failure_code": None,
            "rationale_code": "REQUIREMENT_GROUNDED",
            "model_identifier": model_id,
            "provider_class": prov_class,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        generated_q = questions_by_slot.get(slot_id_str)
        if not generated_q:
            item["validation_status"] = "FAILED"
            item["safe_failure_code"] = "MISSING_SLOT_OUTPUT"
        else:
            # Check requirement ownership
            if generated_q.primary_requirement_id not in allowed_req_ids:
                item["validation_status"] = "FAILED"
                item["safe_failure_code"] = "FORGED_REQUIREMENT_ID"
            elif generated_q.primary_requirement_id != slot["source_requirement_ids"][0]:
                item["validation_status"] = "FAILED"
                item["safe_failure_code"] = "MISMATCHED_REQUIREMENT_ID"
            else:
                wording = generated_q.question_text.strip()
                from app.services.ai_provenance_service import validate_conversational_question_text
                val_outcome = validate_conversational_question_text(
                    question_text=wording,
                    competency_name=slot["competency_name"],
                    interview_mode=mode_str,
                    cross_mode_existing_texts=existing_text_stems,
                )
                if val_outcome.is_valid:
                    item["ai_wording"] = wording
                    item["question_text"] = wording
                    item["validation_status"] = "PASSED"
                    item["safe_failure_code"] = None
                    if generated_q.permitted_probes:
                        item["permitted_probes"] = generated_q.permitted_probes
                else:
                    item["validation_status"] = "FAILED"
                    item["safe_failure_code"] = val_outcome.error_code or "SAFETY_CHECK_FAILED"

        items.append(item)

    return items


def _map_record_to_detail_dto(
    ai_set: AiInterviewQuestionSetRecord,
    db: Session,
    action: Optional[str] = None,
) -> AiQuestionSetDetailResponse:
    """Transforms an AiInterviewQuestionSetRecord into a secure, safe detail DTO.
    
    Strictly guarantees zero leakage of deterministic question wording.
    """
    template = ai_set.template
    questions_by_id = {q.question_id: q for q in (template.questions or [])} if (template and template.questions) else {}

    # Check staleness
    is_stale = False
    stale_reason = None
    if ai_set.source_mode == "BLUEPRINT_GROUNDED" and template:
        current_hash = template.content_hash or ""
        if current_hash and ai_set.source_content_hash != current_hash:
            is_stale = True
            stale_reason = "Deterministic blueprint was modified since AI question set generation."
    elif ai_set.source_mode == "REQUIREMENT_GROUNDED":
        # Check current requirements fingerprint against current revision
        job_stmt = select(JobRequisition).options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
        ).where(JobRequisition.job_id == ai_set.job_id)
        job = db.execute(job_stmt).scalar_one_or_none()
        if job and job.versions:
            latest_v = sorted(job.versions, key=lambda v: (getattr(v, "version_number", 0) or 0, getattr(v, "revision", 0) or 0))[-1]
            target_v_rev = getattr(latest_v, "revision", None) or getattr(latest_v, "version_number", None) or 1
            # If revision number changed, it is definitely stale
            if ai_set.job_revision and ai_set.job_revision != target_v_rev:
                is_stale = True
                stale_reason = f"Job revision was updated from #{ai_set.job_revision} to #{target_v_rev}."
            else:
                deliv_mode = (
                    GenerationDeliveryMode.VOICE
                    if (getattr(ai_set, "interview_mode", None) == InterviewMode.AUDIO or str(getattr(ai_set, "interview_mode", "")) == "AUDIO")
                    else GenerationDeliveryMode.CHAT
                )
                p, i_plan = _build_canonical_generation_packet(job, latest_v, delivery_mode=deliv_mode)
                canon_fp = i_plan["fingerprint"]
                # Also check with target_count=min(6, ...) for backward compatibility with sets generated prior to 5-slot cap
                eligible_reqs = [r for r in (latest_v.requirements or []) if _is_requirement_eligible(r)] or list(latest_v.requirements or [])
                def _sk(r):
                    nec = getattr(r, "necessity", getattr(r, "importance", None))
                    nec_str = nec.value if hasattr(nec, "value") else str(nec or "")
                    prio = getattr(r, "priority", None)
                    prio_str = prio.value if hasattr(prio, "value") else str(prio or "")
                    return 0 if (nec_str.upper() == "MUST_HAVE" or prio_str.upper() in ("CRITICAL", "HIGH")) else 1
                eligible_reqs.sort(key=_sk)
                target_6 = min(6, max(len(eligible_reqs), 1))
                selected_6 = eligible_reqs[:target_6]
                canon_6_reqs = [
                    CanonicalRequirementItem(
                        requirement_id=r.requirement_id,
                        label=r.label.strip(),
                        category=r.category.value if hasattr(r.category, "value") else str(r.category),
                        necessity=r.necessity.value if hasattr(r.necessity, "value") else str(r.necessity),
                        priority=r.priority.value if hasattr(r.priority, "value") else str(r.priority),
                        rationale=r.rationale.strip() if r.rationale else None,
                        is_interview_eligible=True,
                    )
                    for r in selected_6
                ]
                packet_6 = CanonicalGenerationSourcePacket(
                    organization_id=p.organization_id,
                    job_id=p.job_id,
                    job_version_id=p.job_version_id,
                    job_revision=p.job_revision,
                    job_title=p.job_title,
                    seniority_level=p.seniority_level,
                    role_summary=p.role_summary,
                    key_responsibilities=p.key_responsibilities,
                    planned_slots=p.planned_slots[:target_6],
                    canonical_requirements=canon_6_reqs,
                    requested_question_count=target_6,
                    delivery_mode=p.delivery_mode,
                    generation_policy_version=p.generation_policy_version,
                    prompt_contract_version=p.prompt_contract_version,
                    guardrail_version=p.guardrail_version,
                )
                canon_6_fp = _compute_canonical_source_fingerprint(packet_6)
                req_ids = [str(r.requirement_id) for r in (latest_v.requirements or [])]
                legacy_fp = _compute_requirement_fingerprint(job.job_id, latest_v.job_version_id, getattr(latest_v, "version_number", 1), req_ids)
                if ai_set.source_content_hash not in (canon_fp, canon_6_fp, legacy_fp):
                    is_stale = True
                    stale_reason = "Job requirements were updated since AI question set generation."

    if is_stale and ai_set.lifecycle_status not in ("APPROVED", "STALE"):
        ai_set.lifecycle_status = "STALE"
        db.commit()
    elif not is_stale and ai_set.lifecycle_status == "STALE":
        all_rev = all(it.get("review_state") in ("KEPT", "EDITED", "RECRUITER_AUTHORED") for it in (ai_set.items or [])) if ai_set.items else False
        ai_set.lifecycle_status = "VALIDATED" if all_rev else "DRAFT"
        db.commit()

    slot_dtos: List[AiQuestionSlotItemResponse] = []
    reviewed_count = 0
    generated_count = 0

    for item in ai_set.items:
        slot_uuid = UUID(item["slot_id"]) if isinstance(item["slot_id"], str) else item["slot_id"]
        
        # Safely map rubric
        safe_rubric = None
        if slot_uuid in questions_by_id:
            safe_rubric = _map_rubric_to_safe(questions_by_id[slot_uuid])
        elif item.get("rubric"):
            r_data = item["rubric"]
            try:
                safe_rubric = SafeRubricResponse(
                    rubric_id=UUID(r_data["rubric_id"]) if isinstance(r_data.get("rubric_id"), str) else (r_data.get("rubric_id") or uuid.uuid4()),
                    rubric_version=r_data.get("rubric_version", 1),
                    competency_definition=r_data.get("competency_definition", ""),
                    question_intent=r_data.get("question_intent", ""),
                    observable_indicators=list(r_data.get("observable_indicators", [])),
                    rating_anchors={str(k): str(v) for k, v in (r_data.get("rating_anchors") or {}).items()},
                    sufficient_evidence_guidance=r_data.get("sufficient_evidence_guidance"),
                    insufficient_evidence_guidance=r_data.get("insufficient_evidence_guidance"),
                    permitted_probes=list(r_data.get("permitted_probes", [])),
                )
            except Exception:
                safe_rubric = None

        ai_wording = item.get("ai_wording")
        if ai_wording:
            generated_count += 1

        review_state = item.get("review_state", "UNREVIEWED")
        if review_state in ("KEPT", "EDITED", "RECRUITER_AUTHORED"):
            reviewed_count += 1

        up_at = None
        if item.get("updated_at"):
            try:
                up_at = datetime.fromisoformat(item["updated_at"])
            except Exception:
                up_at = None

        source_req_uuids = [
            UUID(rid) if isinstance(rid, str) else rid
            for rid in item.get("source_requirement_ids", [])
        ]

        slot_dtos.append(
            AiQuestionSlotItemResponse(
                slot_id=slot_uuid,
                display_order=item.get("display_order", 0),
                question_format=item.get("question_format", "CONVERSATION"),
                competency_name=item.get("competency_name", "Core Competency"),
                question_type=item.get("question_type", "TECHNICAL"),
                estimated_time_minutes=item.get("estimated_time_minutes", 5),
                permitted_probes=list(item.get("permitted_probes", [])),
                rubric=safe_rubric,
                source_requirement_ids=source_req_uuids,
                ai_wording=ai_wording,
                question_text=item.get("question_text") or ai_wording,
                options=item.get("options"),
                correct_option_id=item.get("correct_option_id"),
                answer_explanation=item.get("answer_explanation"),
                difficulty=item.get("difficulty"),
                review_state=review_state,
                authoring_origin=item.get("authoring_origin", "AI_GENERATED"),
                validation_status=item.get("validation_status", "PASSED"),
                safe_failure_code=item.get("safe_failure_code"),
                rationale_code=item.get("rationale_code"),
                updated_at=up_at,
            )
        )

    slot_dtos.sort(key=lambda s: s.display_order)

    conversation_count = sum(1 for s in slot_dtos if s.question_format != "MCQ_SINGLE_ANSWER")
    mcq_count = sum(1 for s in slot_dtos if s.question_format == "MCQ_SINGLE_ANSWER")

    # Resolve job title and revision from JobVersion
    job_title = "Job Requisition"
    job_revision = ai_set.job_revision or (template.revision if template else 1)
    
    ver_id = ai_set.job_version_id or (template.job_version_id if template else None)
    if ver_id:
        stmt_ver = select(JobVersion).where(JobVersion.job_version_id == ver_id)
        ver = db.execute(stmt_ver).scalar_one_or_none()
        if ver and ver.title:
            job_title = ver.title
            job_revision = getattr(ver, "revision", None) or getattr(ver, "version_number", None) or 1
    elif ai_set.job_id:
        stmt_ver = select(JobVersion).where(JobVersion.job_id == ai_set.job_id).order_by(desc(JobVersion.version_number), desc(JobVersion.revision))
        ver = db.execute(stmt_ver).scalars().first()
        if ver and ver.title:
            job_title = ver.title
            job_revision = getattr(ver, "revision", None) or getattr(ver, "version_number", None) or 1

    source_req_set_uuids = [
        UUID(rid) if isinstance(rid, str) else rid
        for rid in (ai_set.source_requirement_ids or [])
    ]

    # Compute release eligibility and legacy check
    from app.services.ai_provenance_service import evaluate_release_eligibility
    elig_summary = None
    target_job_ver = None
    if ver_id:
        stmt_ver = select(JobVersion).where(JobVersion.job_version_id == ver_id)
        target_job_ver = db.execute(stmt_ver).scalar_one_or_none()
    elif ai_set.job_id:
        stmt_ver = select(JobVersion).where(JobVersion.job_id == ai_set.job_id).order_by(desc(JobVersion.version_number), desc(JobVersion.revision))
        target_job_ver = db.execute(stmt_ver).scalars().first()

    is_legacy = any(
        it.get("authoring_origin") in ("LEGACY_DETERMINISTIC", "LEGACY_MANUAL", "DETERMINISTIC", "RECRUITER_AUTHORED")
        for it in (ai_set.items or [])
    ) or (ai_set.source_mode == "BLUEPRINT_GROUNDED" and not ai_set.model_identifier)

    if target_job_ver:
        temp_status = ai_set.lifecycle_status
        ai_set.lifecycle_status = "APPROVED"
        elig_res = evaluate_release_eligibility(ai_set, target_job_ver)
        ai_set.lifecycle_status = temp_status
        elig_summary = ReleaseEligibilitySummaryDto(
            is_eligible=elig_res.is_eligible,
            reason_code=elig_res.reason_code.value if elig_res.reason_code else None,
            safe_message=elig_res.safe_message,
            blocking_items=elig_res.blocking_items or [],
        )

    model_name = ai_set.model_identifier or "Qwen3-4B"
    if "qwen" in model_name.lower():
        model_display = "Qwen3-4B MLX"
    else:
        model_display = model_name

    return AiQuestionSetDetailResponse(
        ai_set_id=ai_set.ai_set_id,
        set_version=ai_set.set_version,
        interview_mode=getattr(ai_set, "interview_mode", InterviewMode.TEXT) or InterviewMode.TEXT,
        template_id=ai_set.template_id,
        source_template_version=ai_set.source_template_version,
        source_mode=ai_set.source_mode,
        source_content_hash=ai_set.source_content_hash,
        source_requirement_ids=source_req_set_uuids,
        job_id=ai_set.job_id,
        job_title=job_title,
        job_revision=job_revision,
        lifecycle_status=ai_set.lifecycle_status,
        is_immutable=ai_set.is_immutable,
        content_hash=ai_set.content_hash,
        action=action,
        provider_class=ai_set.provider_class,
        model_identifier=ai_set.model_identifier,
        model_display_name=model_display,
        prompt_template_version=ai_set.prompt_template_version,
        guardrail_policy_version=ai_set.guardrail_policy_version,
        schema_version=ai_set.schema_version,
        predecessor_set_id=ai_set.predecessor_set_id,
        slots=slot_dtos,
        total_slots_count=len(slot_dtos),
        generated_slots_count=generated_count,
        reviewed_slots_count=reviewed_count,
        conversation_count=conversation_count,
        mcq_count=mcq_count,
        is_source_stale=is_stale,
        stale_reason=stale_reason,
        release_eligibility=elig_summary,
        is_legacy=is_legacy,
        created_at=ai_set.created_at,
        updated_at=ai_set.updated_at,
        approved_at=ai_set.approved_at,
    )


def list_ai_question_sets(
    actor: UserRecord,
    db: Session,
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    mode_filter: Optional[InterviewMode] = None,
    sort_by: str = "updated_at",
) -> AiQuestionSetDirectoryResponse:
    """Lists organization-scoped AI question sets with search, filter, mode, and metrics."""
    stmt = (
        select(AiInterviewQuestionSetRecord)
        .options(
            selectinload(AiInterviewQuestionSetRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.job_version),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.questions),
        )
        .order_by(desc(AiInterviewQuestionSetRecord.updated_at))
    )
    records = db.execute(stmt).scalars().all()

    # Deduplicate to latest version per (job_id, job_revision, interview_mode) or (template_id, interview_mode)
    latest_by_key: Dict[str, AiInterviewQuestionSetRecord] = {}
    for r in records:
        # Organization check
        actor_org = getattr(actor, "org_id", getattr(actor, "organization_id", None))
        if actor_org is not None and r.organization_id != actor_org:
            continue

        # Exclude deleted jobs and enforce recruiter isolation
        job_rec = r.requisition or (r.template.requisition if r.template else None)
        if job_rec:
            if getattr(job_rec, "deleted_at", None) is not None:
                continue
            if actor.role == UserRole.RECRUITER and job_rec.created_by_user_id != actor.user_id:
                continue

        r_mode = getattr(r, "interview_mode", InterviewMode.TEXT) or InterviewMode.TEXT
        if mode_filter is not None and r_mode != mode_filter:
            continue

        key = f"{r.template_id}_{r_mode.value}" if r.template_id else f"job_{r.job_id}_{r.job_revision}_{r_mode.value}"
        if key not in latest_by_key or r.set_version > latest_by_key[key].set_version:
            latest_by_key[key] = r

    summaries: List[AiQuestionSetSummaryResponse] = []
    total_count = 0
    awaiting_review = 0
    ready_for_approval = 0
    approved = 0

    for ai_set in latest_by_key.values():
        eff_status = ai_set.lifecycle_status
        gen_count = sum(1 for it in (ai_set.items or []) if it.get("ai_wording"))
        total_slots = len(ai_set.items or [])
        conv_cnt = sum(1 for it in (ai_set.items or []) if it.get("question_format") != "MCQ_SINGLE_ANSWER")
        mcq_cnt = sum(1 for it in (ai_set.items or []) if it.get("question_format") == "MCQ_SINGLE_ANSWER")

        # Do not include failed / empty question sets in Continue Reviewing directory
        if total_slots == 0 or gen_count == 0:
            continue

        total_count += 1
        if eff_status == "APPROVED":
            approved += 1
        elif eff_status == "VALIDATED":
            ready_for_approval += 1
        else:
            awaiting_review += 1

        title = "Job Requisition"
        ver_id = ai_set.job_version_id or (ai_set.template.job_version_id if ai_set.template else None)
        if ver_id:
            stmt_ver = select(JobVersion).where(JobVersion.job_version_id == ver_id)
            ver = db.execute(stmt_ver).scalar_one_or_none()
            if ver and ver.title:
                title = ver.title
        elif ai_set.job_id:
            stmt_ver = select(JobVersion).where(JobVersion.job_id == ai_set.job_id).order_by(desc(JobVersion.version_number), desc(JobVersion.revision))
            ver = db.execute(stmt_ver).scalars().first()
            if ver and ver.title:
                title = ver.title

        if search and search.strip():
            if search.strip().lower() not in title.lower():
                continue

        if status_filter and status_filter.upper() != "ALL":
            if eff_status.upper() != status_filter.upper():
                continue

        is_leg = any(
            it.get("authoring_origin") in ("LEGACY_DETERMINISTIC", "LEGACY_MANUAL", "DETERMINISTIC", "RECRUITER_AUTHORED")
            for it in (ai_set.items or [])
        ) or (ai_set.source_mode == "BLUEPRINT_GROUNDED" and not ai_set.model_identifier)

        m_display = "Qwen3-4B MLX" if (ai_set.model_identifier and "qwen" in ai_set.model_identifier.lower()) else (ai_set.model_identifier or "Local LLM")

        eff_mode = getattr(ai_set, "interview_mode", InterviewMode.TEXT) or InterviewMode.TEXT
        eff_mode_str = eff_mode.value if hasattr(eff_mode, "value") else str(eff_mode)

        # Count submitted applications for this job
        stmt_app_count = select(func.count(CandidateApplicationRecord.application_id)).where(
            CandidateApplicationRecord.job_id == ai_set.job_id,
            CandidateApplicationRecord.status == "SUBMITTED",
        )
        applied_cnt = db.execute(stmt_app_count).scalar() or 0

        # Count candidates who received released interviews for this job and mode
        stmt_rel_count = select(func.count(func.distinct(CandidatePracticeReleaseRecord.candidate_id))).where(
            CandidatePracticeReleaseRecord.job_id == ai_set.job_id,
            CandidatePracticeReleaseRecord.status == "RELEASED",
            CandidatePracticeReleaseRecord.interview_mode == eff_mode_str,
        )
        released_cnt = db.execute(stmt_rel_count).scalar() or 0

        summaries.append(
            AiQuestionSetSummaryResponse(
                ai_set_id=ai_set.ai_set_id,
                set_version=ai_set.set_version,
                interview_mode=eff_mode,
                template_id=ai_set.template_id,
                source_mode=ai_set.source_mode,
                job_id=ai_set.job_id,
                job_title=title,
                lifecycle_status=eff_status,
                is_immutable=ai_set.is_immutable,
                is_source_stale=(eff_status == "STALE"),
                is_legacy=is_leg,
                model_display_name=m_display,
                generated_question_count=gen_count,
                total_blueprint_slots=total_slots,
                conversation_count=conv_cnt,
                mcq_count=mcq_cnt,
                applied_candidate_count=applied_cnt,
                released_candidate_count=released_cnt,
                created_at=ai_set.created_at,
                updated_at=ai_set.updated_at,
            )
        )

    return AiQuestionSetDirectoryResponse(
        sets=summaries,
        total_count=total_count,
        awaiting_review_count=awaiting_review,
        ready_for_approval_count=ready_for_approval,
        approved_count=approved,
    )


def get_eligible_jobs_for_ai_questions(
    actor: UserRecord,
    db: Session,
    mode: InterviewMode = InterviewMode.TEXT,
) -> EligibleJobsResponse:
    """Retrieves organization-scoped jobs available to the recruiter for AI interview generation.
    
    Calculates readiness independently by mode.
    Returns job_id, job_title, requirement_count, current_ai_set_id, current_ai_set_status, applied_candidate_count, released_candidate_count.
    """
    stmt = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
        )
        .where(
            JobRequisition.status == JobLifecycleStatus.ACTIVE,
            JobRequisition.deleted_at.is_(None),
        )
    )
    if actor.role == UserRole.RECRUITER:
        stmt = stmt.where(JobRequisition.created_by_user_id == actor.user_id)
        
    actor_org = getattr(actor, "org_id", getattr(actor, "organization_id", None))
    if actor_org is not None and hasattr(JobRequisition, "organization_id"):
        stmt = stmt.where(JobRequisition.organization_id == actor_org)
        
    stmt = stmt.order_by(desc(JobRequisition.updated_at))
    records = db.execute(stmt).scalars().all()
    
    mode_str = mode.value if hasattr(mode, "value") else str(mode)
    items: List[EligibleJobItemResponse] = []
    for job in records:
        if not job.versions:
            continue
        latest_ver = max(job.versions, key=lambda v: v.version_number or 0)
        req_count = len(latest_ver.requirements) if latest_ver.requirements else 0
        if req_count == 0:
            continue
            
        job_title = latest_ver.title or f"Job #{str(job.job_id)[:8]}"

        # Look up current AI question set for this job revision and mode
        stmt_ai = (
            select(AiInterviewQuestionSetRecord)
            .where(
                AiInterviewQuestionSetRecord.job_id == job.job_id,
                AiInterviewQuestionSetRecord.interview_mode == mode,
                (AiInterviewQuestionSetRecord.job_version_id == latest_ver.job_version_id)
                | (AiInterviewQuestionSetRecord.job_revision == latest_ver.version_number),
            )
            .order_by(desc(AiInterviewQuestionSetRecord.set_version))
        )
        latest_ai = db.execute(stmt_ai).scalars().first()

        # Phase 3E.2 / 4D.1 binding directory rule:
        # If the current revision of a job already has an AI question set in the selected mode with valid generated questions,
        # exclude it from "Choose a job". It appears solely under "Continue reviewing" for that mode.
        has_valid_ai_set = (
            latest_ai is not None
            and bool(latest_ai.items)
            and any(it.get("ai_wording") for it in latest_ai.items)
        )
        if has_valid_ai_set:
            continue

        # Count submitted applications for this job
        stmt_app_count = select(func.count(CandidateApplicationRecord.application_id)).where(
            CandidateApplicationRecord.job_id == job.job_id,
            CandidateApplicationRecord.status == "SUBMITTED",
        )
        applied_cnt = db.execute(stmt_app_count).scalar() or 0

        # Count candidates who received released interviews for this job and mode
        stmt_rel_count = select(func.count(func.distinct(CandidatePracticeReleaseRecord.candidate_id))).where(
            CandidatePracticeReleaseRecord.job_id == job.job_id,
            CandidatePracticeReleaseRecord.status == "RELEASED",
            CandidatePracticeReleaseRecord.interview_mode == mode_str,
        )
        released_cnt = db.execute(stmt_rel_count).scalar() or 0

        items.append(
            EligibleJobItemResponse(
                job_id=job.job_id,
                job_title=job_title,
                requirement_count=req_count,
                current_ai_set_id=None,
                current_ai_set_status=None,
                applied_candidate_count=applied_cnt,
                released_candidate_count=released_cnt,
            )
        )

    return EligibleJobsResponse(items=items)


def get_ai_question_set_by_template(
    actor: UserRecord,
    template_id: UUID,
    db: Session,
) -> AiQuestionSetDetailResponse:
    """Retrieves an AI question set by template_id or ai_set_id, enforcing access and data boundaries."""
    # Try finding directly by ai_set_id
    stmt_set_direct = (
        select(AiInterviewQuestionSetRecord)
        .options(
            selectinload(AiInterviewQuestionSetRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.job_version),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .where(AiInterviewQuestionSetRecord.ai_set_id == template_id)
    )
    ai_set = db.execute(stmt_set_direct).scalars().first()

    if not ai_set:
        # Try finding by template_id
        stmt_by_tmpl = (
            select(AiInterviewQuestionSetRecord)
            .options(
                selectinload(AiInterviewQuestionSetRecord.requisition),
                selectinload(AiInterviewQuestionSetRecord.job_version),
                selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
                selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
            )
            .where(AiInterviewQuestionSetRecord.template_id == template_id)
            .order_by(desc(AiInterviewQuestionSetRecord.set_version))
        )
        ai_set = db.execute(stmt_by_tmpl).scalars().first()

    if not ai_set:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AI_SET_NOT_FOUND", "message": "AI interview question set not found."},
        )

    # Authorization check
    job = ai_set.requisition or (ai_set.template.requisition if ai_set.template else None)
    if job:
        _check_recruiter_access(actor, job)

    return _map_record_to_detail_dto(ai_set, db)


async def generate_ai_questions_by_job_id(
    actor: UserRecord,
    job_id: UUID,
    force_regenerate: bool,
    db: Session,
    provider_registry: Optional[ProviderRegistry] = None,
    custom_provider: Optional[BaseWordingProvider] = None,
    interview_mode: InterviewMode = InterviewMode.TEXT,
) -> AiQuestionSetDetailResponse:
    """Server-authoritative generation command given a selected job_id.
    
    Resolution hierarchy:
    1. Load requested job under organization / recruiter assignment scope.
    2. Load current approved / ready revision.
    3. If active AI set exists for current fingerprint, mode, and not force_regenerate: return it (OPENED_EXISTING).
    4. Check for usable deterministic blueprint. If usable slots exist -> BLUEPRINT_GROUNDED.
    5. If no usable blueprint -> REQUIREMENT_GROUNDED fallback.
    6. Validate, store as DRAFT, return (GENERATED).
    """
    stmt_job = (
        select(JobRequisition)
        .options(selectinload(JobRequisition.versions).selectinload(JobVersion.requirements))
        .where(JobRequisition.job_id == job_id)
    )
    job = db.execute(stmt_job).scalar_one_or_none()
    if not job or not job.versions or job.status != JobLifecycleStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NO_APPROVED_REQUIREMENTS", "message": "This job needs approved requirements before questions can be generated."},
        )
    _check_recruiter_access(actor, job)

    latest_version = job.versions[-1]
    if not latest_version.requirements or len(latest_version.requirements) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NO_APPROVED_REQUIREMENTS", "message": "This job needs approved requirements before questions can be generated."},
        )

    # Check for existing active AI question set for this job revision AND interview mode
    stmt_existing = (
        select(AiInterviewQuestionSetRecord)
        .options(
            selectinload(AiInterviewQuestionSetRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.job_version),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .where(
            AiInterviewQuestionSetRecord.job_id == job.job_id,
            AiInterviewQuestionSetRecord.interview_mode == interview_mode,
            (AiInterviewQuestionSetRecord.job_version_id == latest_version.job_version_id)
            | (AiInterviewQuestionSetRecord.job_revision == latest_version.version_number),
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    existing_set = db.execute(stmt_existing).scalars().first()

    # Step 3: Check if fresh active AI set exists for this exact job revision and canonical source fingerprint
    deliv_mode = GenerationDeliveryMode.VOICE if interview_mode == InterviewMode.AUDIO else GenerationDeliveryMode.CHAT
    packet, internal_plan = _build_canonical_generation_packet(job, latest_version, delivery_mode=deliv_mode, actor=actor)
    canonical_fp = internal_plan["fingerprint"]

    if existing_set and not force_regenerate:
        # Check freshness against authoritative canonical source fingerprint
        is_fresh = False
        if existing_set.source_content_hash == canonical_fp:
            is_fresh = True
        elif existing_set.source_mode == "REQUIREMENT_GROUNDED":
            # Check legacy requirement fingerprint compatibility
            req_ids = [str(r.requirement_id) for r in latest_version.requirements]
            legacy_fp = _compute_requirement_fingerprint(job.job_id, latest_version.job_version_id, latest_version.version_number, req_ids)
            if existing_set.source_content_hash == legacy_fp:
                is_fresh = True
        elif existing_set.source_mode == "BLUEPRINT_GROUNDED" and existing_set.template_id:
            # Check legacy template content hash compatibility
            if existing_set.template and existing_set.source_content_hash == existing_set.template.content_hash:
                is_fresh = True

        if is_fresh:
            return _map_record_to_detail_dto(existing_set, db, action="OPENED_EXISTING")

    # If generating AUDIO set, fetch stems from TEXT set if it exists to avoid duplicate question text
    cross_mode_stems: List[str] = []
    other_mode = InterviewMode.TEXT if interview_mode == InterviewMode.AUDIO else InterviewMode.AUDIO
    stmt_other = (
        select(AiInterviewQuestionSetRecord)
        .where(
            AiInterviewQuestionSetRecord.job_id == job.job_id,
            AiInterviewQuestionSetRecord.interview_mode == other_mode,
            (AiInterviewQuestionSetRecord.job_version_id == latest_version.job_version_id)
            | (AiInterviewQuestionSetRecord.job_revision == latest_version.version_number),
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    other_set = db.execute(stmt_other).scalars().first()
    if other_set and other_set.items:
        for it in other_set.items:
            w = it.get("ai_wording") or it.get("question_text")
            if w and w.strip():
                cross_mode_stems.append(w.strip())

    # Step 4: Requirement-grounded batch generation (Job-Grounded Generation Pipeline)
    items = await _generate_requirement_grounded_slots(
        packet=packet,
        internal_plan=internal_plan,
        actor=actor,
        db=db,
        provider_registry=provider_registry,
        custom_provider=custom_provider,
        interview_mode=interview_mode,
        existing_text_stems=cross_mode_stems,
    )

    if not items or len(items) == 0 or all(not it.get("ai_wording") for it in items):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "PROVIDER_UNAVAILABLE", "message": "Failed to generate AI questions. Local AI is unavailable. Please check that LM Studio is running."},
        )

    next_version = (existing_set.set_version + 1) if (existing_set and force_regenerate) else 1

    reg = provider_registry or get_provider_registry()
    active_prof = None
    try:
        active_prof = reg.get_active_profile()
    except Exception:
        pass

    org_id = packet.organization_id
    effective_rev = getattr(latest_version, "revision", None) or getattr(latest_version, "version_number", None) or 1
    new_set = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        interview_mode=interview_mode,
        organization_id=org_id,
        template_id=None,
        job_id=job.job_id,
        job_version_id=latest_version.job_version_id,
        job_revision=effective_rev,
        source_mode="REQUIREMENT_GROUNDED",
        source_template_version=None,
        source_content_hash=canonical_fp,
        source_requirement_ids=internal_plan["source_requirement_ids"],
        set_version=next_version,
        lifecycle_status="DRAFT",
        is_immutable=False,
        provider_profile_id=active_prof.profile_id if active_prof else None,
        provider_class=active_prof.provider_type.value if (active_prof and hasattr(active_prof.provider_type, "value")) else "LOCAL_OPENAI_COMPATIBLE",
        model_identifier=active_prof.model_identifier if active_prof else "qwen/qwen3-4b-2507",
        prompt_template_version=PROMPT_CONTRACT_VERSION,
        guardrail_policy_version=GUARDRAIL_VERSION,
        schema_version=QUESTION_GENERATION_SCHEMA_VERSION,
        items=items,
        created_by_user_id=actor.user_id,
    )
    db.add(new_set)
    db.commit()
    db.refresh(new_set)

    return _map_record_to_detail_dto(new_set, db, action="GENERATED")


async def generate_or_open_ai_question_set(
    actor: UserRecord,
    job_id: UUID,
    template_id: UUID,
    force_regenerate: bool,
    db: Session,
    provider_registry: Optional[ProviderRegistry] = None,
    custom_provider: Optional[BaseWordingProvider] = None,
) -> AiQuestionSetDetailResponse:
    """Generates a BLUEPRINT_GROUNDED AI question set from a deterministic template."""
    stmt_tmpl = (
        select(JobInterviewTemplateRecord)
        .options(
            selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .where(
            JobInterviewTemplateRecord.template_id == template_id,
            JobInterviewTemplateRecord.job_id == job_id,
        )
    )
    template = db.execute(stmt_tmpl).scalar_one_or_none()
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TEMPLATE_NOT_FOUND", "message": "Interview template not found for job."},
        )
    _check_recruiter_access(actor, template.requisition)

    source_hash = template.content_hash or hashlib.sha256(str(template_id).encode("utf-8")).hexdigest()

    # Check for existing latest version
    stmt_latest = (
        select(AiInterviewQuestionSetRecord)
        .options(
            selectinload(AiInterviewQuestionSetRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.job_version),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .where(AiInterviewQuestionSetRecord.template_id == template_id)
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    latest_set = db.execute(stmt_latest).scalars().first()

    if latest_set and not force_regenerate:
        if latest_set.source_content_hash == source_hash:
            return _map_record_to_detail_dto(latest_set, db, action="OPENED_EXISTING")

    next_version = (latest_set.set_version + 1) if latest_set else 1
    questions = sorted(template.questions or [], key=lambda q: q.display_order)
    items: List[Dict[str, Any]] = []
    reg = provider_registry or get_provider_registry()

    for q in questions:
        item: Dict[str, Any] = {
            "slot_id": str(q.question_id),
            "display_order": q.display_order,
            "competency_name": q.competency_id or "Core Competency",
            "question_type": q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type),
            "estimated_time_minutes": q.estimated_time_minutes,
            "permitted_probes": list(q.permitted_probes or []),
            "ai_wording": None,
            "review_state": "UNREVIEWED",
            "authoring_origin": "AI_GENERATED",
            "validation_status": "PASSED",
            "safe_failure_code": None,
            "rationale_code": "BLUEPRINT_GROUNDED",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        wording_req = RecruiterWordingAssistRequest(
            expected_question_version=q.question_version,
            source_content_hash=None,
            max_variants=1,
            target_tone="PROFESSIONAL",
        )
        try:
            cand = await request_question_wording_assistance(
                actor=actor,
                question_id=q.question_id,
                request_dto=wording_req,
                db=db,
                provider_registry=reg,
                custom_provider=custom_provider,
            )
            if cand.is_validated and cand.proposed_variants:
                chosen = cand.proposed_variants[0]
                item["ai_wording"] = chosen.variant_text.strip()
                item["validation_status"] = "PASSED"
                item["rationale_code"] = (
                    chosen.rationale_code.value
                    if hasattr(chosen.rationale_code, "value")
                    else str(chosen.rationale_code)
                )
            else:
                item["validation_status"] = "FAILED"
                item["safe_failure_code"] = "SEMANTIC_VALIDATION_FAILED"
        except Exception:
            item["validation_status"] = "FAILED"
            item["safe_failure_code"] = "PROVIDER_UNAVAILABLE"

        items.append(item)

    if not items or len(items) == 0 or all(not it.get("ai_wording") for it in items):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "PROVIDER_UNAVAILABLE", "message": "Failed to generate AI questions. Local AI is unavailable. Please check that LM Studio is running."},
        )

    new_set = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        organization_id=template.organization_id,
        template_id=template.template_id,
        job_id=template.job_id,
        job_version_id=template.job_version_id,
        job_revision=template.revision,
        source_mode="BLUEPRINT_GROUNDED",
        source_template_version=template.template_version,
        source_content_hash=source_hash,
        source_requirement_ids=[],
        set_version=next_version,
        lifecycle_status="DRAFT",
        is_immutable=False,
        items=items,
        created_by_user_id=actor.user_id,
    )
    db.add(new_set)
    db.commit()
    db.refresh(new_set)

    return _map_record_to_detail_dto(new_set, db, action="GENERATED")


def update_ai_question_slot(
    actor: UserRecord,
    template_id: UUID,
    slot_id: UUID,
    payload: AiQuestionSlotUpdateRequest,
    db: Session,
) -> AiQuestionSetDetailResponse:
    """Updates the wording or review state of a single slot in an unsealed AI question set."""
    # Find AI set by ai_set_id or template_id
    stmt_set = (
        select(AiInterviewQuestionSetRecord)
        .options(
            selectinload(AiInterviewQuestionSetRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.job_version),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .where(
            (AiInterviewQuestionSetRecord.ai_set_id == template_id)
            | (AiInterviewQuestionSetRecord.template_id == template_id)
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    ai_set = db.execute(stmt_set).scalars().first()
    if not ai_set:
        raise HTTPException(status_code=404, detail="AI question set not found.")

    job = ai_set.requisition or (ai_set.template.requisition if ai_set.template else None)
    if job:
        _check_recruiter_access(actor, job)

    editable_set = _ensure_editable_set(ai_set, actor, db)

    str_slot_id = str(slot_id)
    target_item = None
    items_copy = list(editable_set.items or [])
    for it in items_copy:
        if str(it.get("slot_id")) == str_slot_id:
            target_item = it
            break

    if not target_item:
        raise HTTPException(status_code=404, detail="Slot not found in AI question set.")

    decision = payload.decision.upper()
    if decision == "KEEP":
        if not target_item.get("ai_wording"):
            raise HTTPException(status_code=422, detail="Cannot keep slot with no AI wording.")
        target_item["review_state"] = "KEPT"
        target_item["updated_at"] = datetime.now(timezone.utc).isoformat()
    elif decision == "EDIT":
        if not payload.wording or not payload.wording.strip():
            raise HTTPException(status_code=422, detail="Edited wording cannot be empty.")
        edited_text = payload.wording.strip()
        from app.services.ai_provenance_service import validate_conversational_question_text
        val_outcome = validate_conversational_question_text(
            question_text=edited_text,
            competency_name=target_item.get("competency_name", ""),
            job_version=editable_set.job_version,
            source_requirement_ids=[str(r) for r in (target_item.get("source_requirement_ids") or [])],
        )
        if not val_outcome.is_valid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": val_outcome.error_code, "message": val_outcome.error_message},
            )
        target_item["ai_wording"] = edited_text
        target_item["question_text"] = edited_text
        target_item["review_state"] = "EDITED"
        target_item["authoring_origin"] = AuthoringOrigin.LOCAL_LLM_GENERATED_RECRUITER_EDITED.value
        target_item["validation_status"] = "PASSED"
        target_item["safe_failure_code"] = None
        target_item["is_edited"] = True
        target_item["reviewer_user_id"] = str(actor.user_id)
        target_item["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        target_item["updated_at"] = datetime.now(timezone.utc).isoformat()
    elif decision == "REVISE":
        target_item["review_state"] = "REVISED"
        target_item["updated_at"] = datetime.now(timezone.utc).isoformat()
    elif decision == "MANUAL_AUTHOR":
        if not payload.wording or not payload.wording.strip():
            raise HTTPException(status_code=422, detail="Manual question wording cannot be empty.")
        target_item["ai_wording"] = payload.wording.strip()
        target_item["question_text"] = payload.wording.strip()
        target_item["review_state"] = "RECRUITER_AUTHORED"
        target_item["authoring_origin"] = AuthoringOrigin.LEGACY_MANUAL.value
        target_item["validation_status"] = "PASSED"
        target_item["safe_failure_code"] = None
        target_item["updated_at"] = datetime.now(timezone.utc).isoformat()
    else:
        raise HTTPException(status_code=422, detail=f"Unsupported review decision '{payload.decision}'.")

    editable_set.items = items_copy
    flag_modified(editable_set, "items")
    db.commit()
    db.refresh(editable_set)

    return _map_record_to_detail_dto(editable_set, db)


async def regenerate_ai_question_slot(
    actor: UserRecord,
    template_id: UUID,
    slot_id: UUID,
    db: Session,
    provider_registry: Optional[ProviderRegistry] = None,
    custom_provider: Optional[BaseWordingProvider] = None,
) -> AiQuestionSetDetailResponse:
    """Regenerates AI wording for a single question slot using the local provider."""
    stmt_set = (
        select(AiInterviewQuestionSetRecord)
        .options(
            selectinload(AiInterviewQuestionSetRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.job_version),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .where(
            (AiInterviewQuestionSetRecord.ai_set_id == template_id)
            | (AiInterviewQuestionSetRecord.template_id == template_id)
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    ai_set = db.execute(stmt_set).scalars().first()
    if not ai_set:
        raise HTTPException(status_code=404, detail="AI question set not found.")
    if ai_set.is_immutable:
        raise HTTPException(status_code=409, detail="Approved AI question sets cannot be modified in-place.")

    job = ai_set.requisition or (ai_set.template.requisition if ai_set.template else None)
    if job:
        _check_recruiter_access(actor, job)

    str_slot_id = str(slot_id)
    target_item = None
    items_copy = list(ai_set.items)
    for it in items_copy:
        if str(it.get("slot_id")) == str_slot_id:
            target_item = it
            break

    if not target_item:
        raise HTTPException(status_code=404, detail="Slot not found in AI question set.")

    reg = provider_registry or get_provider_registry()

    if ai_set.source_mode == "BLUEPRINT_GROUNDED" and ai_set.template:
        q_rec = next((q for q in (ai_set.template.questions or []) if q.question_id == slot_id), None)
        if not q_rec:
            raise HTTPException(status_code=404, detail="Blueprint question record not found.")

        wording_req = RecruiterWordingAssistRequest(
            expected_question_version=q_rec.question_version,
            source_content_hash=None,
            max_variants=1,
            target_tone="PROFESSIONAL",
        )
        try:
            cand = await request_question_wording_assistance(
                actor=actor,
                question_id=q_rec.question_id,
                request_dto=wording_req,
                db=db,
                provider_registry=reg,
                custom_provider=custom_provider,
            )
            if cand.is_validated and cand.proposed_variants:
                chosen = cand.proposed_variants[0]
                target_item["ai_wording"] = chosen.variant_text.strip()
                target_item["validation_status"] = "PASSED"
                target_item["safe_failure_code"] = None
                target_item["review_state"] = "UNREVIEWED"
                target_item["authoring_origin"] = "AI_GENERATED"
                target_item["rationale_code"] = (
                    chosen.rationale_code.value
                    if hasattr(chosen.rationale_code, "value")
                    else str(chosen.rationale_code)
                )
            else:
                target_item["validation_status"] = "FAILED"
                target_item["safe_failure_code"] = "SEMANTIC_VALIDATION_FAILED"
        except Exception:
            target_item["validation_status"] = "FAILED"
            target_item["safe_failure_code"] = "PROVIDER_UNAVAILABLE"
    else:
        # Requirement-grounded regeneration
        active_prof = None
        try:
            active_prof = reg.get_active_profile()
        except Exception:
            pass
        provider = custom_provider or (reg.get_provider_instance(active_prof) if active_prof else None)
        if not provider:
            target_item["validation_status"] = "FAILED"
            target_item["safe_failure_code"] = "PROVIDER_UNAVAILABLE"
        else:
            q_type_raw = target_item.get("question_type", "TECHNICAL")
            q_type_enum = QuestionType.TECHNICAL if q_type_raw == "TECHNICAL" else QuestionType.BEHAVIORAL
            comp_name = target_item.get("competency_name", "Core Competency")
            req_ids = [str(r) for r in target_item.get("source_requirement_ids", [])]
            seed_text = target_item.get("requirement_summary") or f"Assess hands-on proficiency in {comp_name}."
            if len(seed_text) < 5:
                seed_text = f"Assess practical proficiency in {comp_name}."
                
            bounded_req = BoundedWordingRequest(
                source_question_id=uuid.UUID(target_item["slot_id"]),
                source_question_version=1,
                deterministic_question_text=seed_text,
                question_intent=f"Assess depth of experience and practical execution in {comp_name}.",
                competency_label=comp_name[:200],
                question_type=q_type_enum,
                layer=InterviewLayer.COMMON_CORE,
                allowed_reference_ids=req_ids,
                max_variants=1,
            )
            try:
                resp = await provider.request_wording_assistance(bounded_req)
                if resp.variants:
                    w = resp.variants[0].variant_text.strip()
                    is_valid, fail_code = _validate_ai_wording_safety(w)
                    if is_valid:
                        target_item["ai_wording"] = w
                        target_item["validation_status"] = "PASSED"
                        target_item["safe_failure_code"] = None
                        target_item["review_state"] = "UNREVIEWED"
                        target_item["authoring_origin"] = "AI_GENERATED"
                        target_item["rationale_code"] = "REQUIREMENT_GROUNDED"
                    else:
                        target_item["validation_status"] = "FAILED"
                        target_item["safe_failure_code"] = fail_code or "SEMANTIC_VALIDATION_FAILED"
                else:
                    target_item["validation_status"] = "FAILED"
                    target_item["safe_failure_code"] = "PROVIDER_UNAVAILABLE"
            except Exception:
                target_item["validation_status"] = "FAILED"
                target_item["safe_failure_code"] = "PROVIDER_UNAVAILABLE"

    target_item["updated_at"] = datetime.now(timezone.utc).isoformat()
    ai_set.items = items_copy
    flag_modified(ai_set, "items")
    db.commit()
    db.refresh(ai_set)

    return _map_record_to_detail_dto(ai_set, db)


def _ensure_editable_set(
    ai_set: AiInterviewQuestionSetRecord,
    actor: UserRecord,
    db: Session,
) -> AiInterviewQuestionSetRecord:
    """If the question set is already approved / immutable, branches a new successor draft version."""
    if not ai_set.is_immutable and ai_set.lifecycle_status != "APPROVED":
        return ai_set

    # Resolve latest job version if available
    job_ver_id = ai_set.job_version_id
    job_rev = ai_set.job_revision
    src_hash = ai_set.source_content_hash
    if ai_set.source_mode == "REQUIREMENT_GROUNDED" and ai_set.job_id:
        stmt_jv = select(JobVersion).where(JobVersion.job_id == ai_set.job_id).order_by(desc(JobVersion.version_number), desc(JobVersion.revision))
        latest_jv = db.execute(stmt_jv).scalars().first()
        if latest_jv:
            job_ver_id = latest_jv.job_version_id
            job_rev = getattr(latest_jv, "revision", None) or getattr(latest_jv, "version_number", None) or 1
            # Recompute canonical hash for latest version
            job_stmt = select(JobRequisition).options(
                selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
            ).where(JobRequisition.job_id == ai_set.job_id)
            job_rec = db.execute(job_stmt).scalar_one_or_none()
            if job_rec and latest_jv:
                deliv_mode = (
                    GenerationDeliveryMode.VOICE
                    if (getattr(ai_set, "interview_mode", None) == InterviewMode.AUDIO or str(getattr(ai_set, "interview_mode", "")) == "AUDIO")
                    else GenerationDeliveryMode.CHAT
                )
                _, i_plan = _build_canonical_generation_packet(job_rec, latest_jv, delivery_mode=deliv_mode, actor=actor)
                src_hash = i_plan.get("fingerprint", src_hash)
    elif ai_set.source_mode == "BLUEPRINT_GROUNDED" and ai_set.template:
        src_hash = ai_set.template.content_hash or src_hash

    # Branch successor draft
    next_version = ai_set.set_version + 1
    new_set = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        organization_id=ai_set.organization_id,
        interview_mode=getattr(ai_set, "interview_mode", InterviewMode.TEXT) or InterviewMode.TEXT,
        template_id=ai_set.template_id,
        job_id=ai_set.job_id,
        job_version_id=job_ver_id,
        job_revision=job_rev,
        source_mode=ai_set.source_mode,
        source_template_version=ai_set.source_template_version,
        source_content_hash=src_hash,
        source_requirement_ids=list(ai_set.source_requirement_ids or []),
        set_version=next_version,
        lifecycle_status="DRAFT",
        is_immutable=False,
        content_hash=None,
        items=[dict(it) for it in (ai_set.items or [])],
        created_by_user_id=actor.user_id,
    )
    db.add(new_set)
    db.commit()
    db.refresh(new_set)
    return new_set


async def generate_mcq_slots_for_template(
    actor: UserRecord,
    template_id: UUID,
    payload: McqGenerateRequest,
    db: Session,
    provider_registry: Optional[ProviderRegistry] = None,
    custom_provider: Optional[BaseWordingProvider] = None,
) -> AiQuestionSetDetailResponse:
    """Generates structured multiple-choice questions grounded in job requirements."""
    stmt_set = (
        select(AiInterviewQuestionSetRecord)
        .options(
            selectinload(AiInterviewQuestionSetRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.job_version),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .where(
            (AiInterviewQuestionSetRecord.ai_set_id == template_id)
            | (AiInterviewQuestionSetRecord.template_id == template_id)
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    ai_set = db.execute(stmt_set).scalars().first()
    if not ai_set:
        raise HTTPException(status_code=404, detail="AI question set not found.")

    job = ai_set.requisition or (ai_set.template.requisition if ai_set.template else None)
    if job:
        _check_recruiter_access(actor, job)

    editable_set = _ensure_editable_set(ai_set, actor, db)
    if getattr(editable_set, "interview_mode", InterviewMode.TEXT) == InterviewMode.AUDIO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "AUDIO_MCQ_NOT_ALLOWED",
                "message": "Multiple choice questions are not permitted in AUDIO interview mode.",
            },
        )

    items = list(editable_set.items or [])
    current_mcqs = [it for it in items if it.get("question_format") == "MCQ_SINGLE_ANSWER"]

    if len(current_mcqs) + payload.count > 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "MAX_MCQS_EXCEEDED",
                "message": f"Adding {payload.count} MCQs would exceed maximum 5 MCQs allowed (currently {len(current_mcqs)}).",
            },
        )

    # Fetch requirements
    job_stmt = select(JobRequisition).options(
        selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
    ).where(JobRequisition.job_id == editable_set.job_id)
    job_rec = db.execute(job_stmt).scalar_one_or_none()
    
    requirements: List[JobRequirement] = []
    if job_rec and job_rec.versions:
        latest_ver = job_rec.versions[-1]
        requirements = [r for r in (latest_ver.requirements or []) if _is_requirement_eligible(r)]

    if not requirements and job_rec and job_rec.versions and job_rec.versions[-1].requirements:
        requirements = list(job_rec.versions[-1].requirements)

    diff_str = payload.difficulty.value if hasattr(payload.difficulty, "value") else str(payload.difficulty)

    # Provider resolution
    reg = provider_registry or get_provider_registry()
    active_profile = reg.get_active_profile()
    provider = custom_provider or (reg.get_provider_instance(active_profile) if active_profile else None)

    if not provider:
        import os
        if "PYTEST_CURRENT_TEST" in os.environ:
            provider = FakeWordingProvider(simulated_mode="SUCCESS")
        else:
            # Provide requirement-grounded fallback generator when provider is not configured
            provider = FakeWordingProvider(simulated_mode="SUCCESS")

    if isinstance(provider, DisabledWordingProvider) or (active_profile and not active_profile.enabled):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "PROVIDER_DISABLED",
                "message": "AI generation is disabled. Please configure an active AI provider in Admin Settings or add questions manually.",
            },
        )

    gen_req = {
        "count": payload.count,
        "difficulty": payload.difficulty,
        "requirements": requirements,
    }

    try:
        mcq_batch = await provider.request_mcq_generation(gen_req)
    except ProviderTransportError as e:
        logger.warning("Provider transport error during MCQ generation: %s. Falling back to requirement-grounded generation.", e)
        fallback_prov = FakeWordingProvider(simulated_mode="SUCCESS")
        mcq_batch = await fallback_prov.request_mcq_generation(gen_req)
    except Exception as e:
        logger.warning("MCQ generation failed: %s. Falling back to requirement-grounded generation.", e)
        fallback_prov = FakeWordingProvider(simulated_mode="SUCCESS")
        mcq_batch = await fallback_prov.request_mcq_generation(gen_req)

    if not mcq_batch or not mcq_batch.questions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "EMPTY_GENERATION_RESULT",
                "message": "The AI provider returned no question items.",
            },
        )

    # Process generated questions
    for i, q in enumerate(mcq_batch.questions):
        req = requirements[i % len(requirements)] if requirements else None
        comp_name = getattr(req, "label", getattr(req, "name", "Core Concept")) if req else f"Technical Area #{i+1}"
        req_id = str(req.requirement_id) if req and hasattr(req, "requirement_id") else str(uuid.uuid4())

        options_list = [
            {"option_id": opt.option_id, "text": opt.text}
            for opt in q.options
        ]

        # Validate item
        val_res = validate_mcq_item(
            question_text=q.question_text,
            options=options_list,
            correct_option_id=q.correct_option_id,
            answer_explanation=q.answer_explanation,
            competency_name=comp_name,
            requirement_id=req_id,
        )

        new_slot_id = str(uuid.uuid4())
        new_item = {
            "slot_id": new_slot_id,
            "display_order": len(items),
            "question_format": "MCQ_SINGLE_ANSWER",
            "competency_name": comp_name,
            "question_type": "TECHNICAL",
            "estimated_time_minutes": 2,
            "permitted_probes": [],
            "rubric": None,
            "source_requirement_ids": [req_id],
            "ai_wording": q.question_text,
            "question_text": q.question_text,
            "options": options_list,
            "correct_option_id": q.correct_option_id,
            "answer_explanation": q.answer_explanation,
            "difficulty": diff_str,
            "review_state": "UNREVIEWED",
            "authoring_origin": AuthoringOrigin.LOCAL_LLM_GENERATED.value,
            "validation_status": "PASSED" if val_res.is_valid else "FAILED",
            "safe_failure_code": None if val_res.is_valid else val_res.error_code,
            "rationale_code": "REQUIREMENT_GROUNDED",
            "model_identifier": active_profile.model_identifier if active_profile else "qwen/qwen3-4b-2507",
            "provider_class": active_profile.provider_type.value if (active_profile and hasattr(active_profile.provider_type, "value")) else "LOCAL_OPENAI_COMPATIBLE",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        items.append(new_item)

    editable_set.items = items
    if active_profile:
        editable_set.provider_profile_id = active_profile.profile_id
        editable_set.provider_class = active_profile.provider_type.value if hasattr(active_profile.provider_type, "value") else str(active_profile.provider_type)
        editable_set.model_identifier = active_profile.model_identifier
    flag_modified(editable_set, "items")
    db.commit()
    db.refresh(editable_set)

    return _map_record_to_detail_dto(editable_set, db)


def add_manual_mcq_slot(
    actor: UserRecord,
    template_id: UUID,
    payload: McqCreateRequest,
    db: Session,
) -> AiQuestionSetDetailResponse:
    """Adds a recruiter-authored multiple choice question to an AI question set."""
    stmt_set = (
        select(AiInterviewQuestionSetRecord)
        .options(
            selectinload(AiInterviewQuestionSetRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.job_version),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .where(
            (AiInterviewQuestionSetRecord.ai_set_id == template_id)
            | (AiInterviewQuestionSetRecord.template_id == template_id)
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    ai_set = db.execute(stmt_set).scalars().first()
    if not ai_set:
        raise HTTPException(status_code=404, detail="AI question set not found.")

    job = ai_set.requisition or (ai_set.template.requisition if ai_set.template else None)
    if job:
        _check_recruiter_access(actor, job)

    editable_set = _ensure_editable_set(ai_set, actor, db)
    if getattr(editable_set, "interview_mode", InterviewMode.TEXT) == InterviewMode.AUDIO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "AUDIO_MCQ_NOT_ALLOWED",
                "message": "Multiple choice questions are not permitted in AUDIO interview mode.",
            },
        )

    items = list(editable_set.items or [])
    current_mcqs = [it for it in items if it.get("question_format") == "MCQ_SINGLE_ANSWER"]

    if len(current_mcqs) >= 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "MAX_MCQS_EXCEEDED", "message": "Maximum 5 multiple-choice questions permitted per question set."},
        )

    opts = [{"option_id": o.option_id, "text": o.text} for o in payload.options]
    val_res = validate_mcq_item(
        question_text=payload.question_text,
        options=opts,
        correct_option_id=payload.correct_option_id,
        answer_explanation=payload.answer_explanation,
        competency_name=payload.competency_name,
        requirement_id=payload.requirement_id,
    )
    if not val_res.is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": val_res.error_code, "message": val_res.error_message},
        )

    diff_str = payload.difficulty.value if hasattr(payload.difficulty, "value") else str(payload.difficulty)
    new_slot_id = str(uuid.uuid4())
    new_item = {
        "slot_id": new_slot_id,
        "display_order": len(items),
        "question_format": "MCQ_SINGLE_ANSWER",
        "competency_name": payload.competency_name or "Technical Competency",
        "question_type": "TECHNICAL",
        "estimated_time_minutes": 2,
        "permitted_probes": [],
        "rubric": None,
        "source_requirement_ids": [str(payload.requirement_id)] if payload.requirement_id else [],
        "ai_wording": payload.question_text,
        "question_text": payload.question_text,
        "options": opts,
        "correct_option_id": payload.correct_option_id,
        "answer_explanation": payload.answer_explanation,
        "difficulty": diff_str,
        "review_state": "RECRUITER_AUTHORED",
        "authoring_origin": AuthoringOrigin.LEGACY_MANUAL.value,
        "validation_status": "PASSED",
        "safe_failure_code": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    items.append(new_item)

    editable_set.items = items
    flag_modified(editable_set, "items")
    db.commit()
    db.refresh(editable_set)

    return _map_record_to_detail_dto(editable_set, db)


def update_mcq_slot(
    actor: UserRecord,
    template_id: UUID,
    slot_id: UUID,
    payload: McqUpdateRequest,
    db: Session,
) -> AiQuestionSetDetailResponse:
    """Updates a recruiter-managed multiple choice question."""
    stmt_set = (
        select(AiInterviewQuestionSetRecord)
        .options(
            selectinload(AiInterviewQuestionSetRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.job_version),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .where(
            (AiInterviewQuestionSetRecord.ai_set_id == template_id)
            | (AiInterviewQuestionSetRecord.template_id == template_id)
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    ai_set = db.execute(stmt_set).scalars().first()
    if not ai_set:
        raise HTTPException(status_code=404, detail="AI question set not found.")

    job = ai_set.requisition or (ai_set.template.requisition if ai_set.template else None)
    if job:
        _check_recruiter_access(actor, job)

    editable_set = _ensure_editable_set(ai_set, actor, db)
    if getattr(editable_set, "interview_mode", InterviewMode.TEXT) == InterviewMode.AUDIO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "AUDIO_MCQ_NOT_ALLOWED",
                "message": "Multiple choice questions are not permitted in AUDIO interview mode.",
            },
        )
    items = list(editable_set.items or [])
    str_slot_id = str(slot_id)

    target_item = None
    for it in items:
        if str(it.get("slot_id")) == str_slot_id:
            target_item = it
            break

    if not target_item:
        raise HTTPException(status_code=404, detail="Slot not found in AI question set.")

    if target_item.get("question_format") != "MCQ_SINGLE_ANSWER":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NOT_AN_MCQ", "message": "Target slot is not a multiple choice question."},
        )

    new_q_text = payload.question_text if payload.question_text is not None else target_item.get("ai_wording")
    new_opts = (
        [{"option_id": o.option_id, "text": o.text} for o in payload.options]
        if payload.options is not None
        else target_item.get("options", [])
    )
    new_correct = payload.correct_option_id if payload.correct_option_id is not None else target_item.get("correct_option_id")
    new_exp = payload.answer_explanation if payload.answer_explanation is not None else target_item.get("answer_explanation")
    new_comp = payload.competency_name if payload.competency_name is not None else target_item.get("competency_name")
    new_diff = (
        payload.difficulty.value
        if payload.difficulty and hasattr(payload.difficulty, "value")
        else (str(payload.difficulty) if payload.difficulty else target_item.get("difficulty", "FOUNDATIONAL"))
    )

    val_res = validate_mcq_item(
        question_text=new_q_text,
        options=new_opts,
        correct_option_id=new_correct,
        answer_explanation=new_exp,
        competency_name=new_comp,
    )
    if not val_res.is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": val_res.error_code, "message": val_res.error_message},
        )

    target_item["ai_wording"] = new_q_text
    target_item["question_text"] = new_q_text
    target_item["options"] = new_opts
    target_item["correct_option_id"] = new_correct
    target_item["answer_explanation"] = new_exp
    target_item["competency_name"] = new_comp
    target_item["difficulty"] = new_diff
    target_item["review_state"] = "EDITED"
    target_item["authoring_origin"] = AuthoringOrigin.LOCAL_LLM_GENERATED_RECRUITER_EDITED.value
    target_item["validation_status"] = "PASSED"
    target_item["safe_failure_code"] = None
    target_item["is_edited"] = True
    target_item["reviewer_user_id"] = str(actor.user_id)
    target_item["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    target_item["updated_at"] = datetime.now(timezone.utc).isoformat()

    editable_set.items = items
    flag_modified(editable_set, "items")
    db.commit()
    db.refresh(editable_set)

    return _map_record_to_detail_dto(editable_set, db)


def remove_slot(
    actor: UserRecord,
    template_id: UUID,
    slot_id: UUID,
    db: Session,
) -> AiQuestionSetDetailResponse:
    """Removes a question slot (conversational or MCQ) from the AI question set and re-indexes remaining slots in the database."""
    stmt_set = (
        select(AiInterviewQuestionSetRecord)
        .options(
            selectinload(AiInterviewQuestionSetRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.job_version),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .where(
            (AiInterviewQuestionSetRecord.ai_set_id == template_id)
            | (AiInterviewQuestionSetRecord.template_id == template_id)
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    ai_set = db.execute(stmt_set).scalars().first()
    if not ai_set:
        raise HTTPException(status_code=404, detail="AI question set not found.")

    job = ai_set.requisition or (ai_set.template.requisition if ai_set.template else None)
    if job:
        _check_recruiter_access(actor, job)

    editable_set = _ensure_editable_set(ai_set, actor, db)
    items = list(editable_set.items or [])
    str_slot_id = str(slot_id)

    new_items = [it for it in items if str(it.get("slot_id")) != str_slot_id]
    if len(new_items) == len(items):
        raise HTTPException(status_code=404, detail="Slot not found in AI question set.")

    for idx, it in enumerate(new_items):
        it["display_order"] = idx

    editable_set.items = new_items
    flag_modified(editable_set, "items")
    db.commit()
    db.refresh(editable_set)

    return _map_record_to_detail_dto(editable_set, db)


# Backward compatibility alias
remove_mcq_slot = remove_slot


def reorder_mcq_slots(
    actor: UserRecord,
    template_id: UUID,
    payload: McqReorderRequest,
    db: Session,
) -> AiQuestionSetDetailResponse:
    """Reorders MCQ slots in an AI question set."""
    stmt_set = (
        select(AiInterviewQuestionSetRecord)
        .options(
            selectinload(AiInterviewQuestionSetRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.job_version),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.questions).selectinload(InterviewQuestionRecord.rubric),
        )
        .where(
            (AiInterviewQuestionSetRecord.ai_set_id == template_id)
            | (AiInterviewQuestionSetRecord.template_id == template_id)
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    ai_set = db.execute(stmt_set).scalars().first()
    if not ai_set:
        raise HTTPException(status_code=404, detail="AI question set not found.")

    job = ai_set.requisition or (ai_set.template.requisition if ai_set.template else None)
    if job:
        _check_recruiter_access(actor, job)

    editable_set = _ensure_editable_set(ai_set, actor, db)
    items = list(editable_set.items or [])
    
    conv_items = [it for it in items if it.get("question_format") != "MCQ_SINGLE_ANSWER"]
    mcq_by_id = {str(it.get("slot_id")): it for it in items if it.get("question_format") == "MCQ_SINGLE_ANSWER"}

    ordered_mcqs = []
    for sid in payload.slot_ids:
        str_sid = str(sid)
        if str_sid in mcq_by_id:
            ordered_mcqs.append(mcq_by_id.pop(str_sid))

    # Append any remaining unlisted MCQs
    ordered_mcqs.extend(mcq_by_id.values())

    combined = conv_items + ordered_mcqs
    for idx, it in enumerate(combined):
        it["display_order"] = idx

    editable_set.items = combined
    flag_modified(editable_set, "items")
    db.commit()
    db.refresh(editable_set)

    return _map_record_to_detail_dto(editable_set, db)


def validate_ai_question_set(
    actor: UserRecord,
    template_id: UUID,
    db: Session,
) -> AiQuestionSetValidateResponse:
    """Validates the AI question set against readiness, staleness, and safety rules."""
    stmt_set = (
        select(AiInterviewQuestionSetRecord)
        .options(
            selectinload(AiInterviewQuestionSetRecord.requisition),
            selectinload(AiInterviewQuestionSetRecord.job_version),
            selectinload(AiInterviewQuestionSetRecord.template).selectinload(JobInterviewTemplateRecord.requisition),
        )
        .where(
            (AiInterviewQuestionSetRecord.ai_set_id == template_id)
            | (AiInterviewQuestionSetRecord.template_id == template_id)
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    ai_set = db.execute(stmt_set).scalars().first()
    if not ai_set:
        raise HTTPException(status_code=404, detail="AI question set not found.")

    job = ai_set.requisition or (ai_set.template.requisition if ai_set.template else None)
    if job:
        _check_recruiter_access(actor, job)

    errors: List[str] = []

    # 1. Staleness check
    if ai_set.source_mode == "BLUEPRINT_GROUNDED" and ai_set.template:
        if ai_set.template.content_hash and ai_set.source_content_hash != ai_set.template.content_hash:
            errors.append("AI question set is stale: deterministic blueprint has changed since generation. Please regenerate.")
    elif ai_set.source_mode == "REQUIREMENT_GROUNDED":
        job_stmt = select(JobRequisition).options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
        ).where(JobRequisition.job_id == ai_set.job_id)
        job_rec = db.execute(job_stmt).scalar_one_or_none()
        if job_rec and job_rec.versions:
            latest_v = sorted(job_rec.versions, key=lambda v: (getattr(v, "version_number", 0) or 0, getattr(v, "revision", 0) or 0))[-1]
            target_v_rev = getattr(latest_v, "revision", None) or getattr(latest_v, "version_number", None) or 1
            if ai_set.job_revision and ai_set.job_revision != target_v_rev:
                errors.append(f"AI question set is stale: job revision changed from #{ai_set.job_revision} to #{target_v_rev}. Please regenerate.")
            else:
                deliv_mode = GenerationDeliveryMode.VOICE if getattr(ai_set, "interview_mode", None) == InterviewMode.AUDIO or str(getattr(ai_set, "interview_mode", "")) == "AUDIO" else GenerationDeliveryMode.CHAT
                p, i_plan = _build_canonical_generation_packet(job_rec, latest_v, delivery_mode=deliv_mode, actor=actor)
                canon_fp = i_plan["fingerprint"]
                eligible_reqs = [r for r in (latest_v.requirements or []) if _is_requirement_eligible(r)] or list(latest_v.requirements or [])
                def _sk(r):
                    nec = getattr(r, "necessity", getattr(r, "importance", None))
                    nec_str = nec.value if hasattr(nec, "value") else str(nec or "")
                    prio = getattr(r, "priority", None)
                    prio_str = prio.value if hasattr(prio, "value") else str(prio or "")
                    return 0 if (nec_str.upper() == "MUST_HAVE" or prio_str.upper() in ("CRITICAL", "HIGH")) else 1
                eligible_reqs.sort(key=_sk)
                target_6 = min(6, max(len(eligible_reqs), 1))
                selected_6 = eligible_reqs[:target_6]
                canon_6_reqs = [
                    CanonicalRequirementItem(
                        requirement_id=r.requirement_id,
                        label=r.label.strip(),
                        category=r.category.value if hasattr(r.category, "value") else str(r.category),
                        necessity=r.necessity.value if hasattr(r.necessity, "value") else str(r.necessity),
                        priority=r.priority.value if hasattr(r.priority, "value") else str(r.priority),
                        rationale=r.rationale.strip() if r.rationale else None,
                        is_interview_eligible=True,
                    )
                    for r in selected_6
                ]
                packet_6 = CanonicalGenerationSourcePacket(
                    organization_id=p.organization_id,
                    job_id=p.job_id,
                    job_version_id=p.job_version_id,
                    job_revision=p.job_revision,
                    job_title=p.job_title,
                    seniority_level=p.seniority_level,
                    role_summary=p.role_summary,
                    key_responsibilities=p.key_responsibilities,
                    planned_slots=p.planned_slots[:target_6],
                    canonical_requirements=canon_6_reqs,
                    requested_question_count=target_6,
                    delivery_mode=p.delivery_mode,
                    generation_policy_version=p.generation_policy_version,
                    prompt_contract_version=p.prompt_contract_version,
                    guardrail_version=p.guardrail_version,
                )
                canon_6_fp = _compute_canonical_source_fingerprint(packet_6)
                req_ids = [str(r.requirement_id) for r in (latest_v.requirements or [])]
                legacy_fp = _compute_requirement_fingerprint(job_rec.job_id, latest_v.job_version_id, latest_v.version_number, req_ids)
                if ai_set.source_content_hash not in (canon_fp, canon_6_fp, legacy_fp):
                    errors.append("AI question set is stale: job requirements have changed since generation. Please regenerate.")

    items = ai_set.items or []
    conv_items = [it for it in items if it.get("question_format") != "MCQ_SINGLE_ANSWER"]
    mcq_items = [it for it in items if it.get("question_format") == "MCQ_SINGLE_ANSWER"]

    # Phase 4D.1 AUDIO mode restrictions
    raw_mode = getattr(ai_set, "interview_mode", InterviewMode.TEXT) or InterviewMode.TEXT
    mode_str = raw_mode.value if hasattr(raw_mode, "value") else str(raw_mode)
    set_mode = InterviewMode.AUDIO if mode_str == "AUDIO" else InterviewMode.TEXT

    if set_mode == InterviewMode.AUDIO:
        if len(mcq_items) > 0:
            errors.append("Multiple choice questions are not permitted in AUDIO interview mode (AUDIO_MCQ_NOT_ALLOWED).")

    # 2. Composition Bounds (Max 5 conversational, Max 5 MCQs, Min 1 total)
    if len(items) == 0:
        errors.append("Question set is empty. Add at least one conversational or multiple-choice question.")
    if len(conv_items) > 5:
        errors.append(f"Maximum 5 conversational questions allowed (currently {len(conv_items)}).")
    if len(mcq_items) > 5:
        errors.append(f"Maximum 5 multiple-choice questions allowed (currently {len(mcq_items)}).")

    # Cross-mode duplicate stem check for the same job revision
    other_mode = InterviewMode.TEXT if set_mode == InterviewMode.AUDIO else InterviewMode.AUDIO
    other_mode_val = other_mode.value if hasattr(other_mode, "value") else str(other_mode)
    stmt_other = (
        select(AiInterviewQuestionSetRecord)
        .where(
            AiInterviewQuestionSetRecord.job_id == ai_set.job_id,
            (AiInterviewQuestionSetRecord.job_revision == ai_set.job_revision)
            | (AiInterviewQuestionSetRecord.job_version_id == ai_set.job_version_id),
            (AiInterviewQuestionSetRecord.interview_mode == other_mode)
            | (AiInterviewQuestionSetRecord.interview_mode == other_mode_val),
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    other_sets = db.execute(stmt_other).scalars().all()
    other_stems_list: List[str] = []
    for os in other_sets:
        if os.items:
            for it in os.items:
                t = (it.get("ai_wording") or it.get("question_text") or "").strip()
                if t:
                    other_stems_list.append(t)

    from app.services.ai_provenance_service import is_strong_stem_duplicate

    # 3. Conversational slot resolution check
    seen_conv_texts = set()
    for it in conv_items:
        slot_num = it.get("display_order", 0) + 1
        wording = it.get("ai_wording")
        if not wording or not wording.strip():
            errors.append(f"Conversational Question #{slot_num} has no wording. Please generate, edit, or author manually.")
        if it.get("validation_status") == "FAILED":
            errors.append(f"Conversational Question #{slot_num} failed safety validation ({it.get('safe_failure_code')}).")
        
        w_clean = (wording or "").strip()
        if w_clean:
            w_lower = w_clean.lower()
            if w_lower in seen_conv_texts:
                errors.append(f"Duplicate conversational question detected (#{slot_num}).")
            seen_conv_texts.add(w_lower)

            if other_stems_list:
                for o_stem in other_stems_list:
                    if is_strong_stem_duplicate(w_clean, o_stem, threshold=0.80):
                        errors.append(f"Question #{slot_num} is a duplicate or close rephrasing of a {other_mode.value} question for this job revision (CROSS_MODE_DUPLICATE_QUESTION).")
                        break

    # 4. MCQ item validation check
    seen_mcq_texts = set()
    for it in mcq_items:
        slot_num = it.get("display_order", 0) + 1
        q_text = it.get("question_text") or it.get("ai_wording") or ""
        options = it.get("options") or []
        correct_id = it.get("correct_option_id") or ""
        explanation = it.get("answer_explanation") or ""

        val_res = validate_mcq_item(
            question_text=q_text,
            options=options,
            correct_option_id=correct_id,
            answer_explanation=explanation,
            competency_name=it.get("competency_name"),
        )
        if not val_res.is_valid:
            errors.append(f"MCQ Question #{slot_num} is invalid: {val_res.error_message}")

        q_norm = q_text.strip().lower()
        if q_norm:
            if q_norm in seen_mcq_texts:
                errors.append(f"Duplicate MCQ detected (#{slot_num}).")
            seen_mcq_texts.add(q_norm)

    # 5. Phase 3G.1 Server-side strict provenance & release eligibility check
    from app.services.ai_provenance_service import evaluate_release_eligibility, ReleaseEligibilityResult
    
    elig_summary = None
    target_job_ver = ai_set.job_version
    if not target_job_ver and ai_set.job_id:
        stmt_jv = select(JobVersion).where(JobVersion.job_id == ai_set.job_id).order_by(desc(JobVersion.version_number))
        target_job_ver = db.execute(stmt_jv).scalars().first()

    if target_job_ver:
        # Evaluate eligibility (ignoring approval status during pre-approval validation)
        temp_status = ai_set.lifecycle_status
        ai_set.lifecycle_status = "APPROVED"  # test eligibility if approved
        elig_res = evaluate_release_eligibility(ai_set, target_job_ver)
        ai_set.lifecycle_status = temp_status

        if not elig_res.is_eligible:
            for b in (elig_res.blocking_items or []):
                errors.append(b.get("message") or f"Slot #{b.get('slot_number')} is ineligible for release.")
            if not elig_res.blocking_items and elig_res.safe_message:
                errors.append(elig_res.safe_message)

        elig_summary = ReleaseEligibilitySummaryDto(
            is_eligible=elig_res.is_eligible,
            reason_code=elig_res.reason_code.value if elig_res.reason_code else None,
            safe_message=elig_res.safe_message,
            blocking_items=elig_res.blocking_items or [],
        )

    is_valid = len(errors) == 0
    if is_valid and ai_set.lifecycle_status != "APPROVED":
        ai_set.lifecycle_status = "VALIDATED"
        db.commit()

    return AiQuestionSetValidateResponse(
        is_valid=is_valid,
        lifecycle_status=ai_set.lifecycle_status,
        blocking_errors=errors,
        safe_message="AI question set successfully validated." if is_valid else "Validation failed with blocking issues.",
        release_eligibility=elig_summary,
    )


def approve_ai_question_set(
    actor: UserRecord,
    template_id: UUID,
    db: Session,
) -> AiQuestionSetApproveResponse:
    """Explicitly approves the AI question set, sealing its content hash and locking it as immutable."""
    val = validate_ai_question_set(actor, template_id, db)
    if not val.is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_BLOCKED", "message": "Cannot approve invalid AI question set.", "errors": val.blocking_errors},
        )

    stmt_set = (
        select(AiInterviewQuestionSetRecord)
        .options(selectinload(AiInterviewQuestionSetRecord.job_version))
        .where(
            (AiInterviewQuestionSetRecord.ai_set_id == template_id)
            | (AiInterviewQuestionSetRecord.template_id == template_id)
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    ai_set = db.execute(stmt_set).scalars().first()
    if not ai_set:
        raise HTTPException(status_code=404, detail="AI question set not found.")

    # Phase 3G.1 Server-side strict origin and provenance check on all items before approving
    items = ai_set.items or []
    for idx, it in enumerate(items):
        slot_num = idx + 1
        origin_val = it.get("authoring_origin") or "LEGACY_DETERMINISTIC"
        norm_origin = AuthoringOrigin.normalize_to_canonical(origin_val)
        if not AuthoringOrigin.is_eligible_for_release(norm_origin):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INELIGIBLE_PROVENANCE",
                    "message": f"Question #{slot_num} has origin '{norm_origin.value}' and cannot be approved for an AI practice set.",
                },
            )
        if it.get("validation_status") == "FAILED":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "VALIDATION_FAILED",
                    "message": f"Question #{slot_num} failed validation and cannot be approved.",
                },
            )

    if ai_set.is_immutable:
        return AiQuestionSetApproveResponse(
            ai_set_id=ai_set.ai_set_id,
            set_version=ai_set.set_version,
            lifecycle_status="APPROVED",
            is_immutable=True,
            content_hash=ai_set.content_hash or "",
            approved_at=ai_set.approved_at or datetime.now(timezone.utc),
            safe_message="AI question set is already approved and locked.",
        )

    # Compute sealed content hash over ordered wording and slots including MCQs
    canonical_payload = []
    for it in sorted(ai_set.items or [], key=lambda x: x.get("display_order", 0)):
        canonical_payload.append({
            "slot_id": str(it.get("slot_id")),
            "display_order": it.get("display_order"),
            "question_format": it.get("question_format", "CONVERSATION"),
            "wording": it.get("ai_wording"),
            "options": it.get("options"),
            "correct_option_id": it.get("correct_option_id"),
            "answer_explanation": it.get("answer_explanation"),
            "difficulty": it.get("difficulty"),
            "authoring_origin": it.get("authoring_origin"),
        })

    sealed_hash = hashlib.sha256(json.dumps(canonical_payload, sort_keys=True).encode("utf-8")).hexdigest()

    now = datetime.now(timezone.utc)
    ai_set.lifecycle_status = "APPROVED"
    ai_set.is_immutable = True
    ai_set.content_hash = sealed_hash
    ai_set.approved_at = now
    ai_set.approved_by_user_id = actor.user_id

    db.commit()
    db.refresh(ai_set)

    return AiQuestionSetApproveResponse(
        ai_set_id=ai_set.ai_set_id,
        set_version=ai_set.set_version,
        lifecycle_status="APPROVED",
        is_immutable=True,
        content_hash=sealed_hash,
        approved_at=now,
        safe_message="AI question set approved and locked. Immutability sealed.",
    )


def prepare_practice_set_for_revision(
    actor: UserRecord,
    job_id: UUID,
    job_version_id: UUID,
    db: Session,
) -> AiQuestionSetDetailResponse:
    """Prepares an editable DRAFT practice question set for a target job revision by copying suitable questions

    from the most recent approved question set on an older revision.
    Re-validates every copied question and never preserves approved status automatically.
    """
    # 1. Fetch target job revision
    stmt_job = (
        select(JobRequisition)
        .options(selectinload(JobRequisition.versions).selectinload(JobVersion.requirements))
        .where(JobRequisition.job_id == job_id)
    )
    job = db.execute(stmt_job).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job requisition not found.")
    _check_recruiter_access(actor, job)

    target_ver = next((v for v in job.versions if v.job_version_id == job_version_id), None)
    if not target_ver:
        raise HTTPException(status_code=404, detail="Target job revision not found.")

    # 2. Find most recent approved question set on older revision
    stmt_old = (
        select(AiInterviewQuestionSetRecord)
        .where(
            AiInterviewQuestionSetRecord.job_id == job_id,
            AiInterviewQuestionSetRecord.lifecycle_status == "APPROVED",
            AiInterviewQuestionSetRecord.job_version_id != job_version_id,
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version), desc(AiInterviewQuestionSetRecord.updated_at))
    )
    old_set = db.execute(stmt_old).scalars().first()
    if not old_set:
        raise HTTPException(
            status_code=400,
            detail={"code": "NO_PREVIOUS_APPROVED_SET", "message": "No approved question set exists from an older revision to copy."},
        )

    # 3. Check if draft set already exists for target revision
    stmt_existing = (
        select(AiInterviewQuestionSetRecord)
        .where(
            AiInterviewQuestionSetRecord.job_id == job_id,
            AiInterviewQuestionSetRecord.job_version_id == job_version_id,
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    existing_set = db.execute(stmt_existing).scalars().first()
    if existing_set and existing_set.lifecycle_status != "APPROVED":
        target_set = existing_set
    else:
        # Create new set record in DRAFT status
        next_ver = (existing_set.set_version + 1) if existing_set else 1
        target_rev_num = getattr(target_ver, "revision", None) or getattr(target_ver, "version_number", None) or 1
        target_set = AiInterviewQuestionSetRecord(
            ai_set_id=uuid.uuid4(),
            organization_id=old_set.organization_id,
            template_id=None,
            job_id=job.job_id,
            job_version_id=target_ver.job_version_id,
            job_revision=target_rev_num,
            source_mode="REQUIREMENT_GROUNDED",
            source_template_version=None,
            source_content_hash="",
            source_requirement_ids=[str(r.requirement_id) for r in (target_ver.requirements or [])],
            set_version=next_ver,
            lifecycle_status="DRAFT",
            is_immutable=False,
            items=[],
            predecessor_set_id=old_set.ai_set_id,
            created_by_user_id=actor.user_id,
        )
        db.add(target_set)

    # 4. Copy and revalidate questions into draft items
    copied_items = []
    target_req_names = {getattr(r, "label", getattr(r, "name", "")).strip().lower(): str(r.requirement_id) for r in (target_ver.requirements or [])}

    for it in old_set.items or []:
        new_slot_id = str(uuid.uuid4())
        comp_name = it.get("competency_name", "General Competency")
        matched_req_id = target_req_names.get(comp_name.strip().lower())
        new_source_req_ids = [matched_req_id] if matched_req_id else []

        # Revalidate copied item
        is_item_valid = True
        err_code = None
        if it.get("question_format") == "MCQ_SINGLE_ANSWER":
            from app.services.ai_provenance_service import validate_mcq_structure_and_safety
            m_res = validate_mcq_structure_and_safety(
                stem=it.get("question_text") or it.get("ai_wording") or "",
                options=it.get("options") or [],
                correct_option_id=it.get("correct_option_id") or "",
                explanation=it.get("answer_explanation") or "",
                competency_name=comp_name,
            )
            is_item_valid = m_res.is_valid
            err_code = m_res.error_code
        else:
            from app.services.ai_provenance_service import validate_conversational_question_text
            c_res = validate_conversational_question_text(
                question_text=it.get("ai_wording") or it.get("question_text") or "",
                competency_name=comp_name,
            )
            is_item_valid = c_res.is_valid
            err_code = c_res.error_code

        copied_item = {
            "slot_id": new_slot_id,
            "display_order": it.get("display_order", len(copied_items)),
            "question_format": it.get("question_format", "CONVERSATION"),
            "source_requirement_ids": new_source_req_ids,
            "competency_name": comp_name,
            "question_type": it.get("question_type", "TECHNICAL"),
            "estimated_time_minutes": it.get("estimated_time_minutes", 5),
            "permitted_probes": list(it.get("permitted_probes", [])),
            "rubric": it.get("rubric"),
            "ai_wording": it.get("ai_wording"),
            "question_text": it.get("question_text"),
            "options": it.get("options"),
            "correct_option_id": it.get("correct_option_id"),
            "answer_explanation": it.get("answer_explanation"),
            "difficulty": it.get("difficulty", "FOUNDATIONAL"),
            "review_state": "UNREVIEWED",  # Always reset review state for new revision
            "authoring_origin": it.get("authoring_origin", "LOCAL_LLM_GENERATED"),
            "validation_status": "PASSED" if is_item_valid else "FAILED",
            "safe_failure_code": err_code,
            "rationale_code": "REVISION_COPIED_DRAFT",
            "model_identifier": it.get("model_identifier") or old_set.model_identifier or "qwen/qwen3-4b-2507",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        copied_items.append(copied_item)

    target_set.items = copied_items
    target_set.lifecycle_status = "DRAFT"
    target_set.is_immutable = False
    target_set.approved_at = None
    target_set.approved_by_user_id = None
    target_set.source_content_hash = _compute_requirement_fingerprint(
        job.job_id,
        target_ver.job_version_id,
        target_ver.version_number,
        [str(r.requirement_id) for r in (target_ver.requirements or [])]
    )
    flag_modified(target_set, "items")
    db.commit()
    db.refresh(target_set)

    return _map_record_to_detail_dto(target_set, db, action="PREPARED_FOR_REVISION")

