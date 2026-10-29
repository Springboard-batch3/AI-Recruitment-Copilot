import copy
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.auth import UserRecord, UserRole
from app.models.gap_report import (
    GapAuditEventRecord,
    GapAuditEventType,
    GapReportItemRecord,
    GapReportRecord,
)
from app.models.job import JobLifecycleStatus, JobRequisition, JobVersion, RequirementNecessity
from app.models.matching import (
    MatchResultRecord,
    MatchRunLifecycleStatus,
    MatchRunRecord,
)
from app.schemas.matching import verify_no_prohibited_keys
from app.schemas.requirement_gap import (
    GAP_POLICY_VERSION,
    GAP_SCHEMA_VERSION,
    WORDING_TEMPLATE_VERSION,
    CanonicalGapReportResponse,
    GapReportDetailResponse,
    GapReportItemResponse,
    GapReportSummaryResponse,
    RequirementGapRecord,
    RequirementGapSetContract,
    RequirementGapState,
)
from app.services.canonical_summary import compute_canonical_evidence_summary
from app.services.gap_derivation import derive_requirement_gaps
from app.services.gap_validation import (
    GapSourceValidationError,
    validate_and_adapt_match_result_to_gap_set,
)

logger = logging.getLogger("ai_hiring.services.gap_report")


# Domain Exceptions
class GapReportServiceError(Exception):
    """Base exception for gap report service operations."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class GapReportNotFoundError(GapReportServiceError):
    def __init__(self, message: str = "Gap report not found."):
        super().__init__(code="GAP_REPORT_NOT_FOUND", message=message, status_code=404)


class MatchResultNotFoundError(GapReportServiceError):
    def __init__(self, message: str = "Match result not found."):
        super().__init__(code="MATCH_RESULT_NOT_FOUND", message=message, status_code=404)


class UnauthorizedGapReportAccessError(GapReportServiceError):
    def __init__(self, message: str = "Unauthorized to perform or view this gap report operation."):
        super().__init__(code="UNAUTHORIZED_GAP_REPORT_ACCESS", message=message, status_code=403)


class IdempotencyConflictError(GapReportServiceError):
    def __init__(self, message: str = "Idempotency key already used with different gap report parameters."):
        super().__init__(code="IDEMPOTENCY_CONFLICT", message=message, status_code=409)


# Fingerprinting Functions
def compute_canonical_gap_source_fingerprint(
    match_run_id: uuid.UUID,
    match_result_id: uuid.UUID,
    requisition_id: uuid.UUID,
    job_version_id: uuid.UUID,
    revision: int,
    profile_id: uuid.UUID,
    profile_version: int,
    evaluation_date: datetime,
    matcher_algorithm_version: str,
    matching_rule_version: str,
    scoring_policy_version: str,
    match_input_schema_version: str,
    taxonomy_version: Optional[str],
) -> str:
    """Computes deterministic SHA-256 fingerprint representing the immutable Phase 2C source inputs."""
    eval_iso = (
        evaluation_date.replace(tzinfo=timezone.utc).isoformat()
        if evaluation_date.tzinfo is None
        else evaluation_date.astimezone(timezone.utc).isoformat()
    )

    canonical_obj = {
        "match_run_id": str(match_run_id),
        "match_result_id": str(match_result_id),
        "requisition_id": str(requisition_id),
        "job_version_id": str(job_version_id),
        "revision": revision,
        "profile_id": str(profile_id),
        "profile_version": profile_version,
        "evaluation_date": eval_iso,
        "matcher_algorithm_version": matcher_algorithm_version,
        "matching_rule_version": matching_rule_version,
        "scoring_policy_version": scoring_policy_version,
        "match_input_schema_version": match_input_schema_version,
        "taxonomy_version": taxonomy_version,
    }
    verify_no_prohibited_keys(canonical_obj)
    serialized = json.dumps(canonical_obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()


def compute_canonical_gap_report_fingerprint(
    source_fingerprint: str,
    gap_schema_version: str,
    gap_policy_version: str,
    wording_template_version: str,
    derived_gaps: List[RequirementGapRecord],
) -> str:
    """Computes deterministic SHA-256 fingerprint representing the complete derived gap report content."""
    sorted_gaps_payload = []
    for g in sorted(derived_gaps, key=lambda x: x.display_order):
        sorted_gaps_payload.append({
            "requirement_id": str(g.requirement_id),
            "requirement_name": g.requirement_name,
            "display_order": g.display_order,
            "category": g.category.value,
            "necessity": g.necessity.value,
            "priority": g.priority.value,
            "source_match_state": g.source_match_state.value,
            "source_match_value": g.source_match_value,
            "gap_state": g.gap_state.value,
            "required_value": g.required_value,
            "documented_value": g.documented_value,
            "measurable_shortfall_value": g.measurable_shortfall_value,
            "shortfall_unit": g.shortfall_unit,
            "shortfall_reason_code": g.shortfall_reason_code,
            "evidence_references": sorted(g.evidence_references),
            "review_required": g.review_required,
            "review_action_code": g.review_action_code.value,
            "must_have_warning_reference": g.must_have_warning_reference,
            "recruiter_wording": g.recruiter_wording,
        })

    report_obj = {
        "source_fingerprint": source_fingerprint,
        "gap_schema_version": gap_schema_version,
        "gap_policy_version": gap_policy_version,
        "wording_template_version": wording_template_version,
        "gaps": sorted_gaps_payload,
    }
    verify_no_prohibited_keys(report_obj)
    serialized = json.dumps(report_obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()


# Audit Recording Helper
def record_gap_audit_event(
    db: Session,
    event_type: GapAuditEventType,
    actor_user_id: uuid.UUID,
    match_result_id: Optional[uuid.UUID] = None,
    report_id: Optional[uuid.UUID] = None,
    safe_metadata: Optional[Dict[str, Any]] = None,
) -> GapAuditEventRecord:
    """Records a privacy-safe audit event for gap report operations."""
    clean_metadata = copy.deepcopy(safe_metadata or {})
    verify_no_prohibited_keys(clean_metadata)

    audit_rec = GapAuditEventRecord(
        audit_id=uuid.uuid4(),
        report_id=report_id,
        match_result_id=match_result_id,
        actor_user_id=actor_user_id,
        event_type=event_type.value,
        safe_metadata=clean_metadata,
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(audit_rec)
    return audit_rec


# Core Creation / Idempotent Service
def create_gap_report_for_actor(
    actor: UserRecord,
    match_result_id: uuid.UUID,
    idempotency_key: str,
    db: Session,
) -> Tuple[GapReportRecord, bool]:
    """Atomic gap report generation service.

    Flow:
    1. Loads the source match result and run with organization/recruiter ownership check.
    2. Runs Phase 2D.1 source validation.
    3. Runs Phase 2D.2 deterministic derivation.
    4. Computes canonical source and report fingerprints.
    5. Resolves idempotency: returns existing on match (reused=True) or 409 on conflict.
    6. Persists header and all items atomically in one database transaction.
    7. Records audit event and returns completed record.
    """
    clean_idempotency_key = idempotency_key.strip()
    if not clean_idempotency_key:
        raise GapReportServiceError(
            code="MISSING_IDEMPOTENCY_KEY",
            message="Idempotency key cannot be empty.",
            status_code=400,
        )

    # 1. Load match result with run and job version
    stmt_result = (
        select(MatchResultRecord)
        .options(
            selectinload(MatchResultRecord.run).selectinload(MatchRunRecord.requisition),
            selectinload(MatchResultRecord.run).selectinload(MatchRunRecord.job_version).selectinload(JobVersion.requirements),
        )
        .where(MatchResultRecord.result_id == match_result_id)
    )
    res_rec = db.execute(stmt_result).scalar_one_or_none()

    if res_rec is None:
        raise MatchResultNotFoundError("Match result not found.")

    run_rec = res_rec.run
    if run_rec is None:
        raise GapReportServiceError(
            code="GAP_SOURCE_INCOMPLETE",
            message="Match result has no associated match run.",
            status_code=422,
        )

    # Organization / Recruiter ownership check (404 concealment for unauthorized callers)
    if actor.role == UserRole.RECRUITER and run_rec.recruiter_id != actor.user_id:
        raise MatchResultNotFoundError("Match result not found.")

    # 2. Pre-check idempotency key for this recruiter
    stmt_existing = (
        select(GapReportRecord)
        .options(selectinload(GapReportRecord.items))
        .where(
            GapReportRecord.recruiter_id == actor.user_id,
            GapReportRecord.idempotency_key == clean_idempotency_key,
        )
    )
    existing_report = db.execute(stmt_existing).scalar_one_or_none()

    # 3. Phase 2D.1 Source Validation
    job_ver = run_rec.job_version
    try:
        validated_gap_set = validate_and_adapt_match_result_to_gap_set(
            result_record=res_rec,
            run_record=run_rec,
            job_version=job_ver,
        )
    except GapSourceValidationError as gve:
        record_gap_audit_event(
            db=db,
            event_type=GapAuditEventType.GAP_REPORT_CREATION_FAILED,
            actor_user_id=actor.user_id,
            match_result_id=match_result_id,
            safe_metadata={"error_code": gve.code},
        )
        db.commit()
        raise GapReportServiceError(code=gve.code, message=gve.message, status_code=gve.status_code)

    # 4. Phase 2D.2 Deterministic Derivation
    score_decomp = res_rec.score_decomposition or {}
    derived_contract = derive_requirement_gaps(
        validated_gap_set=validated_gap_set,
        score_decomposition=score_decomp,
    )

    # Invariant: gap record count == source requirement count
    if len(derived_contract.gaps) != validated_gap_set.total_requirements:
        raise GapReportServiceError(
            code="GAP_COUNT_MISMATCH",
            message="Derived gap count does not match source requirement count.",
            status_code=422,
        )

    # 5. Compute canonical fingerprints
    src_fingerprint = compute_canonical_gap_source_fingerprint(
        match_run_id=run_rec.run_id,
        match_result_id=res_rec.result_id,
        requisition_id=run_rec.job_id,
        job_version_id=run_rec.job_version_id,
        revision=run_rec.revision,
        profile_id=res_rec.profile_id,
        profile_version=res_rec.profile_version,
        evaluation_date=run_rec.evaluation_date,
        matcher_algorithm_version=run_rec.matcher_algorithm_version,
        matching_rule_version=run_rec.matching_rule_version,
        scoring_policy_version=run_rec.scoring_policy_version,
        match_input_schema_version=run_rec.match_input_schema_version,
        taxonomy_version=run_rec.job_version.taxonomy_version if run_rec.job_version else None,
    )

    rep_fingerprint = compute_canonical_gap_report_fingerprint(
        source_fingerprint=src_fingerprint,
        gap_schema_version=GAP_SCHEMA_VERSION,
        gap_policy_version=GAP_POLICY_VERSION,
        wording_template_version=WORDING_TEMPLATE_VERSION,
        derived_gaps=derived_contract.gaps,
    )

    # 6. Idempotency evaluation
    if existing_report is not None:
        if existing_report.report_fingerprint == rep_fingerprint:
            record_gap_audit_event(
                db=db,
                event_type=GapAuditEventType.GAP_REPORT_REUSED,
                actor_user_id=actor.user_id,
                match_result_id=match_result_id,
                report_id=existing_report.report_id,
                safe_metadata={"reused": True},
            )
            db.commit()
            return existing_report, True
        else:
            raise IdempotencyConflictError(
                "Idempotency key already used with different match result or parameters."
            )

    # Check if this exact report fingerprint was already generated under another key
    stmt_fp = (
        select(GapReportRecord)
        .options(selectinload(GapReportRecord.items))
        .where(GapReportRecord.report_fingerprint == rep_fingerprint)
    )
    existing_by_fp = db.execute(stmt_fp).scalar_one_or_none()
    if existing_by_fp is not None:
        # Same report content already exists for this actor
        if existing_by_fp.recruiter_id == actor.user_id:
            return existing_by_fp, True

    # 7. Calculate summary counts
    covered_cnt = sum(1 for g in derived_contract.gaps if g.gap_state == RequirementGapState.COVERED)
    partial_cnt = sum(1 for g in derived_contract.gaps if g.gap_state == RequirementGapState.PARTIALLY_COVERED)
    not_ev_cnt = sum(1 for g in derived_contract.gaps if g.gap_state == RequirementGapState.NOT_EVIDENCED)
    unver_cnt = sum(1 for g in derived_contract.gaps if g.gap_state == RequirementGapState.UNVERIFIED)
    conflict_cnt = sum(1 for g in derived_contract.gaps if g.gap_state == RequirementGapState.CONFLICTING_REVIEW_REQUIRED)
    not_app_cnt = sum(1 for g in derived_contract.gaps if g.gap_state == RequirementGapState.NOT_APPLICABLE)
    must_have_warn_cnt = sum(1 for g in derived_contract.gaps if g.must_have_warning_reference is not None)

    # 8. Persist atomically in one transaction
    new_report_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    report_record = GapReportRecord(
        report_id=new_report_id,
        recruiter_id=actor.user_id,
        match_run_id=run_rec.run_id,
        match_result_id=res_rec.result_id,
        job_id=run_rec.job_id,
        job_version_id=run_rec.job_version_id,
        revision=run_rec.revision,
        profile_id=res_rec.profile_id,
        profile_version=res_rec.profile_version,
        evaluation_date=run_rec.evaluation_date,
        matcher_algorithm_version=run_rec.matcher_algorithm_version,
        matching_rule_version=run_rec.matching_rule_version,
        scoring_policy_version=run_rec.scoring_policy_version,
        match_input_schema_version=run_rec.match_input_schema_version,
        taxonomy_version=run_rec.job_version.taxonomy_version if run_rec.job_version else None,
        gap_schema_version=GAP_SCHEMA_VERSION,
        gap_policy_version=GAP_POLICY_VERSION,
        wording_template_version=WORDING_TEMPLATE_VERSION,
        source_fingerprint=src_fingerprint,
        report_fingerprint=rep_fingerprint,
        idempotency_key=clean_idempotency_key,
        total_requirements=len(derived_contract.gaps),
        covered_count=covered_cnt,
        partially_covered_count=partial_cnt,
        not_evidenced_count=not_ev_cnt,
        unverified_count=unver_cnt,
        conflicting_count=conflict_cnt,
        not_applicable_count=not_app_cnt,
        must_have_warning_count=must_have_warn_cnt,
        created_at=now,
    )
    db.add(report_record)

    for g in derived_contract.gaps:
        item_rec = GapReportItemRecord(
            item_id=uuid.uuid4(),
            report_id=new_report_id,
            requirement_id=g.requirement_id,
            requirement_name=g.requirement_name,
            display_order=g.display_order,
            category=g.category,
            necessity=g.necessity,
            priority=g.priority,
            source_match_state=g.source_match_state,
            source_match_value=g.source_match_value,
            gap_state=g.gap_state,
            required_value=g.required_value,
            documented_value=g.documented_value,
            measurable_shortfall_value=g.measurable_shortfall_value,
            shortfall_unit=g.shortfall_unit,
            shortfall_reason_code=g.shortfall_reason_code,
            evidence_references=g.evidence_references,
            review_required=g.review_required,
            review_action_code=g.review_action_code,
            must_have_warning_reference=g.must_have_warning_reference,
            recruiter_wording=g.recruiter_wording or "",
            created_at=now,
        )
        db.add(item_rec)

    # 9. Audit event
    record_gap_audit_event(
        db=db,
        event_type=GapAuditEventType.GAP_REPORT_CREATED,
        actor_user_id=actor.user_id,
        match_result_id=match_result_id,
        report_id=new_report_id,
        safe_metadata={
            "total_requirements": report_record.total_requirements,
            "covered_count": covered_cnt,
            "must_have_warning_count": must_have_warn_cnt,
        },
    )

    try:
        db.commit()
        db.refresh(report_record)
        return report_record, False
    except IntegrityError:
        db.rollback()
        # Concurrent duplicate check
        existing_concurrent = db.execute(stmt_existing).scalar_one_or_none()
        if existing_concurrent is not None:
            if existing_concurrent.report_fingerprint == rep_fingerprint:
                return existing_concurrent, True
            raise IdempotencyConflictError(
                "Idempotency key already used with different parameters."
            )
        raise GapReportServiceError(
            code="GAP_REPORT_INTEGRITY_ERROR",
            message="Database constraint violated during gap report persistence.",
            status_code=422,
        )


# Read Operations
def get_gap_report_for_actor(
    report_id: uuid.UUID,
    actor: UserRecord,
    db: Session,
) -> GapReportRecord:
    """Retrieves a single gap report header with items, enforcing organization/recruiter ownership."""
    stmt = (
        select(GapReportRecord)
        .options(
            selectinload(GapReportRecord.items),
            selectinload(GapReportRecord.match_result),
        )
        .where(GapReportRecord.report_id == report_id)
    )
    report_rec = db.execute(stmt).scalar_one_or_none()

    if report_rec is None or (actor.role == UserRole.RECRUITER and report_rec.recruiter_id != actor.user_id):
        raise GapReportNotFoundError("Gap report not found.")

    record_gap_audit_event(
        db=db,
        event_type=GapAuditEventType.GAP_REPORT_VIEWED,
        actor_user_id=actor.user_id,
        report_id=report_rec.report_id,
        match_result_id=report_rec.match_result_id,
    )
    db.commit()
    return report_rec


def list_gap_reports_for_match_result(
    match_result_id: uuid.UUID,
    actor: UserRecord,
    db: Session,
) -> List[GapReportRecord]:
    """Lists all versioned gap reports for a match result, enforcing ownership."""
    stmt_result = (
        select(MatchResultRecord)
        .options(selectinload(MatchResultRecord.run))
        .where(MatchResultRecord.result_id == match_result_id)
    )
    res_rec = db.execute(stmt_result).scalar_one_or_none()
    if res_rec is None or (actor.role == UserRole.RECRUITER and res_rec.run and res_rec.run.recruiter_id != actor.user_id):
        raise MatchResultNotFoundError("Match result not found.")

    stmt = (
        select(GapReportRecord)
        .options(selectinload(GapReportRecord.items))
        .where(GapReportRecord.match_result_id == match_result_id)
        .order_by(GapReportRecord.created_at.desc())
    )
    reports = list(db.execute(stmt).scalars().all())

    if actor.role == UserRole.RECRUITER:
        reports = [r for r in reports if r.recruiter_id == actor.user_id]

    return reports


def get_canonical_gap_report_for_actor(
    report_id: uuid.UUID,
    actor: UserRecord,
    db: Session,
) -> CanonicalGapReportResponse:
    """Assembles and returns the complete, structured canonical gap report JSON."""
    report_rec = get_gap_report_for_actor(report_id=report_id, actor=actor, db=db)

    # Derive summary counts strictly from persisted gap records
    items = sorted(report_rec.items, key=lambda it: it.display_order)
    summary_counts: Dict[str, int] = {
        RequirementGapState.COVERED.value: 0,
        RequirementGapState.PARTIALLY_COVERED.value: 0,
        RequirementGapState.NOT_EVIDENCED.value: 0,
        RequirementGapState.UNVERIFIED.value: 0,
        RequirementGapState.CONFLICTING_REVIEW_REQUIRED.value: 0,
        RequirementGapState.NOT_APPLICABLE.value: 0,
    }

    all_evidence_refs = set()
    item_responses: List[GapReportItemResponse] = []
    must_have_total = 0
    must_have_evidenced = 0
    is_legacy = (
        report_rec.gap_policy_version != GAP_POLICY_VERSION
        or report_rec.gap_schema_version != GAP_SCHEMA_VERSION
    )

    for it in items:
        summary_counts[it.gap_state.value] = summary_counts.get(it.gap_state.value, 0) + 1
        if it.necessity == RequirementNecessity.MUST_HAVE:
            must_have_total += 1
            if it.gap_state == RequirementGapState.COVERED:
                must_have_evidenced += 1

        for ref in (it.evidence_references or []):
            all_evidence_refs.add(ref)

        req_name = getattr(it, "requirement_name", None) or f"{it.category.value.capitalize()} Requirement"

        # Check for legacy flaw: COVERED with None documented
        doc_val = it.documented_value
        if it.gap_state == RequirementGapState.COVERED and (not doc_val or doc_val.strip().lower() in {"none documented", "none documented."}):
            is_legacy = True
            doc_val = "Evidence on file (legacy record)"

        item_responses.append(
            GapReportItemResponse(
                item_id=it.item_id,
                report_id=it.report_id,
                requirement_id=it.requirement_id,
                requirement_name=req_name,
                display_order=it.display_order,
                category=it.category,
                necessity=it.necessity,
                priority=it.priority,
                source_match_state=it.source_match_state,
                source_match_value=it.source_match_value,
                gap_state=it.gap_state,
                required_value=it.required_value,
                documented_value=doc_val,
                measurable_shortfall_value=it.measurable_shortfall_value,
                shortfall_unit=it.shortfall_unit,
                shortfall_reason_code=it.shortfall_reason_code,
                evidence_references=it.evidence_references,
                review_required=it.review_required,
                review_action_code=it.review_action_code,
                must_have_warning_reference=it.must_have_warning_reference,
                recruiter_wording=it.recruiter_wording,
                created_at=it.created_at,
            )
        )

    legacy_notice = (
        "This is an immutable historical report evaluated under an earlier policy version. "
        "Some requirement names or details may reflect legacy schema standards. Run a new comparison to view updated results."
        if is_legacy
        else None
    )

    record_gap_audit_event(
        db=db,
        event_type=GapAuditEventType.GAP_REPORT_JSON_ACCESSED,
        actor_user_id=actor.user_id,
        report_id=report_rec.report_id,
        match_result_id=report_rec.match_result_id,
    )
    db.commit()

    evidence_summary = compute_canonical_evidence_summary(
        requirement_contributions=items,
        extraction_confidence=report_rec.match_result.confidence_tier if report_rec.match_result else "MEDIUM",
    )

    return CanonicalGapReportResponse(
        report_id=report_rec.report_id,
        match_run_id=report_rec.match_run_id,
        match_result_id=report_rec.match_result_id,
        job_id=report_rec.job_id,
        job_version_id=report_rec.job_version_id,
        revision=report_rec.revision,
        profile_id=report_rec.profile_id,
        profile_version=report_rec.profile_version,
        evaluation_date=report_rec.evaluation_date,
        matcher_algorithm_version=report_rec.matcher_algorithm_version,
        matching_rule_version=report_rec.matching_rule_version,
        scoring_policy_version=report_rec.scoring_policy_version,
        match_input_schema_version=report_rec.match_input_schema_version,
        taxonomy_version=report_rec.taxonomy_version,
        gap_schema_version=report_rec.gap_schema_version,
        gap_policy_version=report_rec.gap_policy_version,
        wording_template_version=report_rec.wording_template_version,
        source_fingerprint=report_rec.source_fingerprint,
        report_fingerprint=report_rec.report_fingerprint,
        total_requirements=len(item_responses),
        summary_counts=summary_counts,
        must_have_warning_count=report_rec.must_have_warning_count,
        must_have_total_count=evidence_summary.required_total if evidence_summary else must_have_total,
        must_have_evidenced_count=evidence_summary.required_evidence_found if evidence_summary else must_have_evidenced,
        evidence_summary=evidence_summary,
        compatibility_score=float(report_rec.match_result.display_compatibility_score) if report_rec.match_result else None,
        confidence_tier=report_rec.match_result.confidence_tier if report_rec.match_result else None,
        legacy_report_notice=legacy_notice,
        gaps=item_responses,
        all_evidence_references=sorted(list(all_evidence_refs)),
        created_at=report_rec.created_at,
    )
