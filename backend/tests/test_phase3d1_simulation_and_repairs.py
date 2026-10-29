import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Tuple
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.auth import Base, UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.interview import (
    CandidateInterviewPlanRecord,
    InterviewQuestionRecord,
    InterviewRubricRecord,
    JobInterviewTemplateRecord,
    SimulationAnswerRecord,
    SimulationAttemptRecord,
)
from app.models.job import (
    JobLifecycleStatus,
    JobRequirement,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementOrigin,
    RequirementPriority,
)
from app.models.matching import (
    MatchResultRecord,
    MatchRunLifecycleStatus,
    MatchRunRecord,
)
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    QuestionType,
    ValidationState,
)
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_VERSION,
)
from app.schemas.interview_snapshot import (
    DETERMINISTIC_BUILDER_VERSION,
    QUESTION_CATALOGUE_VERSION,
    RUBRIC_CATALOGUE_VERSION,
    JobBlueprintInputSnapshot,
    JobRequirementSnapshotItem,
)
from app.services.interview_generator_service import (
    CompetencySelectionResult,
    InterviewPolicyValidator,
    _build_fallback_question_for_missing_competency,
)
from app.services.interview_simulation_service import (
    SimulationServiceError,
    get_simulation_attempt,
    save_draft_simulation_answer,
    start_or_resume_simulation_attempt,
    submit_simulation_attempt,
)
from app.services.interview_snapshot_service import InterviewUnauthorizedError
from app.schemas.interview_api import (
    RestoreDeterministicWordingRequest,
    SimulationAnswerSaveRequest,
    SimulationSubmitRequest,
)
from app.services.wording_assistance_service import (
    QuestionWordingError,
    restore_deterministic_wording,
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
def test_setup(test_db: Session):
    org_id = uuid.uuid4()

    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email=f"recruiter-{uuid.uuid4().hex[:6]}@example.com",
        password_hash="fakehashdummyvalue",
        display_name="Recruiter User",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    other_recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email=f"other-{uuid.uuid4().hex[:6]}@example.com",
        password_hash="fakehashdummyvalue",
        display_name="Other Recruiter",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add_all([recruiter, other_recruiter])
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
        title="Senior Distributed Systems Engineer",
        created_by_user_id=recruiter.user_id,
        version_status=JobVersionStatus.APPROVED,
    )
    test_db.add(job_ver)
    test_db.flush()

    r_item = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=job_ver.job_version_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
        label="Distributed Consensus",
        normalized_label="distributed consensus",
    )
    test_db.add(r_item)
    test_db.flush()

    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="alice_resume.pdf",
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
        full_name="Alice Lovelace",
        primary_email="alice@example.com",
        primary_phone="+1-555-0100",
        identity_payload={"name": "Alice Lovelace", "email": "alice@example.com"},
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
        raw_compatibility_score=Decimal("92.0"),
        display_compatibility_score=Decimal("92.0"),
        confidence_tier="HIGH",
        warning_count=0,
        must_have_warning_count=0,
        rank=1,
        score_decomposition={},
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
        content_hash=hashlib.sha256(b"tmpl_content").hexdigest(),
        input_snapshot_hash=hashlib.sha256(b"snap").hexdigest(),
        created_by_user_id=recruiter.user_id,
        guardrail_policy_version=SAFETY_POLICY_VERSION,
        guardrail_policy_hash=SAFETY_POLICY_HASH,
        deterministic_builder_version="1.0.0",
        question_catalogue_version="1.0.0",
        rubric_catalogue_version="1.0.0",
        record_version=1,
    )
    test_db.add(template)
    test_db.flush()

    q1 = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        display_order=1,
        competency_id="DISTRIBUTED_SYSTEMS",
        question_text="How do you ensure data consistency in a distributed system?",
        question_intent="Assess distributed consensus understanding",
        estimated_time_minutes=8,
        included_in_common_score=True,
        validation_state=ValidationState.PASSED,
        permitted_probes=["What about network partitions?"],
        safe_metadata={
            "deterministic_source_text": "How do you ensure data consistency in a distributed system?",
            "deterministic_source_hash": hashlib.sha256(b"How do you ensure data consistency in a distributed system?").hexdigest(),
        },
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
        rating_anchors={"1": "No understanding", "3": "Solid standard knowledge", "5": "Expert depth"},
        sufficient_evidence_guidance="Concrete consensus examples",
        insufficient_evidence_guidance="Vague answers",
        permitted_probes=["What about network partitions?"],
        prohibited_inferences=[],
        subject_matter_review_status="REVIEWED",
        validation_state=ValidationState.PASSED,
    )
    test_db.add(r1)

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
        guardrail_policy_version=SAFETY_POLICY_VERSION,
        scoring_separation_policy="STRICT_COMMON_CORE_SEPARATION",
        input_snapshot_hash=hashlib.sha256(b"snap").hexdigest(),
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(plan)
    test_db.commit()

    return {
        "org_id": org_id,
        "recruiter": recruiter,
        "other_recruiter": other_recruiter,
        "job": job,
        "job_version": job_ver,
        "identity": identity,
        "profile": profile,
        "match_result": match_result,
        "template": template,
        "q1": q1,
        "plan": plan,
    }


# =========================================================================
# Part A2 Tests: Restore Deterministic Eligibility & Concurrency
# =========================================================================

def test_restore_deterministic_eligibility_and_idempotency(test_db: Session, test_setup: dict):
    """Verifies restore works, is idempotent, and updates version accurately."""
    recruiter = test_setup["recruiter"]
    org_id = test_setup["org_id"]

    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    template = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=uuid.uuid4(),
        revision=1,
        template_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        content_hash="mock",
        input_snapshot_hash="mock",
        created_by_user_id=recruiter.user_id,
        guardrail_policy_version="1.0",
        guardrail_policy_hash="mock",
        deterministic_builder_version="1.0",
        question_catalogue_version="1.0",
        rubric_catalogue_version="1.0",
    )
    test_db.add(template)
    test_db.flush()

    # Question modified by AI / recruiter
    q = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        authoring_origin=AuthoringOrigin.AI_ASSISTED,
        display_order=1,
        question_text="How would you design a fault-tolerant distributed cache?",
        question_intent="Assess distributed architecture",
        estimated_time_minutes=5,
        included_in_common_score=True,
        validation_state=ValidationState.PASSED,
        safe_metadata={
            "deterministic_source_text": "Explain distributed caching strategies.",
            "deterministic_source_hash": hashlib.sha256(b"Explain distributed caching strategies.").hexdigest(),
        },
    )
    test_db.add(q)
    test_db.commit()

    # 1. Restore original deterministic wording
    req = RestoreDeterministicWordingRequest(expected_question_version=1)
    res = restore_deterministic_wording(actor=recruiter, question_id=q.question_id, request=req, db=test_db)

    assert res.question_text == "Explain distributed caching strategies."
    assert res.authoring_origin == AuthoringOrigin.DETERMINISTIC
    assert res.question_version == 2
    assert res.can_restore is False
    assert res.has_wording_drift is False

    # 2. Idempotency test: calling restore again returns current state without error or unnecessary version bump
    req_idempotent = RestoreDeterministicWordingRequest(expected_question_version=2)
    res_idempotent = restore_deterministic_wording(actor=recruiter, question_id=q.question_id, request=req_idempotent, db=test_db)
    assert res_idempotent.question_text == "Explain distributed caching strategies."
    assert res_idempotent.question_version == 2


def test_ai_fallback_without_deterministic_source_cannot_restore(test_db: Session, test_setup: dict):
    """Verifies that an AI fallback question with no deterministic baseline cannot be restored."""
    recruiter = test_setup["recruiter"]
    org_id = test_setup["org_id"]

    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    template = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=uuid.uuid4(),
        revision=1,
        template_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        content_hash="mock",
        input_snapshot_hash="mock",
        created_by_user_id=recruiter.user_id,
        guardrail_policy_version="1.0",
        guardrail_policy_hash="mock",
        deterministic_builder_version="1.0",
        question_catalogue_version="1.0",
        rubric_catalogue_version="1.0",
    )
    test_db.add(template)
    test_db.flush()

    # AI fallback question without deterministic_source_text
    q = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        authoring_origin=AuthoringOrigin.AI_ASSISTED,
        display_order=1,
        question_text="Generated AI fallback question for rare competency.",
        question_intent="Assess rare competency",
        estimated_time_minutes=5,
        included_in_common_score=True,
        validation_state=ValidationState.PASSED,
        safe_metadata={"ai_fallback": True, "reason": "AI_FALLBACK_MISSING_DETERMINISTIC_QUESTION"},
    )
    test_db.add(q)
    test_db.commit()

    req = RestoreDeterministicWordingRequest(expected_question_version=1)
    with pytest.raises(QuestionWordingError) as exc_info:
        restore_deterministic_wording(actor=recruiter, question_id=q.question_id, request=req, db=test_db)

    assert exc_info.value.code == "NO_DETERMINISTIC_SOURCE"
    assert exc_info.value.status_code == 400


def test_stale_question_version_restore_rejected_with_safe_conflict(test_db: Session, test_setup: dict):
    """Verifies that sending a stale question version raises STALE_QUESTION_VERSION (409)."""
    recruiter = test_setup["recruiter"]
    org_id = test_setup["org_id"]

    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    template = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=uuid.uuid4(),
        revision=1,
        template_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        content_hash="mock",
        input_snapshot_hash="mock",
        created_by_user_id=recruiter.user_id,
        guardrail_policy_version="1.0",
        guardrail_policy_hash="mock",
        deterministic_builder_version="1.0",
        question_catalogue_version="1.0",
        rubric_catalogue_version="1.0",
    )
    test_db.add(template)
    test_db.flush()

    q = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template.template_id,
        question_version=3,  # Current is 3
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        authoring_origin=AuthoringOrigin.AI_ASSISTED,
        display_order=1,
        question_text="Current modified text.",
        question_intent="Assess tech",
        estimated_time_minutes=5,
        included_in_common_score=True,
        validation_state=ValidationState.PASSED,
        safe_metadata={
            "deterministic_source_text": "Original text.",
            "deterministic_source_hash": hashlib.sha256(b"Original text.").hexdigest(),
        },
    )
    test_db.add(q)
    test_db.commit()

    # Send stale expected version 1
    req = RestoreDeterministicWordingRequest(expected_question_version=1)
    with pytest.raises(QuestionWordingError) as exc_info:
        restore_deterministic_wording(actor=recruiter, question_id=q.question_id, request=req, db=test_db)

    assert exc_info.value.code == "STALE_QUESTION_VERSION"
    assert exc_info.value.status_code == 409


# =========================================================================
# Part A3 Tests: Bounded AI Fallback for Missing Deterministic Slot
# =========================================================================

def test_missing_deterministic_question_creates_unresolved_slot_when_ai_unavailable():
    """Verifies that missing deterministic catalogue entry creates manual-required unresolved slot."""
    req_item = JobRequirementSnapshotItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Quantum Cryptography",
        normalized_label="quantum cryptography",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
    )

    snapshot = JobBlueprintInputSnapshot(
        job_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        revision=1,
        organization_id=uuid.uuid4(),
        job_readiness_state="APPROVED",
        approved_requirements=[req_item],
    )

    comp = CompetencySelectionResult(
        competency_key="QUANTUM_CRYPTOGRAPHY_ADVANCED",
        competency_label="Quantum Cryptography Advanced",
        priority_score=1.0,
        selection_reason="Target skill",
        covered_requirement_ids=[uuid.uuid4()],
    )

    # When AI is unavailable / None
    q = _build_fallback_question_for_missing_competency(
        competency=comp,
        display_order=1,
        blueprint_id=uuid.uuid4(),
        snapshot=snapshot,
        ai_generated_text=None,
    )

    assert q.question_text == "[Manual question required]"
    assert q.validation_state == ValidationState.PENDING
    assert q.authoring_origin == AuthoringOrigin.DETERMINISTIC


# =========================================================================
# Part B Tests: Simulation Attempt & Answer Lifecycle
# =========================================================================

def test_simulation_start_requires_approved_plan(test_db: Session, test_setup: dict):
    """Verifies that DRAFT plans cannot start simulation."""
    recruiter = test_setup["recruiter"]
    org_id = test_setup["org_id"]

    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    template = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=uuid.uuid4(),
        revision=1,
        template_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        content_hash="mock",
        input_snapshot_hash="mock",
        created_by_user_id=recruiter.user_id,
        guardrail_policy_version="1.0",
        guardrail_policy_hash="mock",
        deterministic_builder_version="1.0",
        question_catalogue_version="1.0",
        rubric_catalogue_version="1.0",
    )
    test_db.add(template)
    test_db.flush()

    plan = CandidateInterviewPlanRecord(
        plan_id=uuid.uuid4(),
        organization_id=org_id,
        template_id=template.template_id,
        candidate_id=test_setup["identity"].identity_id,
        profile_id=test_setup["profile"].profile_id,
        profile_version=1,
        match_result_id=test_setup["match_result"].result_id,
        match_result_fingerprint="fp123",
        template_version=1,
        plan_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        input_snapshot_hash="mock",
        created_by_user_id=recruiter.user_id,
        guardrail_policy_version="1.0",
        scoring_separation_policy="STRICT_COMMON_CORE_SEPARATION",
    )
    test_db.add(plan)
    test_db.commit()

    with pytest.raises(SimulationServiceError) as exc_info:
        start_or_resume_simulation_attempt(
            actor=recruiter,
            job_id=job.job_id,
            template_id=template.template_id,
            plan_id=plan.plan_id,
            db=test_db,
        )

    assert exc_info.value.code == "PLAN_NOT_APPROVED"
    assert exc_info.value.status_code == 409


def test_simulation_cross_org_access_denied(test_db: Session, test_setup: dict):
    """Verifies that an unauthorized recruiter from another org is rejected (403)."""
    other_recruiter = test_setup["other_recruiter"]
    plan = test_setup["plan"]
    template = test_setup["template"]
    job = test_setup["job"]

    with pytest.raises(InterviewUnauthorizedError) as exc_info:
        start_or_resume_simulation_attempt(
            actor=other_recruiter,
            job_id=job.job_id,
            template_id=template.template_id,
            plan_id=plan.plan_id,
            db=test_db,
        )

    assert exc_info.value.code == "UNAUTHORIZED_JOB_ACCESS"
    assert exc_info.value.status_code == 403


def test_simulation_start_save_and_submit_workflow(test_db: Session, test_setup: dict):
    """End-to-end simulation attempt lifecycle: start, autosave draft answer, optimistic concurrency, and submit lock."""
    recruiter = test_setup["recruiter"]
    plan = test_setup["plan"]
    template = test_setup["template"]
    job = test_setup["job"]
    q1 = test_setup["q1"]

    # 1. Start simulation
    attempt_resp = start_or_resume_simulation_attempt(
        actor=recruiter,
        job_id=job.job_id,
        template_id=template.template_id,
        plan_id=plan.plan_id,
        db=test_db,
    )

    assert attempt_resp.lifecycle_status == "IN_PROGRESS"
    assert attempt_resp.plan_id == plan.plan_id
    assert len(attempt_resp.questions) == 1
    assert attempt_resp.questions[0].question_id == q1.question_id

    # 2. Save draft answer
    save_req = SimulationAnswerSaveRequest(
        answer_text="The candidate explained leader election using Raft and split-brain mitigation.",
        expected_answer_version=1,
    )
    save_res = save_draft_simulation_answer(
        actor=recruiter,
        job_id=job.job_id,
        template_id=template.template_id,
        plan_id=plan.plan_id,
        attempt_id=attempt_resp.attempt_id,
        question_id=q1.question_id,
        request=save_req,
        db=test_db,
    )

    assert save_res.answer.answer_version == 2
    assert save_res.attempt_status == "IN_PROGRESS"
    assert save_res.answered_questions_count == 1

    # 3. Concurrency check on answer save
    stale_save_req = SimulationAnswerSaveRequest(
        answer_text="Stale concurrent overwrite attempt.",
        expected_answer_version=1,  # Stale! Current is 2
    )
    with pytest.raises(SimulationServiceError) as exc_info:
        save_draft_simulation_answer(
            actor=recruiter,
            job_id=job.job_id,
            template_id=template.template_id,
            plan_id=plan.plan_id,
            attempt_id=attempt_resp.attempt_id,
            question_id=q1.question_id,
            request=stale_save_req,
            db=test_db,
        )
    assert exc_info.value.code == "STALE_ANSWER_VERSION"
    assert exc_info.value.status_code == 409

    # 4. Submit simulation
    submit_req = SimulationSubmitRequest(expected_record_version=attempt_resp.record_version)
    submit_res = submit_simulation_attempt(
        actor=recruiter,
        job_id=job.job_id,
        template_id=template.template_id,
        plan_id=plan.plan_id,
        attempt_id=attempt_resp.attempt_id,
        request=submit_req,
        db=test_db,
    )

    assert submit_res.lifecycle_status == "SUBMITTED"
    assert submit_res.submitted_at is not None
    assert submit_res.answered_questions_count == 1

    # 5. Immutability verification: saving to a submitted attempt is rejected
    post_submit_req = SimulationAnswerSaveRequest(
        answer_text="Post-submission edit attempt.",
        expected_answer_version=2,
    )
    with pytest.raises(SimulationServiceError) as exc_info:
        save_draft_simulation_answer(
            actor=recruiter,
            job_id=job.job_id,
            template_id=template.template_id,
            plan_id=plan.plan_id,
            attempt_id=attempt_resp.attempt_id,
            question_id=q1.question_id,
            request=post_submit_req,
            db=test_db,
        )
    assert exc_info.value.code == "ATTEMPT_IMMUTABLE"
    assert exc_info.value.status_code == 409
