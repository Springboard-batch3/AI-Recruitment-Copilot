"""Phase 3D.3: Human Review, Evidence Overrides, Finalization, and Aggregate Tests.

Covers:
1. Accept preserves provisional and reviewed values.
2. Edit requires valid citations and rejects empty citations.
3. Forged or unknown citation is rejected.
4. Override requires non-empty reason.
5. Reject preserves the original provisional record.
6. Insufficient evidence is a neutral outcome and not scored as zero.
7. Supplemental question is excluded from core aggregate.
8. Aggregate recalculates deterministically after human review.
9. Finalization is blocked while required reviews are incomplete.
10. Stale review version is rejected (optimistic concurrency).
11. Finalized assessment cannot be edited in place.
12. Phase 2 hashes and scores remain completely unchanged.
"""

import hashlib
import uuid
from decimal import Decimal
from datetime import datetime, timezone
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.auth import UserRecord, UserRole
from app.models.base import Base
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.interview import (
    AssessmentReviewRecord,
    CandidateInterviewPlanRecord,
    InterviewAssessmentRecord,
    InterviewQuestionRecord,
    InterviewRubricRecord,
    JobInterviewTemplateRecord,
    SimulationAnswerRecord,
    SimulationAnswerSegmentRecord,
    SimulationAttemptRecord,
    SimulationQuestionAssessmentRecord,
)
from app.models.job import JobLifecycleStatus, JobRequisition, JobVersion, JobVersionStatus
from app.models.matching import MatchResultRecord, MatchRunLifecycleStatus, MatchRunRecord
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.human_review import (
    AcceptReviewRequest,
    EditReviewRequest,
    FinalizeAssessmentRequest,
    MarkInsufficientRequest,
    OverrideReviewRequest,
    RejectReviewRequest,
)
from app.schemas.phase3_enums import AuthoringOrigin, InterviewLayer, PlanLifecycle, QuestionType, ValidationState
from app.services.human_review_service import (
    HumanReviewError,
    calculate_deterministic_aggregate,
    check_finalization_eligibility,
    finalize_simulation_assessment,
    record_review_command,
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
def setup_scenario(test_db: Session):
    org_id = uuid.uuid4()
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter@example.com",
        display_name="Elena Rostova",
        role=UserRole.RECRUITER,
        password_hash="fakehash",
    )
    test_db.add(recruiter)
    test_db.flush()

    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    job_ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        title="Staff Backend Engineer",
        created_by_user_id=recruiter.user_id,
        version_status=JobVersionStatus.APPROVED,
    )
    test_db.add(job_ver)
    test_db.flush()

    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="alex_resume.pdf",
        sha256_hash="0" * 64,
        size_bytes=1024,
        declared_mime_type="application/pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(artifact)
    test_db.flush()

    identity = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        full_name="Alex Mercer",
        primary_email="alex@example.com",
        primary_phone="+1-555-0100",
        identity_payload={"name": "Alex Mercer", "email": "alex@example.com"},
    )
    test_db.add(identity)
    test_db.flush()

    profile = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=1,
        artifact_id=artifact.artifact_id,
        extraction_id=uuid.uuid4(),
        identity_id=identity.identity_id,
        parser_pipeline_version="1.0.0",
        professional_profile={"headline": "Staff Engineer", "skills": [], "employment": []},
        evidence_spans=[],
    )
    test_db.add(profile)
    test_db.flush()

    match_run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        revision=1,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="1.0.0",
        scoring_policy_version="1.0.0",
        candidate_input_fingerprint="c_fp_" + "a" * 59,
        input_fingerprint="i_fp_" + "b" * 59,
        idempotency_key="idem_" + uuid.uuid4().hex,
        lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
        candidate_count=1,
    )
    test_db.add(match_run)
    test_db.flush()

    match_result = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=match_run.run_id,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        raw_compatibility_score=Decimal("87.500000"),
        display_compatibility_score=Decimal("87.50"),
        confidence_tier="HIGH",
        rank=1,
        warning_count=0,
        must_have_warning_count=0,
        score_decomposition={"breakdown": "frozen"},
    )
    test_db.add(match_result)
    test_db.flush()

    template = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        revision=1,
        template_version=1,
        lifecycle_status=PlanLifecycle.APPROVED,
        content_hash="tpl_hash_abc",
        input_snapshot_hash="snap_hash_abc",
        created_by_user_id=recruiter.user_id,
        guardrail_policy_version="1.0.0",
        guardrail_policy_hash="a" * 64,
        deterministic_builder_version="1.0.0",
        question_catalogue_version="1.0.0",
        rubric_catalogue_version="1.0.0",
        record_version=1,
    )
    test_db.add(template)
    test_db.flush()

    # Question 1: Common Core
    q1 = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        competency_id="DISTRIBUTED_SYSTEMS",
        question_text="How do you handle consensus in partitioned networks?",
        question_intent="Assess distributed consensus understanding",
        display_order=0,
        estimated_time_minutes=15,
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        validation_state=ValidationState.PASSED,
        included_in_common_score=True,
    )
    test_db.add(q1)
    test_db.flush()

    r1 = InterviewRubricRecord(
        rubric_id=uuid.uuid4(),
        question_id=q1.question_id,
        rubric_version=1,
        competency_definition="Knowledge of distributed consensus and replication.",
        question_intent="Assess consistency depth.",
        observable_indicators=["Mentions Raft/Paxos", "Explains trade-offs"],
        rating_anchors={"1": "No understanding", "3": "Solid standard knowledge", "4": "Advanced knowledge", "5": "Expert depth"},
        sufficient_evidence_guidance="Concrete consensus examples",
        insufficient_evidence_guidance="Vague answers",
        permitted_probes=["What about network partitions?"],
        prohibited_inferences=[],
        subject_matter_review_status="REVIEWED",
        validation_state=ValidationState.PASSED,
    )
    test_db.add(r1)

    # Question 2: Common Core
    q2 = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        competency_id="CONCURRENCY_SAFETY",
        question_text="How do you prevent deadlocks in distributed transactions?",
        question_intent="Assess concurrency safety and deadlock prevention",
        display_order=1,
        estimated_time_minutes=15,
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        validation_state=ValidationState.PASSED,
        included_in_common_score=True,
    )
    test_db.add(q2)
    test_db.flush()

    r2 = InterviewRubricRecord(
        rubric_id=uuid.uuid4(),
        question_id=q2.question_id,
        rubric_version=1,
        competency_definition="Knowledge of lock ordering and concurrency control.",
        question_intent="Assess concurrency depth.",
        observable_indicators=["Mentions two-phase locking", "Explains isolation levels"],
        rating_anchors={"1": "No understanding", "3": "Solid standard knowledge", "4": "Advanced knowledge", "5": "Expert depth"},
        sufficient_evidence_guidance="Concrete deadlock prevention strategies",
        insufficient_evidence_guidance="Vague answers",
        permitted_probes=[],
        prohibited_inferences=[],
        subject_matter_review_status="REVIEWED",
        validation_state=ValidationState.PASSED,
    )
    test_db.add(r2)

    # Question 3: Supplemental Evidence Clarification (NOT included in core aggregate)
    q3 = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template.template_id,
        question_version=1,
        layer=InterviewLayer.SUPPLEMENTAL_EVIDENCE,
        question_type=QuestionType.TECHNICAL,
        competency_id="PERFORMANCE_TUNING",
        question_text="Clarify your Redis cluster tuning experience.",
        question_intent="Assess performance tuning depth",
        display_order=2,
        estimated_time_minutes=10,
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        validation_state=ValidationState.PASSED,
        included_in_common_score=False,
    )
    test_db.add(q3)
    test_db.flush()

    r3 = InterviewRubricRecord(
        rubric_id=uuid.uuid4(),
        question_id=q3.question_id,
        rubric_version=1,
        competency_definition="Knowledge of caching and performance tuning.",
        question_intent="Assess cache configuration skills.",
        observable_indicators=["Mentions memory policies", "Explains evictions"],
        rating_anchors={"1": "No understanding", "3": "Solid standard knowledge", "4": "Advanced knowledge", "5": "Expert depth"},
        sufficient_evidence_guidance="Specific Redis configuration details",
        insufficient_evidence_guidance="Vague answers",
        permitted_probes=[],
        prohibited_inferences=[],
        subject_matter_review_status="REVIEWED",
        validation_state=ValidationState.PASSED,
    )
    test_db.add(r3)

    plan = CandidateInterviewPlanRecord(
        plan_id=uuid.uuid4(),
        organization_id=org_id,
        template_id=template.template_id,
        template_version=1,
        candidate_id=identity.identity_id,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        match_result_id=match_result.result_id,
        match_result_fingerprint=match_run.input_fingerprint,
        plan_version=1,
        lifecycle_status=PlanLifecycle.APPROVED,
        guardrail_policy_version="1.0.0",
        scoring_separation_policy="STRICT_COMMON_CORE_SEPARATION",
        input_snapshot_hash="snap_hash_abc",
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(plan)

    attempt = SimulationAttemptRecord(
        attempt_id=uuid.uuid4(),
        organization_id=org_id,
        recruiter_user_id=recruiter.user_id,
        candidate_id=plan.candidate_id,
        job_id=job.job_id,
        job_version_id=template.job_version_id,
        job_revision=1,
        candidate_profile_version=1,
        match_result_id=match_result.result_id,
        plan_id=plan.plan_id,
        template_id=template.template_id,
        plan_version=1,
        record_version=1,
        authoritative_content_hash="auth_hash_123",
        guardrail_policy_hash="a" * 64,
        lifecycle_status="PROVISIONALLY_ASSESSED",
        guardrail_policy_version="1.0.0",
        question_snapshots=[],
        total_questions_count=3,
        answered_questions_count=3,
        started_at=datetime.now(timezone.utc),
        submitted_at=datetime.now(timezone.utc),
    )
    test_db.add(attempt)

    # Answers & Segments for q1
    ans1 = SimulationAnswerRecord(
        answer_id=uuid.uuid4(),
        attempt_id=attempt.attempt_id,
        question_id=q1.question_id,
        question_version=1,
        display_order=0,
        layer=InterviewLayer.COMMON_CORE,
        answer_text="We implemented Raft consensus across three availability zones.",
        answer_version=1,
        is_submitted=True,
        content_hash="ans1_hash",
    )
    test_db.add(ans1)

    seg1 = SimulationAnswerSegmentRecord(
        segment_id=uuid.uuid4(),
        answer_id=ans1.answer_id,
        attempt_id=attempt.attempt_id,
        question_id=q1.question_id,
        sequence_order=1,
        start_char=0,
        end_char=len(ans1.answer_text),
        segment_text=ans1.answer_text,
        segmentation_version="1.0.0",
    )
    test_db.add(seg1)

    # Answers & Segments for q2
    ans2 = SimulationAnswerRecord(
        answer_id=uuid.uuid4(),
        attempt_id=attempt.attempt_id,
        question_id=q2.question_id,
        question_version=1,
        display_order=1,
        layer=InterviewLayer.COMMON_CORE,
        answer_text="We enforce global lock ordering and short timeouts.",
        answer_version=1,
        is_submitted=True,
        content_hash="ans2_hash",
    )
    test_db.add(ans2)

    seg2 = SimulationAnswerSegmentRecord(
        segment_id=uuid.uuid4(),
        answer_id=ans2.answer_id,
        attempt_id=attempt.attempt_id,
        question_id=q2.question_id,
        sequence_order=1,
        start_char=0,
        end_char=len(ans2.answer_text),
        segment_text=ans2.answer_text,
        segmentation_version="1.0.0",
    )
    test_db.add(seg2)

    # Answers & Segments for q3
    ans3 = SimulationAnswerRecord(
        answer_id=uuid.uuid4(),
        attempt_id=attempt.attempt_id,
        question_id=q3.question_id,
        question_version=1,
        display_order=2,
        layer=InterviewLayer.SUPPLEMENTAL_EVIDENCE,
        answer_text="Tuned Redis maxmemory-policy to volatile-lru.",
        answer_version=1,
        is_submitted=True,
        content_hash="ans3_hash",
    )
    test_db.add(ans3)

    seg3 = SimulationAnswerSegmentRecord(
        segment_id=uuid.uuid4(),
        answer_id=ans3.answer_id,
        attempt_id=attempt.attempt_id,
        question_id=q3.question_id,
        sequence_order=1,
        start_char=0,
        end_char=len(ans3.answer_text),
        segment_text=ans3.answer_text,
        segmentation_version="1.0.0",
    )
    test_db.add(seg3)

    # Provisional assessment for q1
    assess1 = SimulationQuestionAssessmentRecord(
        assessment_id=uuid.uuid4(),
        attempt_id=attempt.attempt_id,
        answer_id=ans1.answer_id,
        question_id=q1.question_id,
        assessment_status="PROVISIONAL",
        provisional_level=4,
        criterion_observations=[{
            "criterion_id": "DISTRIBUTED_SYSTEMS",
            "observation": "Candidate demonstrated strong understanding of quorum writes.",
            "cited_segment_ids": [str(seg1.segment_id)],
        }],
        cited_segment_ids=[str(seg1.segment_id)],
        uncertainty_codes=[],
    )
    test_db.add(assess1)

    # Provisional assessment for q2
    assess2 = SimulationQuestionAssessmentRecord(
        assessment_id=uuid.uuid4(),
        attempt_id=attempt.attempt_id,
        answer_id=ans2.answer_id,
        question_id=q2.question_id,
        assessment_status="PROVISIONAL",
        provisional_level=3,
        criterion_observations=[{
            "criterion_id": "CONCURRENCY_SAFETY",
            "observation": "Candidate described lock ordering but omitted distributed rollback details.",
            "cited_segment_ids": [str(seg2.segment_id)],
        }],
        cited_segment_ids=[str(seg2.segment_id)],
        uncertainty_codes=[],
    )
    test_db.add(assess2)

    # Provisional assessment for q3 (supplemental)
    assess3 = SimulationQuestionAssessmentRecord(
        assessment_id=uuid.uuid4(),
        attempt_id=attempt.attempt_id,
        answer_id=ans3.answer_id,
        question_id=q3.question_id,
        assessment_status="PROVISIONAL",
        provisional_level=4,
        criterion_observations=[{
            "criterion_id": "PERFORMANCE_TUNING",
            "observation": "Candidate properly configured cache eviction policies.",
            "cited_segment_ids": [str(seg3.segment_id)],
        }],
        cited_segment_ids=[str(seg3.segment_id)],
        uncertainty_codes=[],
    )
    test_db.add(assess3)

    test_db.commit()

    return {
        "recruiter": recruiter,
        "job": job,
        "template": template,
        "plan": plan,
        "attempt": attempt,
        "q1": q1,
        "q2": q2,
        "q3": q3,
        "seg1": seg1,
        "seg2": seg2,
        "seg3": seg3,
        "assess1": assess1,
        "assess2": assess2,
        "assess3": assess3,
        "match_run": match_run,
        "match_result": match_result,
    }


def test_accept_preserves_provisional_and_reviewed_values(test_db: Session, setup_scenario):
    ctx = setup_scenario
    res = record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q1"].question_id,
        command="accept",
        payload=AcceptReviewRequest(),
    )

    assert res.decision == "ACCEPTED"
    assert res.reviewed_rubric_level == 4
    assert str(ctx["seg1"].segment_id) in res.retained_citations
    assert "quorum writes" in res.reviewed_observation

    # Provisional assessment record remains untouched
    prov = test_db.query(SimulationQuestionAssessmentRecord).filter_by(assessment_id=ctx["assess1"].assessment_id).first()
    assert prov.provisional_level == 4
    assert prov.assessment_status == "PROVISIONAL"


def test_edit_requires_valid_citations_and_rejects_empty(test_db: Session, setup_scenario):
    ctx = setup_scenario
    # Empty citations must be rejected (Rule 5)
    with pytest.raises(Exception):
        EditReviewRequest(
            reviewed_observation="Valid text explaining details.",
            cited_segment_ids=[],
        )

    # Valid edit with citations
    res = record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q1"].question_id,
        command="edit",
        payload=EditReviewRequest(
            reviewed_observation="Recruiter refined notes: verified Raft leader election details.",
            cited_segment_ids=[str(ctx["seg1"].segment_id)],
            reason="Clarified distributed system behavior.",
        ),
    )

    assert res.decision == "EDITED"
    assert res.reviewed_observation == "Recruiter refined notes: verified Raft leader election details."
    assert res.retained_citations == [str(ctx["seg1"].segment_id)]


def test_forged_citation_is_rejected(test_db: Session, setup_scenario):
    ctx = setup_scenario
    forged_uuid = str(uuid.uuid4())
    with pytest.raises(HumanReviewError) as exc_info:
        record_review_command(
            db=test_db,
            actor=ctx["recruiter"],
            job_id=ctx["job"].job_id,
            template_id=ctx["template"].template_id,
            plan_id=ctx["plan"].plan_id,
            attempt_id=ctx["attempt"].attempt_id,
            question_id=ctx["q1"].question_id,
            command="edit",
            payload=EditReviewRequest(
                reviewed_observation="Recruiter notes citing forged segment ID.",
                cited_segment_ids=[forged_uuid],
            ),
        )
    assert exc_info.value.code == "FORGED_OR_UNKNOWN_CITATION"


def test_override_requires_reason(test_db: Session, setup_scenario):
    ctx = setup_scenario
    with pytest.raises(Exception):
        OverrideReviewRequest(
            reviewed_rubric_level=5,
            reason="   ",  # Blank reason
        )

    res = record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q1"].question_id,
        command="override",
        payload=OverrideReviewRequest(
            reviewed_rubric_level=5,
            reason="Demonstrated master-level architectural mastery beyond standard anchor.",
        ),
    )
    assert res.decision == "OVERRIDDEN"
    assert res.reviewed_rubric_level == 5
    assert res.reason == "Demonstrated master-level architectural mastery beyond standard anchor."


def test_reject_preserves_original_provisional_record(test_db: Session, setup_scenario):
    ctx = setup_scenario
    res = record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q2"].question_id,
        command="reject",
        payload=RejectReviewRequest(
            reason="Observation misunderstood lock hierarchy mechanism.",
        ),
    )

    assert res.decision == "REJECTED"
    assert res.reviewed_rubric_level is None
    assert res.reviewed_observation is None

    # Verify provisional evaluation record is preserved and not deleted (Rule 7)
    prov = test_db.query(SimulationQuestionAssessmentRecord).filter_by(assessment_id=ctx["assess2"].assessment_id).first()
    assert prov is not None
    assert prov.provisional_level == 3


def test_insufficient_evidence_is_not_scored_as_zero(test_db: Session, setup_scenario):
    ctx = setup_scenario
    res = record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q2"].question_id,
        command="mark_insufficient",
        payload=MarkInsufficientRequest(reason="Candidate did not articulate answer."),
    )

    assert res.decision == "INSUFFICIENT"
    assert res.reviewed_rubric_level is None

    # Review map
    reviews = {ctx["q2"].question_id: test_db.query(AssessmentReviewRecord).filter_by(review_id=res.review_id).first()}
    agg = calculate_deterministic_aggregate([ctx["q2"]], reviews)
    # Neutral exclusion: numerator and denominator are 0, aggregate is None, NOT 0.0
    assert agg.aggregate_score is None
    assert agg.scored_core_questions == 0
    assert len(agg.excluded_items) == 1
    assert agg.excluded_items[0]["reason"] == "INSUFFICIENT_EVIDENCE_NEUTRAL"


def test_supplemental_question_is_excluded_from_core_aggregate(test_db: Session, setup_scenario):
    ctx = setup_scenario
    # Review q1 (core, level 4) and q3 (supplemental, level 4)
    rev1 = record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q1"].question_id,
        command="accept",
        payload=AcceptReviewRequest(),
    )
    rev3 = record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q3"].question_id,
        command="accept",
        payload=AcceptReviewRequest(),
    )

    reviews = {
        ctx["q1"].question_id: test_db.query(AssessmentReviewRecord).filter_by(review_id=rev1.review_id).first(),
        ctx["q3"].question_id: test_db.query(AssessmentReviewRecord).filter_by(review_id=rev3.review_id).first(),
    }

    agg = calculate_deterministic_aggregate([ctx["q1"], ctx["q3"]], reviews)
    # q3 is supplemental, so excluded from core
    assert agg.total_core_questions == 1
    assert agg.scored_core_questions == 1
    assert agg.aggregate_score == 4.0
    assert any(x["reason"] == "SUPPLEMENTAL_EVIDENCE_EXCLUDED" for x in agg.excluded_items)


def test_finalization_is_blocked_while_incomplete_and_succeeds_when_complete(test_db: Session, setup_scenario):
    ctx = setup_scenario
    # Review only q1, leave q2 unreviewed
    record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q1"].question_id,
        command="accept",
        payload=AcceptReviewRequest(),
    )

    # Attempt to finalize should fail
    with pytest.raises(HumanReviewError) as exc_info:
        finalize_simulation_assessment(
            db=test_db,
            actor=ctx["recruiter"],
            job_id=ctx["job"].job_id,
            template_id=ctx["template"].template_id,
            plan_id=ctx["plan"].plan_id,
            attempt_id=ctx["attempt"].attempt_id,
            payload=FinalizeAssessmentRequest(),
        )
    assert exc_info.value.code == "FINALIZATION_INELIGIBLE"

    # Now review q2
    record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q2"].question_id,
        command="override",
        payload=OverrideReviewRequest(
            reviewed_rubric_level=4,
            reason="Good recovery mechanism.",
        ),
    )

    # Finalize should succeed
    fin = finalize_simulation_assessment(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        payload=FinalizeAssessmentRequest(),
    )

    assert fin.is_finalized is True
    assert fin.lifecycle_status == "FINALIZED"
    assert fin.aggregate.aggregate_score == 4.0  # (4 + 4) / 2


def test_stale_review_version_is_rejected(test_db: Session, setup_scenario):
    ctx = setup_scenario
    rev = record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q1"].question_id,
        command="accept",
        payload=AcceptReviewRequest(),
    )
    assert rev.review_version == 1

    # Attempt second review expecting version 99 (stale)
    with pytest.raises(HumanReviewError) as exc_info:
        record_review_command(
            db=test_db,
            actor=ctx["recruiter"],
            job_id=ctx["job"].job_id,
            template_id=ctx["template"].template_id,
            plan_id=ctx["plan"].plan_id,
            attempt_id=ctx["attempt"].attempt_id,
            question_id=ctx["q1"].question_id,
            command="accept",
            payload=AcceptReviewRequest(expected_review_version=99),
        )
    assert exc_info.value.code == "STALE_REVIEW_VERSION"


def test_finalized_assessment_cannot_be_edited_in_place(test_db: Session, setup_scenario):
    ctx = setup_scenario
    # Complete reviews
    record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q1"].question_id,
        command="accept",
        payload=AcceptReviewRequest(),
    )
    record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q2"].question_id,
        command="accept",
        payload=AcceptReviewRequest(),
    )

    # Finalize
    finalize_simulation_assessment(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        payload=FinalizeAssessmentRequest(),
    )

    # Mutation after finalization must be rejected (Rule 10)
    with pytest.raises(HumanReviewError) as exc_info:
        record_review_command(
            db=test_db,
            actor=ctx["recruiter"],
            job_id=ctx["job"].job_id,
            template_id=ctx["template"].template_id,
            plan_id=ctx["plan"].plan_id,
            attempt_id=ctx["attempt"].attempt_id,
            question_id=ctx["q1"].question_id,
            command="accept",
            payload=AcceptReviewRequest(),
        )
    assert exc_info.value.code == "ASSESSMENT_ALREADY_FINALIZED"


def test_phase2_hashes_remain_unchanged(test_db: Session, setup_scenario):
    ctx = setup_scenario
    match_run_before = ctx["match_run"]
    match_before = ctx["match_result"]
    before_score = match_before.display_compatibility_score
    before_fingerprint = match_run_before.input_fingerprint
    before_rank = match_before.rank

    # Execute reviews and finalization
    record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q1"].question_id,
        command="accept",
        payload=AcceptReviewRequest(),
    )
    record_review_command(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        question_id=ctx["q2"].question_id,
        command="override",
        payload=OverrideReviewRequest(
            reviewed_rubric_level=5,
            reason="Exceptional answer.",
        ),
    )
    finalize_simulation_assessment(
        db=test_db,
        actor=ctx["recruiter"],
        job_id=ctx["job"].job_id,
        template_id=ctx["template"].template_id,
        plan_id=ctx["plan"].plan_id,
        attempt_id=ctx["attempt"].attempt_id,
        payload=FinalizeAssessmentRequest(),
    )

    match_after = test_db.query(MatchResultRecord).filter_by(result_id=match_before.result_id).first()
    match_run_after = test_db.query(MatchRunRecord).filter_by(run_id=match_run_before.run_id).first()
    assert match_after.display_compatibility_score == before_score
    assert match_run_after.input_fingerprint == before_fingerprint
    assert match_after.rank == before_rank
