import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.auth import Base, UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import CandidateApplicationRecord, CandidateApplicationStatus
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
    MatchAuditEventRecord,
    MatchAuditEventType,
    MatchResultRecord,
    MatchRunLifecycleStatus,
    MatchRunRecord,
)
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.match_run import CandidateProfileSelection
from app.services.match_orchestration import orchestrate_match_run


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


def _seed_recruiter(test_db, email="recruiter.persist@example.com") -> UserRecord:
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email=email,
        display_name="Persist Recruiter",
        password_hash="mock_hash",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add(recruiter)
    test_db.commit()
    return recruiter


def _seed_candidate_profile(
    test_db,
    profile_version: int = 1,
    skills: list = None,
) -> CandidateProfileRecord:
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
        primary_email="alice@example.com",
        identity_payload={"fullName": {"value": "Alice Smith"}},
    )
    test_db.add(ident)
    test_db.flush()

    prof = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=profile_version,
        artifact_id=art.artifact_id,
        extraction_id=uuid.uuid4(),
        identity_id=ident.identity_id,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        professional_profile={
            "headline": {"value": "Engineer"},
            "skills": [
                {
                    "value": s,
                    "normalizedValue": s.lower(),
                    "confidence": 0.95,
                    "confidenceBand": "HIGH",
                    "evidenceIds": [f"ev-{idx}"],
                    "reviewStatus": "CONFIRMED",
                }
                for idx, s in enumerate(skills)
            ],
            "employment": [],
            "education": [],
            "projects": [],
            "certifications": [],
            "languages": [],
        },
        evidence_spans=[
            {
                "evidenceId": "ev-0",
                "pageNumber": 1,
                "blockNumber": 1,
                "characterStart": 0,
                "characterEnd": 6,
                "originalText": "Python",
                "extractionMethod": "DIRECT_TEXT",
            }
        ],
        review_required=False,
        warning_metadata=[],
    )
    test_db.add(prof)
    test_db.commit()
    return prof


def _seed_ready_job(
    test_db,
    recruiter_id: uuid.UUID,
    revision: int = 1,
) -> tuple[JobRequisition, JobVersion]:
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
        revision=revision,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        title="Software Engineer",
        created_by_user_id=recruiter_id,
        ready_by_user_id=recruiter_id,
        ready_at=datetime.now(timezone.utc),
    )
    test_db.add(ver)
    test_db.flush()

    req = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        display_order=0,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
        rationale="Core service implementation in Python.",
    )
    test_db.add(req)
    test_db.commit()
    return job, ver


def _seed_application(test_db, job, ver, cand, status=CandidateApplicationStatus.SUBMITTED.value):
    app = CandidateApplicationRecord(
        application_id=uuid.uuid4(),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        user_id=uuid.uuid4(),
        binding_id=uuid.uuid4(),
        candidate_id=cand.identity_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        job_revision=ver.revision,
        artifact_id=cand.artifact_id,
        profile_id=cand.profile_id,
        profile_version=cand.profile_version,
        status=status,
        submitted_at=datetime.now(timezone.utc),
    )
    test_db.add(app)
    test_db.commit()
    return app


def test_match_run_and_results_persistence(test_db):
    """Test that a match run and its results are persisted correctly with all required columns."""
    recruiter = _seed_recruiter(test_db)
    job, ver = _seed_ready_job(test_db, recruiter.user_id)
    cand = _seed_candidate_profile(test_db, profile_version=1, skills=["Python"])
    _seed_application(test_db, job, ver, cand)

    run, results, reused = orchestrate_match_run(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=ver.revision,
        candidates=[CandidateProfileSelection(profile_id=cand.profile_id, profile_version=1)],
        evaluation_date=datetime.now(timezone.utc),
        idempotency_key="key-test-persist-1",
        db=test_db,
    )

    assert reused is False
    assert run.lifecycle_status == MatchRunLifecycleStatus.SUCCEEDED
    assert run.candidate_count == 1
    assert len(results) == 1
    res = results[0]
    assert res.run_id == run.run_id
    assert res.profile_id == cand.profile_id
    assert res.profile_version == 1
    assert res.rank == 1
    assert res.raw_compatibility_score >= Decimal("0.0")
    assert res.display_compatibility_score >= Decimal("0.0")
    assert "requirement_contributions" in res.score_decomposition


def test_duplicate_scoped_idempotency_key_rejected(test_db):
    """Test that duplicate idempotency key for the same recruiter is rejected by uniqueness constraint."""
    recruiter = _seed_recruiter(test_db)
    job, ver = _seed_ready_job(test_db, recruiter.user_id)

    run1 = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=1,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="1.0.0",
        scoring_policy_version="1.0.0",
        candidate_input_fingerprint="abc",
        input_fingerprint="def",
        idempotency_key="unique-key-1",
        lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
        candidate_count=1,
    )
    test_db.add(run1)
    test_db.commit()

    run2 = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=1,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="1.0.0",
        scoring_policy_version="1.0.0",
        candidate_input_fingerprint="abc",
        input_fingerprint="def",
        idempotency_key="unique-key-1",  # Duplicate key for same recruiter
        lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
        candidate_count=1,
    )
    test_db.add(run2)
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()


def test_duplicate_candidate_profile_in_run_rejected(test_db):
    """Test that a candidate profile version cannot be inserted twice into the same match run."""
    recruiter = _seed_recruiter(test_db)
    job, ver = _seed_ready_job(test_db, recruiter.user_id)
    cand = _seed_candidate_profile(test_db, profile_version=1)

    run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter.user_id,
        job_id=job.job_id,
        job_version_id=ver.job_version_id,
        revision=1,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="1.0.0",
        scoring_policy_version="1.0.0",
        candidate_input_fingerprint="abc",
        input_fingerprint="def",
        idempotency_key="key-dup-cand",
        lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
        candidate_count=2,
    )
    test_db.add(run)
    test_db.flush()

    res1 = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=run.run_id,
        profile_id=cand.profile_id,
        profile_version=1,
        raw_compatibility_score=Decimal("80.000000"),
        display_compatibility_score=Decimal("80.00"),
        confidence_tier="HIGH",
        rank=1,
        score_decomposition={},
    )
    res2 = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=run.run_id,
        profile_id=cand.profile_id,
        profile_version=1,  # Duplicate profile_id and profile_version in same run
        raw_compatibility_score=Decimal("80.000000"),
        display_compatibility_score=Decimal("80.00"),
        confidence_tier="HIGH",
        rank=1,
        score_decomposition={},
    )
    test_db.add_all([res1, res2])
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()


def test_historical_results_survive_successor_versions(test_db):
    """Test that creating successor candidate profiles or job revisions preserves historical match results."""
    recruiter = _seed_recruiter(test_db)
    job, ver1 = _seed_ready_job(test_db, recruiter.user_id, revision=1)
    cand_v1 = _seed_candidate_profile(test_db, profile_version=1, skills=["Python"])
    _seed_application(test_db, job, ver1, cand_v1)

    run, results, _ = orchestrate_match_run(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        candidates=[CandidateProfileSelection(profile_id=cand_v1.profile_id, profile_version=1)],
        evaluation_date=datetime.now(timezone.utc),
        idempotency_key="key-successor-test",
        db=test_db,
    )
    original_run_id = run.run_id
    original_result_id = results[0].result_id
    original_score = results[0].raw_compatibility_score

    # Now create candidate profile v2 for same candidate identity / artifact
    cand_v2 = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=2,
        artifact_id=cand_v1.artifact_id,
        extraction_id=cand_v1.extraction_id,
        identity_id=cand_v1.identity_id,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="REVIEWER_CORRECTED",
        professional_profile={"headline": {"value": "Updated Lead"}},
        evidence_spans=[],
        review_required=False,
    )
    test_db.add(cand_v2)

    # Now create Job Version 2 (successor)
    ver2 = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=2,
        revision=1,
        version_status=JobVersionStatus.DRAFT,
        title="Lead Software Engineer",
        created_by_user_id=recruiter.user_id,
    )
    test_db.add(ver2)
    test_db.commit()

    # Verify historical run and results remain intact and unmodified
    stmt = select(MatchRunRecord).where(MatchRunRecord.run_id == original_run_id)
    persisted_run = test_db.execute(stmt).scalar_one()
    assert persisted_run.job_version_id == ver1.job_version_id
    assert persisted_run.revision == 1

    stmt_res = select(MatchResultRecord).where(MatchResultRecord.result_id == original_result_id)
    persisted_res = test_db.execute(stmt_res).scalar_one()
    assert persisted_res.profile_id == cand_v1.profile_id
    assert persisted_res.profile_version == 1
    assert persisted_res.raw_compatibility_score == original_score
