import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Tuple
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models.auth import Base, UserRecord, UserRole
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
    JobRequisition,
    JobRequirement,
    JobVersion,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementOrigin,
    RequirementPriority,
)
from app.models.matching import MatchResultRecord, MatchRunLifecycleStatus, MatchRunRecord
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    QuestionType,
    ValidationState,
)
from app.schemas.requirement_match import MatchResultState
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_VERSION,
)
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


def _seed_user(db: Session, email: str, role: UserRole = UserRole.RECRUITER) -> UserRecord:
    existing = db.query(UserRecord).filter_by(email=canonicalize_email(email)).first()
    if existing:
        return existing
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name=f"User {email}",
        password_hash=hash_password("ValidPassword123!"),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_user(client: TestClient, email: str = "recruiter@example.com", password: str = "ValidPassword123!") -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    csrf_token = resp.json()["csrfToken"]
    return {
        "X-CSRF-Token": csrf_token,
    }


def _seed_job_with_requirements(
    db: Session,
    recruiter_id: uuid.UUID,
    revision: int = 1,
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
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        title="Senior Distributed Infrastructure Engineer",
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
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.CRITICAL,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="PostgreSQL Performance Tuning",
            normalized_label="postgresql performance tuning",
            rationale="Database optimization is critical",
            min_experience_months=36,
        ),
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=1,
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.HIGH,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="Distributed Systems",
            normalized_label="distributed systems",
            rationale="High availability microservices",
            min_experience_months=24,
        ),
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=2,
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.PREFERRED,
            priority=RequirementPriority.HIGH,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="Python Core",
            normalized_label="python core",
            rationale=None,
            min_experience_months=24,
        ),
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=3,
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.PREFERRED,
            priority=RequirementPriority.MEDIUM,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="API Design",
            normalized_label="api design",
            rationale=None,
            min_experience_months=12,
        ),
    ]
    db.add_all(reqs)
    db.commit()
    return job, ver, reqs


def _seed_candidate_and_frozen_match(
    db: Session,
    job: JobRequisition,
    ver: JobVersion,
    recruiter_id: uuid.UUID,
    req_contributions: List[dict],
    candidate_name: str = "Alice",
    all_requirements: Optional[List[JobRequirement]] = None,
) -> Tuple[CandidateProfileRecord, MatchResultRecord, MatchRunRecord]:
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

    profile = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=1,
        artifact_id=artifact.artifact_id,
        extraction_id=uuid.uuid4(),
        identity_id=identity.identity_id,
        parser_pipeline_version="1.0.0",
        professional_profile={"headline": "Software Engineer", "skills": []},
        evidence_spans=[],
    )
    db.add(profile)
    db.flush()

    now = datetime.now(timezone.utc)
    run = MatchRunRecord(
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
        candidate_input_fingerprint="c_fp_" + "1" * 59,
        input_fingerprint="i_fp_" + "2" * 59,
        idempotency_key="idem_" + uuid.uuid4().hex,
        lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
        candidate_count=1,
    )
    db.add(run)
    db.flush()

    provided_req_ids = {str(c.get("requirement_id")) for c in req_contributions if c.get("requirement_id")}
    enriched_contributions = []
    for c in req_contributions:
        c_copy = dict(c)
        c_copy["requirement_id"] = str(c_copy["requirement_id"])
        c_copy.setdefault("necessity", "MUST_HAVE")
        c_copy.setdefault("priority", "HIGH")
        c_copy.setdefault("category", "SKILL")
        enriched_contributions.append(c_copy)

    req_pool = all_requirements or (ver.requirements if hasattr(ver, "requirements") and ver.requirements else [])
    for r in req_pool:
        if str(r.requirement_id) not in provided_req_ids:
            enriched_contributions.append({
                "requirement_id": str(r.requirement_id),
                "category": r.category.value if hasattr(r.category, "value") else str(r.category),
                "necessity": r.necessity.value if hasattr(r.necessity, "value") else str(r.necessity),
                "priority": r.priority.value if hasattr(r.priority, "value") else str(r.priority),
                "match_state": "MATCHED",
                "match_value": 1.0,
                "evidence_references": ["ev-default"],
            })

    result = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=run.run_id,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        raw_compatibility_score=Decimal("85.000000"),
        display_compatibility_score=Decimal("85.00"),
        confidence_tier="HIGH",
        warning_count=0,
        must_have_warning_count=0,
        rank=1,
        score_decomposition={"requirement_contributions": enriched_contributions},
    )
    db.add(result)
    db.commit()
    db.refresh(profile)
    db.refresh(result)
    return profile, result, run
    return profile, result, run


# ==============================================================================
# PHASE 3B.4 TEST SUITE
# ==============================================================================

def test_template_draft_lifecycle_validation_and_approval(client, test_db):
    """Verifies that a DRAFT template can be safely edited, validated, and approved."""
    recruiter = _seed_user(test_db, "recruiter_lifecycle@example.com")
    auth_headers = _login_user(client, "recruiter_lifecycle@example.com")
    job, ver, reqs = _seed_job_with_requirements(test_db, recruiter.user_id, revision=1)

    # 1. Create Draft Template
    create_resp = client.post(
        f"/api/v1/jobs/{job.job_id}/interview-templates/draft?revision=1",
        headers=auth_headers,
    )
    assert create_resp.status_code == status.HTTP_201_CREATED, create_resp.text
    template_data = create_resp.json()
    template_id = template_data["template_id"]
    content_hash = template_data["content_hash"]
    questions = template_data["common_core_questions"]
    assert len(questions) > 0

    # 2. Safe Edit Draft
    edit_payload = {
        "expected_record_version": 1,
        "expected_content_hash": content_hash,
        "edit_reason": "Refined wording for clarity",
        "questions": [
            {
                "question_id": q["question_id"],
                "question_text": q["question_text"] + " (Please provide specific production examples)",
                "display_order": q["display_order"],
                "estimated_time_minutes": q["estimated_time_minutes"],
                "permitted_probes": q["permitted_probes"],
            }
            for q in questions
        ],
    }

    edit_resp = client.patch(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{template_id}/edit",
        json=edit_payload,
        headers=auth_headers,
    )
    assert edit_resp.status_code == status.HTTP_200_OK, edit_resp.text
    edited_data = edit_resp.json()
    new_hash = edited_data["content_hash"]
    assert new_hash != content_hash
    assert edited_data["lifecycle_status"] == "DRAFT"

    # 3. Explicit Validation
    val_resp = client.post(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{template_id}/validate",
        headers=auth_headers,
    )
    assert val_resp.status_code == status.HTTP_200_OK, val_resp.text
    val_data = val_resp.json()
    assert val_data["is_valid"] is True
    assert val_data["lifecycle_status"] == "VALIDATED"
    assert val_data["validated_content_hash"] == new_hash

    # 4. Explicit Approval
    app_payload = {
        "expected_content_hash": new_hash,
        "approval_confirmation": True,
        "approval_notes": "Approved for engineering interviews.",
    }
    app_resp = client.post(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{template_id}/approve",
        json=app_payload,
        headers=auth_headers,
    )
    assert app_resp.status_code == status.HTTP_200_OK, app_resp.text
    app_data = app_resp.json()
    assert app_data["lifecycle_status"] == "APPROVED"
    assert app_data["is_immutable"] is True

    # 5. Immutability verification: In-place edit of APPROVED record must be rejected (409)
    blocked_edit_resp = client.patch(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{template_id}/edit",
        json={
            "expected_record_version": 2,
            "expected_content_hash": new_hash,
            "questions": edit_payload["questions"],
        },
        headers=auth_headers,
    )
    assert blocked_edit_resp.status_code == status.HTTP_409_CONFLICT
    assert "Approved records are immutable" in blocked_edit_resp.json()["detail"]["message"]


def test_template_successor_versioning_and_superseding(client, test_db):
    """Verifies that editing an approved template creates a successor DRAFT and supersedes parent on approval."""
    recruiter = _seed_user(test_db, "recruiter_successor@example.com")
    auth_headers = _login_user(client, "recruiter_successor@example.com")
    job, ver, reqs = _seed_job_with_requirements(test_db, recruiter.user_id, revision=1)

    # Create, Validate, Approve v1
    c_resp = client.post(f"/api/v1/jobs/{job.job_id}/interview-templates/draft?revision=1", headers=auth_headers)
    t1_data = c_resp.json()
    t1_id = t1_data["template_id"]
    t1_hash = t1_data["content_hash"]

    client.post(f"/api/v1/jobs/{job.job_id}/interview-templates/{t1_id}/validate", headers=auth_headers)
    client.post(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{t1_id}/approve",
        json={"expected_content_hash": t1_hash, "approval_confirmation": True},
        headers=auth_headers,
    )

    # Create Successor Draft (v2)
    succ_resp = client.post(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{t1_id}/successor",
        json={"edit_reason": "Adding deeper architecture probes"},
        headers=auth_headers,
    )
    assert succ_resp.status_code == status.HTTP_201_CREATED, succ_resp.text
    t2_data = succ_resp.json()
    t2_id = t2_data["template_id"]
    assert t2_data["template_version"] == 2
    assert t2_data["lifecycle_status"] == "DRAFT"

    # Parent (v1) remains APPROVED while v2 is in DRAFT
    parent_record = test_db.query(JobInterviewTemplateRecord).filter_by(template_id=uuid.UUID(t1_id)).first()
    assert parent_record.lifecycle_status == PlanLifecycle.APPROVED

    # Validate and Approve v2
    client.post(f"/api/v1/jobs/{job.job_id}/interview-templates/{t2_id}/validate", headers=auth_headers)
    t2_app_resp = client.post(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{t2_id}/approve",
        json={"expected_content_hash": t2_data["content_hash"], "approval_confirmation": True},
        headers=auth_headers,
    )
    assert t2_app_resp.status_code == status.HTTP_200_OK

    # Parent (v1) is now SUPERSEDED
    test_db.refresh(parent_record)
    assert parent_record.lifecycle_status == PlanLifecycle.SUPERSEDED


def test_candidate_plan_lifecycle_and_template_approval_dependency(client, test_db):
    """Verifies candidate plan validation and approval, ensuring approval fails if template is not APPROVED."""
    recruiter = _seed_user(test_db, "recruiter_candplan@example.com")
    auth_headers = _login_user(client, "recruiter_candplan@example.com")
    job, ver, reqs = _seed_job_with_requirements(test_db, recruiter.user_id, revision=1)

    profile, match_res, run = _seed_candidate_and_frozen_match(
        test_db,
        job,
        ver,
        recruiter.user_id,
        [
            {
                "requirement_id": reqs[0].requirement_id,
                "requirement_label": reqs[0].label,
                "match_state": MatchResultState.CONFLICTING_EVIDENCE.value,
                "match_score": 0.4,
                "evidence_snippet": "Candidate claims 5 years postgres but resume lists 1 year.",
                "evidence_ids": ["ev-1", "ev-2"],
            }
        ],
        candidate_name="Alice",
        all_requirements=reqs,
    )

    # 1. Create Template Draft (v1 - DRAFT, NOT APPROVED)
    t_resp = client.post(f"/api/v1/jobs/{job.job_id}/interview-templates/draft?revision=1", headers=auth_headers)
    t_id = t_resp.json()["template_id"]
    t_hash = t_resp.json()["content_hash"]

    # 2. Create Candidate Plan Draft
    p_resp = client.post(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{t_id}/candidate-plans/{profile.artifact_id}/draft"
        f"?revision=1&profile_version=1&match_result_id={match_res.result_id}",
        headers=auth_headers,
    )
    assert p_resp.status_code == status.HTTP_201_CREATED, p_resp.text
    p_data = p_resp.json()
    plan_id = p_data["plan_id"]
    plan_hash = p_data["content_hash"]
    assert p_data["lifecycle_status"] == "DRAFT"
    assert p_data["scoring_separation_policy"] == "STRICT_COMMON_CORE_SEPARATION"

    # 3. Validate Candidate Plan
    val_resp = client.post(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{t_id}/candidate-plans/{plan_id}/validate",
        headers=auth_headers,
    )
    assert val_resp.status_code == status.HTTP_200_OK
    assert val_resp.json()["is_valid"] is True
    assert val_resp.json()["lifecycle_status"] == "VALIDATED"

    # 4. Attempt to approve candidate plan while template is still DRAFT -> MUST FAIL (409)
    app_fail_resp = client.post(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{t_id}/candidate-plans/{plan_id}/approve",
        json={"expected_content_hash": plan_hash, "approval_confirmation": True},
        headers=auth_headers,
    )
    assert app_fail_resp.status_code == status.HTTP_409_CONFLICT
    assert "Template must be APPROVED first" in app_fail_resp.json()["detail"]["message"]

    # 5. Validate & Approve Template
    client.post(f"/api/v1/jobs/{job.job_id}/interview-templates/{t_id}/validate", headers=auth_headers)
    client.post(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{t_id}/approve",
        json={"expected_content_hash": t_hash, "approval_confirmation": True},
        headers=auth_headers,
    )

    # 6. Now approve Candidate Plan -> MUST SUCCEED
    app_ok_resp = client.post(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{t_id}/candidate-plans/{plan_id}/approve",
        json={"expected_content_hash": plan_hash, "approval_confirmation": True},
        headers=auth_headers,
    )
    assert app_ok_resp.status_code == status.HTTP_200_OK
    assert app_ok_resp.json()["lifecycle_status"] == "APPROVED"
    assert app_ok_resp.json()["is_immutable"] is True


def test_unauthorized_recruiter_and_stale_concurrency_rejected(client, test_db):
    """Verifies that unauthorized recruiters are rejected and optimistic concurrency conflicts return 409."""
    recruiter = _seed_user(test_db, "recruiter_auth@example.com")
    job, ver, reqs = _seed_job_with_requirements(test_db, recruiter.user_id, revision=1)

    # Login as recruiter and create template
    auth_headers = _login_user(client, "recruiter_auth@example.com")
    c_resp = client.post(
        f"/api/v1/jobs/{job.job_id}/interview-templates/draft?revision=1",
        headers=auth_headers,
    )
    assert c_resp.status_code == status.HTTP_201_CREATED, c_resp.text
    t_id = c_resp.json()["template_id"]
    t_hash = c_resp.json()["content_hash"]

    # Login as other_recruiter
    other_recruiter = _seed_user(test_db, "other_recruiter_auth@example.com")
    other_auth_headers = _login_user(client, "other_recruiter_auth@example.com")    # Other recruiter attempt to edit -> MUST BE 403 Forbidden
    unauth_resp = client.patch(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{t_id}/edit",
        json={
            "expected_record_version": 1,
            "expected_content_hash": t_hash,
            "questions": [{"question_id": q["question_id"], "question_text": q["question_text"], "display_order": 0, "estimated_time_minutes": 5, "permitted_probes": []} for q in c_resp.json()["common_core_questions"]],
        },
        headers=other_auth_headers,
    )
    assert unauth_resp.status_code == status.HTTP_403_FORBIDDEN

    # Re-login as recruiter to clear other_recruiter cookie
    auth_headers = _login_user(client, "recruiter_auth@example.com")

    # Stale version / hash mismatch -> MUST BE 409 Conflict
    stale_resp = client.patch(
        f"/api/v1/jobs/{job.job_id}/interview-templates/{t_id}/edit",
        json={
            "expected_record_version": 99,
            "expected_content_hash": "0" * 64,
            "questions": [{"question_id": q["question_id"], "question_text": q["question_text"], "display_order": 0, "estimated_time_minutes": 5, "permitted_probes": []} for q in c_resp.json()["common_core_questions"]],
        },
        headers=auth_headers,
    )
    assert stale_resp.status_code == status.HTTP_409_CONFLICT
