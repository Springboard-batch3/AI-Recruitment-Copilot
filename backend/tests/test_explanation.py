import uuid
from datetime import datetime, timezone
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models.auth import Base, UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
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
from app.models.matching import MatchResultRecord, MatchRunRecord
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.explanation import (
    DISCLAIMER_TEXT,
    EXPLANATION_TEMPLATE_VERSION,
    ControlledStateLanguage,
    MatchExplanationResponse,
    compute_explanation_checksum,
    verify_no_prohibited_decision_language,
)
from app.schemas.matching import verify_no_prohibited_keys
from app.services.explanation import (
    ExplanationFaithfulnessError,
    generate_match_explanation,
    validate_explanation_faithfulness,
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
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
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


def _seed_user(db, email="user@company.test", role=UserRole.RECRUITER):
    user = UserRecord(
        user_id=uuid.uuid4(),
        email=canonicalize_email(email),
        display_name="Test User",
        password_hash=hash_password("Password123!"),
        role=role,
        is_active=True,
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_user(client, email="user@company.test"):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    assert resp.status_code == status.HTTP_200_OK


def _seed_candidate(test_db, skills=None):
    skills = skills or ["Python"]
    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="resume.pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(art)
    test_db.flush()

    ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        full_name="Alice Smith",
        primary_email="alice.smith@example.com",
        identity_payload={"fullName": {"value": "Alice Smith"}},
    )
    test_db.add(ident)
    test_db.flush()

    prof = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=1,
        artifact_id=art.artifact_id,
        extraction_id=uuid.uuid4(),
        identity_id=ident.identity_id,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        professional_profile={
            "headline": {"value": "Developer"},
            "skills": [
                {
                    "value": s,
                    "normalizedValue": s.lower(),
                    "confidence": 0.95,
                    "confidenceBand": "HIGH",
                    "evidenceIds": [f"ev_{s.lower()}"],
                    "reviewStatus": "CONFIRMED",
                }
                for s in skills
            ],
            "employment": [],
            "education": [],
            "projects": [],
            "certifications": [],
            "languages": [],
        },
        evidence_spans=[
            {
                "evidenceId": f"ev_{s.lower()}",
                "characterStart": 0,
                "characterEnd": 10,
                "extractionMethod": "RULE",
            }
            for s in skills
        ],
        review_required=False,
    )
    test_db.add(prof)
    test_db.commit()
    return prof


def _seed_job(test_db, recruiter_id):
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=1,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        title="Software Engineer",
        created_by_user_id=recruiter_id,
        ready_by_user_id=recruiter_id,
        ready_at=datetime.now(timezone.utc),
    )
    test_db.add(ver)
    test_db.flush()

    req1 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        display_order=0,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    req2 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        category=RequirementCategory.SKILL,
        label="Docker",
        normalized_label="docker",
        display_order=1,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.MEDIUM,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    test_db.add_all([req1, req2])
    test_db.commit()
    return job, ver, [req1, req2]


def test_explanation_determinism_and_checksum(test_db):
    """Same match result and template version produce identical explanation and checksum."""
    recruiter = _seed_user(test_db, email="recruiter.exp@company.test")
    job, ver, reqs = _seed_job(test_db, recruiter.user_id)
    cand = _seed_candidate(test_db, skills=["Python"])

    run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="phase2c2_rules_v1",
        scoring_policy_version="phase2c3_scoring_v1",
        candidate_input_fingerprint="c_hash",
        input_fingerprint="m_hash",
        idempotency_key="key-exp-1",
        lifecycle_status="SUCCEEDED",
        candidate_count=1,
    )
    test_db.add(run)

    decomp = {
        "scoring_policy_version": "phase2c3_scoring_v1",
        "calculation_method": "REQUIREMENT_WEIGHTED_DIRECT",
        "requirement_contributions": [
            {
                "requirement_id": str(reqs[0].requirement_id),
                "display_order": 0,
                "category": "SKILL",
                "necessity": "MUST_HAVE",
                "priority": "HIGH",
                "importance_source": "PRIORITY_DEFAULT",
                "configured_importance": 2.0,
                "effective_importance": 0.666667,
                "match_state": "MATCHED",
                "match_value": 1.0,
                "weighted_contribution": 66.6667,
                "evidence_references": ["ev_python"],
                "comparison_method": "EXACT_NORMALIZED_LABEL",
                "reason_codes": ["EXACT_NORMALIZED_LABEL_MATCH"],
            },
            {
                "requirement_id": str(reqs[1].requirement_id),
                "display_order": 1,
                "category": "SKILL",
                "necessity": "MUST_HAVE",
                "priority": "MEDIUM",
                "importance_source": "PRIORITY_DEFAULT",
                "configured_importance": 1.0,
                "effective_importance": 0.333333,
                "match_state": "NOT_EVIDENCED",
                "match_value": 0.0,
                "weighted_contribution": 0.0,
                "evidence_references": [],
                "comparison_method": "NOT_APPLICABLE",
                "reason_codes": ["NOT_EVIDENCED_IN_PROFILE"],
            },
        ],
        "component_scores": {
            "SKILL": {
                "category": "SKILL",
                "status": "APPLICABLE",
                "requirement_count": 2,
                "applicable_count": 2,
                "total_importance": 3.0,
                "earned_contribution": 2.0,
                "raw_component_score": 0.666667,
                "display_component_score": 66.67,
            }
        },
        "confidence_diagnostics": {
            "confidence_tier": "HIGH",
            "applicable_requirement_count": 2,
            "determinately_evaluated_count": 2,
            "requirements_with_qualifying_evidence": 1,
            "insufficient_evidence_count": 0,
            "conflicting_evidence_count": 0,
            "profile_review_required": False,
            "reasons_lowering_confidence": [],
        },
        "must_have_warnings": [
            {
                "code": "MUST_HAVE_NOT_EVIDENCED",
                "requirement_id": str(reqs[1].requirement_id),
                "category": "SKILL",
                "result_state": "NOT_EVIDENCED",
                "message": "No qualifying evidence was found in the current candidate profile.",
                "evidence_references": [],
                "review_required": True,
            }
        ],
        "general_warnings": [],
    }

    result = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=run.run_id,
        profile_id=cand.profile_id,
        profile_version=1,
        raw_compatibility_score=66.666700,
        display_compatibility_score=66.67,
        confidence_tier="HIGH",
        warning_count=1,
        must_have_warning_count=1,
        rank=1,
        is_tied=False,
        score_decomposition=decomp,
    )
    test_db.add(result)
    test_db.commit()

    exp1 = generate_match_explanation(result_record=result, run_record=run)
    exp2 = generate_match_explanation(result_record=result, run_record=run)

    assert exp1.explanation_checksum == exp2.explanation_checksum
    assert len(exp1.strengths) == 1
    assert exp1.strengths[0].requirement_id == reqs[0].requirement_id
    assert exp1.strengths[0].evidence_references == ["ev_python"]
    assert exp1.raw_compatibility_score == float(result.raw_compatibility_score)
    assert exp1.display_compatibility_score == float(result.display_compatibility_score)
    assert exp1.disclaimer == DISCLAIMER_TEXT


def test_not_evidenced_careful_language(test_db):
    """NOT_EVIDENCED requirements use careful evidence wording and never claim candidate lacks ability."""
    recruiter = _seed_user(test_db, email="recruiter.lang@company.test")
    job, ver, reqs = _seed_job(test_db, recruiter.user_id)
    cand = _seed_candidate(test_db, skills=["Python"])

    run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="phase2c2_rules_v1",
        scoring_policy_version="phase2c3_scoring_v1",
        candidate_input_fingerprint="c_hash",
        input_fingerprint="m_hash",
        idempotency_key="key-exp-lang",
        lifecycle_status="SUCCEEDED",
        candidate_count=1,
    )
    test_db.add(run)

    decomp = {
        "scoring_policy_version": "phase2c3_scoring_v1",
        "calculation_method": "REQUIREMENT_WEIGHTED_DIRECT",
        "requirement_contributions": [
            {
                "requirement_id": str(reqs[0].requirement_id),
                "display_order": 0,
                "category": "SKILL",
                "necessity": "MUST_HAVE",
                "priority": "HIGH",
                "configured_importance": 2.0,
                "effective_importance": 1.0,
                "match_state": "NOT_EVIDENCED",
                "match_value": 0.0,
                "weighted_contribution": 0.0,
                "evidence_references": [],
                "comparison_method": "NOT_APPLICABLE",
                "reason_codes": [],
            }
        ],
        "component_scores": {},
        "confidence_diagnostics": {
            "confidence_tier": "HIGH",
            "applicable_requirement_count": 1,
            "determinately_evaluated_count": 1,
            "requirements_with_qualifying_evidence": 0,
            "insufficient_evidence_count": 0,
            "conflicting_evidence_count": 0,
            "profile_review_required": False,
            "reasons_lowering_confidence": [],
        },
        "must_have_warnings": [],
        "general_warnings": [],
    }

    result = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=run.run_id,
        profile_id=cand.profile_id,
        profile_version=1,
        raw_compatibility_score=0.0,
        display_compatibility_score=0.0,
        confidence_tier="HIGH",
        warning_count=0,
        must_have_warning_count=0,
        rank=1,
        is_tied=False,
        score_decomposition=decomp,
    )
    test_db.add(result)
    test_db.commit()

    exp = generate_match_explanation(result_record=result, run_record=run)
    assert "did not provide qualifying evidence for 1 requirement in the selected profile version" in exp.deterministic_summary
    assert "lacks" not in exp.deterministic_summary.lower()
    assert "unqualified" not in exp.deterministic_summary.lower()


def test_faithfulness_validator_rejects_altered_data(test_db):
    """Validator rejects an explanation if scores or evidence references do not match stored result."""
    recruiter = _seed_user(test_db, email="recruiter.faith@company.test")
    job, ver, reqs = _seed_job(test_db, recruiter.user_id)
    cand = _seed_candidate(test_db, skills=["Python"])

    run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="phase2c2_rules_v1",
        scoring_policy_version="phase2c3_scoring_v1",
        candidate_input_fingerprint="c_hash",
        input_fingerprint="m_hash",
        idempotency_key="key-exp-faith",
        lifecycle_status="SUCCEEDED",
        candidate_count=1,
    )
    test_db.add(run)

    decomp = {
        "scoring_policy_version": "phase2c3_scoring_v1",
        "calculation_method": "REQUIREMENT_WEIGHTED_DIRECT",
        "requirement_contributions": [
            {
                "requirement_id": str(reqs[0].requirement_id),
                "display_order": 0,
                "category": "SKILL",
                "necessity": "MUST_HAVE",
                "priority": "HIGH",
                "configured_importance": 2.0,
                "effective_importance": 1.0,
                "match_state": "MATCHED",
                "match_value": 1.0,
                "weighted_contribution": 100.0,
                "evidence_references": ["ev_python"],
                "comparison_method": "EXACT_NORMALIZED_LABEL",
                "reason_codes": [],
            }
        ],
        "component_scores": {},
        "confidence_diagnostics": {
            "confidence_tier": "HIGH",
            "applicable_requirement_count": 1,
            "determinately_evaluated_count": 1,
            "requirements_with_qualifying_evidence": 1,
            "insufficient_evidence_count": 0,
            "conflicting_evidence_count": 0,
            "profile_review_required": False,
            "reasons_lowering_confidence": [],
        },
        "must_have_warnings": [],
        "general_warnings": [],
    }

    result = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=run.run_id,
        profile_id=cand.profile_id,
        profile_version=1,
        raw_compatibility_score=100.0,
        display_compatibility_score=100.0,
        confidence_tier="HIGH",
        warning_count=0,
        must_have_warning_count=0,
        rank=1,
        is_tied=False,
        score_decomposition=decomp,
    )
    test_db.add(result)
    test_db.commit()

    exp = generate_match_explanation(result_record=result, run_record=run)

    # Corrupt score
    corrupted_result = MatchResultRecord(
        result_id=result.result_id,
        run_id=result.run_id,
        profile_id=result.profile_id,
        profile_version=result.profile_version,
        raw_compatibility_score=50.0,  # Differs from explanation 100.0
        display_compatibility_score=50.0,
        confidence_tier="HIGH",
        warning_count=0,
        must_have_warning_count=0,
        rank=1,
        is_tied=False,
        score_decomposition=decomp,
    )

    with pytest.raises(ExplanationFaithfulnessError) as exc:
        validate_explanation_faithfulness(exp, corrupted_result, run)
    assert exc.value.code == "SCORE_MISMATCH"


def test_explanation_api_access_control(client, test_db):
    """Explanation endpoint enforces recruiter ownership and conceals cross-recruiter access with 404."""
    recruiter_a = _seed_user(test_db, email="recruiter.owner@company.test")
    recruiter_b = _seed_user(test_db, email="recruiter.intruder@company.test")
    job, ver, reqs = _seed_job(test_db, recruiter_a.user_id)
    cand = _seed_candidate(test_db, skills=["Python"])

    run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter_a.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="phase2c2_rules_v1",
        scoring_policy_version="phase2c3_scoring_v1",
        candidate_input_fingerprint="c_hash",
        input_fingerprint="m_hash",
        idempotency_key="key-exp-api",
        lifecycle_status="SUCCEEDED",
        candidate_count=1,
    )
    test_db.add(run)

    decomp = {
        "scoring_policy_version": "phase2c3_scoring_v1",
        "calculation_method": "REQUIREMENT_WEIGHTED_DIRECT",
        "requirement_contributions": [
            {
                "requirement_id": str(reqs[0].requirement_id),
                "display_order": 0,
                "category": "SKILL",
                "necessity": "MUST_HAVE",
                "priority": "HIGH",
                "configured_importance": 2.0,
                "effective_importance": 1.0,
                "match_state": "MATCHED",
                "match_value": 1.0,
                "weighted_contribution": 100.0,
                "evidence_references": ["ev_python"],
                "comparison_method": "EXACT_NORMALIZED_LABEL",
                "reason_codes": [],
            }
        ],
        "component_scores": {},
        "confidence_diagnostics": {
            "confidence_tier": "HIGH",
            "applicable_requirement_count": 1,
            "determinately_evaluated_count": 1,
            "requirements_with_qualifying_evidence": 1,
            "insufficient_evidence_count": 0,
            "conflicting_evidence_count": 0,
            "profile_review_required": False,
            "reasons_lowering_confidence": [],
        },
        "must_have_warnings": [],
        "general_warnings": [],
    }

    result = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=run.run_id,
        profile_id=cand.profile_id,
        profile_version=1,
        raw_compatibility_score=100.0,
        display_compatibility_score=100.0,
        confidence_tier="HIGH",
        warning_count=0,
        must_have_warning_count=0,
        rank=1,
        is_tied=False,
        score_decomposition=decomp,
    )
    test_db.add(result)
    test_db.commit()

    # 1. Unauthenticated -> 401
    unauth_resp = client.get(f"/api/v1/matches/runs/{run.run_id}/results/{result.result_id}/explanation")
    assert unauth_resp.status_code == status.HTTP_401_UNAUTHORIZED

    # 2. Recruiter B (intruder) -> 404 concealment
    _login_user(client, email="recruiter.intruder@company.test")
    resp_b = client.get(f"/api/v1/matches/runs/{run.run_id}/results/{result.result_id}/explanation")
    assert resp_b.status_code == status.HTTP_404_NOT_FOUND

    # 3. Recruiter A (owner) -> 200 OK
    _login_user(client, email="recruiter.owner@company.test")
    resp_a = client.get(f"/api/v1/matches/runs/{run.run_id}/results/{result.result_id}/explanation")
    assert resp_a.status_code == status.HTTP_200_OK
    data = resp_a.json()
    verify_no_prohibited_keys(data)
    assert data["resultId"] == str(result.result_id)
    assert data["runId"] == str(run.run_id)
    assert data["rawCompatibilityScore"] == 100.0
    assert data["disclaimer"] == DISCLAIMER_TEXT
    assert "alice" not in str(data).lower()
    assert "smith" not in str(data).lower()


def test_explanation_does_not_modify_score_decomposition_or_result(test_db):
    """Explanation generation is pure and does not modify the underlying match result."""
    recruiter = _seed_user(test_db, email="recruiter.pure@company.test")
    job, ver, reqs = _seed_job(test_db, recruiter.user_id)
    cand = _seed_candidate(test_db, skills=["Python"])

    decomp = {
        "scoring_policy_version": "phase2c3_scoring_v1",
        "calculation_method": "REQUIREMENT_WEIGHTED_DIRECT",
        "requirement_contributions": [
            {
                "requirement_id": str(reqs[0].requirement_id),
                "display_order": 0,
                "category": "SKILL",
                "necessity": "MUST_HAVE",
                "priority": "HIGH",
                "configured_importance": 2.0,
                "effective_importance": 1.0,
                "match_state": "MATCHED",
                "match_value": 1.0,
                "weighted_contribution": 100.0,
                "evidence_references": ["ev_python"],
                "comparison_method": "EXACT_NORMALIZED_LABEL",
                "reason_codes": [],
            }
        ],
        "component_scores": {},
        "confidence_diagnostics": {
            "confidence_tier": "HIGH",
            "applicable_requirement_count": 1,
            "determinately_evaluated_count": 1,
            "requirements_with_qualifying_evidence": 1,
            "insufficient_evidence_count": 0,
            "conflicting_evidence_count": 0,
            "profile_review_required": False,
            "reasons_lowering_confidence": [],
        },
        "must_have_warnings": [],
        "general_warnings": [],
    }

    run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="phase2c2_rules_v1",
        scoring_policy_version="phase2c3_scoring_v1",
        candidate_input_fingerprint="c_hash",
        input_fingerprint="m_hash",
        idempotency_key="key-pure-test",
        lifecycle_status="SUCCEEDED",
        candidate_count=1,
    )
    test_db.add(run)

    result = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=run.run_id,
        profile_id=cand.profile_id,
        profile_version=1,
        raw_compatibility_score=100.0,
        display_compatibility_score=100.0,
        confidence_tier="HIGH",
        warning_count=0,
        must_have_warning_count=0,
        rank=1,
        is_tied=False,
        score_decomposition=decomp,
    )
    test_db.add(result)
    test_db.commit()

    score_before = float(result.raw_compatibility_score)
    rank_before = result.rank
    decomp_before = dict(result.score_decomposition)

    exp = generate_match_explanation(result_record=result, run_record=run)

    assert float(result.raw_compatibility_score) == score_before
    assert result.rank == rank_before
    assert result.score_decomposition == decomp_before
    assert exp.raw_compatibility_score == score_before


def test_partial_match_contains_exact_reasons_and_values(test_db):
    """Partial matches preserve required, documented, match values and exact reasons."""
    recruiter = _seed_user(test_db, email="recruiter.partial@company.test")
    job, ver, reqs = _seed_job(test_db, recruiter.user_id)
    cand = _seed_candidate(test_db, skills=["Python"])

    decomp = {
        "scoring_policy_version": "phase2c3_scoring_v1",
        "calculation_method": "REQUIREMENT_WEIGHTED_DIRECT",
        "requirement_contributions": [
            {
                "requirement_id": str(reqs[0].requirement_id),
                "display_order": 0,
                "category": "SKILL",
                "necessity": "MUST_HAVE",
                "priority": "HIGH",
                "configured_importance": 2.0,
                "effective_importance": 1.0,
                "match_state": "PARTIALLY_MATCHED",
                "match_value": 0.5,
                "weighted_contribution": 50.0,
                "evidence_references": ["ev_python"],
                "comparison_method": "EXPERIENCE_INTERVAL_CALCULATION",
                "reason_codes": ["EXPERIENCE_TENURE_BELOW_MINIMUM"],
            }
        ],
        "component_scores": {},
        "confidence_diagnostics": {
            "confidence_tier": "MEDIUM",
            "applicable_requirement_count": 1,
            "determinately_evaluated_count": 1,
            "requirements_with_qualifying_evidence": 1,
            "insufficient_evidence_count": 0,
            "conflicting_evidence_count": 0,
            "profile_review_required": False,
            "reasons_lowering_confidence": [],
        },
        "must_have_warnings": [
            {
                "code": "MUST_HAVE_PARTIALLY_MATCHED",
                "requirement_id": str(reqs[0].requirement_id),
                "category": "SKILL",
                "result_state": "PARTIALLY_MATCHED",
                "message": "Requirement is only partially evidenced in the candidate profile.",
                "evidence_references": ["ev_python"],
                "review_required": True,
            }
        ],
        "general_warnings": [],
    }

    run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=ver.revision,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="phase2c2_rules_v1",
        scoring_policy_version="phase2c3_scoring_v1",
        candidate_input_fingerprint="c_hash",
        input_fingerprint="m_hash",
        idempotency_key="key-part-test",
        lifecycle_status="SUCCEEDED",
        candidate_count=1,
    )
    test_db.add(run)

    result = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=run.run_id,
        profile_id=cand.profile_id,
        profile_version=1,
        raw_compatibility_score=50.0,
        display_compatibility_score=50.0,
        confidence_tier="MEDIUM",
        warning_count=1,
        must_have_warning_count=1,
        rank=1,
        is_tied=False,
        score_decomposition=decomp,
    )
    test_db.add(result)
    test_db.commit()

    exp = generate_match_explanation(result_record=result, run_record=run)
    assert len(exp.partial_matches) == 1
    p = exp.partial_matches[0]
    assert p.match_value == 0.5
    assert p.weighted_contribution == 50.0
    assert "EXPERIENCE_TENURE_BELOW_MINIMUM" in p.exact_reason
    assert p.evidence_references == ["ev_python"]
    assert len(exp.must_have_warnings) == 1
    assert exp.must_have_warnings[0].code == "MUST_HAVE_PARTIALLY_MATCHED"
