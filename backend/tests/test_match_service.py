import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, select
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
from app.schemas.matching import verify_no_prohibited_keys
from app.services.match_orchestration import (
    CandidateProfileNotFoundError,
    DuplicateCandidateError,
    MatchOrchestrationError,
    compute_candidate_input_fingerprint,
    compute_canonical_match_fingerprint,
    orchestrate_match_run,
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


def _seed_recruiter(test_db, email="recruiter.svc@example.com") -> UserRecord:
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email=email,
        display_name="Service Recruiter",
        password_hash="mock_hash",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add(recruiter)
    test_db.commit()
    return recruiter


def _seed_candidate(
    test_db,
    name: str = "Alice",
    skills: list = None,
    profile_version: int = 1,
) -> CandidateProfileRecord:
    skills = skills or ["Python"]
    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename=f"{name.lower()}_resume.pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(art)
    test_db.flush()

    ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        full_name=name,
        primary_email=f"{name.lower()}@example.com",
        identity_payload={"fullName": {"value": name}, "email": f"{name.lower()}@example.com"},
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
            "headline": {"value": f"Senior {name}"},
            "skills": [
                {
                    "value": s,
                    "normalizedValue": s.lower(),
                    "confidence": 0.95,
                    "confidenceBand": "HIGH",
                    "evidenceIds": [f"ev-{s.lower()}"],
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
                "evidenceId": f"ev-{s.lower()}",
                "pageNumber": 1,
                "blockNumber": idx + 1,
                "characterStart": idx * 10,
                "characterEnd": idx * 10 + len(s),
                "originalText": s,
                "extractionMethod": "DIRECT_TEXT",
            }
            for idx, s in enumerate(skills)
        ],
        review_required=False,
        warning_metadata=[],
    )
    test_db.add(prof)
    test_db.commit()
    return prof


def _seed_job(
    test_db,
    recruiter_id: uuid.UUID,
    skills: list = None,
    revision: int = 1,
) -> tuple[JobRequisition, JobVersion]:
    skills = skills or ["Python"]
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

    for idx, s in enumerate(skills):
        req = JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=ver.job_version_id,
            category=RequirementCategory.SKILL,
            label=s,
            normalized_label=s.lower(),
            display_order=idx,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.HIGH,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            rationale=f"Core skill requirement {s}.",
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


def test_single_and_multiple_candidates_match_execution(test_db):
    """Test that single and multiple candidates create the expected number of match results."""
    recruiter = _seed_recruiter(test_db)
    job, ver = _seed_job(test_db, recruiter.user_id, skills=["Python", "SQL"])
    cand1 = _seed_candidate(test_db, name="Alice", skills=["Python", "SQL"])
    cand2 = _seed_candidate(test_db, name="Bob", skills=["Python"])
    _seed_application(test_db, job, ver, cand1)
    _seed_application(test_db, job, ver, cand2)

    # 1. Single candidate run
    run1, results1, _ = orchestrate_match_run(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=ver.revision,
        candidates=[CandidateProfileSelection(profile_id=cand1.profile_id, profile_version=1)],
        evaluation_date=datetime.now(timezone.utc),
        idempotency_key="key-single-cand",
        db=test_db,
    )
    assert run1.candidate_count == 1
    assert len(results1) == 1
    assert results1[0].profile_id == cand1.profile_id

    # 2. Multi candidate run
    run2, results2, _ = orchestrate_match_run(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=ver.revision,
        candidates=[
            CandidateProfileSelection(profile_id=cand1.profile_id, profile_version=1),
            CandidateProfileSelection(profile_id=cand2.profile_id, profile_version=1),
        ],
        evaluation_date=datetime.now(timezone.utc),
        idempotency_key="key-multi-cand",
        db=test_db,
    )
    assert run2.candidate_count == 2
    assert len(results2) == 2
    # Verify both candidate profile IDs are present
    res_prof_ids = {r.profile_id for r in results2}
    assert res_prof_ids == {cand1.profile_id, cand2.profile_id}


def test_atomic_rollback_on_candidate_failure(test_db):
    """Test that failure on any candidate causes entire run to roll back without partial persistence."""
    recruiter = _seed_recruiter(test_db)
    job, ver = _seed_job(test_db, recruiter.user_id, skills=["Python"])
    cand1 = _seed_candidate(test_db, name="ValidAlice", skills=["Python"])
    _seed_application(test_db, job, ver, cand1)
    non_existent_id = uuid.uuid4()

    # Pass one valid candidate and one non-existent candidate profile
    with pytest.raises(CandidateProfileNotFoundError):
        orchestrate_match_run(
            actor=recruiter,
            job_id=job.job_id,
            expected_revision=ver.revision,
            candidates=[
                CandidateProfileSelection(profile_id=cand1.profile_id, profile_version=1),
                CandidateProfileSelection(profile_id=non_existent_id, profile_version=1),
            ],
            evaluation_date=datetime.now(timezone.utc),
            idempotency_key="key-atomic-fail",
            db=test_db,
        )

    # Verify no match_run or match_result records exist for this idempotency key
    stmt = select(MatchRunRecord).where(MatchRunRecord.idempotency_key == "key-atomic-fail")
    assert test_db.execute(stmt).scalar_one_or_none() is None

    stmt_res = select(MatchResultRecord)
    assert test_db.execute(stmt_res).scalars().all() == []


def test_canonical_fingerprint_sensitivity_and_invariance(test_db):
    """Test that canonical fingerprint changes on semantic inputs but is invariant to identity."""
    req_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    cand1 = uuid.uuid4()
    cand2 = uuid.uuid4()
    eval_date = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)

    base_reqs = [
        {
            "requirement_id": str(uuid.uuid4()),
            "display_order": 0,
            "category": "SKILL",
            "necessity": "MUST_HAVE",
            "priority": "HIGH",
            "approved_weight": 5.0,
            "min_experience_months": None,
            "recency_years": None,
            "accepted_evidence_types": ["EMPLOYMENT"],
            "accepted_equivalents": [],
            "taxonomy_skill_concept_id": None,
        }
    ]

    base_fingerprint = compute_canonical_match_fingerprint(
        requisition_id=req_id,
        job_version_id=ver_id,
        revision=1,
        version_number=1,
        taxonomy_version=None,
        occupation_concept_id=None,
        requirements_data=base_reqs,
        candidates=[(cand1, 1), (cand2, 1)],
        evaluation_date=eval_date,
    )

    # 1. Identity order changes (same candidates in reverse order) -> SAME fingerprint
    reversed_cand_fingerprint = compute_canonical_match_fingerprint(
        requisition_id=req_id,
        job_version_id=ver_id,
        revision=1,
        version_number=1,
        taxonomy_version=None,
        occupation_concept_id=None,
        requirements_data=base_reqs,
        candidates=[(cand2, 1), (cand1, 1)],  # Reversed
        evaluation_date=eval_date,
    )
    assert base_fingerprint == reversed_cand_fingerprint

    # 2. Changed candidate profile version -> DIFFERENT fingerprint
    ver_changed_fp = compute_canonical_match_fingerprint(
        requisition_id=req_id,
        job_version_id=ver_id,
        revision=1,
        version_number=1,
        taxonomy_version=None,
        occupation_concept_id=None,
        requirements_data=base_reqs,
        candidates=[(cand1, 2), (cand2, 1)],  # Changed profile_version
        evaluation_date=eval_date,
    )
    assert base_fingerprint != ver_changed_fp

    # 3. Changed job revision -> DIFFERENT fingerprint
    rev_changed_fp = compute_canonical_match_fingerprint(
        requisition_id=req_id,
        job_version_id=ver_id,
        revision=2,  # Changed revision
        version_number=1,
        taxonomy_version=None,
        occupation_concept_id=None,
        requirements_data=base_reqs,
        candidates=[(cand1, 1), (cand2, 1)],
        evaluation_date=eval_date,
    )
    assert base_fingerprint != rev_changed_fp

    # 4. Changed evaluation date -> DIFFERENT fingerprint
    diff_date = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    date_changed_fp = compute_canonical_match_fingerprint(
        requisition_id=req_id,
        job_version_id=ver_id,
        revision=1,
        version_number=1,
        taxonomy_version=None,
        occupation_concept_id=None,
        requirements_data=base_reqs,
        candidates=[(cand1, 1), (cand2, 1)],
        evaluation_date=diff_date,
    )
    assert base_fingerprint != date_changed_fp


def test_no_raw_resume_text_or_prohibited_identity_in_persisted_results(test_db):
    """Test that persisted decomposition payloads strictly contain NO prohibited identity keys or raw text."""
    recruiter = _seed_recruiter(test_db)
    job, ver = _seed_job(test_db, recruiter.user_id, skills=["Python"])
    cand = _seed_candidate(test_db, name="ConfidentialCandidate", skills=["Python"])
    _seed_application(test_db, job, ver, cand)

    run, results, _ = orchestrate_match_run(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=ver.revision,
        candidates=[CandidateProfileSelection(profile_id=cand.profile_id, profile_version=1)],
        evaluation_date=datetime.now(timezone.utc),
        idempotency_key="key-privacy-test",
        db=test_db,
    )

    res = results[0]
    # Check that score_decomposition strictly passes privacy verification
    verify_no_prohibited_keys(res.score_decomposition)

    # Ensure candidate's name, email, or raw text are not in decomposition
    serialized_decomp = str(res.score_decomposition).lower()
    assert "confidentialcandidate" not in serialized_decomp
    assert "alice" not in serialized_decomp
    assert "raw_resume_text" not in serialized_decomp
