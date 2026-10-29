"""Focused tests for Phase 3G.3 Candidate Practice Final Coaching Summary and Report."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.auth import UserRecord, UserRole
from app.models.base import Base
from app.models.candidate import (
    CandidateIdentityRecord,
)
from app.models.resume import (
    ArtifactFormat,
    ArtifactValidationStatus,
    ResumeArtifact,
)
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateBindingStatus,
    CandidateInvitationRecord,
    CandidatePracticeAnswerReviewRecord,
    CandidatePracticeMcqAnswerRecord,
    CandidatePracticeMessageRecord,
    CandidatePracticeReleaseRecord,
    CandidatePracticeReportRecord,
    CandidatePracticeSessionRecord,
)
from app.models.job import JobLifecycleStatus, JobRequisition, JobVersion, JobVersionStatus
from app.schemas.phase3_enums import (
    PracticeMessageKind,
    PracticeMessageRole,
    PracticeReleaseStatus,
    PracticeSection,
    PracticeSessionLifecycle,
    QuestionFormat,
)
from app.schemas.practice_report_contract import (
    CompetencyCommentaryItem,
    PracticeCoachingSummaryPayload,
    PriorityImprovementItem,
    ReportLifecycleStatus,
    ResponsePatternItem,
    StrengthAreaItem,
    TrendClassification,
)
from app.schemas.practice_review_contract import (
    AnswerStatus,
    DimensionLevel,
    ReviewLifecycleStatus,
)
from app.services.practice_report_service import (
    calculate_source_review_hash,
    compute_deterministic_aggregates,
    generate_coaching_summary_with_repair,
    validate_coaching_summary,
)
from app.services.practice_session_service import (
    get_or_generate_practice_report,
    retry_practice_report,
    start_or_resume_practice_session,
)


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


@pytest.fixture
def test_candidate_bundle(test_db: Session):
    cand_user = UserRecord(
        user_id=uuid.uuid4(),
        email="candidate_p3g3@example.com",
        display_name="Candidate Practice",
        password_hash="pw_hash",
        role=UserRole.CANDIDATE,
        is_active=True,
    )
    test_db.add(cand_user)
    test_db.flush()

    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="resume.pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=1024,
        sha256_hash="e" * 64,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(artifact)
    test_db.flush()

    identity = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        full_name="Candidate Practice",
        primary_email="candidate_p3g3@example.com",
        identity_payload={},
    )
    test_db.add(identity)
    test_db.flush()

    org_id = uuid.uuid4()
    invitation = CandidateInvitationRecord(
        invitation_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=identity.identity_id,
        token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc),
        created_by_user_id=uuid.uuid4(),
    )
    test_db.add(invitation)
    test_db.flush()

    binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        candidate_id=identity.identity_id,
        user_id=cand_user.user_id,
        organization_id=org_id,
        invitation_id=invitation.invitation_id,
        status=CandidateBindingStatus.ACTIVE.value,
    )
    test_db.add(binding)
    test_db.commit()

    return cand_user, identity, binding, org_id


@pytest.fixture
def test_completed_session(test_db: Session, test_candidate_bundle):
    cand_user, identity, binding, org_id = test_candidate_bundle
    now = datetime.now(timezone.utc)

    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=uuid.uuid4(),
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        title="Senior Distributed Systems Engineer",
        created_by_user_id=job.created_by_user_id,
    )
    test_db.add(ver)
    test_db.flush()

    q0_id = str(uuid.uuid4())
    q1_id = str(uuid.uuid4())
    q2_id = str(uuid.uuid4())
    mcq0_id = str(uuid.uuid4())

    q_items = [
        {
            "question_id": q0_id,
            "question_format": QuestionFormat.CONVERSATION.value,
            "wording": "How do you optimize slow queries in PostgreSQL?",
            "competency_name": "Database Optimization",
            "question_type": "TECHNICAL",
        },
        {
            "question_id": q1_id,
            "question_format": QuestionFormat.CONVERSATION.value,
            "wording": "How do you design distributed messaging with Kafka?",
            "competency_name": "System Architecture",
            "question_type": "TECHNICAL",
        },
        {
            "question_id": q2_id,
            "question_format": QuestionFormat.CONVERSATION.value,
            "wording": "How do you ensure service resilience during network partitions?",
            "competency_name": "Fault Tolerance",
            "question_type": "TECHNICAL",
        },
        {
            "question_id": mcq0_id,
            "question_format": QuestionFormat.MCQ_SINGLE_ANSWER.value,
            "wording": "What isolation level prevents non-repeatable reads in PostgreSQL?",
            "competency_name": "Database Optimization",
            "options": [
                {"option_id": "A", "text": "Read Committed"},
                {"option_id": "B", "text": "Repeatable Read"},
            ],
            "correct_option_id": "B",
            "explanation": "Repeatable Read guarantees snapshots across transaction queries.",
        },
    ]

    release = CandidatePracticeReleaseRecord(
        release_id=uuid.uuid4(),
        organization_id=org_id,
        candidate_id=identity.identity_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        status=PracticeReleaseStatus.RELEASED.value,
        released_by_user_id=cand_user.user_id,
    )
    test_db.add(release)
    test_db.flush()

    session = CandidatePracticeSessionRecord(
        session_id=uuid.uuid4(),
        organization_id=org_id,
        binding_id=binding.binding_id,
        candidate_id=identity.identity_id,
        release_id=release.release_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        lifecycle_status=PracticeSessionLifecycle.COMPLETED.value,
        current_section=PracticeSection.COMPLETE.value,
        current_conv_index=3,
        current_mcq_index=1,
        total_conv_count=3,
        total_mcq_count=1,
        total_response_count=4,
        questions_snapshot=q_items,
        snapshot_hash="f" * 64,
        started_at=now,
        completed_at=now,
        expires_at=now,
    )
    test_db.add(session)
    test_db.flush()

    # Answers & Reviews
    revs = []
    for idx, (qid, comp) in enumerate([
        (q0_id, "Database Optimization"),
        (q1_id, "System Architecture"),
        (q2_id, "Fault Tolerance"),
    ]):
        msg = CandidatePracticeMessageRecord(
            message_id=uuid.uuid4(),
            session_id=session.session_id,
            sequence_number=idx + 1,
            role=PracticeMessageRole.CANDIDATE.value,
            message_kind=PracticeMessageKind.ANSWER.value,
            source_question_id=UUID(qid),
            text=f"This is answer to question {idx}",
        )
        test_db.add(msg)
        test_db.flush()

        rev = CandidatePracticeAnswerReviewRecord(
            review_id=uuid.uuid4(),
            session_id=session.session_id,
            question_id=UUID(qid),
            answer_id=msg.message_id,
            idempotency_key=str(uuid.uuid4()),
            lifecycle_status=ReviewLifecycleStatus.REVIEW_READY.value,
            answer_status=AnswerStatus.ASSESSABLE.value,
            acknowledgement=f"Good explanation on {comp}.",
            strengths=[{"dimension": "relevance", "observation": "Focused answer", "answer_segment_ids": ["segment-1"]}],
            improvement_areas=[{"dimension": "evidence", "observation": "Add verification metrics", "answer_segment_ids": ["segment-1"]}],
            next_steps=["Step 1 action", "Step 2 action"],
            stronger_structure="State problem, action, and verified outcome.",
            dimension_levels={
                "relevance": "STRONG",
                "clarity": "DEVELOPING",
                "technical_depth": "DEVELOPING",
                "evidence": "NEEDS_DETAIL",
            },
            follow_up_needed=False,
            validation_status="PASSED",
        )
        test_db.add(rev)
        revs.append(rev)

    # MCQ Answer
    mcq_ans = CandidatePracticeMcqAnswerRecord(
        answer_id=uuid.uuid4(),
        session_id=session.session_id,
        question_id=UUID(mcq0_id),
        selected_option_id="B",
        is_correct=True,
    )
    test_db.add(mcq_ans)
    test_db.commit()

    return {
        "user": cand_user,
        "session": session,
        "reviews": revs,
        "mcq": mcq_ans,
        "q_items": q_items,
    }


def test_deterministic_competency_and_dimension_aggregates(test_db: Session, test_completed_session):
    session = test_completed_session["session"]
    reviews = test_completed_session["reviews"]
    mcq = test_completed_session["mcq"]

    (
        comp_aggs,
        radar_eligible,
        dim_aggs,
        q_trend,
        trend_class,
        mcq_b,
        counts,
    ) = compute_deterministic_aggregates(session, reviews, [mcq])

    # 3 assessable competencies -> radar eligible
    assert radar_eligible is True
    assert len(comp_aggs) == 3
    assert comp_aggs[0].anchored_level in ("STRONG", "DEVELOPING", "NEEDS_DETAIL")
    assert comp_aggs[0].assessable_count == 1

    # 5 dimensions
    assert len(dim_aggs) == 5
    rel_dim = next(d for d in dim_aggs if d.dimension_key == "relevance")
    assert rel_dim.anchored_level == "STRONG"
    assert rel_dim.numeric_score == 4.0

    # Trend
    assert len(q_trend) == 4
    assert trend_class in (TrendClassification.STABLE.value, TrendClassification.IMPROVING.value, TrendClassification.MIXED.value)

    # MCQ
    assert mcq_b is not None
    assert mcq_b.correct_count == 1
    assert mcq_b.attempted_count == 1
    assert len(mcq_b.topics) == 1

    # Completion counts
    assert counts.total_primary_questions == 4
    assert counts.assessable_answers_count == 3
    assert counts.skipped_count == 0


def test_validate_coaching_summary_strict_grounding(test_completed_session):
    reviews = test_completed_session["reviews"]
    allowed_rids = {str(r.review_id) for r in reviews}
    allowed_comps = {"Database Optimization", "System Architecture", "Fault Tolerance"}

    valid_payload = {
        "schema_version": "practice-coaching-summary.v1",
        "summary": "You demonstrated clear understanding across all database and architecture turns.",
        "strongest_areas": [
            {
                "title": "Clear Technical Focus",
                "observation": "Consistently maintained focused relevance on PostgreSQL query tuning.",
                "source_review_ids": [str(reviews[0].review_id)],
            }
        ],
        "priority_improvements": [
            {
                "title": "Add Verification Steps",
                "observation": "Detail specific metric monitoring used after index creation.",
                "source_review_ids": [str(reviews[0].review_id)],
            }
        ],
        "competency_commentary": [
            {
                "competency_id": "Database Optimization",
                "commentary": "Strong foundational performance on indexing and explain analyze.",
                "source_review_ids": [str(reviews[0].review_id)],
            }
        ],
        "response_patterns": [
            {
                "observation": "Responses were structured and direct.",
                "source_review_ids": [str(reviews[0].review_id)],
            }
        ],
        "next_practice_plan": [
            "Use a problem-action-result structure.",
            "Explain specific verification steps you took.",
            "Practice describing trade-offs for partitioning.",
        ],
        "disclaimer": "This is practice coaching and not a hiring decision.",
    }

    is_valid, errors, parsed = validate_coaching_summary(valid_payload, allowed_rids, allowed_comps)
    assert is_valid is True
    assert parsed is not None
    assert len(errors) == 0


def test_validate_coaching_summary_rejects_hallucinated_review_id_and_hiring_language(test_completed_session):
    reviews = test_completed_session["reviews"]
    allowed_rids = {str(reviews[0].review_id)}
    allowed_comps = {"Database Optimization"}

    invalid_payload = {
        "schema_version": "practice-coaching-summary.v1",
        "summary": "We recommend to hire this candidate immediately based on high performance.",
        "strongest_areas": [
            {
                "title": "Great skill",
                "observation": "Good work",
                "source_review_ids": ["unknown-fake-id-123"],
            }
        ],
        "priority_improvements": [],
        "competency_commentary": [],
        "response_patterns": [],
        "next_practice_plan": ["Action 1", "Action 2", "Action 3"],
        "disclaimer": "This is practice coaching and not a hiring decision.",
    }

    is_valid, errors, parsed = validate_coaching_summary(invalid_payload, allowed_rids, allowed_comps)
    assert is_valid is False
    assert any("UNKNOWN_SOURCE_REVIEW_ID" in e for e in errors)
    assert any("PROHIBITED_TERM" in e for e in errors)


@pytest.mark.anyio
async def test_get_or_generate_practice_report_creates_and_persists_report(test_db: Session, test_completed_session):
    user = test_completed_session["user"]
    session = test_completed_session["session"]
    questions = session.questions_snapshot
    q0_id = questions[0]["question_id"]

    from app.schemas.live_interview_contract import (
        LiveInterviewFinalReviewPayload,
        QuestionReviewItem,
        QuestionReviewDimensionLevels,
        StrongestResponseItem,
        ImprovementAreaItem,
        CompetencyReviewItem,
        ResponseQualityDimensions,
        LiveResponseQualityLevel,
    )

    mock_final_review = LiveInterviewFinalReviewPayload(
        schema_version="live-interview-final-review.v1",
        overall_summary="You demonstrated good technical knowledge.",
        question_reviews=[
            QuestionReviewItem(
                question_id=str(q["question_id"]),
                answer_status="ASSESSABLE",
                answer_summary=f"Analysis for question {i+1}",
                what_was_done_well=["Good technical explanation"],
                what_was_missing=["Could include test cases"],
                how_to_improve=["Add unit tests"],
                stronger_answer_structure="State problem, design, test.",
                evidence_excerpt="This is answer snippet.",
                dimension_levels=QuestionReviewDimensionLevels(),
            )
            for i, q in enumerate(questions[:3])
        ],
        strongest_responses=[
            StrongestResponseItem(
                question_id=str(q0_id),
                reason="Demonstrated clear query optimization knowledge.",
                answer_evidence="This is answer to question 0",
            )
        ],
        improvement_areas=[
            ImprovementAreaItem(
                question_id=str(q0_id),
                issue="Missing verification metrics.",
                how_to_improve="Add explicit verification metrics to answers.",
            )
        ],
        competency_reviews=[
            CompetencyReviewItem(
                competency_id="Database Optimization",
                level=LiveResponseQualityLevel.STRONG,
                commentary="Good understanding of explain analyze.",
                question_ids=[str(q0_id)],
            )
        ],
        response_quality=ResponseQualityDimensions(
            relevance=LiveResponseQualityLevel.STRONG,
            clarity=LiveResponseQualityLevel.DEVELOPING,
            technical_depth=LiveResponseQualityLevel.DEVELOPING,
            evidence=LiveResponseQualityLevel.NEEDS_DETAIL,
            problem_solving_structure=LiveResponseQualityLevel.DEVELOPING,
        ),
        best_answer_question_id=str(q0_id),
        best_answer_reason="Comprehensive optimization details.",
        next_practice_plan=[
            "Structure answers with situation, action, outcome.",
            "Always mention how you verified your work.",
            "Practice distributed systems edge cases.",
        ],
        disclaimer="This is practice coaching and not a hiring decision.",
    )

    with patch("app.services.practice_session_service.generate_final_interview_review_with_repair", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = (True, mock_final_review, "LOCAL_OPENAI_COMPATIBLE", "qwen3-4b-mlx", None)

        report_dto = await get_or_generate_practice_report(
            candidate_user=user,
            session_id=session.session_id,
            db=test_db,
        )

        assert report_dto.session_id == session.session_id
        assert report_dto.lifecycle_status == ReportLifecycleStatus.SUMMARY_READY.value
        assert len(report_dto.strongest_areas) == 1
        assert len(report_dto.priority_improvements) == 1
        assert len(report_dto.next_practice_plan) == 3
        assert report_dto.radar_eligible is True
        assert report_dto.completion_counts.total_primary_questions == 4

        # Verify DB persistence
        db_rep = test_db.query(CandidatePracticeReportRecord).filter_by(session_id=session.session_id).first()
        assert db_rep is not None
        assert db_rep.source_review_hash == report_dto.source_review_hash


@pytest.mark.anyio
async def test_refresh_report_is_idempotent_and_does_not_regenerate(test_db: Session, test_completed_session):
    user = test_completed_session["user"]
    session = test_completed_session["session"]
    questions = session.questions_snapshot
    q0_id = questions[0]["question_id"]

    from app.schemas.live_interview_contract import (
        LiveInterviewFinalReviewPayload,
        QuestionReviewItem,
        QuestionReviewDimensionLevels,
        ResponseQualityDimensions,
        LiveResponseQualityLevel,
    )

    mock_final_review = LiveInterviewFinalReviewPayload(
        schema_version="live-interview-final-review.v1",
        overall_summary="You demonstrated good technical knowledge.",
        question_reviews=[
            QuestionReviewItem(
                question_id=str(q["question_id"]),
                answer_status="ASSESSABLE",
                answer_summary=f"Analysis for question {i+1}",
                what_was_done_well=["Good technical explanation"],
                what_was_missing=["Could include test cases"],
                how_to_improve=["Add unit tests"],
                stronger_answer_structure="State problem, design, test.",
                evidence_excerpt="This is answer snippet.",
                dimension_levels=QuestionReviewDimensionLevels(),
            )
            for i, q in enumerate(questions[:3])
        ],
        strongest_responses=[],
        improvement_areas=[],
        competency_reviews=[],
        response_quality=ResponseQualityDimensions(
            relevance=LiveResponseQualityLevel.STRONG,
            clarity=LiveResponseQualityLevel.DEVELOPING,
            technical_depth=LiveResponseQualityLevel.DEVELOPING,
            evidence=LiveResponseQualityLevel.NEEDS_DETAIL,
            problem_solving_structure=LiveResponseQualityLevel.DEVELOPING,
        ),
        next_practice_plan=["Action 1 here", "Action 2 here", "Action 3 here"],
        disclaimer="This is practice coaching and not a hiring decision.",
    )

    with patch("app.services.practice_session_service.generate_final_interview_review_with_repair", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = (True, mock_final_review, "LOCAL_OPENAI_COMPATIBLE", "qwen3-4b-mlx", None)

        rep1 = await get_or_generate_practice_report(user, session.session_id, test_db)
        rep2 = await get_or_generate_practice_report(user, session.session_id, test_db)

        assert rep1.report_id == rep2.report_id
        assert rep1.source_review_hash == rep2.source_review_hash
        # LLM call was executed only once because existing is_current report is returned on refresh
        assert mock_ai.call_count == 1


@pytest.mark.anyio
async def test_in_progress_session_rejects_report_generation(test_db: Session, test_completed_session):
    user = test_completed_session["user"]
    session = test_completed_session["session"]

    session.lifecycle_status = PracticeSessionLifecycle.IN_PROGRESS.value
    test_db.commit()

    with pytest.raises(HTTPException) as exc:
        await get_or_generate_practice_report(user, session.session_id, test_db)
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "SESSION_NOT_COMPLETED"


@pytest.mark.anyio
async def test_provider_failure_persists_retryable_report_with_deterministic_data(test_db: Session, test_completed_session):
    user = test_completed_session["user"]
    session = test_completed_session["session"]

    with patch("app.services.practice_session_service.generate_final_interview_review_with_repair", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = (False, None, "LOCAL_OPENAI_COMPATIBLE", "qwen3-4b-mlx", "Provider error")

        report_dto = await get_or_generate_practice_report(user, session.session_id, test_db)

        assert report_dto.lifecycle_status == ReportLifecycleStatus.SUMMARY_FAILED_RETRYABLE.value
        assert report_dto.validation_status == "FAILED"
        # Deterministic aggregates are fully preserved
        assert len(report_dto.competency_aggregates) == 3
        assert len(report_dto.dimension_aggregates) == 5
        assert report_dto.completion_counts.total_primary_questions == 4
