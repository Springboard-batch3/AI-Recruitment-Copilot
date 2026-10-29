"""Domain service for Candidate Practice Releases, Eligibility Diagnostics, Catalog, and Requests in Phase 3F.3."""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateBindingStatus,
    CandidateInterviewSessionRecord,
    CandidateInvitationRecord,
    CandidatePracticeReleaseRecord,
    CandidatePracticeRequestRecord,
    CandidatePracticeRequestStatus,
    JobPracticeCatalogRecord,
)
from app.models.interview import AiInterviewQuestionSetRecord, JobInterviewTemplateRecord
from app.models.job import JobLifecycleStatus, JobRequisition, JobVersion, JobVersionStatus
from app.schemas.mcq_contract import (
    CandidatePracticeCatalogItem,
    CandidatePracticeCatalogResponse,
    CandidatePracticeDiscoveryResponse,
    CandidatePracticeJobSummary,
    CandidatePracticeRequestDto,
    PracticeEligibilityDiagnosticResponse,
    PracticeEligibilityReasonCode,
    PracticeEligibleItem,
    PracticeEligibleJob,
    PracticeEligibleJobsResponse,
    PracticeIneligibleItem,
    PublishPracticeCatalogPayload,
)
from app.schemas.phase3_enums import PracticeReleaseStatus


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
    actor_org = getattr(actor, "org_id", getattr(actor, "organization_id", None))
    job_org = getattr(job, "org_id", getattr(job, "organization_id", None))
    if actor_org is not None and job_org is not None and actor_org != job_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Access to requisition denied across organizations."},
        )


def list_eligible_jobs_for_candidate_practice(
    actor: UserRecord,
    candidate_id: UUID,
    db: Session,
) -> PracticeEligibilityDiagnosticResponse:
    """Evaluates all candidate-accessible jobs and returns structured eligible and ineligible diagnostic results.

    Supports domain reason codes:
    - JOB_NOT_READY
    - NO_PRACTICE_SET
    - DRAFT_SET_EXISTS
    - APPROVED_SET_ON_OLDER_REVISION
    - NO_VALID_QUESTIONS
    - ALREADY_RELEASED
    - CANDIDATE_NOT_ELIGIBLE
    - ELIGIBLE
    """
    # 1. Verify candidate identity exists
    stmt_cand = select(CandidateIdentityRecord).where(CandidateIdentityRecord.identity_id == candidate_id)
    cand = db.execute(stmt_cand).scalar_one_or_none()
    if not cand:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CANDIDATE_NOT_FOUND", "message": "Candidate identity record not found."},
        )

    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)

    # Candidate active binding check
    stmt_binding = select(CandidateAccountBindingRecord).where(
        CandidateAccountBindingRecord.candidate_id == candidate_id,
        CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
    )
    binding = db.execute(stmt_binding).scalar_one_or_none()

    # 2. Query active jobs accessible to recruiter
    stmt_jobs = (
        select(JobRequisition)
        .options(selectinload(JobRequisition.versions).selectinload(JobVersion.requirements))
        .where(
            JobRequisition.status == JobLifecycleStatus.ACTIVE,
            JobRequisition.deleted_at.is_(None),
        )
    )
    if actor.role == UserRole.RECRUITER:
        stmt_jobs = stmt_jobs.where(JobRequisition.created_by_user_id == actor.user_id)
    if actor_org is not None and hasattr(JobRequisition, "organization_id"):
        stmt_jobs = stmt_jobs.where(JobRequisition.organization_id == actor_org)

    stmt_jobs = stmt_jobs.order_by(desc(JobRequisition.updated_at))
    jobs = db.execute(stmt_jobs).scalars().all()

    # 3. Query existing releases for candidate to mark already_released
    stmt_releases = select(CandidatePracticeReleaseRecord).where(
        CandidatePracticeReleaseRecord.candidate_id == candidate_id,
        CandidatePracticeReleaseRecord.status == PracticeReleaseStatus.RELEASED.value,
    )
    existing_releases = db.execute(stmt_releases).scalars().all()
    released_by_job_version = {(r.job_id, r.job_version_id): r for r in existing_releases}
    released_job_ids = {r.job_id for r in existing_releases}

    # 4. Query active candidate practice requests
    stmt_requests = select(CandidatePracticeRequestRecord).where(
        CandidatePracticeRequestRecord.candidate_id == candidate_id,
        CandidatePracticeRequestRecord.status == CandidatePracticeRequestStatus.REQUESTED.value,
    )
    active_requests = db.execute(stmt_requests).scalars().all()
    requests_by_job_version = {r.job_version_id: r for r in active_requests}

    eligible_items: List[PracticeEligibleItem] = []
    ineligible_items: List[PracticeIneligibleItem] = []
    legacy_items: List[PracticeEligibleJob] = []

    for job in jobs:
        if not job.versions:
            continue
        latest_ver = max(job.versions, key=lambda v: v.version_number or 0)
        ver_label = f"v{latest_ver.version_number}"
        job_title = latest_ver.title or job.title or f"Job #{str(job.job_id)[:8]}"

        cand_req = requests_by_job_version.get(latest_ver.job_version_id)
        has_request = cand_req is not None
        req_id = cand_req.request_id if cand_req else None

        # Check candidate binding eligibility
        if not binding:
            ineligible_items.append(
                PracticeIneligibleItem(
                    job_id=job.job_id,
                    job_revision_id=latest_ver.job_version_id,
                    job_title=job_title,
                    job_version=ver_label,
                    reason_code=PracticeEligibilityReasonCode.CANDIDATE_NOT_ELIGIBLE,
                    message="Candidate has not activated their portal account.",
                    action="NONE",
                )
            )
            continue

        # Check job lifecycle status
        status_val = latest_ver.version_status.value if hasattr(latest_ver.version_status, "value") else str(latest_ver.version_status)
        if status_val not in ("READY_FOR_MATCHING", "APPROVED"):
            ineligible_items.append(
                PracticeIneligibleItem(
                    job_id=job.job_id,
                    job_revision_id=latest_ver.job_version_id,
                    job_title=job_title,
                    job_version=ver_label,
                    reason_code=PracticeEligibilityReasonCode.JOB_NOT_READY,
                    message=f"Job requisition is {status_val.replace('_', ' ').title()} and not yet marked Ready for matching.",
                    action="NONE",
                )
            )
            continue

        # Check if already released for this exact revision
        if (job.job_id, latest_ver.job_version_id) in released_by_job_version:
            ineligible_items.append(
                PracticeIneligibleItem(
                    job_id=job.job_id,
                    job_revision_id=latest_ver.job_version_id,
                    job_title=job_title,
                    job_version=ver_label,
                    reason_code=PracticeEligibilityReasonCode.ALREADY_RELEASED,
                    message="Practice interview is already active and released for this candidate.",
                    action="NONE",
                )
            )
            continue

        # Look for question sets for this exact revision
        stmt_exact_set = (
            select(AiInterviewQuestionSetRecord)
            .where(
                AiInterviewQuestionSetRecord.job_id == job.job_id,
                AiInterviewQuestionSetRecord.job_version_id == latest_ver.job_version_id,
            )
            .order_by(desc(AiInterviewQuestionSetRecord.set_version))
        )
        exact_set = db.execute(stmt_exact_set).scalars().first()

        if exact_set:
            if exact_set.lifecycle_status == "APPROVED":
                set_items = exact_set.items or []
                conv_count = sum(1 for it in set_items if it.get("question_format", "CONVERSATION") == "CONVERSATION")
                mcq_count = sum(1 for it in set_items if it.get("question_format") == "MCQ_SINGLE_ANSWER")
                total_q = conv_count + mcq_count

                if total_q == 0:
                    ineligible_items.append(
                        PracticeIneligibleItem(
                            job_id=job.job_id,
                            job_revision_id=latest_ver.job_version_id,
                            job_title=job_title,
                            job_version=ver_label,
                            reason_code=PracticeEligibilityReasonCode.NO_VALID_QUESTIONS,
                            message="The approved question set contains no questions.",
                            question_set_id=exact_set.ai_set_id,
                            template_id=exact_set.template_id,
                            action="OPEN_QUESTION_SET",
                        )
                    )
                else:
                    # Eligible!
                    app_at = exact_set.approved_at or exact_set.updated_at or datetime.now(timezone.utc)
                    eligible_items.append(
                        PracticeEligibleItem(
                            job_id=job.job_id,
                            job_revision_id=latest_ver.job_version_id,
                            job_title=job_title,
                            job_version=ver_label,
                            question_set_id=exact_set.ai_set_id,
                            template_id=exact_set.template_id,
                            conversational_question_count=conv_count,
                            mcq_question_count=mcq_count,
                            total_question_count=total_q,
                            status="READY_TO_RELEASE",
                            approved_at=app_at,
                            has_candidate_request=has_request,
                            candidate_request_id=req_id,
                        )
                    )
                    legacy_items.append(
                        PracticeEligibleJob(
                            job_id=job.job_id,
                            job_version_id=latest_ver.job_version_id,
                            job_title=job_title,
                            job_revision=latest_ver.version_number or 1,
                            ai_set_id=exact_set.ai_set_id,
                            set_version=exact_set.set_version,
                            approved_at=app_at,
                            conversation_count=conv_count,
                            mcq_count=mcq_count,
                            total_question_count=total_q,
                            already_released=(job.job_id in released_job_ids),
                            has_candidate_request=has_request,
                            candidate_request_id=req_id,
                        )
                    )
            else:
                # Draft set exists
                ineligible_items.append(
                    PracticeIneligibleItem(
                        job_id=job.job_id,
                        job_revision_id=latest_ver.job_version_id,
                        job_title=job_title,
                        job_version=ver_label,
                        reason_code=PracticeEligibilityReasonCode.DRAFT_SET_EXISTS,
                        message=f"A practice set exists for {ver_label} but has not been approved.",
                        question_set_id=exact_set.ai_set_id,
                        template_id=exact_set.template_id,
                        action="OPEN_QUESTION_SET",
                    )
                )
        else:
            # No set for current revision. Check if older revision had an approved set.
            stmt_old_set = (
                select(AiInterviewQuestionSetRecord)
                .where(
                    AiInterviewQuestionSetRecord.job_id == job.job_id,
                    AiInterviewQuestionSetRecord.lifecycle_status == "APPROVED",
                )
                .order_by(desc(AiInterviewQuestionSetRecord.set_version))
            )
            old_set = db.execute(stmt_old_set).scalars().first()

            if old_set:
                old_rev = old_set.job_revision or 1
                ineligible_items.append(
                    PracticeIneligibleItem(
                        job_id=job.job_id,
                        job_revision_id=latest_ver.job_version_id,
                        job_title=job_title,
                        job_version=ver_label,
                        reason_code=PracticeEligibilityReasonCode.APPROVED_SET_ON_OLDER_REVISION,
                        message=f"An approved set exists for v{old_rev}, but this job is currently {ver_label}.",
                        question_set_id=old_set.ai_set_id,
                        template_id=old_set.template_id,
                        older_revision=old_rev,
                        action="PREPARE_PRACTICE_SET",
                    )
                )
            else:
                # No set at all
                ineligible_items.append(
                    PracticeIneligibleItem(
                        job_id=job.job_id,
                        job_revision_id=latest_ver.job_version_id,
                        job_title=job_title,
                        job_version=ver_label,
                        reason_code=PracticeEligibilityReasonCode.NO_PRACTICE_SET,
                        message=f"No practice question set exists for {ver_label}.",
                        action="CREATE_PRACTICE_SET",
                    )
                )

    # Sort eligible items: candidate requests first, then title
    eligible_items.sort(key=lambda x: (not x.has_candidate_request, x.job_title))

    return PracticeEligibilityDiagnosticResponse(
        eligible=eligible_items,
        ineligible=ineligible_items,
        items=legacy_items,
    )


def release_practice_to_candidate(
    actor: UserRecord,
    candidate_id: UUID,
    job_id: UUID,
    ai_set_id: Optional[UUID] = None,
    job_version_id: Optional[UUID] = None,
    db: Session = None,
) -> Dict[str, Any]:
    """Releases an approved practice question set for a candidate with transactional request binding."""
    # 1. Candidate lookup
    stmt_cand = select(CandidateIdentityRecord).where(CandidateIdentityRecord.identity_id == candidate_id)
    cand = db.execute(stmt_cand).scalar_one_or_none()
    if not cand:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CANDIDATE_NOT_FOUND", "message": "Candidate identity record not found."},
        )

    # 2. Check Candidate Account Binding
    stmt_binding = select(CandidateAccountBindingRecord).where(
        CandidateAccountBindingRecord.candidate_id == candidate_id,
        CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
    )
    binding = db.execute(stmt_binding).scalar_one_or_none()
    if not binding:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CANDIDATE_NOT_ACTIVATED",
                "message": "Candidate must activate their portal account before practice interviews can be released.",
            },
        )

    # 3. Job lookup & access check
    stmt_job = (
        select(JobRequisition)
        .options(selectinload(JobRequisition.versions).selectinload(JobVersion.requirements))
        .where(JobRequisition.job_id == job_id)
    )
    job = db.execute(stmt_job).scalar_one_or_none()
    if not job or not job.versions or job.status != JobLifecycleStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Active job requisition not found."},
        )
    _check_recruiter_access(actor, job)

    # Cross-org verification between candidate binding and job
    job_org = getattr(job, "organization_id", getattr(job, "org_id", None))
    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)
    if job_org is not None and binding.organization_id != job_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN_ORGANIZATION", "message": "Cannot release practice across organization boundaries."},
        )
    if actor_org is not None and job_org is not None and actor_org != job_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN_ORGANIZATION", "message": "Cannot release practice across organization boundaries."},
        )

    latest_version = max(job.versions, key=lambda v: v.version_number or 0)
    target_version = latest_version
    if job_version_id:
        found_ver = next((v for v in job.versions if v.job_version_id == job_version_id), None)
        if found_ver:
            target_version = found_ver

    # 4. Load approved question set for the target job revision
    stmt_set = (
        select(AiInterviewQuestionSetRecord)
        .where(
            AiInterviewQuestionSetRecord.job_id == job.job_id,
            AiInterviewQuestionSetRecord.job_version_id == target_version.job_version_id,
            AiInterviewQuestionSetRecord.lifecycle_status == "APPROVED",
        )
        .order_by(desc(AiInterviewQuestionSetRecord.set_version))
    )
    if ai_set_id:
        stmt_set = select(AiInterviewQuestionSetRecord).where(
            AiInterviewQuestionSetRecord.ai_set_id == ai_set_id,
            AiInterviewQuestionSetRecord.lifecycle_status == "APPROVED",
        )

    ai_set = db.execute(stmt_set).scalars().first()
    if not ai_set:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "NO_APPROVED_QUESTION_SET",
                "message": f"Practice interview cannot be released until an AI question set is approved for {target_version.title or 'this job'} (v{target_version.version_number}).",
            },
        )

    # Phase 3G.1: Authoritative release eligibility verification
    from app.services.ai_provenance_service import evaluate_release_eligibility
    eligibility_result = evaluate_release_eligibility(ai_set, target_version)
    if not eligibility_result.is_eligible:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": eligibility_result.reason_code.value if eligibility_result.reason_code else "INELIGIBLE_FOR_RELEASE",
                "message": eligibility_result.safe_message or "Question set is ineligible for Phase 3G practice release.",
                "blocking_items": eligibility_result.blocking_items,
            },
        )

    # Check question set contains at least 1 valid question
    items = ai_set.items or []
    conv_count = sum(1 for it in items if it.get("question_format", "CONVERSATION") == "CONVERSATION")
    mcq_count = sum(1 for it in items if it.get("question_format") == "MCQ_SINGLE_ANSWER")
    if conv_count + mcq_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NO_VALID_QUESTIONS", "message": "The approved question set contains no questions."},
        )

    # 5. Check existing release for this candidate + target job revision
    stmt_existing = select(CandidatePracticeReleaseRecord).where(
        CandidatePracticeReleaseRecord.candidate_id == candidate_id,
        CandidatePracticeReleaseRecord.job_id == job.job_id,
        CandidatePracticeReleaseRecord.job_version_id == target_version.job_version_id,
        CandidatePracticeReleaseRecord.status == PracticeReleaseStatus.RELEASED.value,
    )
    existing_release = db.execute(stmt_existing).scalars().first()

    now = datetime.now(timezone.utc)

    # Check for active CandidatePracticeRequestRecord to link
    stmt_req = select(CandidatePracticeRequestRecord).where(
        CandidatePracticeRequestRecord.candidate_id == candidate_id,
        CandidatePracticeRequestRecord.job_version_id == target_version.job_version_id,
        CandidatePracticeRequestRecord.status == CandidatePracticeRequestStatus.REQUESTED.value,
    )
    active_req = db.execute(stmt_req).scalars().first()

    if existing_release:
        if existing_release.ai_set_id == ai_set.ai_set_id and existing_release.set_version == ai_set.set_version:
            # Already active for exact version - idempotent return
            if active_req:
                active_req.status = CandidatePracticeRequestStatus.RELEASED.value
                active_req.processed_at = now
                active_req.processed_by_recruiter_id = actor.user_id
                active_req.resulting_release_id = existing_release.release_id
                db.commit()
            return {
                "release_id": existing_release.release_id,
                "candidate_id": candidate_id,
                "job_id": job.job_id,
                "job_title": target_version.title or job.title,
                "job_revision": existing_release.job_revision,
                "set_version": existing_release.set_version,
                "conversation_count": conv_count,
                "mcq_count": mcq_count,
                "status": PracticeReleaseStatus.RELEASED.value,
                "released_at": existing_release.released_at,
                "message": "Practice interview is already active for this candidate.",
            }
        else:
            # Revoke old version release
            existing_release.status = PracticeReleaseStatus.REVOKED.value
            existing_release.revoked_by_user_id = actor.user_id
            existing_release.revoked_at = now

    # 6. Create new release record
    new_release = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=binding.organization_id,
        candidate_id=candidate_id,
        application_id=None,
        job_id=job.job_id,
        job_version_id=target_version.job_version_id,
        job_revision=target_version.version_number,
        template_id=ai_set.template_id,
        ai_set_id=ai_set.ai_set_id,
        set_version=ai_set.set_version,
        interview_mode=ai_set.interview_mode or "TEXT",
        status=PracticeReleaseStatus.RELEASED.value,
        released_by_user_id=actor.user_id,
        released_at=now,
    )
    db.add(new_release)

    # 7. Update candidate request status if present
    if active_req:
        active_req.status = CandidatePracticeRequestStatus.RELEASED.value
        active_req.processed_at = now
        active_req.processed_by_recruiter_id = actor.user_id
        active_req.resulting_release_id = new_release.release_id

    db.commit()
    db.refresh(new_release)

    return {
        "release_id": new_release.release_id,
        "candidate_id": new_release.candidate_id,
        "job_id": new_release.job_id,
        "job_title": latest_version.title or job.title,
        "job_revision": new_release.job_revision,
        "set_version": new_release.set_version,
        "conversation_count": conv_count,
        "mcq_count": mcq_count,
        "status": new_release.status,
        "released_at": new_release.released_at,
        "message": f"Practice interview for '{latest_version.title or job.title}' (Rev #{new_release.job_revision}) released to candidate.",
    }


def list_candidate_practice_releases_for_recruiter(
    actor: UserRecord,
    candidate_id: UUID,
    db: Session,
) -> List[Dict[str, Any]]:
    """Lists practice release records for a given candidate identity."""
    stmt_cand = select(CandidateIdentityRecord).where(CandidateIdentityRecord.identity_id == candidate_id)
    cand = db.execute(stmt_cand).scalar_one_or_none()
    if not cand:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CANDIDATE_NOT_FOUND", "message": "Candidate identity record not found."},
        )

    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)
    stmt = (
        select(CandidatePracticeReleaseRecord)
        .options(
            selectinload(CandidatePracticeReleaseRecord.job),
            selectinload(CandidatePracticeReleaseRecord.job_version),
            selectinload(CandidatePracticeReleaseRecord.ai_set),
        )
        .where(CandidatePracticeReleaseRecord.candidate_id == candidate_id)
        .order_by(desc(CandidatePracticeReleaseRecord.released_at))
    )
    records = db.execute(stmt).scalars().all()

    results = []
    for r in records:
        if actor_org and r.organization_id != actor_org:
            continue

        items = r.ai_set.items if r.ai_set else []
        conv_count = sum(1 for it in items if it.get("question_format", "CONVERSATION") == "CONVERSATION")
        mcq_count = sum(1 for it in items if it.get("question_format") == "MCQ_SINGLE_ANSWER")

        results.append({
            "release_id": r.release_id,
            "candidate_id": r.candidate_id,
            "job_id": r.job_id,
            "job_version_id": r.job_version_id,
            "job_title": (r.job_version.title if r.job_version else (r.job.title if r.job else "Job Requisition")),
            "job_revision": r.job_revision,
            "set_version": r.set_version,
            "status": r.status,
            "conversation_count": conv_count,
            "mcq_count": mcq_count,
            "released_at": r.released_at,
            "revoked_at": r.revoked_at,
        })
    return results


def revoke_candidate_practice_release(
    actor: UserRecord,
    release_id: UUID,
    db: Session,
) -> Dict[str, Any]:
    """Revokes an active practice release."""
    stmt = select(CandidatePracticeReleaseRecord).where(CandidatePracticeReleaseRecord.release_id == release_id)
    release = db.execute(stmt).scalar_one_or_none()
    if not release:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RELEASE_NOT_FOUND", "message": "Practice release record not found."},
        )

    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)
    if actor_org and release.organization_id != actor_org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Cannot revoke release across organizations."},
        )

    release.status = PracticeReleaseStatus.REVOKED.value
    release.revoked_by_user_id = actor.user_id
    release.revoked_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "release_id": release.release_id,
        "status": release.status,
        "revoked_at": release.revoked_at,
        "message": "Practice release revoked successfully.",
    }


# --- Candidate Practice Catalog & Request Services ---

def publish_job_revision_to_catalog(
    actor: UserRecord,
    payload: PublishPracticeCatalogPayload,
    db: Session,
) -> CandidatePracticeCatalogItem:
    """Publishes a job revision to the candidate practice catalog."""
    stmt_ver = (
        select(JobVersion)
        .options(selectinload(JobVersion.requisition), selectinload(JobVersion.requirements))
        .where(JobVersion.job_version_id == payload.job_version_id)
    )
    job_ver = db.execute(stmt_ver).scalar_one_or_none()
    if not job_ver or not job_ver.requisition:
        raise HTTPException(status_code=404, detail="Job revision not found.")

    job = job_ver.requisition
    _check_recruiter_access(actor, job)

    DEFAULT_ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None) or getattr(job, "organization_id", None)
    if not actor_org:
        stmt_inv_org = select(CandidateInvitationRecord.organization_id).where(
            CandidateInvitationRecord.created_by_user_id == actor.user_id
        ).limit(1)
        actor_org = db.execute(stmt_inv_org).scalar_one_or_none()
    if not actor_org:
        actor_org = DEFAULT_ORG
    safe_title = payload.safe_title or job_ver.title
    safe_summary = payload.safe_summary or job_ver.summary
    skill_labels = payload.skill_labels if payload.skill_labels is not None else [
        getattr(r, "label", getattr(r, "name", "")).strip()
        for r in (job_ver.requirements or [])
        if getattr(r, "category", "") in ("SKILL", "HARD_SKILL")
    ]

    stmt_cat = select(JobPracticeCatalogRecord).where(JobPracticeCatalogRecord.job_version_id == job_ver.job_version_id)
    cat_rec = db.execute(stmt_cat).scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if cat_rec:
        cat_rec.organization_id = actor_org
        cat_rec.safe_title = safe_title
        cat_rec.safe_summary = safe_summary
        cat_rec.skill_labels = skill_labels
        cat_rec.is_published = True
        cat_rec.published_at = now
        cat_rec.published_by_user_id = actor.user_id
    else:
        cat_rec = JobPracticeCatalogRecord(
            catalog_id=uuid.uuid4(),
            organization_id=actor_org,
            job_id=job.job_id,
            job_version_id=job_ver.job_version_id,
            safe_title=safe_title,
            safe_summary=safe_summary,
            skill_labels=skill_labels,
            is_published=True,
            published_at=now,
            published_by_user_id=actor.user_id,
        )
        db.add(cat_rec)

    db.commit()
    db.refresh(cat_rec)

    return CandidatePracticeCatalogItem(
        catalog_id=cat_rec.catalog_id,
        job_id=cat_rec.job_id,
        job_version_id=cat_rec.job_version_id,
        safe_title=cat_rec.safe_title,
        safe_summary=cat_rec.safe_summary,
        skill_labels=cat_rec.skill_labels or [],
        published_at=cat_rec.published_at,
        request_status="NOT_REQUESTED",
        active_request_id=None,
        release_id=None,
        decline_reason=None,
    )


def get_candidate_practice_catalog(
    candidate_user: UserRecord,
    db: Session,
) -> CandidatePracticeCatalogResponse:
    """Returns published practice catalog entries strictly for the candidate's organization,

    including candidate's own request / release state.
    """
    stmt_binding = select(CandidateAccountBindingRecord).where(
        CandidateAccountBindingRecord.user_id == candidate_user.user_id,
        CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
    )
    binding = db.execute(stmt_binding).scalar_one_or_none()
    if not binding:
        return CandidatePracticeCatalogResponse(items=[])

    stmt_cat = (
        select(JobPracticeCatalogRecord)
        .where(
            JobPracticeCatalogRecord.organization_id == binding.organization_id,
            JobPracticeCatalogRecord.is_published == True,
        )
        .order_by(desc(JobPracticeCatalogRecord.published_at))
    )
    catalog_records = db.execute(stmt_cat).scalars().all()

    # Query candidate's releases
    stmt_rel = select(CandidatePracticeReleaseRecord).where(
        CandidatePracticeReleaseRecord.candidate_id == binding.candidate_id,
        CandidatePracticeReleaseRecord.status == PracticeReleaseStatus.RELEASED.value,
    )
    releases = db.execute(stmt_rel).scalars().all()
    releases_by_version = {r.job_version_id: r for r in releases}

    # Query candidate's requests
    stmt_req = select(CandidatePracticeRequestRecord).where(
        CandidatePracticeRequestRecord.candidate_id == binding.candidate_id,
    ).order_by(desc(CandidatePracticeRequestRecord.requested_at))
    requests = db.execute(stmt_req).scalars().all()
    latest_req_by_version = {}
    for req in requests:
        if req.job_version_id not in latest_req_by_version:
            latest_req_by_version[req.job_version_id] = req

    items: List[CandidatePracticeCatalogItem] = []
    for cat in catalog_records:
        rel = releases_by_version.get(cat.job_version_id)
        req = latest_req_by_version.get(cat.job_version_id)

        req_status = "NOT_REQUESTED"
        active_req_id = None
        release_id = None
        decline_reason = None

        if rel:
            req_status = "RELEASED"
            release_id = rel.release_id
        elif req:
            req_status = req.status
            active_req_id = req.request_id
            decline_reason = req.decline_reason if req.status == "DECLINED" else None

        items.append(
            CandidatePracticeCatalogItem(
                catalog_id=cat.catalog_id,
                job_id=cat.job_id,
                job_version_id=cat.job_version_id,
                safe_title=cat.safe_title,
                safe_summary=cat.safe_summary,
                skill_labels=cat.skill_labels or [],
                published_at=cat.published_at,
                request_status=req_status,
                active_request_id=active_req_id,
                release_id=release_id,
                decline_reason=decline_reason,
            )
        )

    return CandidatePracticeCatalogResponse(items=items)


def create_candidate_practice_request(
    candidate_user: UserRecord,
    job_version_id: UUID,
    db: Session,
) -> CandidatePracticeRequestDto:
    """Submits a candidate practice request for a published job revision."""
    stmt_binding = select(CandidateAccountBindingRecord).where(
        CandidateAccountBindingRecord.user_id == candidate_user.user_id,
        CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
    )
    binding = db.execute(stmt_binding).scalar_one_or_none()
    if not binding:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NO_ACTIVE_BINDING", "message": "Candidate portal account is not active."},
        )

    # Check job version exists and matches organization
    stmt_ver = (
        select(JobVersion)
        .options(selectinload(JobVersion.requisition))
        .where(JobVersion.job_version_id == job_version_id)
    )
    job_ver = db.execute(stmt_ver).scalar_one_or_none()
    if not job_ver or not job_ver.requisition:
        raise HTTPException(status_code=404, detail="Job revision not found.")

    job = job_ver.requisition
    stmt_cat = select(JobPracticeCatalogRecord).where(
        JobPracticeCatalogRecord.job_version_id == job_version_id,
        JobPracticeCatalogRecord.is_published.is_(True),
    )
    cat_rec = db.execute(stmt_cat).scalar_one_or_none()
    catalog_org = cat_rec.organization_id if cat_rec else None

    job_org = getattr(job, "organization_id", getattr(job, "org_id", None)) or catalog_org
    if job_org is not None and binding.organization_id is not None and job_org != binding.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN_ORGANIZATION", "message": "Cannot request practice for a job outside your organization."},
        )

    # Check if already requested or released
    stmt_existing_req = select(CandidatePracticeRequestRecord).where(
        CandidatePracticeRequestRecord.candidate_id == binding.candidate_id,
        CandidatePracticeRequestRecord.job_version_id == job_version_id,
        CandidatePracticeRequestRecord.status == CandidatePracticeRequestStatus.REQUESTED.value,
    )
    existing_req = db.execute(stmt_existing_req).scalars().first()
    if existing_req:
        return CandidatePracticeRequestDto(
            request_id=existing_req.request_id,
            organization_id=existing_req.organization_id,
            candidate_id=existing_req.candidate_id,
            job_id=existing_req.job_id,
            job_version_id=existing_req.job_version_id,
            job_title=job_ver.title or job.title,
            job_revision=job_ver.version_number,
            status=existing_req.status,
            requested_at=existing_req.requested_at,
            processed_at=existing_req.processed_at,
            resulting_release_id=existing_req.resulting_release_id,
            decline_reason=existing_req.decline_reason,
        )

    # Create new request
    new_req = CandidatePracticeRequestRecord(
        request_id=uuid.uuid4(),
        organization_id=binding.organization_id,
        candidate_id=binding.candidate_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        status=CandidatePracticeRequestStatus.REQUESTED.value,
        requested_at=datetime.now(timezone.utc),
    )
    db.add(new_req)
    db.commit()
    db.refresh(new_req)

    return CandidatePracticeRequestDto(
        request_id=new_req.request_id,
        organization_id=new_req.organization_id,
        candidate_id=new_req.candidate_id,
        job_id=new_req.job_id,
        job_version_id=new_req.job_version_id,
        job_title=job_ver.title or job.title,
        job_revision=job_ver.version_number,
        status=new_req.status,
        requested_at=new_req.requested_at,
    )


def withdraw_candidate_practice_request(
    candidate_user: UserRecord,
    request_id: UUID,
    db: Session,
) -> Dict[str, Any]:
    """Allows candidate to withdraw a pending practice request."""
    stmt_binding = select(CandidateAccountBindingRecord).where(
        CandidateAccountBindingRecord.user_id == candidate_user.user_id,
        CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
    )
    binding = db.execute(stmt_binding).scalar_one_or_none()
    if not binding:
        raise HTTPException(status_code=403, detail="No active candidate binding.")

    stmt_req = select(CandidatePracticeRequestRecord).where(
        CandidatePracticeRequestRecord.request_id == request_id,
        CandidatePracticeRequestRecord.candidate_id == binding.candidate_id,
    )
    req = db.execute(stmt_req).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Practice request not found.")

    if req.status != CandidatePracticeRequestStatus.REQUESTED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STATE", "message": "Only pending requests can be withdrawn."},
        )

    req.status = CandidatePracticeRequestStatus.WITHDRAWN.value
    req.processed_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "request_id": req.request_id,
        "status": req.status,
        "message": "Practice request withdrawn successfully.",
    }


def list_candidate_practice_requests_for_candidate(
    candidate_user: UserRecord,
    db: Session,
) -> List[CandidatePracticeRequestDto]:
    """Lists requests for authenticated candidate."""
    stmt_binding = select(CandidateAccountBindingRecord).where(
        CandidateAccountBindingRecord.user_id == candidate_user.user_id,
        CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
    )
    binding = db.execute(stmt_binding).scalar_one_or_none()
    if not binding:
        return []

    stmt = (
        select(CandidatePracticeRequestRecord)
        .options(selectinload(CandidatePracticeRequestRecord.job_version))
        .where(CandidatePracticeRequestRecord.candidate_id == binding.candidate_id)
        .order_by(desc(CandidatePracticeRequestRecord.requested_at))
    )
    records = db.execute(stmt).scalars().all()

    return [
        CandidatePracticeRequestDto(
            request_id=r.request_id,
            organization_id=r.organization_id,
            candidate_id=r.candidate_id,
            job_id=r.job_id,
            job_version_id=r.job_version_id,
            job_title=r.job_version.title if r.job_version else "Job",
            job_revision=r.job_version.version_number if r.job_version else 1,
            status=r.status,
            requested_at=r.requested_at,
            processed_at=r.processed_at,
            resulting_release_id=r.resulting_release_id,
            decline_reason=r.decline_reason,
        )
        for r in records
    ]


def list_candidate_practice_requests_for_recruiter(
    actor: UserRecord,
    candidate_id: UUID,
    db: Session,
) -> List[CandidatePracticeRequestDto]:
    """Lists candidate requests for recruiter review."""
    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)
    stmt = (
        select(CandidatePracticeRequestRecord)
        .options(selectinload(CandidatePracticeRequestRecord.job_version), selectinload(CandidatePracticeRequestRecord.candidate))
        .where(CandidatePracticeRequestRecord.candidate_id == candidate_id)
        .order_by(desc(CandidatePracticeRequestRecord.requested_at))
    )
    records = db.execute(stmt).scalars().all()

    results = []
    for r in records:
        if actor_org and r.organization_id != actor_org:
            continue
        results.append(
            CandidatePracticeRequestDto(
                request_id=r.request_id,
                organization_id=r.organization_id,
                candidate_id=r.candidate_id,
                candidate_name=r.candidate.full_name if r.candidate else None,
                job_id=r.job_id,
                job_version_id=r.job_version_id,
                job_title=r.job_version.title if r.job_version else "Job",
                job_revision=r.job_version.version_number if r.job_version else 1,
                status=r.status,
                requested_at=r.requested_at,
                processed_at=r.processed_at,
                resulting_release_id=r.resulting_release_id,
                decline_reason=r.decline_reason,
            )
        )
    return results


def decline_candidate_practice_request(
    actor: UserRecord,
    request_id: UUID,
    decline_reason: Optional[str],
    db: Session,
) -> Dict[str, Any]:
    """Recruiter declines a candidate practice request with optional safe reason."""
    stmt = select(CandidatePracticeRequestRecord).where(CandidatePracticeRequestRecord.request_id == request_id)
    req = db.execute(stmt).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Practice request not found.")

    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)
    if actor_org and req.organization_id != actor_org:
        raise HTTPException(status_code=403, detail="Forbidden across organizations.")

    req.status = CandidatePracticeRequestStatus.DECLINED.value
    req.processed_at = datetime.now(timezone.utc)
    req.processed_by_recruiter_id = actor.user_id
    req.decline_reason = decline_reason
    db.commit()

    return {
        "request_id": req.request_id,
        "status": req.status,
        "message": "Practice request declined.",
    }


def release_practice_from_request(
    actor: UserRecord,
    request_id: UUID,
    db: Session,
) -> Dict[str, Any]:
    """Releases practice directly from a candidate request."""
    stmt = select(CandidatePracticeRequestRecord).where(CandidatePracticeRequestRecord.request_id == request_id)
    req = db.execute(stmt).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Practice request not found.")

    return release_practice_to_candidate(
        actor=actor,
        candidate_id=req.candidate_id,
        job_id=req.job_id,
        ai_set_id=None,
        db=db,
    )


def get_candidate_practice_discovery(
    candidate_user: UserRecord,
    db: Session,
) -> CandidatePracticeDiscoveryResponse:
    """Returns candidate-authorized practice interview jobs strictly omitting confidential data."""
    # 1. Resolve candidate account binding
    stmt_binding = select(CandidateAccountBindingRecord).where(
        CandidateAccountBindingRecord.user_id == candidate_user.user_id,
        CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
    )
    binding = db.execute(stmt_binding).scalar_one_or_none()
    if not binding:
        return CandidatePracticeDiscoveryResponse(items=[])

    # 2. Query all candidate identity IDs associated with this candidate user
    user_cand_ids = db.execute(
        select(CandidateApplicationRecord.candidate_id).where(
            (CandidateApplicationRecord.user_id == candidate_user.user_id)
            | (CandidateApplicationRecord.binding_id == binding.binding_id)
            | (CandidateApplicationRecord.candidate_id == binding.candidate_id)
        )
    ).scalars().all()
    all_cand_ids = list(set([binding.candidate_id] + [c for c in user_cand_ids if c]))

    # Query active RELEASED records for this candidate identity/user and organization
    stmt_rel = (
        select(CandidatePracticeReleaseRecord)
        .options(
            selectinload(CandidatePracticeReleaseRecord.job),
            selectinload(CandidatePracticeReleaseRecord.job_version),
            selectinload(CandidatePracticeReleaseRecord.ai_set),
        )
        .where(
            CandidatePracticeReleaseRecord.candidate_id.in_(all_cand_ids),
            CandidatePracticeReleaseRecord.organization_id == binding.organization_id,
            CandidatePracticeReleaseRecord.status == PracticeReleaseStatus.RELEASED.value,
        )
        .order_by(desc(CandidatePracticeReleaseRecord.released_at))
    )
    releases = db.execute(stmt_rel).scalars().all()

    # Query latest interview session status for each release
    release_ids = [rel.release_id for rel in releases]
    sessions_map: Dict[UUID, CandidateInterviewSessionRecord] = {}
    if release_ids:
        stmt_sess = (
            select(CandidateInterviewSessionRecord)
            .where(
                CandidateInterviewSessionRecord.release_id.in_(release_ids),
                CandidateInterviewSessionRecord.candidate_id.in_(all_cand_ids),
            )
            .order_by(desc(CandidateInterviewSessionRecord.created_at))
        )
        sessions = db.execute(stmt_sess).scalars().all()
        for s in sessions:
            if s.release_id not in sessions_map:
                sessions_map[s.release_id] = s

    items: List[CandidatePracticeJobSummary] = []
    for rel in releases:
        job = rel.job
        version = rel.job_version
        ai_set = rel.ai_set

        job_title = (version.title if version else (job.title if job else "Job Requisition"))
        dept = getattr(job, "department", None)
        loc = getattr(job, "location", None)
        summary = getattr(version, "summary", None) or getattr(job, "description", None)
        if summary and len(summary) > 300:
            summary = summary[:297] + "..."

        set_items = ai_set.items if ai_set else []
        conv_count = sum(1 for it in set_items if it.get("question_format", "CONVERSATION") == "CONVERSATION")
        mcq_count = sum(1 for it in set_items if it.get("question_format") == "MCQ_SINGLE_ANSWER")

        sess = sessions_map.get(rel.release_id)
        sess_status = sess.status if sess else None
        is_sub = (sess_status == "SUBMITTED")
        status_lbl = "Interview Submitted" if is_sub else ("In Progress" if sess_status == "IN_PROGRESS" else "Practice content ready")

        items.append(
            CandidatePracticeJobSummary(
                release_id=rel.release_id,
                job_id=rel.job_id,
                job_version_id=rel.job_version_id,
                job_title=job_title,
                job_revision=rel.job_revision or (version.version_number if version else 1),
                department=dept,
                location=loc,
                summary=summary,
                conversation_question_count=conv_count,
                conversation_count=conv_count,
                mcq_count=mcq_count,
                total_question_count=conv_count + mcq_count,
                released_at=rel.released_at,
                interview_mode=rel.interview_mode or (ai_set.interview_mode if ai_set else "TEXT") or "TEXT",
                status_label=status_lbl,
                session_status=sess_status,
                is_submitted=is_sub,
            )
        )

    return CandidatePracticeDiscoveryResponse(items=items)

