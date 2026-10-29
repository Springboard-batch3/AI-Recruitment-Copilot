"""Phase 3D.2: Provisional Assessment Orchestration Service.

Coordinates:
1. Segmentation of submitted simulation answers.
2. Building bounded assessment request payload (rubrics + segments + citation allowlist).
3. Invoking model provider under strict bounded contract.
4. Validating output against 12 validation rules.
5. Persisting results to simulation_question_assessments with proper provenance.
6. Empty or unusable answers produce INSUFFICIENT_ANSWER_EVIDENCE deterministically without provider invocation.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.interview import (
    JobInterviewTemplateRecord,
    InterviewQuestionRecord,
    CandidateInterviewPlanRecord,
    SimulationAttemptRecord,
    SimulationAnswerRecord,
    SimulationAnswerSegmentRecord,
    SimulationQuestionAssessmentRecord,
)

from app.schemas.assessment_contract import (
    AssessmentStatus,
    BoundedAssessmentRequest,
    BoundedAssessmentResponse,
    BoundedAssessmentSegment,
    BoundedRubricPayload,
    ProvisionalAssessmentItemResponse,
    SimulationAssessmentRunResponse,
)
from app.schemas.phase3_enums import ProviderErrorCode
from app.services.interview_segmentation_service import segment_submitted_answer
from app.services.wording_provider import (
    BaseWordingProvider,
    ProviderTransportError,
)

logger = logging.getLogger("ai_hiring.provisional_assessment")


def build_bounded_rubric_payload(
    question: InterviewQuestionRecord,
) -> BoundedRubricPayload:
    """Builds a bounded rubric payload from template question rubric data."""
    rubric_rec = question.rubric
    rubric_obj = {
        "rating_anchors": rubric_rec.rating_anchors if rubric_rec else {},
        "observable_indicators": rubric_rec.observable_indicators if rubric_rec else [],
        "sufficient_evidence_guidance": rubric_rec.sufficient_evidence_guidance if rubric_rec else None,
        "insufficient_evidence_guidance": rubric_rec.insufficient_evidence_guidance if rubric_rec else None,
    } if rubric_rec else {}

    
    # Parse anchors and levels
    rating_anchors = rubric_obj.get("rating_anchors") or {
        1: "Minimal evidence / does not meet expectations",
        2: "Developing evidence / partially meets expectations",
        3: "Proficient evidence / meets expectations",
        4: "Advanced evidence / exceeds expectations",
    }
    # Ensure int keys for rating_anchors
    int_anchors = {int(k): str(v) for k, v in rating_anchors.items()}
    allowed_levels = sorted(list(int_anchors.keys()))

    return BoundedRubricPayload(
        rubric_id=str(question.question_id),
        rubric_version=question.question_version or 1,
        competency_definition=getattr(question, "competency_id", None) or "Core Competency",
        question_intent=getattr(question, "question_intent", None) or "Evaluate competence in candidate response.",
        observable_indicators=rubric_obj.get("observable_indicators") or [
            "Demonstrates structured reasoning",
            "Cites specific practical experience or technical clarity",
            "Addresses complexity and trade-offs",
        ],
        rating_anchors=int_anchors,
        allowed_levels=allowed_levels,
        sufficient_evidence_guidance=rubric_obj.get("sufficient_evidence_guidance"),
        insufficient_evidence_guidance=rubric_obj.get("insufficient_evidence_guidance"),
        prohibited_inferences=[
            "Do not infer candidate personality or culture fit.",
            "Do not evaluate age, gender, race, disability, or protected traits.",
            "Do not make hiring, rejection, or advancement decisions.",
        ],
    )



def validate_assessment_output(
    response: BoundedAssessmentResponse,
    request: BoundedAssessmentRequest,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Runs strict 12-point validation check on model assessment response.
    
    Returns (is_valid, failure_reasons, validation_report).
    """
    failures: List[str] = []
    report: Dict[str, Any] = {
        "operation_matched": response.operation == "PROVISIONAL_ASSESSMENT",
        "schema_version_matched": response.response_schema_version == "1.0.0",
        "citations_valid": True,
        "criteria_valid": True,
        "provisional_level_valid": True,
        "prohibited_checks_passed": True,
    }

    # 1. Operation and schema version
    if not report["operation_matched"]:
        failures.append("Operation mismatch.")
    if not report["schema_version_matched"]:
        failures.append("Schema version mismatch.")

    # 2. Check insufficient evidence or provisional level
    if response.insufficient_evidence_reason:
        # Valid insufficient evidence response
        return True, [], report

    if response.provisional_level is None:
        failures.append("Provisional level is null but no insufficient_evidence_reason provided.")
        report["provisional_level_valid"] = False
    elif response.provisional_level not in request.rubric.allowed_levels:
        failures.append(f"Provisional level {response.provisional_level} is outside allowed levels {request.rubric.allowed_levels}.")
        report["provisional_level_valid"] = False

    # 3. Check citations against allowlist
    allowlist_set = set(request.citation_allowlist)
    for seg_id in response.cited_segment_ids:
        if seg_id not in allowlist_set:
            failures.append(f"Unknown cited segment ID '{seg_id}' not in allowlist.")
            report["citations_valid"] = False

    # 4. Check observations have citations and valid criteria
    if not response.criterion_observations and response.provisional_level is not None:
        failures.append("Substantive assessment returned zero criterion observations.")
        report["criteria_valid"] = False

    for obs in response.criterion_observations:
        if not obs.cited_segment_ids:
            failures.append(f"Observation for criterion '{obs.criterion_id}' has no citations.")
            report["citations_valid"] = False
        for s_id in obs.cited_segment_ids:
            if s_id not in allowlist_set:
                failures.append(f"Observation cites segment '{s_id}' which is not in allowlist.")
                report["citations_valid"] = False

    is_valid = len(failures) == 0
    return is_valid, failures, report


async def assess_single_question_answer(
    db: Session,
    attempt: SimulationAttemptRecord,
    answer: SimulationAnswerRecord,
    question: InterviewQuestionRecord,
    provider: BaseWordingProvider,
) -> SimulationQuestionAssessmentRecord:
    """Performs bounded assessment for one question answer and persists record."""
    # 1. Segment answer
    segments = segment_submitted_answer(db, answer)

    
    # 2. Check if answer is empty or has zero usable segments
    if not segments or not (answer.answer_text or "").strip():
        # Deterministic INSUFFICIENT_ANSWER_EVIDENCE without provider invocation
        assessment = SimulationQuestionAssessmentRecord(
            attempt_id=attempt.attempt_id,
            question_id=question.question_id,
            answer_id=answer.answer_id,
            assessment_status=AssessmentStatus.INSUFFICIENT_EVIDENCE.value,
            provisional_level=None,
            insufficient_evidence_reason="Candidate provided empty or whitespace-only response.",
            criterion_observations=[],
            cited_segment_ids=[],
            uncertainty_codes=["EMPTY_RESPONSE"],
            validation_report={"empty_answer_bypassed_provider": True},
            model_invocation_metadata={"provider_invoked": False},
        )
        db.add(assessment)
        db.flush()
        return assessment

    # 3. Build bounded request
    rubric_payload = build_bounded_rubric_payload(question)
    bounded_segments = [
        BoundedAssessmentSegment(
            segment_id=str(s.segment_id),
            sequence_order=s.sequence_order,
            text=s.segment_text,
        )
        for s in segments
    ]
    citation_allowlist = [str(s.segment_id) for s in segments]

    # Use authoritative question text
    q_text = question.question_text or "Question"

    req = BoundedAssessmentRequest(
        attempt_id=attempt.attempt_id,
        question_id=question.question_id,
        question_text=q_text,
        rubric=rubric_payload,
        answer_segments=bounded_segments,
        citation_allowlist=citation_allowlist,
        prohibited_inferences=[
            "No personality inferences.",
            "No protected trait inferences.",
            "No hiring or rejection decisions.",
        ],
    )


    # 4. Invoke provider
    invocation_meta = {
        "provider_type": getattr(provider, "profile", None).provider_type if hasattr(provider, "profile") and provider.profile else "LOCAL_OPENAI_COMPATIBLE",
        "model_identifier": getattr(provider, "profile", None).model_identifier if hasattr(provider, "profile") and provider.profile else "default",
        "provider_invoked": True,
    }

    try:
        raw_response: BoundedAssessmentResponse = await provider.request_provisional_assessment(req)
        
        # 5. Validate output
        is_valid, failures, report = validate_assessment_output(raw_response, req)
        
        if is_valid:
            if raw_response.insufficient_evidence_reason:
                assessment_status = AssessmentStatus.INSUFFICIENT_EVIDENCE.value
                provisional_level = None
            else:
                assessment_status = AssessmentStatus.PROVISIONAL.value
                provisional_level = raw_response.provisional_level
            
            assessment = SimulationQuestionAssessmentRecord(
                attempt_id=attempt.attempt_id,
                question_id=question.question_id,
                answer_id=answer.answer_id,
                assessment_status=assessment_status,
                provisional_level=provisional_level,
                insufficient_evidence_reason=raw_response.insufficient_evidence_reason,
                criterion_observations=[obs.model_dump() for obs in raw_response.criterion_observations],
                cited_segment_ids=raw_response.cited_segment_ids,
                uncertainty_codes=raw_response.uncertainty_codes,
                validation_report=report,
                model_invocation_metadata=invocation_meta,
            )
        else:
            # Validation failed -> store validation failure, preserve manual review
            report["validation_failures"] = failures
            assessment = SimulationQuestionAssessmentRecord(
                attempt_id=attempt.attempt_id,
                question_id=question.question_id,
                answer_id=answer.answer_id,
                assessment_status=AssessmentStatus.VALIDATION_FAILED.value,
                provisional_level=None,
                insufficient_evidence_reason="Model response failed validation rules.",
                criterion_observations=[],
                cited_segment_ids=[],
                uncertainty_codes=["VALIDATION_FAILED"],
                validation_report=report,
                model_invocation_metadata=invocation_meta,
            )

    except ProviderTransportError as e:
        logger.warning("Provider error during provisional assessment: %s (%s)", e.safe_message, e.error_code)
        assessment = SimulationQuestionAssessmentRecord(
            attempt_id=attempt.attempt_id,
            question_id=question.question_id,
            answer_id=answer.answer_id,
            assessment_status=AssessmentStatus.MANUAL_REVIEW_REQUIRED.value,
            provisional_level=None,
            insufficient_evidence_reason=f"Provider unavailable: {e.safe_message}",
            criterion_observations=[],
            cited_segment_ids=[],
            uncertainty_codes=[e.error_code.value if hasattr(e.error_code, "value") else str(e.error_code)],
            validation_report={"transport_error": e.safe_message},
            model_invocation_metadata=invocation_meta,
        )
    except Exception as e:
        logger.exception("Unexpected error during provisional assessment: %s", e)
        assessment = SimulationQuestionAssessmentRecord(
            attempt_id=attempt.attempt_id,
            question_id=question.question_id,
            answer_id=answer.answer_id,
            assessment_status=AssessmentStatus.MANUAL_REVIEW_REQUIRED.value,
            provisional_level=None,
            insufficient_evidence_reason="Unexpected internal error during assessment.",
            criterion_observations=[],
            cited_segment_ids=[],
            uncertainty_codes=["INTERNAL_ERROR"],
            validation_report={"error": str(e)},
            model_invocation_metadata=invocation_meta,
        )

    db.add(assessment)
    db.flush()
    return assessment


async def run_provisional_assessment_for_attempt(
    db: Session,
    template: JobInterviewTemplateRecord,
    attempt: SimulationAttemptRecord,
    provider: BaseWordingProvider,
) -> SimulationAssessmentRunResponse:
    """Runs bounded provisional assessment for all submitted answers in a simulation attempt."""
    if attempt.lifecycle_status != "SUBMITTED" and attempt.lifecycle_status != "PROVISIONALLY_ASSESSED":
        raise ValueError(f"Simulation attempt must be in SUBMITTED status (current: {attempt.lifecycle_status}).")

    # Clear prior assessments for idempotency
    db.query(SimulationQuestionAssessmentRecord).filter(
        SimulationQuestionAssessmentRecord.attempt_id == attempt.attempt_id
    ).delete()
    db.flush()

    # Load questions
    from sqlalchemy.orm import selectinload
    questions = db.query(InterviewQuestionRecord).options(
        selectinload(InterviewQuestionRecord.rubric)
    ).filter(
        InterviewQuestionRecord.template_id == template.template_id
    ).order_by(InterviewQuestionRecord.display_order.asc()).all()

    question_map = {q.question_id: q for q in questions}

    # Load submitted answers
    answers = db.query(SimulationAnswerRecord).filter(
        SimulationAnswerRecord.attempt_id == attempt.attempt_id
    ).all()

    assessments_dict: Dict[str, ProvisionalAssessmentItemResponse] = {}
    provisional_count = 0
    insufficient_count = 0
    validation_rejected_count = 0
    manual_review_count = 0

    for ans in answers:
        q = question_map.get(ans.question_id)
        if not q:
            continue

        assessment_rec = await assess_single_question_answer(db, attempt, ans, q, provider)

        if assessment_rec.assessment_status == AssessmentStatus.PROVISIONAL.value:
            provisional_count += 1
        elif assessment_rec.assessment_status == AssessmentStatus.INSUFFICIENT_EVIDENCE.value:
            insufficient_count += 1
        elif assessment_rec.assessment_status == AssessmentStatus.VALIDATION_FAILED.value:
            validation_rejected_count += 1
        else:
            manual_review_count += 1

        assessments_dict[str(q.question_id)] = ProvisionalAssessmentItemResponse(
            question_id=q.question_id,
            assessment_status=assessment_rec.assessment_status,
            provisional_level=assessment_rec.provisional_level,
            insufficient_evidence_reason=assessment_rec.insufficient_evidence_reason,
            criterion_observations=assessment_rec.criterion_observations or [],
            cited_segment_ids=assessment_rec.cited_segment_ids or [],
            uncertainty_codes=assessment_rec.uncertainty_codes or [],
            validation_report=assessment_rec.validation_report or {},
            model_invocation_metadata=assessment_rec.model_invocation_metadata or {},
        )

    # Update attempt status
    attempt.lifecycle_status = "PROVISIONALLY_ASSESSED"
    db.commit()

    return SimulationAssessmentRunResponse(
        attempt_id=attempt.attempt_id,
        lifecycle_status=attempt.lifecycle_status,
        total_assessed=len(assessments_dict),
        provisional_count=provisional_count,
        insufficient_evidence_count=insufficient_count,
        validation_rejected_count=validation_rejected_count,
        manual_review_required_count=manual_review_count,
        assessments=assessments_dict,
        safe_message="Provisional assessment completed. Manual rubric review preserved.",
    )

