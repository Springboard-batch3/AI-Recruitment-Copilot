import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedContext, require_role
from app.database import get_db
from app.models.auth import UserRole
from app.schemas.requirement_gap import (
    CanonicalGapReportResponse,
    GapReportCreateRequest,
    GapReportDetailResponse,
    GapReportItemResponse,
    GapReportSummaryResponse,
)
from app.services.canonical_summary import compute_canonical_evidence_summary
from app.services.gap_report_service import (
    GapReportNotFoundError,
    GapReportServiceError,
    IdempotencyConflictError,
    MatchResultNotFoundError,
    UnauthorizedGapReportAccessError,
    create_gap_report_for_actor,
    get_canonical_gap_report_for_actor,
    get_gap_report_for_actor,
    list_gap_reports_for_match_result,
)

logger = logging.getLogger("ai_hiring.api.gap_reports")

router = APIRouter(
    prefix="/api/v1/gap-reports",
    tags=["gap-reports"],
)
gap_reports_router = router

require_recruiter_or_admin = require_role({UserRole.RECRUITER, UserRole.ADMIN})


@router.post(
    "",
    response_model=GapReportSummaryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or Retrieve Idempotent Gap Report",
    description=(
        "Persists deterministic requirement-gap findings derived from an immutable Phase 2C match result. "
        "Enforces recruiter ownership, atomic persistence, and strict idempotency."
    ),
)
async def create_gap_report(
    payload: GapReportCreateRequest,
    response: Response,
    idempotency_key_header: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> GapReportSummaryResponse:
    actor = auth_ctx.user

    effective_idempotency_key = (
        idempotency_key_header.strip()
        if idempotency_key_header and idempotency_key_header.strip()
        else (payload.idempotency_key.strip() if payload.idempotency_key and payload.idempotency_key.strip() else None)
    )

    if not effective_idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "MISSING_IDEMPOTENCY_KEY",
                "message": "Idempotency-Key header or idempotencyKey body field is required.",
            },
        )

    try:
        report_rec, reused = create_gap_report_for_actor(
            actor=actor,
            match_result_id=payload.match_result_id,
            idempotency_key=effective_idempotency_key,
            db=db,
        )

        if reused:
            response.status_code = status.HTTP_200_OK

        return GapReportSummaryResponse(
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
            total_requirements=report_rec.total_requirements,
            covered_count=report_rec.covered_count,
            partially_covered_count=report_rec.partially_covered_count,
            not_evidenced_count=report_rec.not_evidenced_count,
            unverified_count=report_rec.unverified_count,
            conflicting_count=report_rec.conflicting_count,
            not_applicable_count=report_rec.not_applicable_count,
            must_have_warning_count=report_rec.must_have_warning_count,
            reused=reused,
            created_at=report_rec.created_at,
            report_url=f"/api/v1/gap-reports/{report_rec.report_id}",
        )

    except MatchResultNotFoundError as mne:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": mne.code, "message": mne.message},
        )
    except IdempotencyConflictError as ice:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": ice.code, "message": ice.message},
        )
    except UnauthorizedGapReportAccessError as uae:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": uae.code, "message": uae.message},
        )
    except GapReportServiceError as gse:
        raise HTTPException(
            status_code=gse.status_code,
            detail={"code": gse.code, "message": gse.message},
        )


@router.get(
    "/{report_id}",
    response_model=GapReportDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Gap Report Details",
    description="Retrieves persisted gap report metadata, summary counts, and ordered gap items.",
)
async def get_gap_report(
    report_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> GapReportDetailResponse:
    actor = auth_ctx.user
    try:
        report_rec = get_gap_report_for_actor(report_id=report_id, actor=actor, db=db)
        items = sorted(report_rec.items, key=lambda it: it.display_order)
        must_have_total = 0
        must_have_evidenced = 0
        is_legacy = (
            report_rec.gap_policy_version != "phase2d5_gap_v1"
            or report_rec.gap_schema_version != "1.1.0"
        )

        item_responses = []
        for it in items:
            if it.necessity == it.necessity.MUST_HAVE:
                must_have_total += 1
                if it.gap_state == it.gap_state.COVERED:
                    must_have_evidenced += 1

            req_name = getattr(it, "requirement_name", None) or f"{it.category.value.capitalize()} Requirement"
            doc_val = it.documented_value
            if it.gap_state == it.gap_state.COVERED and (not doc_val or doc_val.strip().lower() in {"none documented", "none documented."}):
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

        evidence_summary = compute_canonical_evidence_summary(
            requirement_contributions=items,
            extraction_confidence=report_rec.match_result.confidence_tier if report_rec.match_result else "MEDIUM",
        )

        return GapReportDetailResponse(
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
            total_requirements=report_rec.total_requirements,
            covered_count=report_rec.covered_count,
            partially_covered_count=report_rec.partially_covered_count,
            not_evidenced_count=report_rec.not_evidenced_count,
            unverified_count=report_rec.unverified_count,
            conflicting_count=report_rec.conflicting_count,
            not_applicable_count=report_rec.not_applicable_count,
            must_have_warning_count=report_rec.must_have_warning_count,
            must_have_total_count=evidence_summary.required_total if evidence_summary else must_have_total,
            must_have_evidenced_count=evidence_summary.required_evidence_found if evidence_summary else must_have_evidenced,
            evidence_summary=evidence_summary,
            compatibility_score=float(report_rec.match_result.display_compatibility_score) if report_rec.match_result else None,
            confidence_tier=report_rec.match_result.confidence_tier if report_rec.match_result else None,
            legacy_report_notice=legacy_notice,
            created_at=report_rec.created_at,
            items=item_responses,
        )

    except GapReportNotFoundError as rne:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": rne.code, "message": rne.message},
        )


@router.get(
    "/{report_id}/items",
    response_model=List[GapReportItemResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Gap Report Items",
    description="Retrieves the ordered requirement gap records for a gap report.",
)
async def get_gap_report_items(
    report_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> List[GapReportItemResponse]:
    actor = auth_ctx.user
    try:
        report_rec = get_gap_report_for_actor(report_id=report_id, actor=actor, db=db)
        items = sorted(report_rec.items, key=lambda it: it.display_order)

        result_items = []
        for it in items:
            req_name = getattr(it, "requirement_name", None) or f"{it.category.value.capitalize()} Requirement"
            doc_val = it.documented_value
            if it.gap_state == it.gap_state.COVERED and (not doc_val or doc_val.strip().lower() in {"none documented", "none documented."}):
                doc_val = "Evidence on file (legacy record)"

            result_items.append(
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
        return result_items

    except GapReportNotFoundError as rne:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": rne.code, "message": rne.message},
        )


@router.get(
    "/{report_id}/json",
    response_model=CanonicalGapReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Canonical Gap Report JSON",
    description="Retrieves the complete, structured canonical gap report JSON with derived summary counts.",
)
async def get_gap_report_json(
    report_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> CanonicalGapReportResponse:
    actor = auth_ctx.user
    try:
        return get_canonical_gap_report_for_actor(report_id=report_id, actor=actor, db=db)
    except GapReportNotFoundError as rne:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": rne.code, "message": rne.message},
        )


# List endpoint nested under matches/results
result_reports_router = APIRouter(
    prefix="/api/v1/matches/results",
    tags=["gap-reports"],
)


@result_reports_router.get(
    "/{result_id}/gap-reports",
    response_model=List[GapReportSummaryResponse],
    status_code=status.HTTP_200_OK,
    summary="List Gap Reports for Match Result",
    description="Lists all versioned gap reports generated for a single match result.",
)
async def list_gap_reports_for_result(
    result_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> List[GapReportSummaryResponse]:
    actor = auth_ctx.user
    try:
        reports = list_gap_reports_for_match_result(match_result_id=result_id, actor=actor, db=db)
        return [
            GapReportSummaryResponse(
                report_id=r.report_id,
                match_run_id=r.match_run_id,
                match_result_id=r.match_result_id,
                job_id=r.job_id,
                job_version_id=r.job_version_id,
                revision=r.revision,
                profile_id=r.profile_id,
                profile_version=r.profile_version,
                evaluation_date=r.evaluation_date,
                matcher_algorithm_version=r.matcher_algorithm_version,
                matching_rule_version=r.matching_rule_version,
                scoring_policy_version=r.scoring_policy_version,
                match_input_schema_version=r.match_input_schema_version,
                taxonomy_version=r.taxonomy_version,
                gap_schema_version=r.gap_schema_version,
                gap_policy_version=r.gap_policy_version,
                wording_template_version=r.wording_template_version,
                source_fingerprint=r.source_fingerprint,
                report_fingerprint=r.report_fingerprint,
                total_requirements=r.total_requirements,
                covered_count=r.covered_count,
                partially_covered_count=r.partially_covered_count,
                not_evidenced_count=r.not_evidenced_count,
                unverified_count=r.unverified_count,
                conflicting_count=r.conflicting_count,
                not_applicable_count=r.not_applicable_count,
                must_have_warning_count=r.must_have_warning_count,
                reused=False,
                created_at=r.created_at,
                report_url=f"/api/v1/gap-reports/{r.report_id}",
            )
            for r in reports
        ]
    except MatchResultNotFoundError as mne:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": mne.code, "message": mne.message},
        )
