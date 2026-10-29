"""API endpoints for Phase 3D.5 Dedicated AI Interview Questions Workspace."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedContext, require_recruiter
from app.database import get_db
from app.schemas.ai_question_set import (
    AiQuestionSetApproveResponse,
    AiQuestionSetDetailResponse,
    AiQuestionSetDirectoryResponse,
    AiQuestionSetGenerateRequest,
    AiQuestionSetValidateResponse,
    AiQuestionSlotUpdateRequest,
    EligibleJobsResponse,
)
from app.schemas.mcq_contract import (
    McqCreateRequest,
    McqGenerateRequest,
    McqReorderRequest,
    McqUpdateRequest,
)
from app.services.ai_question_set_service import (
    add_manual_mcq_slot,
    approve_ai_question_set,
    generate_ai_questions_by_job_id,
    generate_mcq_slots_for_template,
    generate_or_open_ai_question_set,
    get_ai_question_set_by_template,
    get_eligible_jobs_for_ai_questions,
    list_ai_question_sets,
    regenerate_ai_question_slot,
    remove_mcq_slot,
    remove_slot,
    reorder_mcq_slots,
    update_ai_question_slot,
    update_mcq_slot,
    validate_ai_question_set,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["ai-interviews"],
)


from app.schemas.phase3_enums import InterviewMode


@router.get(
    "/ai-interviews/eligible-jobs",
    response_model=EligibleJobsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Eligible Jobs for AI Question Generation",
    description="Returns organization/recruiter-scoped jobs with requirement counts only, preserving zero-leakage security boundary.",
)
@router.get(
    "/ai-question-sets/eligible-jobs",
    response_model=EligibleJobsResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def get_eligible_jobs_endpoint(
    mode: InterviewMode = Query(default=InterviewMode.TEXT),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> EligibleJobsResponse:
    return get_eligible_jobs_for_ai_questions(
        actor=auth_ctx.user,
        db=db,
        mode=mode,
    )


@router.post(
    "/ai-interviews/generate",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate AI Question Set by Job ID",
    description="Server-authoritative generation of AI questions using approved job revision and deterministic blueprint.",
)
@router.post(
    "/ai-question-sets/generate",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def generate_ai_questions_by_job_endpoint(
    payload: AiQuestionSetGenerateRequest,
    force_regenerate: bool = Query(default=False),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDetailResponse:
    return await generate_ai_questions_by_job_id(
        actor=auth_ctx.user,
        job_id=payload.job_id,
        force_regenerate=force_regenerate,
        db=db,
        interview_mode=payload.interview_mode,
    )


@router.get(
    "/ai-interviews",
    response_model=AiQuestionSetDirectoryResponse,
    status_code=status.HTTP_200_OK,
    summary="List AI Question Sets",
    description="Lists organization-scoped AI interview question sets with filter, search, and status counts.",
)
@router.get(
    "/ai-question-sets",
    response_model=AiQuestionSetDirectoryResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def list_ai_interviews_endpoint(
    search: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None),
    mode: Optional[InterviewMode] = Query(default=None),
    mode_filter: Optional[InterviewMode] = Query(default=None),
    sort: str = Query(default="updated_at"),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDirectoryResponse:
    effective_status = status_filter or status
    effective_mode = mode_filter or mode
    return list_ai_question_sets(
        actor=auth_ctx.user,
        db=db,
        search=search,
        status_filter=effective_status,
        mode_filter=effective_mode,
        sort_by=sort,
    )


@router.get(
    "/ai-interviews/{template_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get AI Question Set For Template",
    description="Retrieves the detailed AI question set for an authorized template. Strictly omits deterministic question text.",
)
@router.get(
    "/ai-question-sets/{template_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.get(
    "/templates/{template_id}/ai-question-set",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def get_ai_interview_set_endpoint(
    template_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDetailResponse:
    return get_ai_question_set_by_template(
        actor=auth_ctx.user,
        template_id=template_id,
        db=db,
    )


@router.post(
    "/jobs/{job_id}/interview-templates/{template_id}/generate-ai-questions",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate or Open AI Question Set",
    description="Generates or opens the dedicated AI question set grounded in the deterministic blueprint.",
)
@router.post(
    "/jobs/{job_id}/templates/{template_id}/ai-question-sets/generate",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.post(
    "/jobs/{job_id}/interview-templates/{template_id}/ai-question-sets/generate",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def generate_or_open_ai_questions_endpoint(
    job_id: uuid.UUID,
    template_id: uuid.UUID,
    force_regenerate: bool = Query(default=False),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDetailResponse:
    return await generate_or_open_ai_question_set(
        actor=auth_ctx.user,
        job_id=job_id,
        template_id=template_id,
        force_regenerate=force_regenerate,
        db=db,
    )


@router.patch(
    "/ai-interviews/{template_id}/items/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Update AI Question Slot",
    description="Records a review decision (keep, edit, manual author, or revise) for a single question slot.",
)
@router.patch(
    "/ai-question-sets/{template_id}/items/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.patch(
    "/templates/{template_id}/slots/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def update_ai_question_slot_endpoint(
    template_id: uuid.UUID,
    slot_id: uuid.UUID,
    payload: AiQuestionSlotUpdateRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDetailResponse:
    return update_ai_question_slot(
        actor=auth_ctx.user,
        template_id=template_id,
        slot_id=slot_id,
        payload=payload,
        db=db,
    )


@router.delete(
    "/ai-interviews/{template_id}/items/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Question Slot from AI Question Set",
    description="Removes any question slot (conversational or MCQ) from the AI question set and database.",
)
@router.delete(
    "/ai-interviews/{template_id}/slots/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.delete(
    "/ai-question-sets/{template_id}/items/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.delete(
    "/templates/{template_id}/slots/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def remove_ai_question_slot_endpoint(
    template_id: uuid.UUID,
    slot_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDetailResponse:
    return remove_slot(
        actor=auth_ctx.user,
        template_id=template_id,
        slot_id=slot_id,
        db=db,
    )


@router.post(
    "/ai-interviews/{template_id}/regenerate-slot/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Regenerate Single Question Slot",
    description="Regenerates AI question wording for a single blueprint slot using the local provider.",
)
@router.post(
    "/ai-question-sets/{template_id}/regenerate-slot/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.post(
    "/templates/{template_id}/slots/{slot_id}/regenerate",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def regenerate_ai_question_slot_endpoint(
    template_id: uuid.UUID,
    slot_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDetailResponse:
    return await regenerate_ai_question_slot(
        actor=auth_ctx.user,
        template_id=template_id,
        slot_id=slot_id,
        db=db,
    )


@router.post(
    "/ai-interviews/{template_id}/validate",
    response_model=AiQuestionSetValidateResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate AI Question Set",
    description="Checks readiness, staleness, and safety compliance for the entire AI question set.",
)
@router.post(
    "/ai-question-sets/{template_id}/validate",
    response_model=AiQuestionSetValidateResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.post(
    "/templates/{template_id}/ai-question-set/validate",
    response_model=AiQuestionSetValidateResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def validate_ai_question_set_endpoint(
    template_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetValidateResponse:
    return validate_ai_question_set(
        actor=auth_ctx.user,
        template_id=template_id,
        db=db,
    )


@router.post(
    "/ai-interviews/{template_id}/approve",
    response_model=AiQuestionSetApproveResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve and Seal AI Question Set",
    description="Seals the content hash and marks the approved AI question set as immutable.",
)
@router.post(
    "/ai-question-sets/{template_id}/approve",
    response_model=AiQuestionSetApproveResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.post(
    "/templates/{template_id}/ai-question-set/approve",
    response_model=AiQuestionSetApproveResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def approve_ai_question_set_endpoint(
    template_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetApproveResponse:
    return approve_ai_question_set(
        actor=auth_ctx.user,
        template_id=template_id,
        db=db,
    )


@router.post(
    "/ai-interviews/{template_id}/mcqs/generate",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate Structured MCQs for AI Question Set",
    description="Generates requirement-grounded multiple-choice questions using local AI.",
)
@router.post(
    "/ai-interviews/{template_id}/generate-mcqs",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.post(
    "/ai-question-sets/{template_id}/mcqs/generate",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.post(
    "/ai-question-sets/{template_id}/generate-mcqs",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def generate_mcqs_endpoint(
    template_id: uuid.UUID,
    payload: McqGenerateRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDetailResponse:
    return await generate_mcq_slots_for_template(
        actor=auth_ctx.user,
        template_id=template_id,
        payload=payload,
        db=db,
    )


@router.post(
    "/ai-interviews/{template_id}/mcqs",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add Manual MCQ to AI Question Set",
    description="Adds a recruiter-authored multiple choice question to the question set.",
)
@router.post(
    "/ai-interviews/{template_id}/mcq-slots",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
@router.post(
    "/ai-question-sets/{template_id}/mcqs",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
@router.post(
    "/ai-question-sets/{template_id}/mcq-slots",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def add_manual_mcq_endpoint(
    template_id: uuid.UUID,
    payload: McqCreateRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDetailResponse:
    return add_manual_mcq_slot(
        actor=auth_ctx.user,
        template_id=template_id,
        payload=payload,
        db=db,
    )


@router.patch(
    "/ai-interviews/{template_id}/mcqs/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Update MCQ Slot in AI Question Set",
    description="Updates a multiple choice question's text, options, correct answer, or explanation.",
)
@router.patch(
    "/ai-interviews/{template_id}/mcq-slots/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.patch(
    "/ai-question-sets/{template_id}/mcqs/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.patch(
    "/ai-question-sets/{template_id}/mcq-slots/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def update_mcq_slot_endpoint(
    template_id: uuid.UUID,
    slot_id: uuid.UUID,
    payload: McqUpdateRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDetailResponse:
    return update_mcq_slot(
        actor=auth_ctx.user,
        template_id=template_id,
        slot_id=slot_id,
        payload=payload,
        db=db,
    )


@router.delete(
    "/ai-interviews/{template_id}/mcqs/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete MCQ Slot from AI Question Set",
    description="Removes a multiple choice question from the question set.",
)
@router.delete(
    "/ai-interviews/{template_id}/mcq-slots/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.delete(
    "/ai-question-sets/{template_id}/mcqs/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.delete(
    "/ai-question-sets/{template_id}/mcq-slots/{slot_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def remove_mcq_slot_endpoint(
    template_id: uuid.UUID,
    slot_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDetailResponse:
    return remove_mcq_slot(
        actor=auth_ctx.user,
        template_id=template_id,
        slot_id=slot_id,
        db=db,
    )


@router.post(
    "/ai-interviews/{template_id}/mcqs/reorder",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Reorder MCQ Slots in AI Question Set",
    description="Reorders multiple choice questions according to the specified slot IDs.",
)
@router.post(
    "/ai-interviews/{template_id}/reorder-mcqs",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.post(
    "/ai-question-sets/{template_id}/mcqs/reorder",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.post(
    "/ai-question-sets/{template_id}/reorder-mcqs",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def reorder_mcq_slots_endpoint(
    template_id: uuid.UUID,
    payload: McqReorderRequest,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDetailResponse:
    return reorder_mcq_slots(
        actor=auth_ctx.user,
        template_id=template_id,
        payload=payload,
        db=db,
    )


@router.post(
    "/ai-interviews/jobs/{job_id}/prepare-for-revision/{job_version_id}",
    response_model=AiQuestionSetDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Prepare Practice Set For New Revision",
    description="Copies approved questions from older revision into a new draft set for the target job revision.",
)
async def prepare_practice_set_endpoint(
    job_id: uuid.UUID,
    job_version_id: uuid.UUID,
    auth_ctx: AuthenticatedContext = Depends(require_recruiter),
    db: Session = Depends(get_db),
) -> AiQuestionSetDetailResponse:
    from app.services.ai_question_set_service import prepare_practice_set_for_revision
    return prepare_practice_set_for_revision(
        actor=auth_ctx.user,
        job_id=job_id,
        job_version_id=job_version_id,
        db=db,
    )

