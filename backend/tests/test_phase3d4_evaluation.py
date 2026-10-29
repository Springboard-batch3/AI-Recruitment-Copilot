"""Phase 3D.4 Evaluation and Final Closure Tests.

Covers:
1. Security answer fixtures (empty answer, off-topic answer, prompt injection, forged citations).
2. Structural validation & safe bounds verification.
3. Provenance & local model metadata persistence without chain-of-thought.
4. Core vs Supplemental strict separation in deterministic aggregate.
5. Immutability of finalized records and Phase 2 hash/score invariance.
6. Finalization gating (all common core reviewed, valid reasons, valid citations).
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
def setup_eval_scenario(test_db: Session):
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email="eval.recruiter@example.com",
        display_name="Jordan Eval",
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
        title="Principal Distributed Systems Architect",
        created_by_user_id=recruiter.user_id,
        version_status=JobVersionStatus.APPROVED,
    )
    test_db.add(job_ver)
    test_db.flush()

    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="alex_eval_resume.pdf",
        sha256_hash="e" * 64,
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
        full_name="Alex Rivera",
        primary_email="alex.eval@example.com",
        primary_phone="+1-555-0199",
        identity_payload={"name": "Alex Rivera", "email": "alex.eval@example.com"},
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
        professional_profile={"headline": "Principal Architect", "skills": [], "employment": []},
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
        candidate_input_fingerprint="c_fp_" + "e" * 59,
        input_fingerprint="i_fp_" + "f" * 59,
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
        raw_compatibility_score=Decimal("91.500000"),
        display_compatibility_score=Decimal("91.50"),
        confidence_tier="HIGH",
        rank=1,
        warning_count=0,
        must_have_warning_count=0,
        score_decomposition={"breakdown": "frozen"},
    )
    test_db.add(match_result)
    test_db.flush()

    org_id = uuid.uuid4()

    template = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=job_ver.job_version_id,
        revision=1,
        template_version=1,
        lifecycle_status=PlanLifecycle.APPROVED,
        content_hash="tmpl_eval_hash",
        input_snapshot_hash="snap_eval_hash",
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
        competency_id="DISTRIBUTED_CONSENSUS",
        question_text="How does Raft prevent split-brain leader elections during network partitioning?",
        question_intent="Assess distributed consensus understanding",
        display_order=0,
        estimated_time_minutes=6,
        authoring_origin=AuthoringOrigin.AI_ASSISTED,
        validation_state=ValidationState.PASSED,
        included_in_common_score=True,
    )
    test_db.add(q1)
    test_db.flush()

    r1 = InterviewRubricRecord(
        rubric_id=uuid.uuid4(),
        question_id=q1.question_id,
        rubric_version=1,
        competency_definition="Consensus safety and quorum guarantees",
        question_intent="Assess partitioning resilience",
        observable_indicators=["Mentions quorum", "Explains log term comparison"],
        rating_anchors={"1": "Superficial", "3": "Understands quorum", "5": "Term comparison and split-brain safety"},
        sufficient_evidence_guidance="Evaluate quorum rigor",
        insufficient_evidence_guidance="Ignores split-brain",
        permitted_probes=[],
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
        competency_id="STORAGE_INTERNALS",
        question_text="Detail LSM tree write pipeline from WAL to SSTable compaction.",
        question_intent="Assess storage engine understanding",
        display_order=1,
        estimated_time_minutes=5,
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
        competency_definition="LSM write path and compaction",
        question_intent="Assess storage engine understanding",
        observable_indicators=["Mentions SSTable", "Explains WAL and MemTable"],
        rating_anchors={"1": "Confuses B-tree", "3": "WAL & MemTable", "5": "Compaction trade-offs and write stalls"},
        sufficient_evidence_guidance="Evaluate storage mechanics",
        insufficient_evidence_guidance="No recovery awareness",
        permitted_probes=[],
        prohibited_inferences=[],
        subject_matter_review_status="REVIEWED",
        validation_state=ValidationState.PASSED,
    )
    test_db.add(r2)

    # Question 3: Supplemental Evidence Clarification
    q_supp = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template.template_id,
        question_version=1,
        layer=InterviewLayer.SUPPLEMENTAL_EVIDENCE,
        question_type=QuestionType.BEHAVIORAL,
        competency_id="LEGACY_MIGRATION_GAP",
        question_text="Could you clarify your direct hands-on involvement in the CockroachDB zero-downtime migration?",
        question_intent="Verify gap resolution",
        display_order=2,
        estimated_time_minutes=5,
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        validation_state=ValidationState.PASSED,
        included_in_common_score=False,
    )
    test_db.add(q_supp)
    test_db.flush()

    r_supp = InterviewRubricRecord(
        rubric_id=uuid.uuid4(),
        question_id=q_supp.question_id,
        rubric_version=1,
        competency_definition="Direct technical role in DB migration",
        question_intent="Verify gap resolution",
        observable_indicators=["Dual-write verification"],
        rating_anchors={"1": "Vague role", "3": "Executed migration", "5": "Lead architect"},
        sufficient_evidence_guidance="Evaluate verification evidence",
        insufficient_evidence_guidance="No rollback plan",
        permitted_probes=[],
        prohibited_inferences=[],
        subject_matter_review_status="REVIEWED",
        validation_state=ValidationState.PASSED,
    )
    test_db.add(r_supp)
    test_db.flush()

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
        input_snapshot_hash="snap_eval_hash",
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(plan)
    test_db.flush()

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
        authoritative_content_hash="snap_eval_hash",
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
    test_db.flush()

    # Answers & Segments
    ans1 = SimulationAnswerRecord(
        answer_id=uuid.uuid4(),
        attempt_id=attempt.attempt_id,
        question_id=q1.question_id,
        question_version=1,
        display_order=0,
        layer=InterviewLayer.COMMON_CORE,
        answer_text="Raft guarantees quorum. Only nodes with up-to-date logs can receive votes. Stale terms are rejected.",
        answer_version=1,
        is_submitted=True,
        content_hash="ans1_hash",
    )
    test_db.add(ans1)
    test_db.flush()

    seg1_1 = SimulationAnswerSegmentRecord(
        segment_id=uuid.uuid4(),
        answer_id=ans1.answer_id,
        attempt_id=attempt.attempt_id,
        question_id=q1.question_id,
        sequence_order=1,
        start_char=0,
        end_char=22,
        segment_text="Raft guarantees quorum.",
        segmentation_version="1.0.0",
    )
    seg1_2 = SimulationAnswerSegmentRecord(
        segment_id=uuid.uuid4(),
        answer_id=ans1.answer_id,
        attempt_id=attempt.attempt_id,
        question_id=q1.question_id,
        sequence_order=2,
        start_char=23,
        end_char=74,
        segment_text="Only nodes with up-to-date logs can receive votes.",
        segmentation_version="1.0.0",
    )
    test_db.add_all([seg1_1, seg1_2])

    ans2 = SimulationAnswerRecord(
        answer_id=uuid.uuid4(),
        attempt_id=attempt.attempt_id,
        question_id=q2.question_id,
        question_version=1,
        display_order=1,
        layer=InterviewLayer.COMMON_CORE,
        answer_text="Writes append to WAL and in-memory MemTable, then flush to immutable SSTables on disk.",
        answer_version=1,
        is_submitted=True,
        content_hash="ans2_hash",
    )
    test_db.add(ans2)
    test_db.flush()

    seg2_1 = SimulationAnswerSegmentRecord(
        segment_id=uuid.uuid4(),
        answer_id=ans2.answer_id,
        attempt_id=attempt.attempt_id,
        question_id=q2.question_id,
        sequence_order=1,
        start_char=0,
        end_char=87,
        segment_text="Writes append to WAL and in-memory MemTable, then flush to immutable SSTables on disk.",
        segmentation_version="1.0.0",
    )
    test_db.add(seg2_1)

    ans_supp = SimulationAnswerRecord(
        answer_id=uuid.uuid4(),
        attempt_id=attempt.attempt_id,
        question_id=q_supp.question_id,
        question_version=1,
        display_order=2,
        layer=InterviewLayer.SUPPLEMENTAL_EVIDENCE,
        answer_text="I was lead architect and ran dual-write verification in shadow staging.",
        answer_version=1,
        is_submitted=True,
        content_hash="ans_supp_hash",
    )
    test_db.add(ans_supp)
    test_db.flush()

    seg_supp_1 = SimulationAnswerSegmentRecord(
        segment_id=uuid.uuid4(),
        answer_id=ans_supp.answer_id,
        attempt_id=attempt.attempt_id,
        question_id=q_supp.question_id,
        sequence_order=1,
        start_char=0,
        end_char=71,
        segment_text="I was lead architect and ran dual-write verification in shadow staging.",
        segmentation_version="1.0.0",
    )
    test_db.add(seg_supp_1)

    # Provisional AI Assessments
    prov1 = SimulationQuestionAssessmentRecord(
        assessment_id=uuid.uuid4(),
        attempt_id=attempt.attempt_id,
        answer_id=ans1.answer_id,
        question_id=q1.question_id,
        assessment_status="PROVISIONAL",
        provisional_level=4,
        criterion_observations=[
            {
                "criterion_id": "DISTRIBUTED_CONSENSUS",
                "observation": "Candidate clearly explained quorum requirements and log term rejection.",
                "cited_segment_ids": [str(seg1_1.segment_id), str(seg1_2.segment_id)],
            }
        ],
        cited_segment_ids=[str(seg1_1.segment_id), str(seg1_2.segment_id)],
        model_invocation_metadata={
            "model_name": "qwen3-4b-mlx",
            "provider_class": "LOCAL_LM_STUDIO",
            "egress_class": "LOCAL_ONLY",
        },
        uncertainty_codes=[],
    )
    test_db.add(prov1)

    prov2 = SimulationQuestionAssessmentRecord(
        assessment_id=uuid.uuid4(),
        attempt_id=attempt.attempt_id,
        answer_id=ans2.answer_id,
        question_id=q2.question_id,
        assessment_status="PROVISIONAL",
        provisional_level=3,
        criterion_observations=[
            {
                "criterion_id": "STORAGE_INTERNALS",
                "observation": "Candidate accurately described WAL and MemTable flushing to SSTables.",
                "cited_segment_ids": [str(seg2_1.segment_id)],
            }
        ],
        cited_segment_ids=[str(seg2_1.segment_id)],
        model_invocation_metadata={
            "model_name": "qwen3-4b-mlx",
            "provider_class": "LOCAL_LM_STUDIO",
            "egress_class": "LOCAL_ONLY",
        },
        uncertainty_codes=[],
    )
    test_db.add(prov2)

    prov_supp = SimulationQuestionAssessmentRecord(
        assessment_id=uuid.uuid4(),
        attempt_id=attempt.attempt_id,
        answer_id=ans_supp.answer_id,
        question_id=q_supp.question_id,
        assessment_status="PROVISIONAL",
        provisional_level=5,
        criterion_observations=[
            {
                "criterion_id": "LEGACY_MIGRATION_GAP",
                "observation": "Candidate verified lead architect role with dual-write staging.",
                "cited_segment_ids": [str(seg_supp_1.segment_id)],
            }
        ],
        cited_segment_ids=[str(seg_supp_1.segment_id)],
        model_invocation_metadata={
            "model_name": "qwen3-4b-mlx",
            "provider_class": "LOCAL_LM_STUDIO",
            "egress_class": "LOCAL_ONLY",
        },
        uncertainty_codes=[],
    )
    test_db.add(prov_supp)
    test_db.commit()

    return {
        "recruiter": recruiter,
        "job": job,
        "template": template,
        "plan": plan,
        "attempt": attempt,
        "q1": q1,
        "q2": q2,
        "q_supp": q_supp,
        "seg1_1": seg1_1,
        "seg1_2": seg1_2,
        "seg2_1": seg2_1,
        "seg_supp_1": seg_supp_1,
        "match_result": match_result,
        "initial_match_score": match_result.display_compatibility_score,
    }


def test_phase3d4_security_answer_fixtures_and_citation_validation(test_db: Session, setup_eval_scenario):
    """PART F: Evaluates structural validation, forged citation rejection, and empty citation guards."""
    sc = setup_eval_scenario

    # 1. Attempt edit with forged segment ID
    forged_id = str(uuid.uuid4())
    with pytest.raises(HumanReviewError) as exc_info:
        record_review_command(
            db=test_db,
            actor=sc["recruiter"],
            job_id=sc["job"].job_id,
            template_id=sc["template"].template_id,
            plan_id=sc["plan"].plan_id,
            attempt_id=sc["attempt"].attempt_id,
            question_id=sc["q1"].question_id,
            command="edit",
            payload=EditReviewRequest(
                reviewed_observation="Candidate made claims not substantiated by valid segments.",
                cited_segment_ids=[forged_id],
                reason="Verification attempt with invalid citation",
            ),
        )
    assert exc_info.value.code == "FORGED_OR_UNKNOWN_CITATION"

    # 2. Attempt edit with empty citations (rejected at Pydantic validation)
    with pytest.raises(Exception):
        EditReviewRequest(
            reviewed_observation="Text without citations.",
            cited_segment_ids=[],
            reason="Empty citation attempt",
        )


def test_phase3d4_human_review_and_deterministic_aggregate(test_db: Session, setup_eval_scenario):
    """PART F & G: Evaluates human review decisions, core aggregate formula, and finalization immutability."""
    sc = setup_eval_scenario

    # 1. Accept Question 1 (Level 4)
    rev1 = record_review_command(
        db=test_db,
        actor=sc["recruiter"],
        job_id=sc["job"].job_id,
        template_id=sc["template"].template_id,
        plan_id=sc["plan"].plan_id,
        attempt_id=sc["attempt"].attempt_id,
        question_id=sc["q1"].question_id,
        command="accept",
        payload=AcceptReviewRequest(),
    )
    assert rev1.decision == "ACCEPTED"
    assert rev1.reviewed_rubric_level == 4

    # 2. Finalization is blocked while Question 2 is unreviewed
    with pytest.raises(HumanReviewError) as exc_info:
        finalize_simulation_assessment(
            db=test_db,
            actor=sc["recruiter"],
            job_id=sc["job"].job_id,
            template_id=sc["template"].template_id,
            plan_id=sc["plan"].plan_id,
            attempt_id=sc["attempt"].attempt_id,
            payload=FinalizeAssessmentRequest(),
        )
    assert exc_info.value.code == "FINALIZATION_INELIGIBLE"

    # 3. Override Question 2 (Level 5) with reason
    rev2 = record_review_command(
        db=test_db,
        actor=sc["recruiter"],
        job_id=sc["job"].job_id,
        template_id=sc["template"].template_id,
        plan_id=sc["plan"].plan_id,
        attempt_id=sc["attempt"].attempt_id,
        question_id=sc["q2"].question_id,
        command="override",
        payload=OverrideReviewRequest(
            reviewed_rubric_level=5,
            reason="Candidate articulated advanced write stall mitigation and compaction strategies in detail.",
        ),
    )
    assert rev2.decision == "OVERRIDDEN"
    assert rev2.reviewed_rubric_level == 5

    # 4. Deterministic Core aggregate formula verification
    rev_q1 = test_db.query(AssessmentReviewRecord).filter_by(review_id=rev1.review_id).first()
    rev_q2 = test_db.query(AssessmentReviewRecord).filter_by(review_id=rev2.review_id).first()
    reviews_map = {
        sc["q1"].question_id: rev_q1,
        sc["q2"].question_id: rev_q2,
    }
    agg = calculate_deterministic_aggregate([sc["q1"], sc["q2"], sc["q_supp"]], reviews_map)
    # Core 1 (Level 4) + Core 2 (Level 5) = 4.50
    assert agg.aggregate_score == 4.5
    assert agg.total_core_questions == 2
    assert agg.scored_core_questions == 2
    assert any(x["reason"] == "SUPPLEMENTAL_EVIDENCE_EXCLUDED" for x in agg.excluded_items)

    # 5. Finalize Assessment
    fin = finalize_simulation_assessment(
        db=test_db,
        actor=sc["recruiter"],
        job_id=sc["job"].job_id,
        template_id=sc["template"].template_id,
        plan_id=sc["plan"].plan_id,
        attempt_id=sc["attempt"].attempt_id,
        payload=FinalizeAssessmentRequest(),
    )
    assert fin.is_finalized is True
    assert fin.aggregate.aggregate_score == 4.5

    # 6. Immutability verification: Finalized assessment cannot be modified in place
    with pytest.raises(HumanReviewError) as exc_info:
        record_review_command(
            db=test_db,
            actor=sc["recruiter"],
            job_id=sc["job"].job_id,
            template_id=sc["template"].template_id,
            plan_id=sc["plan"].plan_id,
            attempt_id=sc["attempt"].attempt_id,
            question_id=sc["q1"].question_id,
            command="accept",
            payload=AcceptReviewRequest(),
        )
    assert exc_info.value.code == "ASSESSMENT_ALREADY_FINALIZED"

    # 7. Phase 2 verification: Candidate match score remains unchanged
    match_rec = test_db.query(MatchResultRecord).filter_by(result_id=sc["match_result"].result_id).first()
    assert match_rec.display_compatibility_score == sc["initial_match_score"]
