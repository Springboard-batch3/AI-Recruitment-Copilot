"""FastAPI Router for Phase 4C.4: Recruiter Evaluation Workspace & Candidate Feedback.

Enforces strict role authorization (Recruiter for evaluation, Candidate for published feedback),
CSRF / origin verification, and immutable finalization semantics.
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedContext, require_recruiter
from app.api.candidate_auth import (
    CandidateAuthenticatedContext,
    verify_candidate_csrf_and_origin,
)
from app.database import get_db
from app.schemas.candidate_evaluation import (
    CandidateEvaluationExportDto,
    CandidateInterviewEvaluationDetailResponse,
    CandidateSafeFeedbackReportResponse,
    EvaluationCandidateSessionSummaryDto,
    EvaluationJobSummaryDto,
    FinalizeEvaluationRequest,
    FinalizeEvaluationResponse,
    PublishCandidateFeedbackResponse,
    SaveQuestionReviewRequest,
    SaveQuestionReviewResponse,
)
from app.services.candidate_evaluation_service import (
    export_candidate_interview_evaluation,
    finalize_candidate_interview_evaluation,
    get_candidate_interview_evaluation_detail,
    get_candidate_safe_feedback,
    list_evaluation_jobs,
    list_job_candidates_for_evaluation,
    publish_candidate_feedback,
    save_question_evaluation_review,
)

router = APIRouter(prefix="/api/v1", tags=["Candidate Evaluation"])


# =============================================================================
# Level 1: Recruiter Evaluation Jobs Directory
# =============================================================================

@router.get(
    "/recruiter/evaluations/jobs",
    response_model=List[EvaluationJobSummaryDto],
    status_code=status.HTTP_200_OK,
    summary="List Evaluation Jobs Directory",
    description="Groups official candidate interviews by exact Job requisition and revision.",
)
async def list_evaluation_jobs_endpoint(
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> List[EvaluationJobSummaryDto]:
    return list_evaluation_jobs(actor=auth_ctx.user, db=db)


# =============================================================================
# Level 2: Recruiter Job Revision Candidates List
# =============================================================================

@router.get(
    "/recruiter/evaluations/jobs/{job_id}/revisions/{job_revision}/candidates",
    response_model=List[EvaluationCandidateSessionSummaryDto],
    status_code=status.HTTP_200_OK,
    summary="List Candidate Sessions for Job Revision",
    description="Lists candidate interview sessions for an exact Job revision with separate resume match references and mode filtering.",
)
async def list_job_candidates_endpoint(
    job_id: UUID,
    job_revision: int,
    mode: Optional[str] = Query(None, description="Filter candidate sessions by interview mode ('TEXT' or 'AUDIO')"),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> List[EvaluationCandidateSessionSummaryDto]:
    return list_job_candidates_for_evaluation(
        job_id=job_id,
        job_revision=job_revision,
        actor=auth_ctx.user,
        db=db,
        mode=mode,
    )


# =============================================================================
# Level 3: Candidate Evaluation Workspace Detail & JSON Export
# =============================================================================

@router.get(
    "/recruiter/evaluations/sessions/{session_id}",
    response_model=CandidateInterviewEvaluationDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Candidate Interview Evaluation Workspace",
    description="Returns full evaluation details with separate evidence channels and question breakdown.",
)
async def get_candidate_evaluation_detail_endpoint(
    session_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> CandidateInterviewEvaluationDetailResponse:
    return get_candidate_interview_evaluation_detail(
        session_id=session_id,
        actor=auth_ctx.user,
        db=db,
    )


@router.get(
    "/recruiter/evaluations/sessions/{session_id}/export",
    response_model=CandidateEvaluationExportDto,
    status_code=status.HTTP_200_OK,
    summary="Export Candidate Interview Evaluation JSON",
    description="Exports candidate interview evaluation with mode-aware data and zero audio binaries/paths.",
)
async def export_candidate_evaluation_endpoint(
    session_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> CandidateEvaluationExportDto:
    return export_candidate_interview_evaluation(
        session_id=session_id,
        actor=auth_ctx.user,
        db=db,
    )



# =============================================================================
# Level 3: Run AI Interview Analysis
# =============================================================================

@router.post(
    "/recruiter/evaluations/sessions/{session_id}/analyze",
    response_model=CandidateInterviewEvaluationDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Run AI Analysis on Candidate Interview Responses",
    description="Invokes the local LLM assessment engine on candidate answers and updates provisional ratings and observations.",
)
async def run_ai_analysis_endpoint(
    session_id: UUID,
    reanalyze_failed: bool = False,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> CandidateInterviewEvaluationDetailResponse:
    from app.services.candidate_evaluation_service import run_ai_analysis_for_session
    return await run_ai_analysis_for_session(
        session_id=session_id,
        actor=auth_ctx.user,
        db=db,
        only_failed=reanalyze_failed,
    )


# =============================================================================
# Level 3: Save Question Review
# =============================================================================

@router.put(
    "/recruiter/evaluations/sessions/{session_id}/questions/{slot_id}/review",
    response_model=SaveQuestionReviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Save Question Human Review",
    description="Saves or updates recruiter review, observations, or override for a single question slot.",
)
async def save_question_review_endpoint(
    session_id: UUID,
    slot_id: UUID,
    payload: SaveQuestionReviewRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> SaveQuestionReviewResponse:
    return save_question_evaluation_review(
        session_id=session_id,
        slot_id=slot_id,
        payload=payload,
        actor=auth_ctx.user,
        db=db,
    )


# =============================================================================
# Level 3: Finalize Evaluation
# =============================================================================

@router.post(
    "/recruiter/evaluations/sessions/{session_id}/finalize",
    response_model=FinalizeEvaluationResponse,
    status_code=status.HTTP_200_OK,
    summary="Finalize Candidate Interview Evaluation",
    description="Locks official evaluation against further edits and computes deterministic core aggregate score.",
)
async def finalize_evaluation_endpoint(
    session_id: UUID,
    payload: FinalizeEvaluationRequest = FinalizeEvaluationRequest(),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> FinalizeEvaluationResponse:
    return finalize_candidate_interview_evaluation(
        session_id=session_id,
        actor=auth_ctx.user,
        db=db,
    )


# =============================================================================
# Level 3: Publish Candidate Feedback
# =============================================================================

@router.post(
    "/recruiter/evaluations/sessions/{session_id}/publish-feedback",
    response_model=PublishCandidateFeedbackResponse,
    status_code=status.HTTP_200_OK,
    summary="Publish Candidate-Safe Feedback",
    description="Exposes candidate-safe report while strictly redacting internal recruiter notes and rubrics.",
)
async def publish_candidate_feedback_endpoint(
    session_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> PublishCandidateFeedbackResponse:
    return publish_candidate_feedback(
        session_id=session_id,
        actor=auth_ctx.user,
        db=db,
    )


# =============================================================================
# Candidate Access: Fetch Published Feedback
# =============================================================================

@router.get(
    "/candidate/interviews/sessions/{session_id}/feedback",
    response_model=CandidateSafeFeedbackReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Candidate-Safe Published Feedback",
    description="Returns read-only published feedback for candidate's own submitted interview.",
)
async def get_candidate_feedback_endpoint(
    session_id: UUID,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> CandidateSafeFeedbackReportResponse:
    return get_candidate_safe_feedback(
        session_id=session_id,
        candidate_user=cand_ctx.user,
        db=db,
    )
