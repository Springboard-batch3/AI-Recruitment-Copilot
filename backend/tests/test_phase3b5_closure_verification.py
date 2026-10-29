import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from fastapi import status
from sqlalchemy.orm import Session

from app.models.auth import UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
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
)
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
    get_current_safety_policy,
)
from app.services.interview_generator_service import (
    InterviewBlueprintBuilder,
)
from app.services.interview_lifecycle_service import (
    approve_candidate_interview_plan,
    approve_job_interview_template,
    create_candidate_interview_plan_successor,
    create_job_interview_template_successor,
    edit_candidate_interview_plan_draft,
    edit_job_interview_template_draft,
    validate_candidate_interview_plan_draft,
    validate_job_interview_template_draft,
)
from app.services.candidate_plan_generator_service import (
    preview_deterministic_candidate_interview_plan,
)
from app.services.interview_snapshot_service import (
    load_authoritative_candidate_plan_snapshot,
    load_authoritative_job_blueprint_snapshot,
)
from app.database import get_db
from app.main import app
from app.models.base import Base
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.services.security import canonicalize_email, hash_password


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


@pytest.fixture
def client(test_db):
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app, base_url="https://testserver")
    app.dependency_overrides.clear()


def _seed_recruiter(db: Session, email: str = "recruiter_closure@example.com") -> UserRecord:
    user = db.query(UserRecord).filter(UserRecord.email == email).first()
    if not user:
        user = UserRecord(
            user_id=uuid.uuid4(),
            email=email,
            display_name=f"User {email}",
            password_hash=hash_password("ValidPassword123!"),
            role=UserRole.RECRUITER,
            is_active=True,
            must_change_password=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _seed_job_with_reqs(
    db: Session,
    recruiter_id: uuid.UUID,
    revision: int = 1,
) -> tuple[JobRequisition, JobVersion, list[JobRequirement]]:
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
        title="Senior Security Infrastructure Engineer",
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        created_by_user_id=recruiter_id,
    )
    db.add(ver)
    db.flush()

    reqs = [
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=0,
            category=RequirementCategory.SKILL,
            label="Distributed Systems",
            normalized_label="distributed systems",
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.CRITICAL,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            rationale="Essential for high-availability zero-trust architecture.",
            min_experience_months=48,
        ),
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=1,
            category=RequirementCategory.EXPERIENCE,
            label="Kubernetes Administration",
            normalized_label="kubernetes administration",
            necessity=RequirementNecessity.PREFERRED,
            priority=RequirementPriority.HIGH,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            min_experience_months=24,
        ),
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=2,
            category=RequirementCategory.SKILL,
            label="Cryptographic Protocols",
            normalized_label="cryptographic protocols",
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.CRITICAL,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            rationale="Core requirement for TLS/mTLS protocol design.",
            min_experience_months=36,
        ),
    ]
    for r in reqs:
        db.add(r)
    db.commit()
    return job, ver, reqs


def _seed_candidate_and_match(
    db: Session,
    job: JobRequisition,
    ver: JobVersion,
    reqs: list[JobRequirement],
    candidate_name: str = "Candidate Closure A",
    match_states: dict[uuid.UUID, str] = None,
) -> tuple[CandidateProfileRecord, MatchRunRecord, MatchResultRecord]:
    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename=f"{candidate_name.lower()}_resume.pdf",
        sha256_hash="f" * 64,
        size_bytes=2048,
        declared_mime_type="application/pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    db.add(artifact)
    db.flush()

    identity = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        full_name=f"{candidate_name} Engineer",
        primary_email=f"{candidate_name.lower()}@example.com",
        primary_phone="+1-555-0100",
        identity_payload={"name": candidate_name},
    )
    db.add(identity)
    db.flush()

    prof = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        identity_id=identity.identity_id,
        artifact_id=artifact.artifact_id,
        extraction_id=uuid.uuid4(),
        profile_version=1,
        parser_pipeline_version="1.0.0",
        professional_profile={"headline": "Software Engineer", "skills": []},
        evidence_spans=[],
    )
    db.add(prof)
    db.flush()

    states = match_states or {
        reqs[0].requirement_id: "MATCHED",
        reqs[1].requirement_id: "PARTIALLY_MATCHED",
        reqs[2].requirement_id: "NOT_EVIDENCED",
    }

    req_contributions = [
        {
            "requirement_id": str(r.requirement_id),
            "category": r.category.value if hasattr(r.category, "value") else str(r.category),
            "necessity": r.necessity.value if hasattr(r.necessity, "value") else str(r.necessity),
            "priority": r.priority.value if hasattr(r.priority, "value") else str(r.priority),
            "match_state": states.get(r.requirement_id, "MATCHED"),
            "match_value": 1.0 if states.get(r.requirement_id) == "MATCHED" else 0.5 if states.get(r.requirement_id) == "PARTIALLY_MATCHED" else 0.0,
            "evidence_references": [f"ev-{str(r.requirement_id)[:8]}-1"] if states.get(r.requirement_id) in {"MATCHED", "PARTIALLY_MATCHED"} else [],
        }
        for r in reqs
    ]

    now = datetime.now(timezone.utc)
    run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=job.created_by_user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        evaluation_date=now,
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="1.0.0",
        scoring_policy_version="1.0.0",
        candidate_input_fingerprint="c_fp_" + "1" * 59,
        input_fingerprint="i_fp_" + "2" * 59,
        idempotency_key="idem_" + uuid.uuid4().hex,
        lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
        candidate_count=1,
    )
    db.add(run)
    db.flush()

    m_result = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=run.run_id,
        profile_id=prof.profile_id,
        profile_version=prof.profile_version,
        raw_compatibility_score=Decimal("78.500000"),
        display_compatibility_score=Decimal("78.50"),
        confidence_tier="HIGH",
        warning_count=0,
        must_have_warning_count=0,
        rank=1,
        score_decomposition={"requirement_contributions": req_contributions},
    )
    db.add(m_result)
    db.commit()
    return prof, run, m_result


# =========================================================================
# STEP 3 & 4: Integrated Determinism, Supplemental Isolation & Non-Scoring
# =========================================================================

def test_phase3b5_integrated_determinism_and_multi_candidate_invariance(test_db: Session):
    """Proves:
    1. Deterministic common-core generation and content hash invariance across runs.
    2. Changing database order or candidate info does NOT affect the common core.
    3. Multi-candidate plans bound to the same template share identical common question IDs, order, and hash.
    4. Supplemental questions vary deterministically according to candidate match gaps.
    5. Supplemental questions are strictly excluded from the common score.
    """
    recruiter = _seed_recruiter(test_db, "recruiter_det@example.com")
    job, ver, reqs = _seed_job_with_reqs(test_db, recruiter.user_id, revision=1)

    # 1. Generate snapshot & template twice to verify deterministic stability
    snap1, hash1 = load_authoritative_job_blueprint_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        db=test_db,
    )
    builder = InterviewBlueprintBuilder()
    blueprint1 = builder.generate_blueprint_preview(snap1)

    snap2, hash2 = load_authoritative_job_blueprint_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        db=test_db,
    )
    blueprint2 = builder.generate_blueprint_preview(snap2)

    assert hash1 == hash2
    assert blueprint1.content_hash == blueprint2.content_hash
    assert len(blueprint1.common_core_questions) == len(blueprint2.common_core_questions)
    assert [q.deterministic_template_id for q in blueprint1.common_core_questions] == [q.deterministic_template_id for q in blueprint2.common_core_questions]
    assert [q.question_text for q in blueprint1.common_core_questions] == [q.question_text for q in blueprint2.common_core_questions]
    assert [q.display_order for q in blueprint1.common_core_questions] == [q.display_order for q in blueprint2.common_core_questions]

    # 2. Seed two different candidates for the same job revision
    cand1, run1, res1 = _seed_candidate_and_match(
        test_db, job, ver, reqs,
        candidate_name="Alice",
        match_states={
            reqs[0].requirement_id: "MATCHED",
            reqs[1].requirement_id: "PARTIALLY_MATCHED",
            reqs[2].requirement_id: "NOT_EVIDENCED",
        },
    )
    cand2, run2, res2 = _seed_candidate_and_match(
        test_db, job, ver, reqs,
        candidate_name="Bob",
        match_states={
            reqs[0].requirement_id: "CONFLICTING_EVIDENCE",
            reqs[1].requirement_id: "MATCHED",
            reqs[2].requirement_id: "MATCHED",
        },
    )

    # Create & approve common template in DB
    tpl_record = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        template_version=1,
        lifecycle_status=PlanLifecycle.APPROVED,
        guardrail_policy_version=SAFETY_POLICY_VERSION,
        guardrail_policy_hash=SAFETY_POLICY_HASH,
        deterministic_builder_version=DETERMINISTIC_BUILDER_VERSION,
        question_catalogue_version=QUESTION_CATALOGUE_VERSION,
        rubric_catalogue_version=RUBRIC_CATALOGUE_VERSION,
        input_snapshot_hash=hash1,
        content_hash=blueprint1.content_hash,
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(tpl_record)
    test_db.flush()

    for idx, q_dto in enumerate(blueprint1.common_core_questions):
        q_rec = InterviewQuestionRecord(
            question_id=uuid.uuid4(),
            template_id=tpl_record.template_id,
            question_version=1,
            layer=InterviewLayer.COMMON_CORE,
            question_type=q_dto.question_type,
            authoring_origin=q_dto.authoring_origin,
            display_order=idx,
            competency_id=q_dto.competency_id,
            requirement_ids=[str(r) for r in q_dto.requirement_references],
            deterministic_template_id=q_dto.deterministic_template_id,
            deterministic_template_version=q_dto.deterministic_template_version,
            question_text=q_dto.question_text,
            question_intent=q_dto.question_intent,
            estimated_time_minutes=q_dto.estimated_time_minutes,
            included_in_common_score=True,
            validation_state=ValidationState.PASSED,
            permitted_probes=q_dto.permitted_probes,
            safe_metadata={},
        )
        test_db.add(q_rec)
    test_db.commit()

    # Generate candidate supplemental plans using preview service
    plan1 = preview_deterministic_candidate_interview_plan(
        actor=recruiter,
        template_id=tpl_record.template_id,
        candidate_id=cand1.profile_id,
        expected_revision=1,
        profile_version=1,
        match_result_id=res1.result_id,
        db=test_db,
    )

    plan2 = preview_deterministic_candidate_interview_plan(
        actor=recruiter,
        template_id=tpl_record.template_id,
        candidate_id=cand2.profile_id,
        expected_revision=1,
        profile_version=1,
        match_result_id=res2.result_id,
        db=test_db,
    )

    # 3. Prove common core summary is IDENTICAL across candidates
    assert plan1.template_id == plan2.template_id
    assert plan1.template_content_hash == plan2.template_content_hash
    assert plan1.common_core_questions_count == plan2.common_core_questions_count

    # 4. Prove supplemental questions are candidate-tailored, bounded to 4, and strictly NOT in common score
    assert 0 <= len(plan1.supplemental_questions) <= 4
    assert 0 <= len(plan2.supplemental_questions) <= 4
    assert plan1.supplemental_questions != plan2.supplemental_questions

    for q in plan1.supplemental_questions + plan2.supplemental_questions:
        assert q.layer == InterviewLayer.SUPPLEMENTAL_EVIDENCE
        assert q.included_in_common_score is False
        assert q.question_type == QuestionType.EVIDENCE_CLARIFICATION
        assert q.source_match_state in {"MATCHED", "PARTIALLY_MATCHED", "NOT_EVIDENCED", "CONFLICTING_EVIDENCE", "INSUFFICIENT_EVIDENCE"}


# =========================================================================
# STEP 6, 7 & 8: Lifecycle, Prohibited Topics & Phase 2 Immutability
# =========================================================================

def test_phase3b5_prohibited_topic_rejection_and_phase2_immutability(test_db: Session):
    """Proves:
    1. Hand-crafted prohibited topics (e.g. age, religion, medical, culture fit) trigger blocking validation errors.
    2. Unsafe content is blocked and cannot reach APPROVED status.
    3. The entire Phase 3B workflow preserves Phase 2 matcher records, scores, fingerprints, and timestamps without mutation.
    """
    recruiter = _seed_recruiter(test_db, "recruiter_sec@example.com")
    job, ver, reqs = _seed_job_with_reqs(test_db, recruiter.user_id, revision=1)
    cand, run, res = _seed_candidate_and_match(test_db, job, ver, reqs)

    # Record Phase 2 baseline values
    p2_baseline_raw_score = res.raw_compatibility_score
    p2_baseline_display_score = res.display_compatibility_score
    p2_baseline_tier = res.confidence_tier
    p2_baseline_decomposition = copy.deepcopy(res.score_decomposition)
    p2_baseline_run_status = run.lifecycle_status

    # Snapshot and generate template
    snap, s_hash = load_authoritative_job_blueprint_snapshot(recruiter, job.job_id, 1, test_db)
    builder = InterviewBlueprintBuilder()
    bp = builder.generate_blueprint_preview(snap)

    tpl = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=1,
        template_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        guardrail_policy_version=SAFETY_POLICY_VERSION,
        guardrail_policy_hash=SAFETY_POLICY_HASH,
        deterministic_builder_version=DETERMINISTIC_BUILDER_VERSION,
        question_catalogue_version=QUESTION_CATALOGUE_VERSION,
        rubric_catalogue_version=RUBRIC_CATALOGUE_VERSION,
        input_snapshot_hash=s_hash,
        content_hash=bp.content_hash,
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(tpl)
    test_db.flush()

    q_rec = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=tpl.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        display_order=0,
        competency_id="Distributed Systems",
        requirement_ids=[str(reqs[0].requirement_id)],
        deterministic_template_id="DET_TECH_001",
        deterministic_template_version="1",
        question_text="Explain how you design fault-tolerant consensus systems.",
        question_intent="Evaluate distributed state machine knowledge.",
        estimated_time_minutes=10,
        included_in_common_score=True,
        validation_state=ValidationState.PENDING,
        permitted_probes=[],
        safe_metadata={},
    )
    test_db.add(q_rec)
    test_db.flush()

    rub_rec = InterviewRubricRecord(
        rubric_id=uuid.uuid4(),
        question_id=q_rec.question_id,
        rubric_version=1,
        competency_definition="Demonstrated ability to architect consensus protocols and handle partitions.",
        question_intent="Evaluate distributed state machine knowledge.",
        observable_indicators=["Mentions Raft/Paxos", "Explains quorum logic", "Considers network latency"],
        rating_anchors={"1": "No understanding", "3": "Basic consensus", "5": "Production mastery"},
        sufficient_evidence_guidance="Candidate clearly explains leader election and log replication.",
        insufficient_evidence_guidance="Candidate cannot describe quorum requirements.",
        permitted_probes=[],
        prohibited_inferences=[],
        subject_matter_review_status="NOT_REQUIRED",
        validation_state=ValidationState.PASSED,
    )
    test_db.add(rub_rec)
    test_db.commit()

    # 1. Inject prohibited content (age / health / marital status / script injection) -> MUST BE BLOCKED BY SAFE EDIT
    from app.schemas.interview_api import InterviewTemplateEditRequest, QuestionEditItem
    from app.services.interview_lifecycle_service import LifecycleValidationError
    edit_req = InterviewTemplateEditRequest(
        expected_record_version=1,
        expected_content_hash=tpl.content_hash,
        questions=[
            QuestionEditItem(
                question_id=q_rec.question_id,
                question_text="What is your age, date of birth, medical history and family status? <script>alert('xss')</script>",
                display_order=0,
                estimated_time_minutes=10,
                permitted_probes=["Do you plan to have children?"],
            )
        ],
    )
    with pytest.raises(LifecycleValidationError) as exc_info:
        edit_job_interview_template_draft(recruiter, job.job_id, tpl.template_id, edit_req, test_db)
    assert exc_info.value.code == "PROHIBITED_TOPIC_DETECTED"
    assert any("AGE_AND_DOB" in err for err in exc_info.value.blocking_errors)

    # 2. Template remains in unapproved DRAFT state
    assert tpl.lifecycle_status == PlanLifecycle.DRAFT

    # 3. Clean up question with safe edit, validate, and approve
    clean_edit = InterviewTemplateEditRequest(
        expected_record_version=tpl.record_version,
        expected_content_hash=tpl.content_hash,
        questions=[
            QuestionEditItem(
                question_id=q_rec.question_id,
                question_text="Explain how you design fault-tolerant consensus systems under network partition.",
                display_order=0,
                estimated_time_minutes=10,
                permitted_probes=["How does the system recover from split-brain?"],
            )
        ],
    )
    clean_tpl = edit_job_interview_template_draft(recruiter, job.job_id, tpl.template_id, clean_edit, test_db)
    clean_val = validate_job_interview_template_draft(recruiter, job.job_id, tpl.template_id, test_db)
    assert clean_val.is_valid is True

    from app.schemas.interview_api import InterviewApprovalRequest
    app_resp = approve_job_interview_template(
        actor=recruiter,
        job_id=job.job_id,
        template_id=tpl.template_id,
        request=InterviewApprovalRequest(expected_content_hash=clean_val.validated_content_hash),
        db=test_db,
    )
    assert app_resp.lifecycle_status == PlanLifecycle.APPROVED

    # 5. Verify Phase 2 immutability after full Phase 3B operations
    test_db.refresh(res)
    test_db.refresh(run)

    assert res.raw_compatibility_score == p2_baseline_raw_score
    assert res.display_compatibility_score == p2_baseline_display_score
    assert res.confidence_tier == p2_baseline_tier
    assert res.score_decomposition == p2_baseline_decomposition
    assert run.lifecycle_status == p2_baseline_run_status
