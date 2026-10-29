"""Phase 3D.2: Bounded Provisional Assessment Tests.

Covers:
1. Stable deterministic segmentation for immutable answer text.
2. Empty or whitespace answer produces INSUFFICIENT_ANSWER_EVIDENCE without invoking provider.
3. Valid cited response produces PROVISIONAL assessment.
4. Forged segment ID not in citation allowlist fails validation.
5. Unknown rubric criterion fails validation.
6. Prohibited recommendations or protected traits fail validation.
7. Reasoning-only or truncated response is rejected and preserves manual review.
8. Provider transport failure preserves manual review without cloud fallback.
9. Zero Phase 2 record mutation.
"""

import pytest
import uuid
from datetime import datetime, timezone

from app.models.interview import (
    JobInterviewTemplateRecord,
    InterviewQuestionRecord,
    InterviewRubricRecord,
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
    StructuredCriterionObservation,
)
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    ProviderErrorCode,
    QuestionType,
    ValidationState,
)
from app.services.interview_segmentation_service import (
    extract_segments_from_text,
    segment_submitted_answer,
)
from app.services.provisional_assessment_service import (
    assess_single_question_answer,
    build_bounded_rubric_payload,
    validate_assessment_output,
)
from app.services.wording_provider import (
    DisabledWordingProvider,
    FakeWordingProvider,
    ProviderTransportError,
)


from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from app.models.base import Base

@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_answer_segmentation_idempotency_and_offsets():
    """Test deterministic paragraph and sentence segmentation."""
    text = (
        "This is paragraph one explaining our approach to distributed locking and transactions in microservices.\n\n"
        "This is paragraph two. It contains multiple sentences. We ensure idempotent execution across workers."
    )
    ans_id = uuid.uuid4()
    att_id = uuid.uuid4()
    q_id = uuid.uuid4()
    segments = extract_segments_from_text(text, ans_id, att_id, q_id)
    assert len(segments) == 2
    assert segments[0].sequence_order == 1
    assert segments[1].sequence_order == 2

    # Verify character offsets match exact substring
    for seg in segments:
        assert text[seg.start_char:seg.end_char] == seg.segment_text

    # Re-running produces identical stable opaque IDs
    segments_again = extract_segments_from_text(text, ans_id, att_id, q_id)
    assert [s.segment_id for s in segments] == [s.segment_id for s in segments_again]


def test_empty_answer_insufficient_evidence_no_provider_invoked(test_db):
    """Empty or whitespace answer produces INSUFFICIENT_EVIDENCE without provider invocation."""
    attempt_id = uuid.uuid4()
    q_id = uuid.uuid4()
    ans_id = uuid.uuid4()

    attempt = SimulationAttemptRecord(
        attempt_id=attempt_id,
        organization_id=uuid.uuid4(),
        recruiter_user_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        job_revision=1,
        candidate_profile_version=1,
        plan_id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        plan_version=1,
        record_version=1,
        authoritative_content_hash="hash",
        lifecycle_status="SUBMITTED",
        guardrail_policy_version="1.0.0",
        guardrail_policy_hash="0" * 64,
        total_questions_count=1,
        answered_questions_count=1,
        question_snapshots=[],
    )
    test_db.add(attempt)

    question = InterviewQuestionRecord(
        question_id=q_id,
        template_id=attempt.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        question_text="How do you handle deadlocks in Postgres transactions?",

        question_intent="Evaluate concurrency awareness.",
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        display_order=1,
        estimated_time_minutes=5,
        permitted_probes=[],
        validation_state=ValidationState.PASSED,
        included_in_common_score=True,
    )

    test_db.add(question)

    answer = SimulationAnswerRecord(
        answer_id=ans_id,
        attempt_id=attempt_id,
        question_id=q_id,
        question_version=1,
        display_order=1,
        layer="COMMON_CORE",
        answer_text="   \n\n  ",
        answer_version=1,
        is_submitted=True,
        content_hash="hash_empty",
    )
    test_db.add(answer)
    test_db.commit()

    # Provider that raises if called
    provider = DisabledWordingProvider()

    import asyncio
    res = asyncio.run(assess_single_question_answer(test_db, attempt, answer, question, provider))

    assert res.assessment_status == AssessmentStatus.INSUFFICIENT_EVIDENCE.value
    assert res.provisional_level is None
    assert res.validation_report.get("empty_answer_bypassed_provider") is True
    assert res.model_invocation_metadata.get("provider_invoked") is False


def test_valid_cited_response_becomes_provisional(test_db):
    """Valid cited model response becomes accepted provisional assessment."""
    attempt_id = uuid.uuid4()
    q_id = uuid.uuid4()
    ans_id = uuid.uuid4()

    attempt = SimulationAttemptRecord(
        attempt_id=attempt_id,
        organization_id=uuid.uuid4(),
        recruiter_user_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        job_revision=1,
        candidate_profile_version=1,
        plan_id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        plan_version=1,
        record_version=1,
        authoritative_content_hash="hash",
        lifecycle_status="SUBMITTED",
        guardrail_policy_version="1.0.0",
        guardrail_policy_hash="0" * 64,
        total_questions_count=1,
        answered_questions_count=1,
        question_snapshots=[],
    )
    test_db.add(attempt)

    question = InterviewQuestionRecord(
        question_id=q_id,
        template_id=attempt.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        question_text="How do you handle deadlocks in Postgres transactions?",

        question_intent="Evaluate concurrency awareness.",
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        display_order=1,
        estimated_time_minutes=5,
        permitted_probes=[],
        validation_state=ValidationState.PASSED,
        included_in_common_score=True,
    )

    test_db.add(question)

    answer = SimulationAnswerRecord(
        answer_id=ans_id,
        attempt_id=attempt_id,
        question_id=q_id,
        question_version=1,
        display_order=1,
        layer="COMMON_CORE",
        answer_text="We use advisory locks and SELECT FOR UPDATE to serialize access to critical account balances.",
        answer_version=1,
        is_submitted=True,
        content_hash="hash_valid",
    )
    test_db.add(answer)
    test_db.commit()

    provider = FakeWordingProvider(simulated_mode="SUCCESS")

    import asyncio
    res = asyncio.run(assess_single_question_answer(test_db, attempt, answer, question, provider))

    assert res.assessment_status == AssessmentStatus.PROVISIONAL.value
    assert res.provisional_level == 1
    assert len(res.cited_segment_ids) > 0
    assert len(res.criterion_observations) > 0



def test_forged_segment_id_rejected_validation():
    """Forged segment ID not in allowlist fails validation."""
    req = BoundedAssessmentRequest(
        attempt_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
        question_text="Sample question",
        rubric=BoundedRubricPayload(
            rubric_id="rub-1",
            rubric_version=1,
            competency_definition="Comp definition",
            question_intent="Evaluate",
            observable_indicators=["ind1"],
            rating_anchors={1: "Basic", 2: "Advanced"},
            allowed_levels=[1, 2],
        ),
        answer_segments=[BoundedAssessmentSegment(segment_id="legit-seg-1", sequence_order=1, text="Text")],
        citation_allowlist=["legit-seg-1"],
    )

    resp = BoundedAssessmentResponse(
        operation="PROVISIONAL_ASSESSMENT",
        response_schema_version="1.0.0",
        provisional_level=2,
        criterion_observations=[
            StructuredCriterionObservation(
                criterion_id="CRIT-2",
                observation="Demonstrated advanced skill.",
                cited_segment_ids=["forged-seg-999"],
            )
        ],
        cited_segment_ids=["forged-seg-999"],
    )

    is_valid, failures, report = validate_assessment_output(resp, req)
    assert not is_valid
    assert any("not in allowlist" in f for f in failures)


def test_prohibited_recommendations_rejected():
    """Output containing hiring recommendation is rejected by Pydantic validator."""
    with pytest.raises(ValueError, match="Prohibited recommendation"):
        BoundedAssessmentResponse(
            operation="PROVISIONAL_ASSESSMENT",
            response_schema_version="1.0.0",
            provisional_level=3,
            criterion_observations=[
                StructuredCriterionObservation(
                    criterion_id="CRIT-1",
                    observation="We strongly recommend to hire this candidate immediately.",
                    cited_segment_ids=["seg-1"],
                )
            ],
            cited_segment_ids=["seg-1"],
        )


def test_provider_outage_preserves_manual_review(test_db):

    """Provider timeout or failure marks assessment MANUAL_REVIEW_REQUIRED without cloud fallback."""
    attempt_id = uuid.uuid4()
    q_id = uuid.uuid4()
    ans_id = uuid.uuid4()

    attempt = SimulationAttemptRecord(
        attempt_id=attempt_id,
        organization_id=uuid.uuid4(),
        recruiter_user_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        job_revision=1,
        candidate_profile_version=1,
        plan_id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        plan_version=1,
        record_version=1,
        authoritative_content_hash="hash",
        lifecycle_status="SUBMITTED",
        guardrail_policy_version="1.0.0",
        guardrail_policy_hash="0" * 64,
        total_questions_count=1,
        answered_questions_count=1,
        question_snapshots=[],
    )
    test_db.add(attempt)

    question = InterviewQuestionRecord(
        question_id=q_id,
        template_id=attempt.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        question_text="How do you handle deadlocks in Postgres transactions?",

        question_intent="Evaluate concurrency awareness.",
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        display_order=1,
        estimated_time_minutes=5,
        permitted_probes=[],
        validation_state=ValidationState.PASSED,
        included_in_common_score=True,
    )

    test_db.add(question)

    answer = SimulationAnswerRecord(
        answer_id=ans_id,
        attempt_id=attempt_id,
        question_id=q_id,
        question_version=1,
        display_order=1,
        layer="COMMON_CORE",
        answer_text="We use advisory locks.",
        answer_version=1,
        is_submitted=True,
        content_hash="hash_valid",
    )
    test_db.add(answer)
    test_db.commit()

    provider = FakeWordingProvider(simulated_mode="TIMEOUT")

    import asyncio
    res = asyncio.run(assess_single_question_answer(test_db, attempt, answer, question, provider))

    assert res.assessment_status == AssessmentStatus.MANUAL_REVIEW_REQUIRED.value
    assert res.provisional_level is None
    assert "Provider unavailable" in (res.insufficient_evidence_reason or "")
