import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedContext, require_role
from app.database import get_db
from app.models.auth import UserRole
from app.schemas.explanation import MatchExplanationResponse
from app.schemas.match_run import (
    MatchResultDetailResponse,
    MatchResultRankingItemResponse,
    MatchRunCreateRequest,
    MatchRunDetailResponse,
    MatchRunSummaryResponse,
    PaginatedMatchResultsResponse,
)
from app.services.canonical_summary import compute_canonical_evidence_summary
from app.services.explanation import generate_match_explanation
from app.services.match_orchestration import (
    DuplicateCandidateError,
    IdempotencyConflictError,
    InvalidEvaluationDateError,
    CandidateProfileNotFoundError,
    MatchOrchestrationError,
    MatchResultNotFoundError,
    MatchRunNotFoundError,
    RequisitionNotFoundError,
    RequisitionNotReadyError,
    UnauthorizedMatchAccessError,
    get_match_result_detail_for_actor,
    get_match_run_for_actor,
    list_match_results_for_actor,
    orchestrate_match_run,
)

logger = logging.getLogger("ai_hiring.api.matches")

router = APIRouter(
    prefix="/api/v1/matches",
    tags=["matches"],
)

require_recruiter_or_admin = require_role({UserRole.RECRUITER, UserRole.ADMIN})


@router.post(
    "/runs",
    response_model=MatchRunSummaryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Deterministic Match Run",
    description=(
        "Runs deterministic requirement matching and scoring for explicitly selected candidate profile versions "
        "against an exact READY_FOR_MATCHING requisition revision. Enforces recruiter ownership and idempotency."
    ),
)
async def create_match_run(
    payload: MatchRunCreateRequest,
    response: Response,
    idempotency_key_header: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> MatchRunSummaryResponse:
    actor = auth_ctx.user

    # Resolve idempotency key from header or body
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
        run_rec, results, reused = orchestrate_match_run(
            actor=actor,
            job_id=payload.job_id,
            expected_revision=payload.expected_revision,
            candidates=payload.candidates,
            evaluation_date=payload.evaluation_date,
            idempotency_key=effective_idempotency_key,
            db=db,
        )

        if reused:
            response.status_code = status.HTTP_200_OK

        return MatchRunSummaryResponse(
            run_id=run_rec.run_id,
            lifecycle_status=run_rec.lifecycle_status,
            job_id=run_rec.job_id,
            job_version_id=run_rec.job_version_id,
            revision=run_rec.revision,
            candidate_count=run_rec.candidate_count,
            matcher_algorithm_version=run_rec.matcher_algorithm_version,
            matching_rule_version=run_rec.matching_rule_version,
            scoring_policy_version=run_rec.scoring_policy_version,
            match_input_schema_version=run_rec.match_input_schema_version,
            input_fingerprint=run_rec.input_fingerprint,
            candidate_input_fingerprint=run_rec.candidate_input_fingerprint,
            reused=reused,
            created_at=run_rec.created_at,
            started_at=run_rec.started_at,
            completed_at=run_rec.completed_at,
            results_url=f"/api/v1/matches/runs/{run_rec.run_id}/results",
        )

    except RequisitionNotFoundError as rne:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": rne.code, "message": rne.message},
        )
    except RequisitionNotReadyError as rnre:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": rnre.code, "message": rnre.message},
        )
    except CandidateProfileNotFoundError as cnfe:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": cnfe.code, "message": cnfe.message},
        )
    except DuplicateCandidateError as dce:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": dce.code, "message": dce.message},
        )
    except IdempotencyConflictError as ice:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": ice.code, "message": ice.message},
        )
    except InvalidEvaluationDateError as iede:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": iede.code, "message": iede.message},
        )
    except UnauthorizedMatchAccessError as umae:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": umae.code, "message": umae.message},
        )
    except MatchOrchestrationError as moe:
        raise HTTPException(
            status_code=moe.status_code,
            detail={"code": moe.code, "message": moe.message},
        )


@router.get(
    "/runs/{run_id}",
    response_model=MatchRunDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Match Run Details",
    description="Retrieves match run execution metadata, lifecycle status, versions, and safe diagnostic status.",
)
async def get_match_run(
    run_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> MatchRunDetailResponse:
    actor = auth_ctx.user
    try:
        run_rec = get_match_run_for_actor(run_id=run_id, actor=actor, db=db)
        return MatchRunDetailResponse(
            run_id=run_rec.run_id,
            lifecycle_status=run_rec.lifecycle_status,
            job_id=run_rec.job_id,
            job_version_id=run_rec.job_version_id,
            revision=run_rec.revision,
            candidate_count=run_rec.candidate_count,
            matcher_algorithm_version=run_rec.matcher_algorithm_version,
            matching_rule_version=run_rec.matching_rule_version,
            scoring_policy_version=run_rec.scoring_policy_version,
            match_input_schema_version=run_rec.match_input_schema_version,
            input_fingerprint=run_rec.input_fingerprint,
            candidate_input_fingerprint=run_rec.candidate_input_fingerprint,
            error_code=run_rec.error_code,
            error_message=run_rec.error_message,
            created_at=run_rec.created_at,
            started_at=run_rec.started_at,
            completed_at=run_rec.completed_at,
            results_url=f"/api/v1/matches/runs/{run_rec.run_id}/results",
        )
    except MatchRunNotFoundError as rne:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": rne.code, "message": rne.message},
        )


@router.get(
    "/runs/{run_id}/results",
    response_model=PaginatedMatchResultsResponse,
    status_code=status.HTTP_200_OK,
    summary="List Deterministic Ranked Match Results",
    description=(
        "Returns paginated candidate results ranked deterministically by unrounded raw compatibility score. "
        "Preserves dense ranking for ties without automated decision making or identity leakage."
    ),
)
async def list_match_results(
    run_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> PaginatedMatchResultsResponse:
    actor = auth_ctx.user
    try:
        records, total, run_rec = list_match_results_for_actor(
            run_id=run_id,
            actor=actor,
            limit=limit,
            offset=offset,
            db=db,
        )

        items = []
        for r in records:
            decomp = r.score_decomposition or {}
            req_contribs = decomp.get("requirement_contributions", [])
            evidence_summary = compute_canonical_evidence_summary(
                requirement_contributions=req_contribs,
                extraction_confidence=r.confidence_tier,
            )
            mh_total = evidence_summary.required_total if evidence_summary else 0
            mh_evidenced = evidence_summary.required_evidence_found if evidence_summary else 0
            not_ev_cnt = evidence_summary.overall_not_found if evidence_summary else 0
            needs_conf_cnt = evidence_summary.required_needs_verification if evidence_summary else 0

            items.append(
                MatchResultRankingItemResponse(
                    result_id=r.result_id,
                    run_id=r.run_id,
                    profile_id=r.profile_id,
                    profile_version=r.profile_version,
                    rank=r.rank,
                    is_tied=r.is_tied,
                    tie_group_id=r.tie_group_id,
                    raw_compatibility_score=float(r.raw_compatibility_score),
                    display_compatibility_score=float(r.display_compatibility_score),
                    confidence_tier=r.confidence_tier,
                    warning_count=r.warning_count,
                    must_have_warning_count=r.must_have_warning_count,
                    must_have_total_count=mh_total,
                    must_have_evidenced_count=mh_evidenced,
                    not_evidenced_count=not_ev_cnt,
                    needs_confirmation_count=needs_conf_cnt,
                    evidence_summary=evidence_summary,
                    component_scores=decomp.get("component_scores", {}),
                    confidence_diagnostics=decomp.get("confidence_diagnostics", {}),
                    must_have_warnings=decomp.get("must_have_warnings", []),
                    general_warnings=decomp.get("general_warnings", []),
                    requirement_contributions=req_contribs,
                    detail_url=f"/api/v1/matches/runs/{r.run_id}/results/{r.result_id}",
                )
            )

        return PaginatedMatchResultsResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            run_id=run_rec.run_id,
            job_id=run_rec.job_id,
            revision=run_rec.revision,
        )
    except MatchRunNotFoundError as rne:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": rne.code, "message": rne.message},
        )


@router.get(
    "/runs/{run_id}/results/{result_id}",
    response_model=MatchResultDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Detailed Candidate Match Result",
    description="Retrieves full requirement-level score decomposition and evidence references for one candidate match result.",
)
async def get_match_result_detail(
    run_id: uuid.UUID,
    result_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> MatchResultDetailResponse:
    actor = auth_ctx.user
    try:
        r = get_match_result_detail_for_actor(
            run_id=run_id,
            result_id=result_id,
            actor=actor,
            db=db,
        )
        decomp = r.score_decomposition or {}
        evidence_summary = compute_canonical_evidence_summary(
            requirement_contributions=decomp.get("requirement_contributions", []),
            extraction_confidence=r.confidence_tier,
        )
        return MatchResultDetailResponse(
            result_id=r.result_id,
            run_id=r.run_id,
            profile_id=r.profile_id,
            profile_version=r.profile_version,
            rank=r.rank,
            is_tied=r.is_tied,
            tie_group_id=r.tie_group_id,
            raw_compatibility_score=float(r.raw_compatibility_score),
            display_compatibility_score=float(r.display_compatibility_score),
            confidence_tier=r.confidence_tier,
            warning_count=r.warning_count,
            must_have_warning_count=r.must_have_warning_count,
            evidence_summary=evidence_summary,
            component_scores=decomp.get("component_scores", {}),
            confidence_diagnostics=decomp.get("confidence_diagnostics", {}),
            must_have_warnings=decomp.get("must_have_warnings", []),
            general_warnings=decomp.get("general_warnings", []),
            score_decomposition=decomp,
            created_at=r.created_at,
        )
    except (MatchRunNotFoundError, MatchResultNotFoundError) as ne:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": ne.code, "message": ne.message},
        )


@router.get(
    "/runs/{run_id}/results/{result_id}/explanation",
    response_model=MatchExplanationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Deterministic Match Explanation",
    description=(
        "Retrieves a deterministic, evidence-addressable explanation for an immutable match result. "
        "Consumes only the stored score decomposition; never recalculates scores or creates hiring decisions."
    ),
)
async def get_match_result_explanation(
    run_id: uuid.UUID,
    result_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> MatchExplanationResponse:
    actor = auth_ctx.user
    try:
        run_rec = get_match_run_for_actor(run_id=run_id, actor=actor, db=db)
        result_rec = get_match_result_detail_for_actor(
            run_id=run_id,
            result_id=result_id,
            actor=actor,
            db=db,
        )
        return generate_match_explanation(
            result_record=result_rec,
            run_record=run_rec,
        )
    except (MatchRunNotFoundError, MatchResultNotFoundError) as ne:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": ne.code, "message": ne.message},
        )

