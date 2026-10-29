import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.models.auth import UserRecord, UserRole
from app.models.candidate import CandidateProfileRecord
from app.models.candidate_access import (
    CandidateApplicationRecord,
    CandidateApplicationStatus,
)
from app.models.job import (
    JobLifecycleStatus,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
)
from app.models.matching import (
    MatchAuditEventRecord,
    MatchAuditEventType,
    MatchResultRecord,
    MatchRunLifecycleStatus,
    MatchRunRecord,
)
from app.schemas.match_run import (
    CandidateProfileSelection,
)
from app.schemas.matching import (
    MATCH_INPUT_SCHEMA_VERSION,
    MATCHER_ALGORITHM_VERSION,
    verify_no_prohibited_keys,
)
from app.schemas.scoring import (
    CompatibilityScoreResult,
    SCORING_POLICY_VERSION,
)
from app.services.deterministic_matching import (
    MATCHER_RULES_VERSION,
    match_requisition_requirements,
)
from app.services.matching_preflight import (
    RequisitionNotFoundError,
    RequisitionNotReadyError,
    prepare_match_input_snapshot,
)
from app.services.scoring import calculate_compatibility_score

logger = logging.getLogger("ai_hiring.services.match_orchestration")


# Domain Exceptions
class MatchOrchestrationError(Exception):
    """Base exception for match orchestration errors."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class DuplicateCandidateError(MatchOrchestrationError):
    def __init__(self, message: str = "Duplicate candidate profile in match run request."):
        super().__init__(code="DUPLICATE_CANDIDATE_INPUT", message=message, status_code=422)


class CandidateProfileNotFoundError(MatchOrchestrationError):
    def __init__(self, message: str = "Candidate profile not found or inaccessible."):
        super().__init__(code="CANDIDATE_PROFILE_NOT_FOUND", message=message, status_code=404)


class IdempotencyConflictError(MatchOrchestrationError):
    def __init__(self, message: str = "Idempotency key already used with different match run parameters."):
        super().__init__(code="IDEMPOTENCY_CONFLICT", message=message, status_code=409)


class MatchRunNotFoundError(MatchOrchestrationError):
    def __init__(self, message: str = "Match run not found."):
        super().__init__(code="MATCH_RUN_NOT_FOUND", message=message, status_code=404)


class MatchResultNotFoundError(MatchOrchestrationError):
    def __init__(self, message: str = "Match result not found."):
        super().__init__(code="MATCH_RESULT_NOT_FOUND", message=message, status_code=404)


class InvalidEvaluationDateError(MatchOrchestrationError):
    def __init__(self, message: str = "Evaluation date cannot be in the future or invalid."):
        super().__init__(code="INVALID_EVALUATION_DATE", message=message, status_code=422)


class UnauthorizedMatchAccessError(MatchOrchestrationError):
    def __init__(self, message: str = "Unauthorized to perform or view this match operation."):
        super().__init__(code="UNAUTHORIZED_MATCH_ACCESS", message=message, status_code=403)


# Fingerprinting
def compute_candidate_input_fingerprint(candidates: List[Tuple[uuid.UUID, int]]) -> str:
    """Computes deterministic SHA-256 hash of the canonically sorted candidate list.

    Canonical format: sorted by (str(profile_id), profile_version)
    Excludes all candidate names, emails, resume text, or identity characteristics.
    """
    sorted_cands = sorted(candidates, key=lambda c: (str(c[0]), c[1]))
    canonical_list = [
        {"profile_id": str(p_id), "profile_version": p_ver}
        for p_id, p_ver in sorted_cands
    ]
    serialized = json.dumps(canonical_list, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()


def compute_canonical_match_fingerprint(
    requisition_id: uuid.UUID,
    job_version_id: uuid.UUID,
    revision: int,
    version_number: int,
    taxonomy_version: Optional[str],
    occupation_concept_id: Optional[uuid.UUID],
    requirements_data: List[Dict[str, Any]],
    candidates: List[Tuple[uuid.UUID, int]],
    evaluation_date: datetime,
) -> str:
    """Computes authoritative deterministic SHA-256 fingerprint representing all canonical inputs.

    Guarantees:
    - Independent of dictionary insertion order (sort_keys=True)
    - Identity fields and raw document text strictly excluded
    - Display rounding strictly excluded
    - Stable ISO 8601 evaluation date
    """
    # Sort requirements deterministically by requirement_id
    sorted_reqs = sorted(requirements_data, key=lambda r: str(r["requirement_id"]))

    # Sort candidates deterministically by (profile_id, profile_version)
    sorted_cands = sorted(candidates, key=lambda c: (str(c[0]), c[1]))

    # Ensure evaluation_date is always normalized to UTC ISO string
    if evaluation_date.tzinfo is None:
        eval_dt = evaluation_date.replace(tzinfo=timezone.utc)
    else:
        eval_dt = evaluation_date.astimezone(timezone.utc)

    canonical_obj = {
        "match_input_schema_version": MATCH_INPUT_SCHEMA_VERSION,
        "matcher_algorithm_version": MATCHER_ALGORITHM_VERSION,
        "matching_rule_version": MATCHER_RULES_VERSION,
        "scoring_policy_version": SCORING_POLICY_VERSION,
        "requisition": {
            "job_id": str(requisition_id),
            "job_version_id": str(job_version_id),
            "revision": revision,
            "version_number": version_number,
            "taxonomy_version": taxonomy_version,
            "occupation_concept_id": str(occupation_concept_id) if occupation_concept_id else None,
            "requirements": sorted_reqs,
        },
        "candidates": [
            {"profile_id": str(p_id), "profile_version": p_ver}
            for p_id, p_ver in sorted_cands
        ],
        "evaluation_date": eval_dt.isoformat(),
    }

    # Verify no prohibited identity or operational keys enter the fingerprint
    verify_no_prohibited_keys(canonical_obj)

    serialized = json.dumps(canonical_obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()


# Deterministic Dense Ranking
class RankedCandidateResult:
    def __init__(
        self,
        profile_id: uuid.UUID,
        profile_version: int,
        score_result: CompatibilityScoreResult,
        raw_compatibility_score: Decimal,
        display_compatibility_score: Decimal,
        rank: int,
        is_tied: bool,
        tie_group_id: Optional[str],
    ):
        self.profile_id = profile_id
        self.profile_version = profile_version
        self.score_result = score_result
        self.raw_compatibility_score = raw_compatibility_score
        self.display_compatibility_score = display_compatibility_score
        self.rank = rank
        self.is_tied = is_tied
        self.tie_group_id = tie_group_id


def compute_deterministic_ranking(
    results: List[Tuple[uuid.UUID, int, CompatibilityScoreResult]],
) -> List[RankedCandidateResult]:
    """Applies strict deterministic dense ranking to compatibility results.

    Rules:
    - Primary ordering: raw_compatibility_score descending.
    - Equal raw scores receive the same dense rank (e.g. 92.5 -> rank 1, 92.5 -> rank 1, 81.25 -> rank 2).
    - Display rounding does not create or break ties.
    - Confidence diagnostics and warnings do not alter compatibility ties.
    - Identity fields never enter ranking.
    - Tied items receive is_tied = True and a tie_group_id.
    - Tied items are sorted stably by (str(profile_id), profile_version) ascending for reproducible ordering.
    """
    if not results:
        return []

    # Map to working items with Decimal precision for raw score comparison
    parsed_items = []
    for profile_id, profile_version, score_res in results:
        raw_dec = Decimal(str(score_res.raw_compatibility_score)).quantize(Decimal("0.000001"))
        disp_dec = Decimal(str(score_res.display_compatibility_score)).quantize(Decimal("0.01"))
        parsed_items.append({
            "profile_id": profile_id,
            "profile_version": profile_version,
            "score_result": score_res,
            "raw_score": raw_dec,
            "display_score": disp_dec,
        })

    # Primary sort: raw_score descending, secondary sort: (profile_id, profile_version) ascending
    parsed_items.sort(key=lambda x: (-x["raw_score"], str(x["profile_id"]), x["profile_version"]))

    # Assign dense rank
    current_rank = 0
    prev_raw_score: Optional[Decimal] = None
    for item in parsed_items:
        if prev_raw_score is None or item["raw_score"] != prev_raw_score:
            current_rank += 1
            prev_raw_score = item["raw_score"]
        item["rank"] = current_rank

    # Count rank frequencies for tie detection
    rank_counts: Dict[int, int] = {}
    for item in parsed_items:
        r = item["rank"]
        rank_counts[r] = rank_counts.get(r, 0) + 1

    ranked_results: List[RankedCandidateResult] = []
    for item in parsed_items:
        r = item["rank"]
        tied = rank_counts[r] > 1
        tie_group = f"tie_rank_{r}" if tied else None

        ranked_results.append(
            RankedCandidateResult(
                profile_id=item["profile_id"],
                profile_version=item["profile_version"],
                score_result=item["score_result"],
                raw_compatibility_score=item["raw_score"],
                display_compatibility_score=item["display_score"],
                rank=r,
                is_tied=tied,
                tie_group_id=tie_group,
            )
        )

    return ranked_results


# Audit Event Logger
def record_match_audit_event(
    db: Session,
    event_type: MatchAuditEventType,
    actor_user_id: Optional[uuid.UUID],
    job_id: uuid.UUID,
    run_id: Optional[uuid.UUID] = None,
    job_version_id: Optional[uuid.UUID] = None,
    revision: Optional[int] = None,
    candidate_count: Optional[int] = None,
    matcher_algorithm_version: Optional[str] = None,
    scoring_policy_version: Optional[str] = None,
    input_fingerprint: Optional[str] = None,
    safe_metadata: Optional[Dict[str, Any]] = None,
) -> MatchAuditEventRecord:
    """Safely logs a consequential match event. Never records identity or resume text."""
    meta = dict(safe_metadata or {})
    verify_no_prohibited_keys(meta)

    audit_rec = MatchAuditEventRecord(
        audit_id=uuid.uuid4(),
        run_id=run_id,
        actor_user_id=actor_user_id,
        event_type=event_type.value,
        job_id=job_id,
        job_version_id=job_version_id,
        revision=revision,
        candidate_count=candidate_count,
        matcher_algorithm_version=matcher_algorithm_version,
        scoring_policy_version=scoring_policy_version,
        input_fingerprint=input_fingerprint,
        safe_metadata=meta,
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(audit_rec)
    return audit_rec


# Match-Run Orchestration
def orchestrate_match_run(
    actor: UserRecord,
    job_id: uuid.UUID,
    expected_revision: int,
    candidates: List[CandidateProfileSelection],
    evaluation_date: Optional[datetime],
    idempotency_key: str,
    db: Session,
) -> Tuple[MatchRunRecord, List[MatchResultRecord], bool]:
    """Executes atomic, transactional match-run persistence with strict recruiter ownership,

    idempotent concurrency control, and deterministic dense ranking.

    Returns: (MatchRunRecord, List[MatchResultRecord], reused_boolean)
    """
    # 1. Enforce Recruiter or Admin role
    if actor.role not in {UserRole.RECRUITER, UserRole.ADMIN}:
        raise UnauthorizedMatchAccessError("Actor must possess RECRUITER or ADMIN role.")

    clean_idempotency_key = (idempotency_key or "").strip()
    if not clean_idempotency_key:
        raise MatchOrchestrationError(
            code="MISSING_IDEMPOTENCY_KEY",
            message="A valid idempotency key is required.",
            status_code=400,
        )
    if len(clean_idempotency_key) > 255:
        raise MatchOrchestrationError(
            code="INVALID_IDEMPOTENCY_KEY",
            message="Idempotency key exceeds maximum length of 255 characters.",
            status_code=400,
        )

    # 2. Check candidate inputs: must not be empty and must not contain duplicates
    if not candidates:
        raise MatchOrchestrationError(
            code="EMPTY_CANDIDATE_SELECTION",
            message="At least one candidate profile must be selected.",
            status_code=422,
        )

    cand_keys: List[Tuple[uuid.UUID, int]] = []
    seen_cands: Set[Tuple[uuid.UUID, int]] = set()
    for cand in candidates:
        key = (cand.profile_id, cand.profile_version)
        if key in seen_cands:
            raise DuplicateCandidateError(
                f"Duplicate candidate profile ({cand.profile_id}, v{cand.profile_version}) in selection."
            )
        seen_cands.add(key)
        cand_keys.append(key)

    # 3. Load requisition and target version; enforce ownership (conceal with 404 if unauthorized)
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
        raise RequisitionNotFoundError("Requisition not found.")

    if req.status != JobLifecycleStatus.ACTIVE:
        raise RequisitionNotFoundError("Requisition is not active.")

    target_version = next((v for v in req.versions if v.revision == expected_revision), None)
    if target_version is None:
        raise RequisitionNotFoundError(f"Requisition revision {expected_revision} not found.")

    # 4. Enforce READY_FOR_MATCHING status
    if target_version.version_status != JobVersionStatus.READY_FOR_MATCHING:
        raise RequisitionNotReadyError("Requisition revision is not locked as READY_FOR_MATCHING.")

    if not target_version.requirements:
        raise MatchOrchestrationError(
            code="EMPTY_REQUIREMENT_COLLECTION",
            message="Requisition has no structured requirements.",
            status_code=422,
        )

    # 5. Verify every candidate has an eligible, SUBMITTED application for this exact job and revision
    # Support both direct profile_id and artifact_id lookup for robustness
    normalized_cand_keys: List[Tuple[uuid.UUID, int]] = []
    seen_normalized: Set[Tuple[uuid.UUID, int]] = set()

    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)
    job_org = getattr(req, "organization_id", None) or getattr(req, "org_id", None)

    for cand_id, profile_version in cand_keys:
        stmt_cand = select(CandidateProfileRecord).where(
            or_(
                CandidateProfileRecord.profile_id == cand_id,
                CandidateProfileRecord.artifact_id == cand_id,
            ),
            CandidateProfileRecord.profile_version == profile_version,
        )
        cand_rec = db.execute(stmt_cand).scalar_one_or_none()
        if cand_rec is None:
            raise CandidateProfileNotFoundError(
                f"Candidate profile ({cand_id}, v{profile_version}) not found."
            )
        norm_key = (cand_rec.profile_id, profile_version)
        if norm_key in seen_normalized:
            raise DuplicateCandidateError(
                f"Duplicate candidate profile ({cand_rec.profile_id}, v{profile_version}) in selection."
            )
        seen_normalized.add(norm_key)

        # Enforce Authoritative Application Eligibility Rule:
        # 1. Candidate must have a CandidateApplicationRecord for this job and revision
        # 2. Application status must be SUBMITTED
        # 3. Application organization must match recruiter/job organization
        # 4. Application profile_id and profile_version must match the selected profile version
        stmt_app = select(CandidateApplicationRecord).where(
            CandidateApplicationRecord.job_id == req.job_id,
            CandidateApplicationRecord.job_version_id == target_version.job_version_id,
            CandidateApplicationRecord.job_revision == target_version.revision,
            CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
            or_(
                CandidateApplicationRecord.profile_id == cand_rec.profile_id,
                CandidateApplicationRecord.artifact_id == cand_rec.artifact_id,
                CandidateApplicationRecord.candidate_id == cand_rec.identity_id,
            ),
        )
        apps = db.execute(stmt_app).scalars().all()
        if not apps:
            # Check if an application exists in another status or for another job
            any_app = db.execute(
                select(CandidateApplicationRecord).where(
                    CandidateApplicationRecord.job_id == req.job_id,
                    or_(
                        CandidateApplicationRecord.profile_id == cand_rec.profile_id,
                        CandidateApplicationRecord.artifact_id == cand_rec.artifact_id,
                        CandidateApplicationRecord.candidate_id == cand_rec.identity_id,
                    ),
                )
            ).scalars().first()
            if any_app:
                if any_app.status != CandidateApplicationStatus.SUBMITTED.value:
                    raise MatchOrchestrationError(
                        code="APPLICATION_NOT_SUBMITTED",
                        message=f"Candidate application is not submitted (status: {any_app.status}).",
                        status_code=400,
                    )
                if any_app.job_version_id != target_version.job_version_id or any_app.job_revision != target_version.revision:
                    raise MatchOrchestrationError(
                        code="APPLICATION_JOB_REVISION_MISMATCH",
                        message=f"Candidate application belongs to revision {any_app.job_revision}, not requested revision {target_version.revision}.",
                        status_code=400,
                    )
            raise MatchOrchestrationError(
                code="APPLICATION_REQUIRED_FOR_JOB",
                message="Candidate has no submitted application for this job revision.",
                status_code=400,
            )

        matching_app = None
        for app in apps:
            if actor_org is not None and app.organization_id != actor_org:
                continue
            if job_org is not None and app.organization_id != job_org:
                continue
            if app.profile_version is not None and app.profile_version != profile_version:
                continue
            matching_app = app
            break

        if not matching_app:
            raise MatchOrchestrationError(
                code="APPLICATION_PROFILE_MISMATCH",
                message="Candidate application profile version or organization mismatch.",
                status_code=400,
            )

        normalized_cand_keys.append(norm_key)

    cand_keys = normalized_cand_keys

    # 6. Idempotency pre-lookup
    stmt_existing = (
        select(MatchRunRecord)
        .options(selectinload(MatchRunRecord.results))
        .where(
            MatchRunRecord.recruiter_id == actor.user_id,
            MatchRunRecord.idempotency_key == clean_idempotency_key,
        )
    )
    existing_run = db.execute(stmt_existing).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if evaluation_date is None:
        if existing_run is not None:
            # Re-use existing_run evaluation_date verbatim to ensure fingerprint consistency on retry
            eval_date = existing_run.evaluation_date
        else:
            eval_date = now
    else:
        if evaluation_date.tzinfo is None:
            eval_date = evaluation_date.replace(tzinfo=timezone.utc)
        else:
            eval_date = evaluation_date
        # Disallow evaluation dates in the distant future (> 24 hours ahead)
        if (eval_date - now).total_seconds() > 86400:
            raise InvalidEvaluationDateError("Evaluation date cannot be in the future.")

    # 8. Build requirement representation for canonical fingerprint
    requirements_data: List[Dict[str, Any]] = []
    for r in target_version.requirements:
        requirements_data.append({
            "requirement_id": str(r.requirement_id),
            "display_order": r.display_order,
            "category": r.category.value,
            "necessity": r.necessity.value,
            "priority": r.priority.value,
            "approved_weight": float(r.custom_weight_override) if r.custom_weight_override is not None else None,
            "min_experience_months": r.min_experience_months,
            "recency_years": r.recency_years,
            "accepted_evidence_types": sorted([et.value if hasattr(et, "value") else str(et) for et in (r.accepted_evidence_types or [])]),
            "accepted_equivalents": sorted(list(r.accepted_equivalents or [])),
            "taxonomy_skill_concept_id": str(r.taxonomy_skill_concept_id) if r.taxonomy_skill_concept_id else None,
        })

    candidate_fingerprint = compute_candidate_input_fingerprint(cand_keys)
    canonical_fingerprint = compute_canonical_match_fingerprint(
        requisition_id=req.job_id,
        job_version_id=target_version.job_version_id,
        revision=target_version.revision,
        version_number=target_version.version_number,
        taxonomy_version=target_version.taxonomy_version,
        occupation_concept_id=target_version.occupation_concept_id,
        requirements_data=requirements_data,
        candidates=cand_keys,
        evaluation_date=eval_date,
    )

    # 9. Idempotency evaluation
    if existing_run is not None:
        if existing_run.input_fingerprint == canonical_fingerprint:
            # Reused run: log audit event and return
            record_match_audit_event(
                db=db,
                event_type=MatchAuditEventType.MATCH_RUN_REUSED,
                actor_user_id=actor.user_id,
                job_id=req.job_id,
                run_id=existing_run.run_id,
                job_version_id=existing_run.job_version_id,
                revision=existing_run.revision,
                candidate_count=existing_run.candidate_count,
                matcher_algorithm_version=existing_run.matcher_algorithm_version,
                scoring_policy_version=existing_run.scoring_policy_version,
                input_fingerprint=existing_run.input_fingerprint,
                safe_metadata={"reused": True},
            )
            db.commit()
            return existing_run, existing_run.results, True
        else:
            raise IdempotencyConflictError(
                "Idempotency key already used with different requisition, candidates, or parameters."
            )

    # 9. Perform Deterministic Matching & Scoring for each candidate
    candidate_scores: List[Tuple[uuid.UUID, int, CompatibilityScoreResult]] = []
    try:
        for profile_id, profile_version in cand_keys:
            # 9.1 Phase 2C.1 loader: builds frozen pseudonymous snapshot
            snapshot = prepare_match_input_snapshot(
                actor=actor,
                job_id=req.job_id,
                expected_revision=expected_revision,
                profile_version=profile_version,
                profile_id=profile_id,
                db=db,
            )

            # 9.2 Phase 2C.2 matcher: deterministic requirement matching
            match_result_set = match_requisition_requirements(
                snapshot=snapshot,
                db=db,
                evaluation_date=eval_date,
            )

            # 9.3 Phase 2C.3 scorer: component scoring, confidence, warnings
            score_result = calculate_compatibility_score(
                result_set=match_result_set,
                snapshot=snapshot,
            )

            candidate_scores.append((profile_id, profile_version, score_result))

        # 10. Compute deterministic dense ranking
        ranked_candidates = compute_deterministic_ranking(candidate_scores)

        # 11. Transactional persistence
        run_id = uuid.uuid4()
        run_record = MatchRunRecord(
            run_id=run_id,
            recruiter_id=actor.user_id,
            job_id=req.job_id,
            job_version_id=target_version.job_version_id,
            revision=target_version.revision,
            evaluation_date=eval_date,
            match_input_schema_version=MATCH_INPUT_SCHEMA_VERSION,
            matcher_algorithm_version=MATCHER_ALGORITHM_VERSION,
            matching_rule_version=MATCHER_RULES_VERSION,
            scoring_policy_version=SCORING_POLICY_VERSION,
            candidate_input_fingerprint=candidate_fingerprint,
            input_fingerprint=canonical_fingerprint,
            idempotency_key=clean_idempotency_key,
            lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
            error_code=None,
            error_message=None,
            candidate_count=len(cand_keys),
            created_at=now,
            started_at=now,
            completed_at=datetime.now(timezone.utc),
        )
        db.add(run_record)

        result_records: List[MatchResultRecord] = []
        for rc in ranked_candidates:
            # Build immutable score decomposition payload
            decomp = rc.score_result.model_dump(mode="json")
            verify_no_prohibited_keys(decomp)

            res_record = MatchResultRecord(
                result_id=uuid.uuid4(),
                run_id=run_id,
                profile_id=rc.profile_id,
                profile_version=rc.profile_version,
                raw_compatibility_score=rc.raw_compatibility_score,
                display_compatibility_score=rc.display_compatibility_score,
                confidence_tier=rc.score_result.confidence_diagnostics.confidence_tier.value,
                warning_count=len(rc.score_result.must_have_warnings) + len(rc.score_result.general_warnings),
                must_have_warning_count=len(rc.score_result.must_have_warnings),
                rank=rc.rank,
                is_tied=rc.is_tied,
                tie_group_id=rc.tie_group_id,
                score_decomposition=decomp,
                created_at=now,
            )
            db.add(res_record)
            result_records.append(res_record)

        # Audit events
        record_match_audit_event(
            db=db,
            event_type=MatchAuditEventType.MATCH_RUN_CREATED,
            actor_user_id=actor.user_id,
            job_id=req.job_id,
            run_id=run_id,
            job_version_id=target_version.job_version_id,
            revision=target_version.revision,
            candidate_count=len(cand_keys),
            matcher_algorithm_version=MATCHER_ALGORITHM_VERSION,
            scoring_policy_version=SCORING_POLICY_VERSION,
            input_fingerprint=canonical_fingerprint,
        )
        record_match_audit_event(
            db=db,
            event_type=MatchAuditEventType.MATCH_RUN_SUCCEEDED,
            actor_user_id=actor.user_id,
            job_id=req.job_id,
            run_id=run_id,
            job_version_id=target_version.job_version_id,
            revision=target_version.revision,
            candidate_count=len(cand_keys),
            matcher_algorithm_version=MATCHER_ALGORITHM_VERSION,
            scoring_policy_version=SCORING_POLICY_VERSION,
            input_fingerprint=canonical_fingerprint,
        )

        db.commit()
        db.refresh(run_record)
        return run_record, result_records, False

    except IntegrityError as ie:
        db.rollback()
        # Handle concurrent submission with the same idempotency key
        existing_concurrent = db.execute(stmt_existing).scalar_one_or_none()
        if existing_concurrent is not None:
            if existing_concurrent.input_fingerprint == canonical_fingerprint:
                return existing_concurrent, existing_concurrent.results, True
            raise IdempotencyConflictError(
                "Idempotency key already used with different requisition or parameters."
            )
        logger.error("Database integrity error during match run", extra={"job_id": str(job_id)})
        raise MatchOrchestrationError(
            code="MATCH_RUN_INTEGRITY_ERROR",
            message="Database constraint violated during match run.",
            status_code=422,
        )
    except MatchOrchestrationError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error(
            "Match run execution failed: %s", exc,
            exc_info=True,
            extra={"job_id": str(job_id), "error_type": type(exc).__name__},
        )
        # Record safe diagnostic failure audit event
        try:
            record_match_audit_event(
                db=db,
                event_type=MatchAuditEventType.MATCH_RUN_FAILED,
                actor_user_id=actor.user_id,
                job_id=req.job_id,
                job_version_id=target_version.job_version_id if target_version else None,
                revision=target_version.revision if target_version else None,
                candidate_count=len(cand_keys),
                matcher_algorithm_version=MATCHER_ALGORITHM_VERSION,
                scoring_policy_version=SCORING_POLICY_VERSION,
                input_fingerprint=canonical_fingerprint,
                safe_metadata={"error_code": type(exc).__name__},
            )
            db.commit()
        except Exception:
            db.rollback()

        raise MatchOrchestrationError(
            code="MATCH_RUN_FAILED",
            message="Match run execution failed due to an internal processing error.",
            status_code=500,
        )


# Query operations
def get_match_run_for_actor(run_id: uuid.UUID, actor: UserRecord, db: Session) -> MatchRunRecord:
    """Retrieves match run metadata, enforcing recruiter ownership (404 concealment)."""
    stmt = select(MatchRunRecord).where(MatchRunRecord.run_id == run_id)
    run_rec = db.execute(stmt).scalar_one_or_none()

    if run_rec is None or (actor.role == UserRole.RECRUITER and run_rec.recruiter_id != actor.user_id):
        raise MatchRunNotFoundError("Match run not found.")

    return run_rec


def list_match_results_for_actor(
    run_id: uuid.UUID,
    actor: UserRecord,
    limit: int,
    offset: int,
    db: Session,
) -> Tuple[List[MatchResultRecord], int, MatchRunRecord]:
    """Retrieves paginated, ranked candidate results for a match run.

    Enforces ownership and records MATCH_RESULTS_VIEWED audit event.
    """
    run_rec = get_match_run_for_actor(run_id=run_id, actor=actor, db=db)

    # Count total
    count_stmt = select(func.count()).select_from(MatchResultRecord).where(MatchResultRecord.run_id == run_id)
    total = db.execute(count_stmt).scalar_one()

    # Query items ordered deterministically by rank asc, profile_id asc, profile_version asc
    items_stmt = (
        select(MatchResultRecord)
        .where(MatchResultRecord.run_id == run_id)
        .order_by(
            MatchResultRecord.rank.asc(),
            MatchResultRecord.profile_id.asc(),
            MatchResultRecord.profile_version.asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    records = list(db.execute(items_stmt).scalars().all())

    # Log audit event
    record_match_audit_event(
        db=db,
        event_type=MatchAuditEventType.MATCH_RESULTS_VIEWED,
        actor_user_id=actor.user_id,
        job_id=run_rec.job_id,
        run_id=run_rec.run_id,
        job_version_id=run_rec.job_version_id,
        revision=run_rec.revision,
        candidate_count=total,
        safe_metadata={"limit": limit, "offset": offset},
    )
    db.commit()

    return records, total, run_rec


def get_match_result_detail_for_actor(
    run_id: uuid.UUID,
    result_id: uuid.UUID,
    actor: UserRecord,
    db: Session,
) -> MatchResultRecord:
    """Retrieves detailed single candidate match result with full score decomposition."""
    # Ensure caller has access to the run
    get_match_run_for_actor(run_id=run_id, actor=actor, db=db)

    stmt = select(MatchResultRecord).where(
        MatchResultRecord.run_id == run_id,
        MatchResultRecord.result_id == result_id,
    )
    result_rec = db.execute(stmt).scalar_one_or_none()

    if result_rec is None:
        raise MatchResultNotFoundError("Match result not found.")

    return result_rec
