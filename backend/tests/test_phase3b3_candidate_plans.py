import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Tuple

import pytest
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
from app.schemas.safety_policy import SAFETY_POLICY_HASH, SAFETY_POLICY_VERSION
from app.services.candidate_plan_generator_service import (
    CandidatePlanPolicyValidator,
    create_deterministic_candidate_interview_plan_draft,
    preview_deterministic_candidate_interview_plan,
    select_deterministic_candidate_targets,
)
from app.services.interview_catalogue import (
    CURATED_SUPPLEMENTAL_CATALOGUE,
    SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION,
    get_catalogue_supplemental_template,
)
from app.services.interview_generator_service import create_deterministic_interview_template_draft
from app.services.interview_snapshot_service import (
    InterviewMatchResultMismatchError,
    InterviewProfileVersionMismatchError,
    InterviewUnauthorizedError,
    load_authoritative_candidate_plan_snapshot,
)
from app.services.security import canonicalize_email, hash_password


# ==============================================================================
# FIXTURES AND SEED HELPERS
# ==============================================================================

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
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=4,
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.PREFERRED,
            priority=RequirementPriority.LOW,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="Incident Response",
            normalized_label="incident response",
            rationale=None,
            min_experience_months=12,
        ),
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            display_order=5,
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.PREFERRED,
            priority=RequirementPriority.LOW,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            label="Relational Data Modeling",
            normalized_label="data modeling",
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

    # Build full list of requirement contributions
    provided_req_ids = {str(c.get("requirement_id")) for c in req_contributions if c.get("requirement_id")}
    enriched_contributions = []
    for c in req_contributions:
        c_copy = dict(c)
        c_copy.setdefault("necessity", "MUST_HAVE")
        c_copy.setdefault("priority", "HIGH")
        c_copy.setdefault("category", "SKILL")
        enriched_contributions.append(c_copy)

    # Fill in missing requirements from all_requirements or ver.requirements if available
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
        candidate_input_fingerprint="c_fp_" + "1" * 59,
        input_fingerprint="i_fp_" + "2" * 59,
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
        raw_compatibility_score=Decimal("85.000000"),
        display_compatibility_score=Decimal("85.00"),
        confidence_tier="HIGH",
        warning_count=0,
        must_have_warning_count=0,
        rank=1,
        score_decomposition={"requirement_contributions": enriched_contributions},
    )
    db.add(match_result)
    db.commit()
    return profile, match_result, match_run


# ==============================================================================
# TEST GROUP 1: MATCH-STATE MAPPING AND DETERMINISTIC SELECTION
# ==============================================================================

def test_match_state_mapping_and_neutral_safe_intents(test_db):
    """Verifies that each match state maps to a curated safe template without accusatory language."""
    # Check all 5 active match states
    for state in [
        "CONFLICTING_EVIDENCE",
        "INSUFFICIENT_EVIDENCE",
        "NOT_EVIDENCED",
        "PARTIALLY_MATCHED",
        "MATCHED",
    ]:
        tmpl = get_catalogue_supplemental_template(state)
        assert tmpl is not None
        assert tmpl.is_active is True
        assert "{label}" in tmpl.text_template
        assert len(tmpl.question_intent) > 5
        assert len(tmpl.clarification_purpose) > 5
        assert len(tmpl.facts_to_confirm) >= 1
        assert len(tmpl.prohibited_inferences) >= 1
        # Neutral phrasing checks
        rendered = tmpl.text_template.replace("{label}", "PostgreSQL Tuning")
        assert "falsehood" not in rendered.lower()
        assert "dishonest" not in rendered.lower()
        assert "failure" not in rendered.lower()
        assert "incompetent" not in rendered.lower()

    # NOT_APPLICABLE has no template
    assert get_catalogue_supplemental_template("NOT_APPLICABLE") is None


def test_deterministic_target_selection_priority_and_max_four_bound(test_db):
    """Verifies that targets are prioritized (CONFLICT > INSUFFICIENT > NOT_EVIDENCED > PARTIAL > MATCHED) and bounded to max 4."""
    recruiter = _seed_user(test_db, "recruiter.sel@example.com")
    job, ver, reqs = _seed_job_with_requirements(test_db, recruiter.user_id)

    # 6 requirements with varying match states
    contributions = [
        {"requirement_id": str(reqs[0].requirement_id), "category": "SKILL", "match_state": "MATCHED", "match_value": 1.0, "evidence_references": ["ev-1"]},
        {"requirement_id": str(reqs[1].requirement_id), "category": "SKILL", "match_state": "CONFLICTING_EVIDENCE", "match_value": 0.5, "evidence_references": ["ev-2"]},
        {"requirement_id": str(reqs[2].requirement_id), "category": "SKILL", "match_state": "INSUFFICIENT_EVIDENCE", "match_value": 0.3, "evidence_references": ["ev-3"]},
        {"requirement_id": str(reqs[3].requirement_id), "category": "SKILL", "match_state": "NOT_EVIDENCED", "match_value": 0.0, "evidence_references": []},
        {"requirement_id": str(reqs[4].requirement_id), "category": "SKILL", "match_state": "PARTIALLY_MATCHED", "match_value": 0.6, "evidence_references": ["ev-4"]},
        {"requirement_id": str(reqs[5].requirement_id), "category": "SKILL", "match_state": "NOT_APPLICABLE", "match_value": 0.0, "evidence_references": []},
    ]

    profile, match_res, match_run = _seed_candidate_and_frozen_match(
        test_db, job, ver, recruiter.user_id, contributions, "Alice", all_requirements=reqs
    )

    snapshot, _ = load_authoritative_candidate_plan_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        candidate_id=profile.identity_id,
        profile_version=1,
        match_result_id=match_res.result_id,
        db=test_db,
    )

    targets = select_deterministic_candidate_targets(snapshot, max_targets=4)

    # Bound: exactly 4 targets selected (out of 5 eligible, since NOT_APPLICABLE is excluded)
    assert len(targets) == 4

    # Priority order verification:
    # 1. CONFLICTING_EVIDENCE (reqs[1])
    # 2. INSUFFICIENT_EVIDENCE (reqs[2])
    # 3. NOT_EVIDENCED (reqs[3])
    # 4. PARTIALLY_MATCHED (reqs[4])
    # MATCHED (reqs[0]) is 5th priority and omitted due to max_targets=4
    assert targets[0].requirement_id == reqs[1].requirement_id
    assert targets[0].match_state == MatchResultState.CONFLICTING_EVIDENCE
    assert targets[1].requirement_id == reqs[2].requirement_id
    assert targets[1].match_state == MatchResultState.INSUFFICIENT_EVIDENCE
    assert targets[2].requirement_id == reqs[3].requirement_id
    assert targets[2].match_state == MatchResultState.NOT_EVIDENCED
    assert targets[3].requirement_id == reqs[4].requirement_id
    assert targets[3].match_state == MatchResultState.PARTIALLY_MATCHED

    # NOT_APPLICABLE was never included
    assert all(t.match_state != MatchResultState.NOT_APPLICABLE for t in targets)


def test_zero_eligible_states_produces_valid_empty_supplemental_plan(test_db):
    """Verifies that when all requirements are fully matched or not applicable, plan is valid with 0 questions."""
    recruiter = _seed_user(test_db, "recruiter.zero@example.com")
    job, ver, reqs = _seed_job_with_requirements(test_db, recruiter.user_id)

    contributions = [
        {"requirement_id": str(r.requirement_id), "category": "SKILL", "match_state": "NOT_APPLICABLE", "match_value": 0.0, "evidence_references": []}
        for r in reqs
    ]

    profile, match_res, match_run = _seed_candidate_and_frozen_match(
        test_db, job, ver, recruiter.user_id, contributions, "Zero", all_requirements=reqs
    )
    template_record, blueprint = create_deterministic_interview_template_draft(recruiter, job.job_id, 1, test_db)

    plan = preview_deterministic_candidate_interview_plan(
        actor=recruiter,
        template_id=template_record.template_id,
        candidate_id=profile.identity_id,
        expected_revision=1,
        profile_version=1,
        match_result_id=match_res.result_id,
        db=test_db,
    )

    assert len(plan.supplemental_questions) == 0
    assert plan.coverage_summary.selected_target_count == 0
    assert plan.validation_report.is_valid is True
    assert any("No additional evidence-clarification questions" in w for w in plan.validation_report.warnings)


# ==============================================================================
# TEST GROUP 2: PROVENANCE, SCORING SEPARATION, AND COMMON-CORE INVARIANCE
# ==============================================================================

def test_scoring_separation_and_common_core_invariance_across_candidates(test_db):
    """Verifies that supplemental questions have score_inclusion=False and the common core remains identical across candidates."""
    recruiter = _seed_user(test_db, "recruiter.inv@example.com")
    job, ver, reqs = _seed_job_with_requirements(test_db, recruiter.user_id)

    # Common-core template draft
    template_record, common_blueprint = create_deterministic_interview_template_draft(recruiter, job.job_id, 1, test_db)
    common_hash_initial = template_record.content_hash
    common_q_ids_initial = [q.question_id for q in template_record.questions]

    # Candidate 1: Alice (has conflict on Distributed Systems)
    c1_contribs = [
        {"requirement_id": str(reqs[0].requirement_id), "category": "SKILL", "match_state": "MATCHED", "match_value": 1.0, "evidence_references": ["ev-1"]},
        {"requirement_id": str(reqs[1].requirement_id), "category": "SKILL", "match_state": "CONFLICTING_EVIDENCE", "match_value": 0.5, "evidence_references": ["ev-2"]},
    ]
    p1, m1, _ = _seed_candidate_and_frozen_match(test_db, job, ver, recruiter.user_id, c1_contribs, "Alice", all_requirements=reqs)

    # Candidate 2: Bob (has insufficient evidence on Python Core)
    c2_contribs = [
        {"requirement_id": str(reqs[0].requirement_id), "category": "SKILL", "match_state": "MATCHED", "match_value": 1.0, "evidence_references": ["ev-1"]},
        {"requirement_id": str(reqs[2].requirement_id), "category": "SKILL", "match_state": "INSUFFICIENT_EVIDENCE", "match_value": 0.4, "evidence_references": ["ev-3"]},
    ]
    p2, m2, _ = _seed_candidate_and_frozen_match(test_db, job, ver, recruiter.user_id, c2_contribs, "Bob", all_requirements=reqs)

    # Generate Candidate 1 Plan
    plan1_record, plan1 = create_deterministic_candidate_interview_plan_draft(
        actor=recruiter,
        template_id=template_record.template_id,
        candidate_id=p1.identity_id,
        expected_revision=1,
        profile_version=1,
        match_result_id=m1.result_id,
        db=test_db,
    )

    # Generate Candidate 2 Plan
    plan2_record, plan2 = create_deterministic_candidate_interview_plan_draft(
        actor=recruiter,
        template_id=template_record.template_id,
        candidate_id=p2.identity_id,
        expected_revision=1,
        profile_version=1,
        match_result_id=m2.result_id,
        db=test_db,
    )

    # 1. Scoring separation: All supplemental questions must have included_in_common_score=False
    for q in plan1.supplemental_questions:
        assert q.included_in_common_score is False
        assert q.layer == InterviewLayer.SUPPLEMENTAL_EVIDENCE
        assert q.question_type == QuestionType.EVIDENCE_CLARIFICATION
        assert q.authoring_origin == AuthoringOrigin.DETERMINISTIC

    for q in plan2.supplemental_questions:
        assert q.included_in_common_score is False
        assert q.layer == InterviewLayer.SUPPLEMENTAL_EVIDENCE

    # 2. Candidate differences: Supplemental questions differ based on gaps
    assert plan1.supplemental_questions[0].source_requirement_id == reqs[1].requirement_id
    assert plan2.supplemental_questions[0].source_requirement_id == reqs[2].requirement_id
    assert plan1.content_hash != plan2.content_hash

    # 3. Common-core invariance: JobInterviewTemplateRecord and questions remain untouched
    reloaded_tpl = test_db.query(JobInterviewTemplateRecord).filter_by(template_id=template_record.template_id).first()
    assert reloaded_tpl.content_hash == common_hash_initial
    assert [q.question_id for q in reloaded_tpl.questions if q.layer == InterviewLayer.COMMON_CORE] == common_q_ids_initial


# ==============================================================================
# TEST GROUP 3: API FLOW, AUTHORIZATION, AND REGRESSIONS
# ==============================================================================

def test_recruiter_preview_and_draft_persistence_api_flow(client, test_db):
    """Verifies full API endpoints for candidate plan preview and draft creation."""
    recruiter = _seed_user(test_db, "recruiter.candapi@example.com")
    job, ver, reqs = _seed_job_with_requirements(test_db, recruiter.user_id)

    contribs = [
        {"requirement_id": str(reqs[0].requirement_id), "category": "SKILL", "match_state": "PARTIALLY_MATCHED", "match_value": 0.6, "evidence_references": ["ev-1"]},
    ]
    profile, match_res, _ = _seed_candidate_and_frozen_match(test_db, job, ver, recruiter.user_id, contribs, "Charlie", all_requirements=reqs)

    template_record, _ = create_deterministic_interview_template_draft(recruiter, job.job_id, 1, test_db)
    headers = _login_user(client, "recruiter.candapi@example.com")

    preview_url = (
        f"/api/v1/jobs/{job.job_id}/interview-templates/{template_record.template_id}"
        f"/candidate-plans/{profile.identity_id}/preview?revision=1&profile_version=1&match_result_id={match_res.result_id}"
    )

    # 1. Preview endpoint
    resp_prev = client.get(preview_url, headers=headers)
    assert resp_prev.status_code == 200, resp_prev.text
    prev_json = resp_prev.json()
    assert prev_json["lifecycle_status"] == "DRAFT"
    assert prev_json["scoring_separation_policy"] == "STRICT_COMMON_CORE_SEPARATION"
    assert len(prev_json["supplemental_questions"]) >= 1
    assert prev_json["supplemental_questions"][0]["included_in_common_score"] is False
    assert prev_json["validation_report"]["is_valid"] is True
    assert prev_json["common_template_summary"]["template_id"] == str(template_record.template_id)

    # 2. Draft creation endpoint
    draft_url = (
        f"/api/v1/jobs/{job.job_id}/interview-templates/{template_record.template_id}"
        f"/candidate-plans/{profile.identity_id}/draft?revision=1&profile_version=1&match_result_id={match_res.result_id}"
    )
    resp_draft = client.post(draft_url, headers=headers)
    assert resp_draft.status_code == 201, resp_draft.text
    draft_json = resp_draft.json()
    assert draft_json["plan_id"] is not None
    assert draft_json["content_hash"] == prev_json["content_hash"]

    # 3. Idempotency check: Calling draft creation again returns the same draft
    resp_draft_2 = client.post(draft_url, headers=headers)
    assert resp_draft_2.status_code == 201
    draft_json_2 = resp_draft_2.json()
    assert draft_json_2["plan_id"] == draft_json["plan_id"]
    assert draft_json_2["content_hash"] == draft_json["content_hash"]


def test_unauthorized_and_mismatched_versions_rejected(client, test_db):
    """Verifies that unassigned recruiters and stale/mismatched versions are rejected."""
    recruiter_owner = _seed_user(test_db, "recruiter.owner2@example.com")
    recruiter_other = _seed_user(test_db, "recruiter.other2@example.com")
    reviewer = _seed_user(test_db, "reviewer@example.com", role=UserRole.REVIEWER)
    job, ver, reqs = _seed_job_with_requirements(test_db, recruiter_owner.user_id)

    contribs = [
        {"requirement_id": str(reqs[0].requirement_id), "category": "SKILL", "match_state": "PARTIALLY_MATCHED", "match_value": 0.6, "evidence_references": ["ev-1"]},
    ]
    profile, match_res, _ = _seed_candidate_and_frozen_match(test_db, job, ver, recruiter_owner.user_id, contribs, "Dave", all_requirements=reqs)

    template_record, _ = create_deterministic_interview_template_draft(recruiter_owner, job.job_id, 1, test_db)

    # 1. Other recruiter denied (404 safe hiding)
    headers_other = _login_user(client, "recruiter.other2@example.com")
    url_base = (
        f"/api/v1/jobs/{job.job_id}/interview-templates/{template_record.template_id}"
        f"/candidate-plans/{profile.identity_id}/preview"
    )
    resp_denied = client.get(f"{url_base}?revision=1&profile_version=1&match_result_id={match_res.result_id}", headers=headers_other)
    assert resp_denied.status_code in {403, 404}

    # 2. Non-recruiter / reviewer role denied (403)
    headers_rev = _login_user(client, "reviewer@example.com")
    resp_rev = client.get(f"{url_base}?revision=1&profile_version=1&match_result_id={match_res.result_id}", headers=headers_rev)
    assert resp_rev.status_code == 403

    # 3. Stale/mismatched profile version rejected (409)
    headers_owner = _login_user(client, "recruiter.owner2@example.com")
    resp_mismatch = client.get(f"{url_base}?revision=1&profile_version=999&match_result_id={match_res.result_id}", headers=headers_owner)
    assert resp_mismatch.status_code == 409

    # 4. Mismatched match_result_id rejected (409)
    random_match_id = uuid.uuid4()
    resp_bad_match = client.get(f"{url_base}?revision=1&profile_version=1&match_result_id={random_match_id}", headers=headers_owner)
    assert resp_bad_match.status_code == 409
