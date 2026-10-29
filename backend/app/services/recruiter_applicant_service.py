"""Recruiter Applicant Service (Phase 4B.5).

Implements transactional, deny-by-default applicant listing, application detail,
review state transitions, deterministic match/gap evidence retrieval,
and approved interview release handoff.
Enforces recruiter job ownership and organization isolation.
Never computes match scores automatically on view.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import AuditEventType, AuditReasonCode, AuditResult, UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateBindingStatus,
    CandidatePracticeReleaseRecord,
    RecruiterReviewState,
)
from app.models.interview import AiInterviewQuestionSetRecord
from app.models.job import JobLifecycleStatus, JobRequisition, JobVersion
from app.models.matching import MatchResultRecord, MatchRunRecord
from app.schemas.phase3_enums import PracticeReleaseStatus
from app.schemas.recruiter_application import (
    RecruiterApplicationDetailResponse,
    RecruiterApplicationListResponse,
    RecruiterApplicationSummaryResponse,
    RecruiterInterviewReleaseEligibilityDto,
    RecruiterMatchEvidenceDto,
    RecruiterReleaseInterviewRequest,
    RecruiterReleaseInterviewResponse,
    RecruiterReviewStateUpdateRequest,
)

logger = logging.getLogger("ai_hiring.services.recruiter_applicant")

ALLOWED_REVIEW_TRANSITIONS = {
    RecruiterReviewState.RECEIVED.value: {RecruiterReviewState.IN_REVIEW.value},
    RecruiterReviewState.IN_REVIEW.value: {RecruiterReviewState.INTERVIEW_READY.value, RecruiterReviewState.RECEIVED.value},
    RecruiterReviewState.INTERVIEW_READY.value: {RecruiterReviewState.IN_REVIEW.value},
}


def _record_recruiter_audit(
    db: Session,
    actor: UserRecord,
    event_type: AuditEventType,
    result: AuditResult,
    reason_code: AuditReasonCode,
    resource_type: str,
    resource_id: str,
    safe_metadata: Optional[Dict[str, Any]] = None,
) -> None:
    from app.api.auth import _record_audit_event
    _record_audit_event(
        db=db,
        event_type=event_type,
        result=result,
        reason_code=reason_code,
        actor_user_id=actor.user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        safe_metadata=safe_metadata or {},
    )


def _check_recruiter_job_access(actor: UserRecord, job: JobRequisition) -> None:
    """Enforces recruiter job ownership and organization isolation."""
    if actor.role == UserRole.RECRUITER and job.created_by_user_id != actor.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ACCESS_DENIED", "message": "You are not authorized to access applications for this job."},
        )
    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)
    job_org = getattr(job, "organization_id", None) or getattr(job, "org_id", None)
    if actor_org is not None and job_org is not None and actor_org != job_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN_ORGANIZATION", "message": "Cross-organization access is strictly forbidden."},
        )


def _get_scoped_job(job_id: UUID, actor: UserRecord, db: Session) -> JobRequisition:
    stmt_job = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements)
        )
        .where(
            JobRequisition.job_id == job_id,
            JobRequisition.deleted_at.is_(None),
        )
    )
    job = db.execute(stmt_job).scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job requisition not found."},
        )
    _check_recruiter_job_access(actor, job)
    return job


def _get_match_evidence(job_id: UUID, profile_id: UUID, db: Session) -> Optional[RecruiterMatchEvidenceDto]:
    stmt_match = (
        select(MatchResultRecord, MatchRunRecord)
        .join(MatchRunRecord, MatchResultRecord.run_id == MatchRunRecord.run_id)
        .where(
            MatchRunRecord.job_id == job_id,
            MatchResultRecord.profile_id == profile_id,
        )
        .order_by(desc(MatchRunRecord.created_at))
    )
    res = db.execute(stmt_match).first()
    if not res:
        return None
    match_rec, run_rec = res
    raw_s = float(match_rec.raw_compatibility_score) if match_rec.raw_compatibility_score is not None else 0.0
    disp_s = float(match_rec.display_compatibility_score) if match_rec.display_compatibility_score is not None else 0.0
    return RecruiterMatchEvidenceDto(
        run_id=match_rec.run_id,
        rank=match_rec.rank,
        raw_score=raw_s,
        display_score=disp_s,
        confidence_tier=match_rec.confidence_tier or "UNSPECIFIED",
        warning_count=match_rec.warning_count,
        must_have_warning_count=match_rec.must_have_warning_count,
        is_tied=match_rec.is_tied,
        score_decomposition=match_rec.score_decomposition or {},
        computed_at=run_rec.completed_at or run_rec.created_at,
    )


from app.services.ai_provenance_service import evaluate_release_eligibility


def _evaluate_interview_eligibility(
    application: CandidateApplicationRecord,
    db: Session,
    mode: Optional[str] = None,
) -> RecruiterInterviewReleaseEligibilityDto:
    target_mode = (mode or "TEXT").upper()

    # 1. Check if already released for this target mode
    stmt_rel = (
        select(CandidatePracticeReleaseRecord)
        .where(
            CandidatePracticeReleaseRecord.candidate_id == application.candidate_id,
            CandidatePracticeReleaseRecord.job_id == application.job_id,
            CandidatePracticeReleaseRecord.job_version_id == application.job_version_id,
            CandidatePracticeReleaseRecord.interview_mode == target_mode,
            CandidatePracticeReleaseRecord.status == PracticeReleaseStatus.RELEASED.value,
        )
        .order_by(desc(CandidatePracticeReleaseRecord.released_at))
    )
    existing_release = db.execute(stmt_rel).scalars().first()

    if existing_release:
        return RecruiterInterviewReleaseEligibilityDto(
            is_eligible=False,
            reason_code="ALREADY_RELEASED",
            message=f"An interview for this job revision is already released to this candidate in {target_mode} mode.",
            interview_mode=target_mode,
            question_set_id=existing_release.ai_set_id,
            set_version=existing_release.set_version,
            question_count=0,
            existing_release_id=existing_release.release_id,
            existing_release_status=existing_release.status,
        )

    # 2. Check application status
    if application.status != CandidateApplicationStatus.SUBMITTED.value:
        return RecruiterInterviewReleaseEligibilityDto(
            is_eligible=False,
            reason_code="APPLICATION_NOT_SUBMITTED",
            message="Cannot release interview for a non-submitted or withdrawn application.",
            interview_mode=target_mode,
        )

    # 3. Check candidate active account binding
    binding = None
    if application.candidate_id:
        stmt_binding = select(CandidateAccountBindingRecord).where(
            CandidateAccountBindingRecord.candidate_id == application.candidate_id,
            CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
        )
        binding = db.execute(stmt_binding).scalar_one_or_none()
    if not binding and application.binding_id:
        stmt_binding = select(CandidateAccountBindingRecord).where(
            CandidateAccountBindingRecord.binding_id == application.binding_id,
            CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
        )
        binding = db.execute(stmt_binding).scalar_one_or_none()
    if not binding and application.user_id:
        stmt_binding = select(CandidateAccountBindingRecord).where(
            CandidateAccountBindingRecord.user_id == application.user_id,
            CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
        )
        binding = db.execute(stmt_binding).scalar_one_or_none()

    if not binding:
        return RecruiterInterviewReleaseEligibilityDto(
            is_eligible=False,
            reason_code="CANDIDATE_NOT_ACTIVATED",
            message="Candidate must activate their portal account before an interview can be released.",
            interview_mode=target_mode,
        )

    # 5. Check approved question set for exact submitted revision and target mode
    stmt_set = (
        select(AiInterviewQuestionSetRecord)
        .where(
            AiInterviewQuestionSetRecord.job_id == application.job_id,
            AiInterviewQuestionSetRecord.interview_mode == target_mode,
            AiInterviewQuestionSetRecord.lifecycle_status == "APPROVED",
        )
        .filter(
            (AiInterviewQuestionSetRecord.job_version_id == application.job_version_id)
            | (AiInterviewQuestionSetRecord.job_revision == application.job_revision)
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    ai_set = db.execute(stmt_set).scalars().first()

    if not ai_set:
        return RecruiterInterviewReleaseEligibilityDto(
            is_eligible=False,
            reason_code="NO_APPROVED_QUESTION_SET",
            message=f"No approved AI interview question set exists for submitted job revision (Rev #{application.job_revision}) in {target_mode} mode.",
            interview_mode=target_mode,
        )

    # 6. Provenance & Server Validation Check
    if application.job_version:
        prov_eval = evaluate_release_eligibility(ai_set=ai_set, target_job_version=application.job_version)
        if not prov_eval.is_eligible:
            return RecruiterInterviewReleaseEligibilityDto(
                is_eligible=False,
                reason_code=prov_eval.reason_code.value if prov_eval.reason_code else "PROVENANCE_INELIGIBLE",
                message=prov_eval.safe_message or "Question set validation failed for release.",
                interview_mode=target_mode,
                question_set_id=ai_set.ai_set_id,
                set_version=ai_set.set_version,
                question_count=0,
            )

    # Check question count and format constraints
    items = ai_set.items or []
    conv_count = sum(1 for it in items if it.get("question_format", "CONVERSATION") == "CONVERSATION")
    mcq_count = sum(1 for it in items if it.get("question_format") == "MCQ_SINGLE_ANSWER")
    total_q = conv_count + mcq_count

    if total_q == 0:
        return RecruiterInterviewReleaseEligibilityDto(
            is_eligible=False,
            reason_code="NO_VALID_QUESTIONS",
            message="The approved question set contains no questions.",
            interview_mode=target_mode,
            question_set_id=ai_set.ai_set_id,
            set_version=ai_set.set_version,
            question_count=0,
        )

    if target_mode == "AUDIO" and mcq_count > 0:
        return RecruiterInterviewReleaseEligibilityDto(
            is_eligible=False,
            reason_code="AUDIO_MCQ_NOT_ALLOWED",
            message="Audio interview question set cannot contain MCQs.",
            interview_mode=target_mode,
            question_set_id=ai_set.ai_set_id,
            set_version=ai_set.set_version,
            question_count=total_q,
        )

    return RecruiterInterviewReleaseEligibilityDto(
        is_eligible=True,
        reason_code=None,
        message=f"Ready to release approved {target_mode} interview to candidate.",
        interview_mode=target_mode,
        question_set_id=ai_set.ai_set_id,
        set_version=ai_set.set_version,
        question_count=total_q,
    )


def list_job_applications_for_recruiter(
    job_id: UUID,
    actor: UserRecord,
    db: Session,
    status_filter: Optional[str] = None,
    review_state_filter: Optional[str] = None,
    mode_filter: Optional[str] = None,
    revision: Optional[int] = None,
    page: int = 1,
    limit: int = 20,
) -> RecruiterApplicationListResponse:
    job = _get_scoped_job(job_id, actor, db)
    target_mode = (mode_filter or "TEXT").upper() if mode_filter else None

    # Count total active vs withdrawn for this job
    stmt_active_count = select(func.count()).select_from(CandidateApplicationRecord).where(
        CandidateApplicationRecord.job_id == job_id,
        CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
    )
    stmt_withdrawn_count = select(func.count()).select_from(CandidateApplicationRecord).where(
        CandidateApplicationRecord.job_id == job_id,
        CandidateApplicationRecord.status == CandidateApplicationStatus.WITHDRAWN.value,
    )
    active_count = db.execute(stmt_active_count).scalar_one()
    withdrawn_count = db.execute(stmt_withdrawn_count).scalar_one()

    # Query with filters
    stmt = (
        select(CandidateApplicationRecord)
        .options(
            selectinload(CandidateApplicationRecord.candidate),
            selectinload(CandidateApplicationRecord.profile),
            selectinload(CandidateApplicationRecord.job_version),
        )
        .where(CandidateApplicationRecord.job_id == job_id)
    )

    if status_filter and status_filter.upper() in [CandidateApplicationStatus.SUBMITTED.value, CandidateApplicationStatus.WITHDRAWN.value]:
        stmt = stmt.where(CandidateApplicationRecord.status == status_filter.upper())

    if review_state_filter and review_state_filter.upper() in [r.value for r in RecruiterReviewState]:
        stmt = stmt.where(CandidateApplicationRecord.recruiter_review_state == review_state_filter.upper())

    if revision is not None:
        stmt = stmt.where(CandidateApplicationRecord.job_revision == revision)

    # Count total filtered items
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()

    # Deterministic operational ordering: newest submission first
    stmt = stmt.order_by(desc(CandidateApplicationRecord.submitted_at))
    stmt = stmt.offset((page - 1) * limit).limit(limit)
    applications = db.execute(stmt).scalars().all()

    items: List[RecruiterApplicationSummaryResponse] = []
    for app in applications:
        cand_name = app.candidate.full_name or "Applicant" if app.candidate else "Applicant"
        cand_email = app.candidate.primary_email if app.candidate else None
        cand_phone = app.candidate.primary_phone if app.candidate else None

        # Check existing match
        match_ev = _get_match_evidence(app.job_id, app.profile_id, db)
        match_status = "Evaluated" if match_ev else "Not evaluated"
        match_score = match_ev.display_score if match_ev else None
        conf_tier = match_ev.confidence_tier if match_ev else None

        # Check existing release for target mode if specified, or default TEXT
        stmt_rel = select(CandidatePracticeReleaseRecord).where(
            CandidatePracticeReleaseRecord.candidate_id == app.candidate_id,
            CandidatePracticeReleaseRecord.job_id == app.job_id,
            CandidatePracticeReleaseRecord.job_version_id == app.job_version_id,
            CandidatePracticeReleaseRecord.status == PracticeReleaseStatus.RELEASED.value,
        )
        if target_mode:
            stmt_rel = stmt_rel.where(CandidatePracticeReleaseRecord.interview_mode == target_mode)
        stmt_rel = stmt_rel.order_by(desc(CandidatePracticeReleaseRecord.released_at))

        existing_rel = db.execute(stmt_rel).scalars().first()
        if existing_rel:
            interview_status = "RELEASED"
            release_id = existing_rel.release_id
            item_mode = existing_rel.interview_mode
        else:
            interview_status = "NOT_RELEASED"
            release_id = None
            item_mode = target_mode or "TEXT"

        items.append(
            RecruiterApplicationSummaryResponse(
                application_id=app.application_id,
                job_id=app.job_id,
                job_version_id=app.job_version_id,
                job_revision=app.job_revision,
                candidate_id=app.candidate_id,
                profile_id=app.profile_id,
                profile_version=app.profile_version,
                candidate_name=cand_name,
                candidate_email=cand_email,
                candidate_phone=cand_phone,
                current_role=None,
                status=CandidateApplicationStatus(app.status),
                recruiter_review_state=RecruiterReviewState(app.recruiter_review_state),
                submitted_at=app.submitted_at,
                withdrawn_at=app.withdrawn_at,
                match_status=match_status,
                match_score=match_score,
                confidence_tier=conf_tier,
                interview_status=interview_status,
                release_id=release_id,
                interview_mode=item_mode,
            )
        )

    _record_recruiter_audit(
        db=db,
        actor=actor,
        event_type=AuditEventType.CANDIDATE_REVIEW_STATE_UPDATED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.REVIEW_STATE_CHANGED,
        resource_type="job_applications",
        resource_id=str(job_id),
        safe_metadata={"action": "LIST_APPLICANTS", "count": len(items)},
    )
    db.commit()

    return RecruiterApplicationListResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
        active_count=active_count,
        withdrawn_count=withdrawn_count,
    )


def get_job_application_detail_for_recruiter(
    job_id: UUID,
    application_id: UUID,
    actor: UserRecord,
    db: Session,
    mode: Optional[str] = None,
) -> RecruiterApplicationDetailResponse:
    job = _get_scoped_job(job_id, actor, db)

    stmt_app = (
        select(CandidateApplicationRecord)
        .options(
            selectinload(CandidateApplicationRecord.candidate),
            selectinload(CandidateApplicationRecord.profile),
            selectinload(CandidateApplicationRecord.job_version),
        )
        .where(
            CandidateApplicationRecord.application_id == application_id,
            CandidateApplicationRecord.job_id == job_id,
        )
    )
    app = db.execute(stmt_app).scalar_one_or_none()
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "APPLICATION_NOT_FOUND", "message": "Candidate application not found in this job context."},
        )

    cand_name = app.candidate.full_name or "Applicant" if app.candidate else "Applicant"
    cand_email = app.candidate.primary_email if app.candidate else None
    cand_phone = app.candidate.primary_phone if app.candidate else None

    # Load match evidence (WITHOUT recomputing)
    match_ev = _get_match_evidence(app.job_id, app.profile_id, db)

    # Evaluate interview release eligibility
    eligibility = _evaluate_interview_eligibility(app, db, mode=mode)

    _record_recruiter_audit(
        db=db,
        actor=actor,
        event_type=AuditEventType.CANDIDATE_REVIEW_STATE_UPDATED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.REVIEW_STATE_CHANGED,
        resource_type="candidate_application",
        resource_id=str(application_id),
        safe_metadata={"action": "VIEW_APPLICATION_DETAIL", "job_id": str(job_id)},
    )
    db.commit()

    return RecruiterApplicationDetailResponse(
        application_id=app.application_id,
        job_id=app.job_id,
        job_title=app.job_version.title if app.job_version else job.title,
        job_version_id=app.job_version_id,
        job_revision=app.job_revision,
        candidate_id=app.candidate_id,
        profile_id=app.profile_id,
        profile_version=app.profile_version,
        candidate_name=cand_name,
        candidate_email=cand_email,
        candidate_phone=cand_phone,
        current_role=None,
        status=CandidateApplicationStatus(app.status),
        recruiter_review_state=RecruiterReviewState(app.recruiter_review_state),
        submitted_at=app.submitted_at,
        withdrawn_at=app.withdrawn_at,
        profile_skills=[],
        profile_headline=None,
        match_evidence=match_ev,
        interview_eligibility=eligibility,
    )


def update_application_review_state(
    job_id: UUID,
    application_id: UUID,
    payload: RecruiterReviewStateUpdateRequest,
    actor: UserRecord,
    db: Session,
) -> RecruiterApplicationDetailResponse:
    job = _get_scoped_job(job_id, actor, db)

    stmt_app = (
        select(CandidateApplicationRecord)
        .options(
            selectinload(CandidateApplicationRecord.candidate),
            selectinload(CandidateApplicationRecord.profile),
            selectinload(CandidateApplicationRecord.job_version),
        )
        .where(
            CandidateApplicationRecord.application_id == application_id,
            CandidateApplicationRecord.job_id == job_id,
        )
    )
    app = db.execute(stmt_app).scalar_one_or_none()
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "APPLICATION_NOT_FOUND", "message": "Candidate application not found in this job context."},
        )

    # Withdrawn applications cannot be transitioned to INTERVIEW_READY
    if app.status == CandidateApplicationStatus.WITHDRAWN.value:
        if payload.new_state == RecruiterReviewState.INTERVIEW_READY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "WITHDRAWN_APPLICATION", "message": "Withdrawn applications cannot be marked as INTERVIEW_READY."},
            )

    # Optimistic concurrency check if expected_state is supplied
    if payload.expected_state and app.recruiter_review_state != payload.expected_state.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "STATE_CONFLICT",
                "message": f"Application review state changed concurrently from expected '{payload.expected_state.value}' to '{app.recruiter_review_state}'.",
            },
        )

    # Validate transition
    current_s = app.recruiter_review_state
    target_s = payload.new_state.value
    if current_s != target_s:
        allowed = ALLOWED_REVIEW_TRANSITIONS.get(current_s, set())
        if target_s not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_STATE_TRANSITION",
                    "message": f"Transition from '{current_s}' to '{target_s}' is not permitted.",
                },
            )
        app.recruiter_review_state = target_s
        app.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(app)

        _record_recruiter_audit(
            db=db,
            actor=actor,
            event_type=AuditEventType.CANDIDATE_REVIEW_STATE_UPDATED,
            result=AuditResult.SUCCESS,
            reason_code=AuditReasonCode.REVIEW_STATE_CHANGED,
            resource_type="candidate_application",
            resource_id=str(application_id),
            safe_metadata={"action": "UPDATE_REVIEW_STATE", "from_state": current_s, "to_state": target_s},
        )
        db.commit()

    return get_job_application_detail_for_recruiter(job_id, application_id, actor, db)


def release_interview_for_application(
    job_id: UUID,
    application_id: UUID,
    payload: RecruiterReleaseInterviewRequest,
    actor: UserRecord,
    db: Session,
) -> RecruiterReleaseInterviewResponse:
    job = _get_scoped_job(job_id, actor, db)

    stmt_app = (
        select(CandidateApplicationRecord)
        .options(
            selectinload(CandidateApplicationRecord.candidate),
            selectinload(CandidateApplicationRecord.profile),
            selectinload(CandidateApplicationRecord.job_version),
        )
        .where(
            CandidateApplicationRecord.application_id == application_id,
            CandidateApplicationRecord.job_id == job_id,
        )
    )
    app = db.execute(stmt_app).scalar_one_or_none()
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "APPLICATION_NOT_FOUND", "message": "Candidate application not found in this job context."},
        )

    if app.status != CandidateApplicationStatus.SUBMITTED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "APPLICATION_NOT_SUBMITTED", "message": "Cannot release an interview for a non-submitted or withdrawn application."},
        )

    # Check candidate binding
    binding = None
    if app.candidate_id:
        stmt_binding = select(CandidateAccountBindingRecord).where(
            CandidateAccountBindingRecord.candidate_id == app.candidate_id,
            CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
        )
        binding = db.execute(stmt_binding).scalar_one_or_none()
    if not binding and app.binding_id:
        stmt_binding = select(CandidateAccountBindingRecord).where(
            CandidateAccountBindingRecord.binding_id == app.binding_id,
            CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
        )
        binding = db.execute(stmt_binding).scalar_one_or_none()
    if not binding and app.user_id:
        stmt_binding = select(CandidateAccountBindingRecord).where(
            CandidateAccountBindingRecord.user_id == app.user_id,
            CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
        )
        binding = db.execute(stmt_binding).scalar_one_or_none()

    if not binding:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CANDIDATE_NOT_ACTIVATED", "message": "Candidate account must be active before releasing an interview."},
        )

    # Check approved question set for exact submitted revision
    if payload.ai_set_id:
        stmt_set = select(AiInterviewQuestionSetRecord).where(
            (AiInterviewQuestionSetRecord.ai_set_id == payload.ai_set_id)
            | (AiInterviewQuestionSetRecord.template_id == payload.ai_set_id),
            AiInterviewQuestionSetRecord.job_id == app.job_id,
        ).filter(
            (AiInterviewQuestionSetRecord.job_version_id == app.job_version_id)
            | (AiInterviewQuestionSetRecord.job_revision == app.job_revision)
        )
        ai_set = db.execute(stmt_set).scalars().first()
        if not ai_set:
            # Check if job revision or job mismatches
            mismatch_check = db.execute(
                select(AiInterviewQuestionSetRecord).where(
                    (AiInterviewQuestionSetRecord.ai_set_id == payload.ai_set_id)
                    | (AiInterviewQuestionSetRecord.template_id == payload.ai_set_id)
                )
            ).scalars().first()
            if mismatch_check:
                if mismatch_check.job_id != app.job_id or (
                    mismatch_check.job_version_id != app.job_version_id
                    and mismatch_check.job_revision != app.job_revision
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={"code": "JOB_REVISION_MISMATCH", "message": "Question set does not match the application job revision."},
                    )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "QUESTION_SET_NOT_FOUND", "message": "Specified question set not found."},
            )
        if ai_set.lifecycle_status != "APPROVED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "QUESTION_SET_NOT_APPROVED", "message": "Only approved question sets can be released."},
            )
    else:
        stmt_set = (
            select(AiInterviewQuestionSetRecord)
            .where(
                AiInterviewQuestionSetRecord.job_id == app.job_id,
                AiInterviewQuestionSetRecord.lifecycle_status == "APPROVED",
            )
            .filter(
                (AiInterviewQuestionSetRecord.job_version_id == app.job_version_id)
                | (AiInterviewQuestionSetRecord.job_revision == app.job_revision)
            )
            .order_by(desc(AiInterviewQuestionSetRecord.set_version))
        )
        ai_set = db.execute(stmt_set).scalars().first()
        if not ai_set:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "NO_APPROVED_QUESTION_SET",
                    "message": f"No approved question set exists for submitted revision (Rev #{app.job_revision}).",
                },
            )

    # Derive immutable mode authoritative from persisted question set
    interview_mode = ai_set.interview_mode or "TEXT"

    # Check question set contains questions
    items = ai_set.items or []
    conv_count = sum(1 for it in items if it.get("question_format", "CONVERSATION") == "CONVERSATION")
    mcq_count = sum(1 for it in items if it.get("question_format") == "MCQ_SINGLE_ANSWER")
    if conv_count + mcq_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NO_VALID_QUESTIONS", "message": "The approved question set contains no questions."},
        )

    # Revalidate: AUDIO set rejects MCQs
    if interview_mode == "AUDIO" and mcq_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "AUDIO_MCQ_NOT_ALLOWED", "message": "Audio interview question set cannot contain MCQs."},
        )

    # Server-side validation and provenance check
    if app.job_version:
        prov_eval = evaluate_release_eligibility(ai_set=ai_set, target_job_version=app.job_version)
        if not prov_eval.is_eligible:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": prov_eval.reason_code.value if prov_eval.reason_code else "PROVENANCE_INELIGIBLE",
                    "message": prov_eval.safe_message or "Question set is not eligible for release.",
                },
            )

    now = datetime.now(timezone.utc)

    # Idempotency / duplicate check for exact release in this mode
    stmt_existing = select(CandidatePracticeReleaseRecord).where(
        CandidatePracticeReleaseRecord.candidate_id == app.candidate_id,
        CandidatePracticeReleaseRecord.job_id == app.job_id,
        CandidatePracticeReleaseRecord.job_version_id == app.job_version_id,
        CandidatePracticeReleaseRecord.interview_mode == interview_mode,
        CandidatePracticeReleaseRecord.status == PracticeReleaseStatus.RELEASED.value,
    )
    existing_release = db.execute(stmt_existing).scalars().first()

    if existing_release:
        if existing_release.ai_set_id == ai_set.ai_set_id and existing_release.set_version == ai_set.set_version:
            # Idempotent return
            _record_recruiter_audit(
                db=db,
                actor=actor,
                event_type=AuditEventType.CANDIDATE_INTERVIEW_RELEASED,
                result=AuditResult.SUCCESS,
                reason_code=AuditReasonCode.INTERVIEW_RELEASED,
                resource_type="interview_release",
                resource_id=str(existing_release.release_id),
                safe_metadata={
                    "action": "RELEASE_INTERVIEW_IDEMPOTENT_REUSE",
                    "application_id": str(application_id),
                    "interview_mode": interview_mode,
                },
            )
            db.commit()
            return RecruiterReleaseInterviewResponse(
                release_id=existing_release.release_id,
                application_id=app.application_id,
                candidate_id=app.candidate_id,
                job_id=app.job_id,
                job_title=app.job_version.title if app.job_version else job.title,
                job_revision=app.job_revision,
                interview_mode=interview_mode,
                ai_set_id=ai_set.ai_set_id,
                set_version=ai_set.set_version,
                status=existing_release.status,
                released_at=existing_release.released_at,
                message=f"Approved {interview_mode} interview has already been released to this candidate.",
            )
        else:
            # Revoke previous revision release safely for THIS MODE ONLY
            existing_release.status = PracticeReleaseStatus.REVOKED.value
            existing_release.revoked_by_user_id = actor.user_id
            existing_release.revoked_at = now

    # Look up existing compatible MatchResult if available
    stmt_match = (
        select(MatchResultRecord)
        .join(MatchRunRecord, MatchResultRecord.run_id == MatchRunRecord.run_id)
        .where(
            MatchRunRecord.job_id == app.job_id,
            MatchResultRecord.profile_id == app.profile_id,
            MatchResultRecord.profile_version == app.profile_version,
        )
        .order_by(desc(MatchRunRecord.completed_at))
    )
    match_res = db.execute(stmt_match).scalars().first()
    match_result_id = match_res.result_id if match_res else None

    # Create new release linked to this application and mode
    new_release = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=binding.organization_id,
        candidate_id=app.candidate_id,
        application_id=app.application_id,
        job_id=app.job_id,
        job_version_id=app.job_version_id,
        job_revision=app.job_revision,
        template_id=ai_set.template_id,
        ai_set_id=ai_set.ai_set_id,
        match_result_id=match_result_id,
        set_version=ai_set.set_version,
        interview_mode=interview_mode,
        status=PracticeReleaseStatus.RELEASED.value,
        released_by_user_id=actor.user_id,
        released_at=now,
    )
    db.add(new_release)

    _record_recruiter_audit(
        db=db,
        actor=actor,
        event_type=AuditEventType.CANDIDATE_INTERVIEW_RELEASED,
        result=AuditResult.SUCCESS,
        reason_code=AuditReasonCode.INTERVIEW_RELEASED,
        resource_type="interview_release",
        resource_id=str(new_release.release_id),
        safe_metadata={
            "action": "RELEASE_INTERVIEW",
            "application_id": str(application_id),
            "job_id": str(job_id),
            "revision": app.job_revision,
            "interview_mode": interview_mode,
            "ai_set_id": str(ai_set.ai_set_id),
            "match_result_id": str(match_result_id) if match_result_id else None,
        },
    )
    db.commit()
    db.refresh(new_release)

    return RecruiterReleaseInterviewResponse(
        release_id=new_release.release_id,
        application_id=app.application_id,
        candidate_id=app.candidate_id,
        job_id=app.job_id,
        job_title=app.job_version.title if app.job_version else job.title,
        job_revision=new_release.job_revision,
        interview_mode=interview_mode,
        ai_set_id=ai_set.ai_set_id,
        set_version=ai_set.set_version,
        status=new_release.status,
        released_at=new_release.released_at,
        message=f"Approved {interview_mode} interview for '{app.job_version.title if app.job_version else job.title}' (Rev #{app.job_revision}) successfully released to candidate.",
    )
