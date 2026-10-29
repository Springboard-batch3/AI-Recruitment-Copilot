"""API endpoints for Recruiter Practice Releases and Candidate Practice Discovery in Phase 3F.2."""
import uuid
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import (
    AuthenticatedContext,
    CandidateAuthenticatedContext,
    require_candidate,
    require_recruiter,
    verify_candidate_csrf_and_origin,
)
from app.database import get_db
from app.schemas.mcq_contract import (
    CandidatePracticeCatalogItem,
    CandidatePracticeCatalogResponse,
    CandidatePracticeDiscoveryResponse,
    CandidatePracticeRequestCreatePayload,
    CandidatePracticeRequestDeclinePayload,
    CandidatePracticeRequestDto,
    PracticeEligibilityDiagnosticResponse,
    PracticeEligibleJobsResponse,
    PublishPracticeCatalogPayload,
    StrictSchema,
)
from app.schemas.practice_session_contract import (
    PracticeAdvanceQuestionRequest,
    PracticeConversationAnswerRequest,
    PracticeMcqAnswerRequest,
    PracticeQuestionRetryRequest,
    PracticeReviewRetryRequest,
    PracticeSessionAbandonResponse,
    PracticeSessionDetailResponse,
    PracticeSessionStartRequest,
)
from app.services.candidate_practice_service import (
    get_candidate_practice_discovery,
    list_candidate_practice_releases_for_recruiter,
    list_eligible_jobs_for_candidate_practice,
    release_practice_to_candidate,
    revoke_candidate_practice_release,
)
from app.services.practice_session_service import (
    abandon_practice_session,
    advance_practice_question,
    get_practice_session_state,
    retry_answer_review,
    start_or_resume_practice_session,
    submit_conversational_answer,
    submit_mcq_answer,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["candidate-practice"],
)


class CreatePracticeReleaseRequest(StrictSchema):
    candidate_id: UUID
    job_id: UUID
    job_version_id: Optional[UUID] = None
    ai_set_id: Optional[UUID] = None


class CandidateScopedReleaseRequest(StrictSchema):
    job_id: UUID
    job_version_id: Optional[UUID] = None
    ai_set_id: Optional[UUID] = None


@router.get(
    "/recruiter/candidate-practice/candidates/{candidate_id}/eligible-jobs",
    response_model=PracticeEligibilityDiagnosticResponse,
    status_code=status.HTTP_200_OK,
    summary="List Eligible & Ineligible Practice Jobs for Candidate (Diagnostic)",
)
async def list_candidate_practice_eligible_jobs_endpoint(
    candidate_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> PracticeEligibilityDiagnosticResponse:
    return list_eligible_jobs_for_candidate_practice(
        actor=auth_ctx.user,
        candidate_id=candidate_id,
        db=db,
    )


@router.get(
    "/recruiter/candidate-practice/candidates/{candidate_id}/releases",
    status_code=status.HTTP_200_OK,
    summary="List Practice Releases for Candidate (Recruiter)",
)
async def list_candidate_practice_releases_endpoint(
    candidate_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    return list_candidate_practice_releases_for_recruiter(
        actor=auth_ctx.user,
        candidate_id=candidate_id,
        db=db,
    )


@router.get(
    "/recruiter/candidate-practice/candidates/{candidate_id}/requests",
    response_model=List[CandidatePracticeRequestDto],
    status_code=status.HTTP_200_OK,
    summary="List Practice Requests for Candidate (Recruiter)",
)
async def list_candidate_practice_requests_recruiter_endpoint(
    candidate_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> List[CandidatePracticeRequestDto]:
    from app.services.candidate_practice_service import list_candidate_practice_requests_for_recruiter
    return list_candidate_practice_requests_for_recruiter(
        actor=auth_ctx.user,
        candidate_id=candidate_id,
        db=db,
    )


@router.post(
    "/recruiter/candidate-practice/requests/{request_id}/decline",
    status_code=status.HTTP_200_OK,
    summary="Decline Candidate Practice Request",
)
async def decline_candidate_practice_request_endpoint(
    request_id: UUID,
    payload: Optional[CandidatePracticeRequestDeclinePayload] = None,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    from app.services.candidate_practice_service import decline_candidate_practice_request
    return decline_candidate_practice_request(
        actor=auth_ctx.user,
        request_id=request_id,
        decline_reason=payload.decline_reason if payload else None,
        db=db,
    )


@router.post(
    "/recruiter/candidate-practice/requests/{request_id}/release",
    status_code=status.HTTP_200_OK,
    summary="Release Practice From Candidate Request",
)
async def release_practice_from_request_endpoint(
    request_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    from app.services.candidate_practice_service import release_practice_from_request
    return release_practice_from_request(
        actor=auth_ctx.user,
        request_id=request_id,
        db=db,
    )


@router.post(
    "/recruiter/candidate-practice/catalog/publish",
    response_model=CandidatePracticeCatalogItem,
    status_code=status.HTTP_200_OK,
    summary="Publish Job Revision to Candidate Practice Catalog",
)
async def publish_practice_catalog_endpoint(
    payload: PublishPracticeCatalogPayload,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> CandidatePracticeCatalogItem:
    from app.services.candidate_practice_service import publish_job_revision_to_catalog
    return publish_job_revision_to_catalog(
        actor=auth_ctx.user,
        payload=payload,
        db=db,
    )


@router.post(
    "/recruiter/candidate-practice/candidates/{candidate_id}/release",
    status_code=status.HTTP_200_OK,
    summary="Release Practice Interview to Candidate (Scoped)",
)
async def release_practice_to_candidate_endpoint(
    candidate_id: UUID,
    payload: CandidateScopedReleaseRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    return release_practice_to_candidate(
        actor=auth_ctx.user,
        candidate_id=candidate_id,
        job_id=payload.job_id,
        job_version_id=payload.job_version_id,
        ai_set_id=payload.ai_set_id,
        db=db,
    )


@router.post(
    "/recruiter/candidate-practice/releases/{release_id}/revoke",
    status_code=status.HTTP_200_OK,
    summary="Revoke Practice Release",
)
async def revoke_candidate_practice_release_endpoint(
    release_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    return revoke_candidate_practice_release(
        actor=auth_ctx.user,
        release_id=release_id,
        db=db,
    )


# --- Candidate Practice Catalog & Request Endpoints ---

@router.get(
    "/candidate/practice/catalog",
    response_model=CandidatePracticeCatalogResponse,
    status_code=status.HTTP_200_OK,
    summary="Candidate Practice Catalog",
    description="Returns published practice job opportunities available to the candidate's organization.",
)
async def get_candidate_practice_catalog_endpoint(
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidatePracticeCatalogResponse:
    from app.services.candidate_practice_service import get_candidate_practice_catalog
    return get_candidate_practice_catalog(
        candidate_user=auth_ctx.user,
        db=db,
    )


@router.post(
    "/candidate/practice/requests",
    response_model=CandidatePracticeRequestDto,
    status_code=status.HTTP_200_OK,
    summary="Create Candidate Practice Request",
    description="Submits a request by the candidate for practice access to a published job revision.",
)
async def create_candidate_practice_request_endpoint(
    payload: CandidatePracticeRequestCreatePayload,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> CandidatePracticeRequestDto:
    from app.services.candidate_practice_service import create_candidate_practice_request
    return create_candidate_practice_request(
        candidate_user=cand_ctx.user,
        job_version_id=payload.job_version_id,
        db=db,
    )


@router.post(
    "/candidate/practice/requests/{request_id}/withdraw",
    status_code=status.HTTP_200_OK,
    summary="Withdraw Candidate Practice Request",
)
@router.delete(
    "/candidate/practice/requests/{request_id}/withdraw",
    status_code=status.HTTP_200_OK,
    summary="Withdraw Candidate Practice Request (Delete alias)",
)
async def withdraw_candidate_practice_request_endpoint(
    request_id: UUID,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    from app.services.candidate_practice_service import withdraw_candidate_practice_request
    return withdraw_candidate_practice_request(
        candidate_user=cand_ctx.user,
        request_id=request_id,
        db=db,
    )


@router.get(
    "/candidate/practice/requests",
    response_model=List[CandidatePracticeRequestDto],
    status_code=status.HTTP_200_OK,
    summary="List Candidate's Own Practice Requests",
)
async def list_candidate_own_practice_requests_endpoint(
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> List[CandidatePracticeRequestDto]:
    from app.services.candidate_practice_service import list_candidate_practice_requests_for_candidate
    return list_candidate_practice_requests_for_candidate(
        candidate_user=auth_ctx.user,
        db=db,
    )


@router.get(
    "/candidate/practice/releases",
    response_model=CandidatePracticeDiscoveryResponse,
    status_code=status.HTTP_200_OK,
    summary="Candidate Released Practice Sessions",
    description="Returns released practice sessions available to the candidate.",
)
async def get_candidate_practice_releases_endpoint(
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidatePracticeDiscoveryResponse:
    return get_candidate_practice_discovery(
        candidate_user=auth_ctx.user,
        db=db,
    )


# --- Legacy / Generic Aliases ---
@router.post(
    "/recruiter/practice-releases",
    status_code=status.HTTP_200_OK,
    summary="Release Practice Interview to Candidate (Legacy)",
)
async def create_practice_release_endpoint(
    payload: CreatePracticeReleaseRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    return release_practice_to_candidate(
        actor=auth_ctx.user,
        candidate_id=payload.candidate_id,
        job_id=payload.job_id,
        ai_set_id=payload.ai_set_id,
        db=db,
    )


@router.get(
    "/recruiter/practice-releases/{candidate_id}",
    status_code=status.HTTP_200_OK,
    summary="List Practice Releases for Candidate (Legacy)",
)
async def list_practice_releases_legacy_endpoint(
    candidate_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    return list_candidate_practice_releases_for_recruiter(
        actor=auth_ctx.user,
        candidate_id=candidate_id,
        db=db,
    )


@router.post(
    "/recruiter/practice-releases/{release_id}/revoke",
    status_code=status.HTTP_200_OK,
    summary="Revoke Practice Release (Legacy)",
)
async def revoke_practice_release_legacy_endpoint(
    release_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    return revoke_candidate_practice_release(
        actor=auth_ctx.user,
        release_id=release_id,
        db=db,
    )


@router.get(
    "/candidate/practice/jobs",
    response_model=CandidatePracticeDiscoveryResponse,
    status_code=status.HTTP_200_OK,
    summary="Candidate Practice Discovery",
    description="Returns active practice interview jobs released to the authenticated candidate.",
)
@router.get(
    "/candidate/practice-jobs",
    response_model=CandidatePracticeDiscoveryResponse,
    status_code=status.HTTP_200_OK,
    summary="Candidate Practice Discovery (Alias)",
    description="Returns active practice interview jobs released to the authenticated candidate.",
)
async def get_candidate_practice_jobs_endpoint(
    auth_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidatePracticeDiscoveryResponse:
    return get_candidate_practice_discovery(
        candidate_user=auth_ctx.user,
        db=db,
    )


# --- Phase 3F.3: Candidate Practice Execution Endpoints ---


@router.post(
    "/candidate/practice-sessions/start",
    response_model=PracticeSessionDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Start or Resume Practice Session",
    description="Initializes or resumes an active live practice session with live AI generated questions.",
)
async def start_practice_session_endpoint(
    payload: PracticeSessionStartRequest,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> PracticeSessionDetailResponse:
    return await start_or_resume_practice_session(
        candidate_user=cand_ctx.user,
        release_id=payload.release_id,
        db=db,
    )


@router.get(
    "/candidate/practice-sessions/{session_id}",
    response_model=PracticeSessionDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Practice Session State",
    description="Retrieves current session state, messages, and active question.",
)
async def get_practice_session_endpoint(
    session_id: UUID,
    cand_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> PracticeSessionDetailResponse:
    return get_practice_session_state(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        db=db,
    )


@router.post(
    "/candidate/practice-sessions/{session_id}/answer-conversation",
    response_model=PracticeSessionDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit Conversational Answer",
    description="Submits a candidate conversational answer, persists it immediately, and generates next question or final evaluation.",
)
async def answer_conversation_endpoint(
    session_id: UUID,
    payload: PracticeConversationAnswerRequest,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> PracticeSessionDetailResponse:
    return await submit_conversational_answer(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        payload=payload,
        db=db,
    )


@router.post(
    "/candidate/practice-sessions/{session_id}/retry-question",
    response_model=PracticeSessionDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Retry Live Question Generation",
    description="Retries AI question generation on failure without skipping or inserting deterministic templates.",
)
async def retry_question_endpoint(
    session_id: UUID,
    payload: PracticeQuestionRetryRequest,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> PracticeSessionDetailResponse:
    from app.services.practice_session_service import retry_question_generation
    return await retry_question_generation(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        payload=payload,
        db=db,
    )


@router.post(
    "/candidate/practice-sessions/{session_id}/retry-final-review",
    response_model=PracticeSessionDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Retry Final AI Interview Review",
    description="Retries final evaluation on complete transcript on failure.",
)
async def retry_final_review_endpoint(
    session_id: UUID,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> PracticeSessionDetailResponse:
    from app.services.practice_session_service import retry_final_review
    return await retry_final_review(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        db=db,
    )


@router.post(
    "/candidate/practice-sessions/{session_id}/answer-mcq",
    response_model=PracticeSessionDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit MCQ Answer",
    description="Records deterministic candidate MCQ answer and advances to next MCQ or completes practice.",
)
async def answer_mcq_endpoint(
    session_id: UUID,
    payload: PracticeMcqAnswerRequest,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> PracticeSessionDetailResponse:
    return submit_mcq_answer(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        payload=payload,
        db=db,
    )


@router.post(
    "/candidate/practice-sessions/{session_id}/retry-review",
    response_model=PracticeSessionDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Retry Conversational Review",
    description="Retries review generation on a previously saved answer without modifying the answer text.",
)
async def retry_review_endpoint(
    session_id: UUID,
    payload: PracticeReviewRetryRequest,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> PracticeSessionDetailResponse:
    return await retry_answer_review(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        payload=payload,
        db=db,
    )


@router.post(
    "/candidate/practice-sessions/{session_id}/advance-question",
    response_model=PracticeSessionDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Advance to Next Question",
    description="Advances to the next primary question or completes conversational section without generating another follow-up.",
)
async def advance_question_endpoint(
    session_id: UUID,
    payload: Optional[PracticeAdvanceQuestionRequest] = None,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> PracticeSessionDetailResponse:
    return advance_practice_question(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        db=db,
    )


@router.post(
    "/candidate/practice-sessions/{session_id}/abandon",
    response_model=PracticeSessionAbandonResponse,
    status_code=status.HTTP_200_OK,
    summary="Abandon Practice Session",
    description="Marks an in-progress practice session as abandoned.",
)
async def abandon_practice_session_endpoint(
    session_id: UUID,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> PracticeSessionAbandonResponse:
    return abandon_practice_session(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        db=db,
    )


@router.get(
    "/candidate/practice-sessions/{session_id}/report",
    status_code=status.HTTP_200_OK,
    summary="Get or Generate Practice Coaching Report",
    description="Retrieves the completed practice coaching report or generates it from validated reviews and deterministic aggregates.",
)
async def get_practice_report_endpoint(
    session_id: UUID,
    cand_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
):
    from app.services.practice_session_service import get_or_generate_practice_report
    return await get_or_generate_practice_report(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        db=db,
    )


@router.get(
    "/recruiter/candidate-practice/sessions/{session_id}/report",
    status_code=status.HTTP_200_OK,
    summary="Get Recruiter Structured Practice Assessment Report",
    description="Retrieves the recruiter-facing structured audit report with per-criterion citations, uncertainty codes, and human review status.",
)
async def get_recruiter_practice_report_endpoint(
    session_id: UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
):
    from app.services.answer_assessment_service import process_and_persist_structured_session_assessment
    from app.models.candidate_access import CandidatePracticeSessionRecord
    
    stmt = select(CandidatePracticeSessionRecord).where(
        CandidatePracticeSessionRecord.session_id == session_id,
        CandidatePracticeSessionRecord.organization_id == auth_ctx.user.organization_id,
    )
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Practice session not found."},
        )
    
    _, recruiter_dto = await process_and_persist_structured_session_assessment(session=session, db=db)
    return recruiter_dto


@router.post(
    "/candidate/practice-sessions/{session_id}/retry-report",
    status_code=status.HTTP_200_OK,
    summary="Retry Practice Coaching Report Summary",
    description="Retries AI coaching summary generation for a completed practice report without duplicating data.",
)
async def retry_practice_report_endpoint(
    session_id: UUID,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
):
    from app.services.practice_session_service import retry_practice_report
    return await retry_practice_report(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        db=db,
    )


# ==========================================
# Phase 4C.2 & 4D.3: Official Interview Endpoints
# ==========================================

from fastapi import File, Response, UploadFile
from app.schemas.candidate_interview import (
    CandidateInterviewCapabilityResponse,
    CandidateInterviewSessionResponse,
    CandidateSaveAnswerRequest,
    CandidateSaveAnswerResponse,
    CandidateSubmitInterviewRequest,
    CandidateSubmitInterviewResponse,
    CandidateTranscriptionResponse,
)
from app.services.candidate_interview_service import (
    get_interview_capabilities,
    get_interview_session,
    get_question_audio,
    save_interview_answer,
    start_or_resume_interview_session,
    submit_interview_session,
    transcribe_candidate_question_audio,
)


@router.get(
    "/candidate/interviews/capabilities",
    response_model=CandidateInterviewCapabilityResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Interview Capabilities",
    description="Returns candidate-safe speech capabilities and constraints for local audio interview sessions.",
)
async def get_interview_capabilities_endpoint(
    cand_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
) -> CandidateInterviewCapabilityResponse:
    return get_interview_capabilities()


@router.post(
    "/candidate/interviews/{release_id}/start",
    response_model=CandidateInterviewSessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Start or Resume Official Interview Session",
    description="Starts or resumes an official interview session for an approved, candidate-owned assignment release.",
)
async def start_or_resume_interview_session_endpoint(
    release_id: UUID,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> CandidateInterviewSessionResponse:
    return start_or_resume_interview_session(
        candidate_user=cand_ctx.user,
        release_id=release_id,
        db=db,
    )


@router.get(
    "/candidate/interviews/sessions/{session_id}",
    response_model=CandidateInterviewSessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Official Interview Session",
    description="Retrieves the current official interview session state and saved answers for the authenticated candidate.",
)
async def get_interview_session_endpoint(
    session_id: UUID,
    cand_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> CandidateInterviewSessionResponse:
    return get_interview_session(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        db=db,
    )


@router.get(
    "/candidate/interviews/sessions/{session_id}/questions/{slot_id}/audio",
    status_code=status.HTTP_200_OK,
    summary="Get Question Audio Speech",
    description="Synthesizes or retrieves Piper TTS question audio for an active candidate Audio interview session.",
)
async def get_question_audio_endpoint(
    session_id: UUID,
    slot_id: str,
    cand_ctx: CandidateAuthenticatedContext = Depends(require_candidate),
    db: Session = Depends(get_db),
) -> Response:
    audio_bytes = get_question_audio(
        session_id=session_id,
        slot_id=slot_id,
        candidate_user=cand_ctx.user,
        db=db,
    )
    return Response(
        content=audio_bytes,
        media_type="audio/wav",
        headers={
            "Cache-Control": "private, no-cache, no-store, must-revalidate",
            "Content-Disposition": f'inline; filename="question_{slot_id}.wav"',
        },
    )


@router.post(
    "/candidate/interviews/sessions/{session_id}/questions/{slot_id}/transcribe",
    response_model=CandidateTranscriptionResponse,
    status_code=status.HTTP_200_OK,
    summary="Transcribe Question Audio Ephemerally",
    description="Ephemerally converts and transcribes candidate speech via whisper.cpp. Zero retention, never persisted to database.",
)
async def transcribe_question_audio_endpoint(
    session_id: UUID,
    slot_id: str,
    file: UploadFile = File(...),
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> CandidateTranscriptionResponse:
    content_type = file.content_type or "audio/webm"
    audio_bytes = await file.read()
    return transcribe_candidate_question_audio(
        session_id=session_id,
        slot_id=slot_id,
        audio_bytes=audio_bytes,
        original_content_type=content_type,
        candidate_user=cand_ctx.user,
        db=db,
    )


@router.put(
    "/candidate/interviews/sessions/{session_id}/answers/{slot_id}",
    response_model=CandidateSaveAnswerResponse,
    status_code=status.HTTP_200_OK,
    summary="Save Official Interview Answer",
    description="Persists or updates a candidate's answer for a question slot in an in-progress official interview session.",
)
async def save_interview_answer_endpoint(
    session_id: UUID,
    slot_id: str,
    payload: CandidateSaveAnswerRequest,
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> CandidateSaveAnswerResponse:
    return save_interview_answer(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        slot_id=slot_id,
        payload=payload,
        db=db,
    )


@router.post(
    "/candidate/interviews/sessions/{session_id}/submit",
    response_model=CandidateSubmitInterviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit Official Interview Session",
    description="Atomically submits and locks an official interview session.",
)
async def submit_interview_session_endpoint(
    session_id: UUID,
    payload: CandidateSubmitInterviewRequest = CandidateSubmitInterviewRequest(),
    cand_ctx: CandidateAuthenticatedContext = Depends(verify_candidate_csrf_and_origin),
    db: Session = Depends(get_db),
) -> CandidateSubmitInterviewResponse:
    return submit_interview_session(
        candidate_user=cand_ctx.user,
        session_id=session_id,
        payload=payload,
        db=db,
    )
