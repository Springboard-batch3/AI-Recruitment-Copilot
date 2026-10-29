import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from app.models.auth import UserRecord
    from app.models.candidate import CandidateProfileRecord
    from app.models.gap_report import GapReportItemRecord, GapReportRecord
    from app.models.job import JobRequirement, JobRequisition, JobVersion
    from app.models.matching import MatchResultRecord, MatchRunRecord
    from app.models.taxonomy import OccupationConcept, SkillConcept, TaxonomySnapshot
from app.schemas.interview_snapshot import (
    DETERMINISTIC_BUILDER_VERSION,
    QUESTION_CATALOGUE_VERSION,
    RUBRIC_CATALOGUE_VERSION,
    SCORING_SEPARATION_POLICY,
    CandidatePlanInputSnapshot,
    CandidateRequirementResultSnapshotItem,
    JobBlueprintInputSnapshot,
    JobRequirementSnapshotItem,
    PolicyVersionReference,
    canonicalize_candidate_plan_snapshot,
    canonicalize_job_blueprint_snapshot,
    compute_candidate_plan_snapshot_hash,
    compute_job_blueprint_snapshot_hash,
)
from app.schemas.requirement_gap import RequirementGapState
from app.schemas.requirement_match import MatchResultState
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_ID,
    SAFETY_POLICY_SCHEMA_VERSION,
    SAFETY_POLICY_VERSION,
    get_current_safety_policy,
)
from app.services.gap_validation import validate_and_adapt_match_result_to_gap_set

logger = logging.getLogger("ai_hiring.services.interview_snapshot")


# Domain Error Hierarchy
class InterviewSnapshotError(Exception):
    """Base exception for interview snapshot loading and validation operations."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class InterviewUnauthorizedError(InterviewSnapshotError):
    def __init__(self, message: str = "Unauthorized to access interview input data.", code: str = "INTERVIEW_SOURCE_UNAUTHORIZED"):
        super().__init__(code=code, message=message, status_code=403)


class InterviewJobNotReadyError(InterviewSnapshotError):
    def __init__(self, message: str = "Job requisition revision is not in ready or approved state."):
        super().__init__(code="INTERVIEW_JOB_NOT_READY", message=message, status_code=409)


class InterviewJobRevisionStaleError(InterviewSnapshotError):
    def __init__(self, message: str = "Requested job requisition revision is stale or invalid."):
        super().__init__(code="INTERVIEW_JOB_REVISION_STALE", message=message, status_code=409)


class InterviewProfileVersionMismatchError(InterviewSnapshotError):
    def __init__(self, message: str = "Candidate profile version mismatch."):
        super().__init__(code="INTERVIEW_PROFILE_VERSION_MISMATCH", message=message, status_code=409)


class InterviewMatchResultMismatchError(InterviewSnapshotError):
    def __init__(self, message: str = "Match result does not match the specified job revision or candidate."):
        super().__init__(code="INTERVIEW_MATCH_RESULT_MISMATCH", message=message, status_code=409)


class InterviewSourceRelationshipInvalidError(InterviewSnapshotError):
    def __init__(self, message: str = "Invalid relationship between job, candidate, or match result."):
        super().__init__(code="INTERVIEW_SOURCE_RELATIONSHIP_INVALID", message=message, status_code=422)


class InterviewRequisitionNotFoundError(InterviewSnapshotError):
    def __init__(self, message: str = "Job requisition not found."):
        super().__init__(code="INTERVIEW_REQUISITION_NOT_FOUND", message=message, status_code=404)


class InterviewCandidateNotFoundError(InterviewSnapshotError):
    def __init__(self, message: str = "Candidate profile not found."):
        super().__init__(code="INTERVIEW_CANDIDATE_NOT_FOUND", message=message, status_code=404)


# Authoritative Loaders

def load_authoritative_job_blueprint_snapshot(
    actor: UserRecord,
    job_id: UUID,
    expected_revision: Optional[int],
    db: Session,
    organization_id: Optional[UUID] = None,
) -> Tuple[JobBlueprintInputSnapshot, str]:
    """Loads authoritative, server-side verified job requirements and builds an immutable JobBlueprintInputSnapshot.

    Enforces:
    - Recruiter or Admin role requirement.
    - Recruiter ownership / assignment verification.
    - Active requisition status.
    - Exact requested revision lookup.
    - READY_FOR_MATCHING or APPROVED revision status.
    - Server-side requirement loading with O*NET skill normalization.
    - Guardrail policy reference attachment.
    - Deterministic canonicalization and SHA-256 snapshot hashing.
    """
    from app.models.auth import UserRole
    from app.models.job import JobLifecycleStatus, JobRequisition, JobVersion, JobVersionStatus
    from app.models.taxonomy import OccupationConcept, SkillConcept, TaxonomySnapshot, TaxonomySnapshotState

    if actor.role not in {UserRole.RECRUITER, UserRole.ADMIN}:
        raise InterviewUnauthorizedError("Actor must possess RECRUITER or ADMIN role.")

    stmt_req = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements),
            selectinload(JobRequisition.versions).selectinload(JobVersion.occupation),
        )
        .where(JobRequisition.job_id == job_id)
    )
    req = db.execute(stmt_req).scalar_one_or_none()

    if req is None or (actor.role == UserRole.RECRUITER and req.created_by_user_id != actor.user_id):
        raise InterviewRequisitionNotFoundError("Requisition not found.")

    if req.status != JobLifecycleStatus.ACTIVE:
        raise InterviewRequisitionNotFoundError("Requisition is not active.")

    if expected_revision is not None:
        target_version = next(
            (v for v in req.versions if v.revision == expected_revision or v.version_number == expected_revision),
            None,
        )
        if target_version is None:
            raise InterviewJobRevisionStaleError(f"Job revision {expected_revision} not found on requisition.")
    else:
        # Default to latest version
        if not req.versions:
            raise InterviewRequisitionNotFoundError("Requisition has no versions.")
        target_version = sorted(req.versions, key=lambda v: (v.version_number, v.revision))[-1]

    # Verify ready or approved state
    if target_version.version_status not in {JobVersionStatus.READY_FOR_MATCHING, JobVersionStatus.APPROVED}:
        raise InterviewJobNotReadyError(
            f"Job revision {target_version.revision} is in status '{target_version.version_status}'; "
            "must be READY_FOR_MATCHING or APPROVED for interview generation."
        )

    if not target_version.requirements:
        raise InterviewSourceRelationshipInvalidError("Job revision contains no requirements.")

    # Load active taxonomy snapshot if needed for skill/occupation resolution
    stmt_active_tax = select(TaxonomySnapshot).where(TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE)
    active_tax = db.execute(stmt_active_tax).scalar_one_or_none()

    # Build normalized requirement snapshot items
    approved_reqs: List[JobRequirementSnapshotItem] = []
    for r in target_version.requirements:
        tax_label = None
        tax_ext_id = None
        if r.taxonomy_skill_concept_id is not None and active_tax is not None:
            stmt_skill = select(SkillConcept).where(
                SkillConcept.concept_id == r.taxonomy_skill_concept_id,
                SkillConcept.snapshot_id == active_tax.snapshot_id,
            )
            skill_obj = db.execute(stmt_skill).scalar_one_or_none()
            if skill_obj is None:
                stmt_prev_skill = select(SkillConcept).where(
                    SkillConcept.concept_id == r.taxonomy_skill_concept_id,
                )
                skill_obj = db.execute(stmt_prev_skill).scalar_one_or_none()

            if skill_obj is not None:
                tax_label = skill_obj.canonical_label
                tax_ext_id = skill_obj.external_id

        approved_reqs.append(
            JobRequirementSnapshotItem(
                requirement_id=r.requirement_id,
                display_order=r.display_order,
                category=r.category,
                label=r.label.strip(),
                normalized_label=r.normalized_label.strip(),
                necessity=r.necessity,
                priority=r.priority,
                weight=r.custom_weight_override,
                must_have_rationale=r.rationale.strip() if r.rationale else None,
                min_experience_months=r.min_experience_months,
                recency_years=r.recency_years,
                accepted_evidence_types=list(r.accepted_evidence_types or []),
                accepted_equivalents=list(r.accepted_equivalents or []),
                taxonomy_skill_concept_id=r.taxonomy_skill_concept_id,
                taxonomy_canonical_label=tax_label,
                taxonomy_external_id=tax_ext_id,
            )
        )

    # Occupation resolution
    occ_title = None
    occ_soc = None
    if target_version.occupation_concept_id is not None and active_tax is not None:
        stmt_occ = select(OccupationConcept).where(
            OccupationConcept.concept_id == target_version.occupation_concept_id,
            OccupationConcept.snapshot_id == active_tax.snapshot_id,
        )
        occ_obj = db.execute(stmt_occ).scalar_one_or_none()
        if occ_obj is not None:
            occ_title = occ_obj.canonical_title
            occ_soc = occ_obj.onet_soc_code

    # Safety policy reference
    curr_policy = get_current_safety_policy()
    policy_ref = PolicyVersionReference(
        policy_id=curr_policy.policy_id,
        policy_version=curr_policy.policy_version,
        policy_hash=curr_policy.policy_hash,
        schema_version=SAFETY_POLICY_SCHEMA_VERSION,
    )

    effective_org_id = organization_id or req.created_by_user_id

    snapshot = JobBlueprintInputSnapshot(
        organization_id=effective_org_id,
        job_id=req.job_id,
        job_version_id=target_version.job_version_id,
        revision=target_version.revision,
        job_readiness_state=target_version.version_status.value if hasattr(target_version.version_status, "value") else str(target_version.version_status),
        approved_requirements=approved_reqs,
        occupation_concept_id=target_version.occupation_concept_id,
        occupation_title=occ_title,
        occupation_soc_code=occ_soc,
        policy_reference=policy_ref,
        deterministic_builder_version=DETERMINISTIC_BUILDER_VERSION,
        question_catalogue_version=QUESTION_CATALOGUE_VERSION,
        rubric_catalogue_version=RUBRIC_CATALOGUE_VERSION,
        captured_at=datetime.now(timezone.utc),
    )

    snapshot_hash = compute_job_blueprint_snapshot_hash(snapshot)
    return snapshot, snapshot_hash


def load_authoritative_candidate_plan_snapshot(
    actor: UserRecord,
    job_id: UUID,
    expected_revision: int,
    candidate_id: UUID,
    profile_version: int,
    match_result_id: UUID,
    db: Session,
    organization_id: Optional[UUID] = None,
) -> Tuple[CandidatePlanInputSnapshot, str]:
    """Loads authoritative, server-side verified job and candidate match inputs and builds an immutable CandidatePlanInputSnapshot.

    Enforces:
    - Prior authorization and loading of the JobBlueprintInputSnapshot.
    - Verification that candidate belongs to the authorized job context.
    - Verification that match result belongs to the requested job and revision.
    - Verification that candidate profile version matches the frozen match result.
    - Positive allowlisting of requirement and evidence identifiers.
    - Complete exclusion of candidate identity and unprotected resume text.
    - Deterministic canonicalization and SHA-256 snapshot hashing.
    """
    from app.models.candidate import CandidateProfileRecord
    from app.models.gap_report import GapReportRecord
    from app.models.job import JobVersion
    from app.models.matching import MatchResultRecord, MatchRunLifecycleStatus, MatchRunRecord

    # 1. Authorize and load the job blueprint snapshot
    job_snapshot, _ = load_authoritative_job_blueprint_snapshot(
        actor=actor,
        job_id=job_id,
        expected_revision=expected_revision,
        db=db,
        organization_id=organization_id,
    )

    # 2. Load the match result and parent run
    stmt_match = (
        select(MatchResultRecord)
        .options(
            selectinload(MatchResultRecord.run),
            selectinload(MatchResultRecord.profile),
        )
        .where(MatchResultRecord.result_id == match_result_id)
    )
    match_result = db.execute(stmt_match).scalar_one_or_none()
    if match_result is None:
        raise InterviewMatchResultMismatchError("Match result not found.")

    # Verify run relationship
    match_run = match_result.run
    if match_run.job_id != job_id or match_run.revision != expected_revision:
        raise InterviewMatchResultMismatchError(
            f"Match result belongs to job {match_run.job_id} revision {match_run.revision}, "
            f"expected job {job_id} revision {expected_revision}."
        )

    if match_run.lifecycle_status != MatchRunLifecycleStatus.SUCCEEDED:
        raise InterviewMatchResultMismatchError(
            f"Match run status is {match_run.lifecycle_status}; must be SUCCEEDED."
        )

    # Verify profile and candidate relationship
    profile = match_result.profile
    if profile is None:
        raise InterviewCandidateNotFoundError("Candidate profile associated with match result not found.")

    # Match result profile version check
    if match_result.profile_version != profile_version or profile.profile_version != profile_version:
        raise InterviewProfileVersionMismatchError(
            f"Match result references profile version {match_result.profile_version}, "
            f"requested profile version {profile_version}."
        )

    # Candidate ID check: candidate_id may match profile_id, identity_id, or artifact_id
    if candidate_id not in {profile.profile_id, profile.identity_id, profile.artifact_id}:
        raise InterviewSourceRelationshipInvalidError(
            "Specified candidate_id does not match the profile, identity, or artifact of the match result."
        )

    # 3. Check for existing GapReportRecord or adapt from match result
    stmt_gap = (
        select(GapReportRecord)
        .options(selectinload(GapReportRecord.items))
        .where(GapReportRecord.match_result_id == match_result_id)
    )
    gap_report = db.execute(stmt_gap).scalar_one_or_none()

    req_results: List[CandidateRequirementResultSnapshotItem] = []
    all_evidence_ids: List[str] = []
    all_req_ids: List[UUID] = []

    if gap_report is not None and gap_report.items:
        for item in sorted(gap_report.items, key=lambda it: it.display_order):
            ev_list = list(item.evidence_references or [])
            all_evidence_ids.extend(ev_list)
            all_req_ids.append(item.requirement_id)
            is_conf = item.source_match_state == MatchResultState.CONFLICTING_EVIDENCE
            is_unver = item.source_match_state == MatchResultState.INSUFFICIENT_EVIDENCE
            req_results.append(
                CandidateRequirementResultSnapshotItem(
                    requirement_id=item.requirement_id,
                    match_state=item.source_match_state,
                    match_value=item.source_match_value,
                    gap_state=item.gap_state,
                    shortfall_reason_code=item.shortfall_reason_code,
                    allowlisted_evidence_ids=ev_list,
                    is_conflict=is_conf,
                    is_unverified=is_unver,
                )
            )
    else:
        # Adapt match result to gap set dynamically
        stmt_version = (
            select(JobVersion)
            .options(selectinload(JobVersion.requirements))
            .where(JobVersion.job_version_id == job_snapshot.job_version_id)
        )
        job_version_obj = db.execute(stmt_version).scalar_one_or_none()

        gap_set = validate_and_adapt_match_result_to_gap_set(
            result_record=match_result,
            run_record=match_run,
            job_version=job_version_obj,
        )
        for g in gap_set.gaps:
            ev_list = list(g.evidence_references or [])
            all_evidence_ids.extend(ev_list)
            all_req_ids.append(g.requirement_id)
            is_conf = g.source_match_state == MatchResultState.CONFLICTING_EVIDENCE
            is_unver = g.source_match_state == MatchResultState.INSUFFICIENT_EVIDENCE
            req_results.append(
                CandidateRequirementResultSnapshotItem(
                    requirement_id=g.requirement_id,
                    match_state=g.source_match_state,
                    match_value=g.source_match_value,
                    gap_state=g.gap_state,
                    shortfall_reason_code=g.shortfall_reason_code,
                    allowlisted_evidence_ids=ev_list,
                    is_conflict=is_conf,
                    is_unverified=is_unver,
                )
            )

    unique_evidence_ids = sorted(list(dict.fromkeys(all_evidence_ids)))
    unique_req_ids = sorted(list(dict.fromkeys(all_req_ids)), key=str)

    snapshot = CandidatePlanInputSnapshot(
        job_snapshot=job_snapshot,
        candidate_id=candidate_id,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        match_run_id=match_run.run_id,
        match_result_id=match_result.result_id,
        match_result_fingerprint=match_run.input_fingerprint,
        match_score=float(match_result.raw_compatibility_score),
        confidence_tier=match_result.confidence_tier,
        requirement_results=req_results,
        allowlisted_requirement_ids=unique_req_ids,
        allowlisted_evidence_ids=unique_evidence_ids,
        scoring_separation_policy=SCORING_SEPARATION_POLICY,
        captured_at=datetime.now(timezone.utc),
    )

    snapshot_hash = compute_candidate_plan_snapshot_hash(snapshot)
    return snapshot, snapshot_hash
