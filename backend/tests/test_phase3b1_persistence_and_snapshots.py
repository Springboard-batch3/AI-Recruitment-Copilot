import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.auth import Base, UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.gap_report import GapReportItemRecord, GapReportRecord
from app.models.interview import (
    CandidateInterviewPlanRecord,
    InterviewQuestionRecord,
    InterviewRubricRecord,
    JobInterviewTemplateRecord,
    SupplementalQuestionProvenanceRecord,
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
from app.schemas.interview_snapshot import (
    DETERMINISTIC_BUILDER_VERSION,
    QUESTION_CATALOGUE_VERSION,
    RUBRIC_CATALOGUE_VERSION,
    SCORING_SEPARATION_POLICY,
    CandidatePlanInputSnapshot,
    CandidateRequirementResultSnapshotItem,
    JobBlueprintInputSnapshot,
    JobRequirementSnapshotItem,
    PolicyVersionReference,
    canonicalize_candidate_plan_snapshot,
    canonicalize_job_blueprint_snapshot,
    compute_candidate_plan_snapshot_hash,
    compute_job_blueprint_snapshot_hash,
)
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    QuestionType,
    ValidationState,
)
from app.schemas.requirement_gap import RequirementGapState
from app.schemas.requirement_match import MatchResultState
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_ID,
    SAFETY_POLICY_VERSION,
    get_current_safety_policy,
)
from app.services.interview_snapshot_service import (
    InterviewCandidateNotFoundError,
    InterviewJobNotReadyError,
    InterviewJobRevisionStaleError,
    InterviewMatchResultMismatchError,
    InterviewProfileVersionMismatchError,
    InterviewRequisitionNotFoundError,
    InterviewSnapshotError,
    InterviewSourceRelationshipInvalidError,
    InterviewUnauthorizedError,
    load_authoritative_candidate_plan_snapshot,
    load_authoritative_job_blueprint_snapshot,
)


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _seed_recruiter(db, email="recruiter.phase3b@example.com") -> UserRecord:
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email=email,
        display_name="Phase 3B Recruiter",
        password_hash="mock_hash_123",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    db.add(recruiter)
    db.commit()
    return recruiter


def _seed_job_and_version(
    db,
    recruiter_id: uuid.UUID,
    status=JobVersionStatus.READY_FOR_MATCHING,
    revision=1,
) -> Tuple[JobRequisition, JobVersion, List[JobRequirement]]:
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    db.add(job)
    db.flush()

    ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=revision,
        version_status=status,
        title="Staff Platform Engineer",
        created_by_user_id=recruiter_id,
    )
    db.add(ver)
    db.flush()

    r1 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.CRITICAL,
        origin=RequirementOrigin.RECRUITER_ENTERED,
        label="PostgreSQL Performance Tuning",
        normalized_label="postgresql performance tuning",
        rationale="Critical for database scaling and query optimization",
        min_experience_months=36,
        recency_years=3,
        accepted_evidence_types=["EMPLOYMENT", "PROJECT"],
    )
    r2 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        display_order=1,
        category=RequirementCategory.SKILL,
        necessity=RequirementNecessity.PREFERRED,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
        label="Distributed Systems",
        normalized_label="distributed systems",
        rationale=None,
        min_experience_months=24,
    )
    db.add_all([r1, r2])
    db.commit()
    return job, ver, [r1, r2]


def _seed_candidate_and_match(
    db,
    job: JobRequisition,
    ver: JobVersion,
    recruiter_id: uuid.UUID,
    requirements: List[JobRequirement],
) -> Tuple[CandidateProfileRecord, MatchResultRecord, MatchRunRecord]:
    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="alice_platform_resume.pdf",
        sha256_hash="0" * 64,
        size_bytes=1024,
        declared_mime_type="application/pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    db.add(artifact)
    db.flush()

    identity = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        full_name="Alice Candidate",
        primary_email="alice.candidate@example.com",
        primary_phone="+1-555-0199",
        identity_payload={"name": "Alice Candidate", "email": "alice@example.com"},
    )
    db.add(identity)
    db.flush()

    profile = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=1,
        artifact_id=artifact.artifact_id,
        extraction_id=uuid.uuid4(),
        identity_id=identity.identity_id,
        parser_pipeline_version="1.0.0",
        professional_profile={
            "headline": "Staff Platform Engineer",
            "skills": [
                {"value": "PostgreSQL", "confidence": 0.95, "evidence_ids": ["ev-1"]},
                {"value": "Distributed Systems", "confidence": 0.90, "evidence_ids": ["ev-2"]},
            ],
            "employment": [],
        },
        evidence_spans=[],
    )
    db.add(profile)
    db.flush()

    now = datetime.now(timezone.utc)
    match_run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        evaluation_date=now,
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
    db.add(match_run)
    db.flush()

    match_result = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=match_run.run_id,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        raw_compatibility_score=Decimal("88.500000"),
        display_compatibility_score=Decimal("88.50"),
        confidence_tier="HIGH",
        warning_count=0,
        must_have_warning_count=0,
        rank=1,
        score_decomposition={
            "requirement_contributions": [
                {
                    "requirement_id": str(requirements[0].requirement_id),
                    "category": "SKILL",
                    "necessity": "MUST_HAVE",
                    "priority": "CRITICAL",
                    "match_state": "MATCHED",
                    "match_value": 1.0,
                    "evidence_references": ["ev-1"],
                },
                {
                    "requirement_id": str(requirements[1].requirement_id),
                    "category": "SKILL",
                    "necessity": "PREFERRED",
                    "priority": "HIGH",
                    "match_state": "PARTIALLY_MATCHED",
                    "match_value": 0.5,
                    "evidence_references": ["ev-2"],
                },
            ]
        },
    )
    db.add(match_result)
    db.commit()
    return profile, match_result, match_run


# ==============================================================================
# TEST GROUP 1: PERSISTENCE AND CONSTRAINTS
# ==============================================================================

def test_job_interview_template_and_question_persistence(test_db):
    """Verifies that Phase 3B models instantiate, link, and persist accurately in the relational DB."""
    recruiter = _seed_recruiter(test_db)
    job, ver, reqs = _seed_job_and_version(test_db, recruiter.user_id)

    # 1. Create JobInterviewTemplateRecord
    template = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        template_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        guardrail_policy_version=SAFETY_POLICY_VERSION,
        guardrail_policy_hash=SAFETY_POLICY_HASH,
        deterministic_builder_version=DETERMINISTIC_BUILDER_VERSION,
        question_catalogue_version=QUESTION_CATALOGUE_VERSION,
        rubric_catalogue_version=RUBRIC_CATALOGUE_VERSION,
        input_snapshot_hash="hash_" + "0" * 59,
        created_by_user_id=recruiter.user_id,
        record_version=1,
    )
    test_db.add(template)
    test_db.commit()

    # 2. Add InterviewQuestionRecord (Common Core)
    q1 = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        display_order=0,
        competency_id="DB_PERF_TUNING",
        requirement_ids=[str(reqs[0].requirement_id)],
        question_text="How do you investigate and optimize high-latency queries in PostgreSQL?",
        question_intent="Evaluate deep understanding of query planning and indexing.",
        estimated_time_minutes=10,
        included_in_common_score=True,
        validation_state=ValidationState.PASSED,
    )
    test_db.add(q1)
    test_db.commit()

    # 3. Add InterviewRubricRecord
    rubric = InterviewRubricRecord(
        rubric_id=uuid.uuid4(),
        question_id=q1.question_id,
        rubric_version=1,
        competency_definition="Demonstrated expertise in PostgreSQL indexing and execution plans.",
        question_intent="Assess database execution plan interpretation.",
        observable_indicators=["Mentions EXPLAIN ANALYZE", "Discusses index types and buffer cache"],
        rating_anchors={
            "1": "Superficial knowledge; does not know EXPLAIN.",
            "3": "Knows EXPLAIN ANALYZE, discusses B-tree indexes.",
            "5": "Expert analysis; discusses buffer cache, vacuuming, and lock contention.",
        },
        sufficient_evidence_guidance="Candidate provides real examples of isolating sequential scans.",
        insufficient_evidence_guidance="Candidate only mentions general SQL syntax without tuning details.",
        validation_state=ValidationState.PASSED,
    )
    test_db.add(rubric)
    test_db.commit()

    # Verify relationships
    loaded_tpl = test_db.query(JobInterviewTemplateRecord).filter_by(template_id=template.template_id).first()
    assert loaded_tpl is not None
    assert len(loaded_tpl.questions) == 1
    assert loaded_tpl.questions[0].rubric is not None
    assert loaded_tpl.questions[0].rubric.rubric_id == rubric.rubric_id


def test_layer_scoring_integrity_check_constraint(test_db):
    """Verifies that DB-level check constraint rejects invalid layer/scoring combinations."""
    recruiter = _seed_recruiter(test_db)
    job, ver, reqs = _seed_job_and_version(test_db, recruiter.user_id)

    template = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        template_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        guardrail_policy_version=SAFETY_POLICY_VERSION,
        guardrail_policy_hash=SAFETY_POLICY_HASH,
        deterministic_builder_version=DETERMINISTIC_BUILDER_VERSION,
        question_catalogue_version=QUESTION_CATALOGUE_VERSION,
        rubric_catalogue_version=RUBRIC_CATALOGUE_VERSION,
        input_snapshot_hash="hash_" + "1" * 59,
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(template)
    test_db.commit()

    # Violate constraint: COMMON_CORE with included_in_common_score=False
    invalid_q = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        display_order=0,
        question_text="Valid question text here.",
        question_intent="Valid intent.",
        estimated_time_minutes=5,
        included_in_common_score=False,  # Violation!
    )
    test_db.add(invalid_q)
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()


def test_candidate_interview_plan_and_supplemental_provenance_persistence(test_db):
    """Verifies persistence of CandidateInterviewPlanRecord and SupplementalQuestionProvenanceRecord."""
    recruiter = _seed_recruiter(test_db)
    job, ver, reqs = _seed_job_and_version(test_db, recruiter.user_id)
    profile, match_result, match_run = _seed_candidate_and_match(test_db, job, ver, recruiter.user_id, reqs)

    template = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        template_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        guardrail_policy_version=SAFETY_POLICY_VERSION,
        guardrail_policy_hash=SAFETY_POLICY_HASH,
        deterministic_builder_version=DETERMINISTIC_BUILDER_VERSION,
        question_catalogue_version=QUESTION_CATALOGUE_VERSION,
        rubric_catalogue_version=RUBRIC_CATALOGUE_VERSION,
        input_snapshot_hash="hash_" + "2" * 59,
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(template)
    test_db.commit()

    # Supplemental Question
    q_supp = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template.template_id,
        question_version=1,
        layer=InterviewLayer.SUPPLEMENTAL_EVIDENCE,
        question_type=QuestionType.EVIDENCE_CLARIFICATION,
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        display_order=1,
        question_text="Could you describe your hands-on experience with distributed consensus algorithms?",
        question_intent="Clarify partial evidence on distributed systems.",
        estimated_time_minutes=5,
        included_in_common_score=False,
    )
    test_db.add(q_supp)
    test_db.commit()

    plan = CandidateInterviewPlanRecord(
        plan_id=uuid.uuid4(),
        organization_id=recruiter.user_id,
        template_id=template.template_id,
        template_version=template.template_version,
        candidate_id=profile.identity_id,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        match_result_id=match_result.result_id,
        match_result_fingerprint=match_run.input_fingerprint,
        plan_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        guardrail_policy_version=SAFETY_POLICY_VERSION,
        scoring_separation_policy=SCORING_SEPARATION_POLICY,
        input_snapshot_hash="cand_hash_" + "3" * 54,
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(plan)
    test_db.commit()

    prov = SupplementalQuestionProvenanceRecord(
        provenance_id=uuid.uuid4(),
        question_id=q_supp.question_id,
        plan_id=plan.plan_id,
        source_requirement_id=reqs[1].requirement_id,
        source_match_result_id=match_result.result_id,
        source_match_state="PARTIALLY_MATCHED",
        allowlisted_evidence_ids=["ev-2"],
        excluded_from_common_score=True,
    )
    test_db.add(prov)
    test_db.commit()

    loaded_plan = test_db.query(CandidateInterviewPlanRecord).filter_by(plan_id=plan.plan_id).first()
    assert loaded_plan is not None
    assert len(loaded_plan.provenances) == 1
    assert loaded_plan.provenances[0].source_requirement_id == reqs[1].requirement_id
    assert loaded_plan.provenances[0].excluded_from_common_score is True


# ==============================================================================
# TEST GROUP 2: AUTHORITATIVE INPUT AND AUTHORIZATION
# ==============================================================================

def test_authoritative_job_blueprint_loader_assigned_recruiter_success(test_db):
    """Verifies that an assigned recruiter can load a valid ready job snapshot with full integrity."""
    recruiter = _seed_recruiter(test_db)
    job, ver, reqs = _seed_job_and_version(test_db, recruiter.user_id)

    snapshot, snapshot_hash = load_authoritative_job_blueprint_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        db=test_db,
    )

    assert isinstance(snapshot, JobBlueprintInputSnapshot)
    assert snapshot.job_id == job.job_id
    assert snapshot.job_version_id == ver.job_version_id
    assert snapshot.revision == 1
    assert snapshot.job_readiness_state == JobVersionStatus.READY_FOR_MATCHING.value
    assert len(snapshot.approved_requirements) == 2
    assert snapshot.approved_requirements[0].must_have_rationale == "Critical for database scaling and query optimization"
    assert len(snapshot_hash) == 64
    assert snapshot.policy_reference.policy_version == SAFETY_POLICY_VERSION


def test_authoritative_job_blueprint_loader_wrong_recruiter_denied(test_db):
    """Verifies that a recruiter who does not own the requisition is denied (existence concealed with 404)."""
    owner_recruiter = _seed_recruiter(test_db, email="owner@example.com")
    intruder_recruiter = _seed_recruiter(test_db, email="intruder@example.com")
    job, ver, reqs = _seed_job_and_version(test_db, owner_recruiter.user_id)

    with pytest.raises(InterviewRequisitionNotFoundError):
        load_authoritative_job_blueprint_snapshot(
            actor=intruder_recruiter,
            job_id=job.job_id,
            expected_revision=1,
            db=test_db,
        )


def test_authoritative_job_blueprint_loader_rejects_non_ready_and_stale_revisions(test_db):
    """Verifies that DRAFT revisions and non-existent revisions are rejected with proper domain errors."""
    recruiter = _seed_recruiter(test_db)
    job, ver, reqs = _seed_job_and_version(
        test_db,
        recruiter.user_id,
        status=JobVersionStatus.DRAFT,
        revision=1,
    )

    # 1. Reject DRAFT status with INTERVIEW_JOB_NOT_READY
    with pytest.raises(InterviewJobNotReadyError):
        load_authoritative_job_blueprint_snapshot(
            actor=recruiter,
            job_id=job.job_id,
            expected_revision=1,
            db=test_db,
        )

    # 2. Reject stale revision number with INTERVIEW_JOB_REVISION_STALE
    ver.version_status = JobVersionStatus.READY_FOR_MATCHING
    test_db.commit()

    with pytest.raises(InterviewJobRevisionStaleError):
        load_authoritative_job_blueprint_snapshot(
            actor=recruiter,
            job_id=job.job_id,
            expected_revision=99,  # Stale
            db=test_db,
        )


def test_authoritative_candidate_plan_loader_success_and_relationship_mismatches(test_db):
    """Verifies candidate snapshot loading and enforcement of candidate/job/match relationships."""
    recruiter = _seed_recruiter(test_db)
    job, ver, reqs = _seed_job_and_version(test_db, recruiter.user_id)
    profile, match_result, match_run = _seed_candidate_and_match(test_db, job, ver, recruiter.user_id, reqs)

    # 1. Success case
    cand_snapshot, cand_hash = load_authoritative_candidate_plan_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        candidate_id=profile.identity_id,
        profile_version=1,
        match_result_id=match_result.result_id,
        db=test_db,
    )

    assert isinstance(cand_snapshot, CandidatePlanInputSnapshot)
    assert cand_snapshot.profile_id == profile.profile_id
    assert cand_snapshot.profile_version == 1
    assert cand_snapshot.match_score == 88.5
    assert len(cand_snapshot.requirement_results) == 2
    assert len(cand_hash) == 64

    # 2. Mismatched candidate ID rejected
    with pytest.raises(InterviewSourceRelationshipInvalidError):
        load_authoritative_candidate_plan_snapshot(
            actor=recruiter,
            job_id=job.job_id,
            expected_revision=1,
            candidate_id=uuid.uuid4(),  # Unrelated candidate ID
            profile_version=1,
            match_result_id=match_result.result_id,
            db=test_db,
        )

    # 3. Mismatched profile version rejected
    with pytest.raises(InterviewProfileVersionMismatchError):
        load_authoritative_candidate_plan_snapshot(
            actor=recruiter,
            job_id=job.job_id,
            expected_revision=1,
            candidate_id=profile.identity_id,
            profile_version=2,  # Mismatched version
            match_result_id=match_result.result_id,
            db=test_db,
        )


# ==============================================================================
# TEST GROUP 3: DETERMINISTIC INTEGRITY
# ==============================================================================

def test_deterministic_snapshot_hashing_stability(test_db):
    """Verifies that two independent loads of identical records produce bit-for-bit identical hashes."""
    recruiter = _seed_recruiter(test_db)
    job, ver, reqs = _seed_job_and_version(test_db, recruiter.user_id)
    profile, match_result, match_run = _seed_candidate_and_match(test_db, job, ver, recruiter.user_id, reqs)

    snap1, hash1 = load_authoritative_candidate_plan_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        candidate_id=profile.identity_id,
        profile_version=1,
        match_result_id=match_result.result_id,
        db=test_db,
    )

    snap2, hash2 = load_authoritative_candidate_plan_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        candidate_id=profile.identity_id,
        profile_version=1,
        match_result_id=match_result.result_id,
        db=test_db,
    )

    assert hash1 == hash2
    assert canonicalize_candidate_plan_snapshot(snap1) == canonicalize_candidate_plan_snapshot(snap2)


def test_deterministic_hashing_independent_of_requirement_order():
    """Verifies that shuffling requirement ordering does not alter the canonical snapshot hash."""
    req_id1 = uuid.uuid4()
    req_id2 = uuid.uuid4()

    item1 = JobRequirementSnapshotItem(
        requirement_id=req_id1,
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.CRITICAL,
    )
    item2 = JobRequirementSnapshotItem(
        requirement_id=req_id2,
        display_order=1,
        category=RequirementCategory.SKILL,
        label="Docker",
        normalized_label="docker",
        necessity=RequirementNecessity.PREFERRED,
        priority=RequirementPriority.MEDIUM,
    )

    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    ver_id = uuid.uuid4()

    snap_order_a = JobBlueprintInputSnapshot(
        organization_id=org_id,
        job_id=job_id,
        job_version_id=ver_id,
        revision=1,
        job_readiness_state="READY_FOR_MATCHING",
        approved_requirements=[item1, item2],
    )

    snap_order_b = JobBlueprintInputSnapshot(
        organization_id=org_id,
        job_id=job_id,
        job_version_id=ver_id,
        revision=1,
        job_readiness_state="READY_FOR_MATCHING",
        approved_requirements=[item2, item1],  # Shuffled
    )

    hash_a = compute_job_blueprint_snapshot_hash(snap_order_a)
    hash_b = compute_job_blueprint_snapshot_hash(snap_order_b)

    assert hash_a == hash_b


def test_candidate_pii_and_secrets_strictly_excluded():
    """Verifies that candidate PII and provider secrets are strictly excluded from snapshots."""
    req_id = uuid.uuid4()
    item = JobRequirementSnapshotItem(
        requirement_id=req_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Kubernetes",
        normalized_label="kubernetes",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
    )
    job_snap = JobBlueprintInputSnapshot(
        organization_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        revision=1,
        job_readiness_state="READY_FOR_MATCHING",
        approved_requirements=[item],
    )

    cand_snap = CandidatePlanInputSnapshot(
        job_snapshot=job_snap,
        candidate_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        profile_version=1,
        match_run_id=uuid.uuid4(),
        match_result_id=uuid.uuid4(),
        match_result_fingerprint="fp_" + "f" * 61,
        match_score=90.0,
        confidence_tier="HIGH",
        requirement_results=[
            CandidateRequirementResultSnapshotItem(
                requirement_id=req_id,
                match_state=MatchResultState.MATCHED,
                match_value=1.0,
                allowlisted_evidence_ids=["ev-k8s-1"],
            )
        ],
        allowlisted_requirement_ids=[req_id],
        allowlisted_evidence_ids=["ev-k8s-1"],
    )

    serialized_dict = canonicalize_candidate_plan_snapshot(cand_snap)

    # Check that forbidden PII keys are absent
    forbidden_keys = [
        "candidate_name",
        "full_name",
        "email",
        "phone",
        "address",
        "photograph",
        "api_key",
        "bearer",
        "secret",
        "raw_text",
        "resume_text",
    ]
    str_repr = str(serialized_dict).lower()
    for fk in forbidden_keys:
        assert fk not in serialized_dict
        assert f"'{fk}'" not in str_repr
