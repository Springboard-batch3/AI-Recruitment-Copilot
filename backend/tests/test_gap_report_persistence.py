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
from app.models.gap_report import (
    GapAuditEventRecord,
    GapAuditEventType,
    GapReportItemRecord,
    GapReportRecord,
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
from app.schemas.requirement_gap import (
    GAP_POLICY_VERSION,
    GAP_SCHEMA_VERSION,
    WORDING_TEMPLATE_VERSION,
    GapReviewActionCode,
    RequirementGapState,
)
from app.schemas.requirement_match import MatchResultState
from app.services.gap_report_service import (
    GapReportNotFoundError,
    GapReportServiceError,
    IdempotencyConflictError,
    MatchResultNotFoundError,
    create_gap_report_for_actor,
    get_canonical_gap_report_for_actor,
    get_gap_report_for_actor,
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


def _seed_recruiter(test_db, email="recruiter.gap@example.com") -> UserRecord:
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email=email,
        display_name="Gap Recruiter",
        password_hash="mock_hash",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add(recruiter)
    test_db.commit()
    return recruiter


def _seed_job_and_version(test_db, recruiter_id: uuid.UUID):
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
        title="Backend Engineer",
        created_by_user_id=recruiter_id,
    )
    test_db.add(ver)
    test_db.flush()

    req1 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
        label="Python",
        normalized_label="python",
        rationale="Must know Python",
    )
    req2 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        display_order=1,
        category=RequirementCategory.EXPERIENCE,
        necessity=RequirementNecessity.PREFERRED,
        priority=RequirementPriority.MEDIUM,
        origin=RequirementOrigin.RECRUITER_ENTERED,
        label="36 months experience",
        normalized_label="36 months experience",
        rationale="36 months experience preferred",
    )
    test_db.add_all([req1, req2])
    test_db.flush()
    return job, ver, [req1, req2]


def _seed_candidate(test_db):
    art = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="candidate_resume.pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(art)
    test_db.flush()

    ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=art.artifact_id,
        full_name="Bob Candidate",
        primary_email="bob@example.com",
        identity_payload={"fullName": {"value": "Bob Candidate"}},
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
            "skills": [{"name": "Python", "category": "TECHNICAL"}],
            "experience": [{"title": "Software Engineer", "years": 3.0}],
        },
    )
    test_db.add(prof)
    test_db.flush()
    return ident, prof


def _seed_match_run_and_result(test_db, recruiter_id: uuid.UUID, job_ver, prof, reqs):
    run = MatchRunRecord(
        run_id=uuid.uuid4(),
        recruiter_id=recruiter_id,
        job_id=job_ver.job_id,
        job_version_id=job_ver.job_version_id,
        revision=job_ver.revision,
        evaluation_date=datetime.now(timezone.utc),
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        matching_rule_version="1.0.0",
        scoring_policy_version="phase2c3_scoring_v1",
        candidate_input_fingerprint="c_input_fp",
        input_fingerprint="input_fp",
        idempotency_key=f"run_{uuid.uuid4()}",
        lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
        candidate_count=1,
    )
    test_db.add(run)
    test_db.flush()

    requirement_contributions = [
        {
            "requirement_id": str(reqs[0].requirement_id),
            "display_order": 0,
            "category": reqs[0].category.value,
            "necessity": reqs[0].necessity.value,
            "priority": reqs[0].priority.value,
            "match_state": "MATCHED",
            "match_value": 1.0,
            "evidence_references": ["resume.pdf#ev1"],
            "expected_value": "Python",
            "observed_value": "Python",
            "review_recommended": False,
        },
        {
            "requirement_id": str(reqs[1].requirement_id),
            "display_order": 1,
            "category": reqs[1].category.value,
            "necessity": reqs[1].necessity.value,
            "priority": reqs[1].priority.value,
            "match_state": "PARTIALLY_MATCHED",
            "match_value": 0.6667,
            "evidence_references": ["resume.pdf#ev2"],
            "expected_value": "36 months",
            "observed_value": "24 months",
            "reason_codes": ["EXPERIENCE_BELOW_THRESHOLD_24_OF_36_MONTHS"],
            "review_recommended": False,
        },
    ]

    res = MatchResultRecord(
        result_id=uuid.uuid4(),
        run_id=run.run_id,
        profile_id=prof.profile_id,
        profile_version=prof.profile_version,
        raw_compatibility_score=Decimal("83.333333"),
        display_compatibility_score=Decimal("83.33"),
        confidence_tier="HIGH",
        rank=1,
        warning_count=0,
        must_have_warning_count=0,
        score_decomposition={
            "requirement_contributions": requirement_contributions,
            "must_have_warnings": [],
        },
    )
    test_db.add(res)
    test_db.commit()
    return run, res


def test_atomic_report_creation_and_provenance(test_db):
    """Test successful atomic report creation, exact provenance, and record derivation."""
    recruiter = _seed_recruiter(test_db)
    _, job_ver, reqs = _seed_job_and_version(test_db, recruiter.user_id)
    _, prof = _seed_candidate(test_db)
    run, res = _seed_match_run_and_result(test_db, recruiter.user_id, job_ver, prof, reqs)

    report, is_reused = create_gap_report_for_actor(
        actor=recruiter,
        match_result_id=res.result_id,
        idempotency_key="key_test_atomic_1",
        db=test_db,
    )

    assert is_reused is False
    assert report is not None
    assert report.recruiter_id == recruiter.user_id
    assert report.match_run_id == run.run_id
    assert report.match_result_id == res.result_id
    assert report.job_id == job_ver.job_id
    assert report.job_version_id == job_ver.job_version_id
    assert report.revision == 1
    assert report.profile_id == prof.profile_id
    assert report.profile_version == 1
    assert report.gap_schema_version == GAP_SCHEMA_VERSION
    assert report.gap_policy_version == GAP_POLICY_VERSION
    assert report.wording_template_version == WORDING_TEMPLATE_VERSION

    # Check items count == requirement results count
    items = list(report.items)
    assert len(items) == 2

    # Check summary counts
    assert report.total_requirements == 2
    assert report.covered_count == 1
    assert report.partially_covered_count == 1
    assert report.not_evidenced_count == 0
    assert report.unverified_count == 0
    assert report.conflicting_count == 0
    assert report.not_applicable_count == 0
    assert report.must_have_warning_count == 0

    # Verify audit event logged
    audit = test_db.execute(
        select(GapAuditEventRecord).where(
            GapAuditEventRecord.report_id == report.report_id,
            GapAuditEventRecord.event_type == GapAuditEventType.GAP_REPORT_CREATED.value,
        )
    ).scalar_one_or_none()
    assert audit is not None
    assert audit.actor_user_id == recruiter.user_id
    assert "Bob Candidate" not in str(audit.safe_metadata)
    assert "bob@example.com" not in str(audit.safe_metadata)


def test_idempotent_reuse_and_conflict(test_db):
    """Test same-key same-request reuse (is_reused=True) and same-key different-request 409 conflict."""
    recruiter = _seed_recruiter(test_db)
    _, job_ver, reqs = _seed_job_and_version(test_db, recruiter.user_id)
    _, prof = _seed_candidate(test_db)
    _, res1 = _seed_match_run_and_result(test_db, recruiter.user_id, job_ver, prof, reqs)
    _, res2 = _seed_match_run_and_result(test_db, recruiter.user_id, job_ver, prof, reqs)

    # First call creates report
    report1, is_reused1 = create_gap_report_for_actor(
        actor=recruiter,
        match_result_id=res1.result_id,
        idempotency_key="idemp_key_reuse_test",
        db=test_db,
    )
    assert is_reused1 is False

    # Same key, same request -> reuse existing report
    report2, is_reused2 = create_gap_report_for_actor(
        actor=recruiter,
        match_result_id=res1.result_id,
        idempotency_key="idemp_key_reuse_test",
        db=test_db,
    )
    assert is_reused2 is True
    assert report2.report_id == report1.report_id

    # Same key, different request (different match_result_id) -> 409 conflict
    with pytest.raises(IdempotencyConflictError):
        create_gap_report_for_actor(
            actor=recruiter,
            match_result_id=res2.result_id,
            idempotency_key="idemp_key_reuse_test",
            db=test_db,
        )


def test_cross_recruiter_denial(test_db):
    """Test recruiter cannot access or generate gap report for match result owned by another recruiter."""
    recruiter1 = _seed_recruiter(test_db, email="rec1@test.com")
    recruiter2 = _seed_recruiter(test_db, email="rec2@test.com")
    _, job_ver, reqs = _seed_job_and_version(test_db, recruiter1.user_id)
    _, prof = _seed_candidate(test_db)
    _, res = _seed_match_run_and_result(test_db, recruiter1.user_id, job_ver, prof, reqs)

    # Recruiter 2 attempts to create report for Recruiter 1's match result -> 404 (non-disclosing)
    with pytest.raises(MatchResultNotFoundError):
        create_gap_report_for_actor(
            actor=recruiter2,
            match_result_id=res.result_id,
            idempotency_key="rec2_key",
            db=test_db,
        )


def test_completed_report_update_prevention(test_db):
    """Test that completed reports and report items reject modifications."""
    recruiter = _seed_recruiter(test_db)
    _, job_ver, reqs = _seed_job_and_version(test_db, recruiter.user_id)
    _, prof = _seed_candidate(test_db)
    _, res = _seed_match_run_and_result(test_db, recruiter.user_id, job_ver, prof, reqs)

    report, _ = create_gap_report_for_actor(
        actor=recruiter,
        match_result_id=res.result_id,
        idempotency_key="immutability_key",
        db=test_db,
    )

    # Attempt to update report record via ORM
    report.total_requirements = 999
    with pytest.raises(ValueError, match="Gap reports are immutable"):
        test_db.flush()
    test_db.rollback()

    # Verify original state unchanged
    refreshed_report = test_db.execute(
        select(GapReportRecord).where(GapReportRecord.report_id == report.report_id)
    ).scalar_one()
    assert refreshed_report.total_requirements == 2

    # Attempt to update report item record
    item = refreshed_report.items[0]
    item.source_match_value = 0.0
    with pytest.raises(ValueError, match="Gap report items are immutable"):
        test_db.flush()
    test_db.rollback()


def test_database_constraints_enforcement(test_db):
    """Test SQL constraints: bounds on match_value, positive shortfall, unique requirement order."""
    recruiter = _seed_recruiter(test_db)
    _, job_ver, reqs = _seed_job_and_version(test_db, recruiter.user_id)
    _, prof = _seed_candidate(test_db)
    _, res = _seed_match_run_and_result(test_db, recruiter.user_id, job_ver, prof, reqs)

    report, _ = create_gap_report_for_actor(
        actor=recruiter,
        match_result_id=res.result_id,
        idempotency_key="constraint_base_report",
        db=test_db,
    )

    # 1. Duplicate display_order inside same report
    dup_item = GapReportItemRecord(
        item_id=uuid.uuid4(),
        report_id=report.report_id,
        requirement_id=uuid.uuid4(),
        display_order=0,  # Duplicate of order 0
        category=RequirementCategory.SKILL,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        source_match_state=MatchResultState.MATCHED,
        source_match_value=1.0,
        gap_state=RequirementGapState.COVERED,
        review_required=False,
        recruiter_wording="Test wording",
    )
    test_db.add(dup_item)
    with pytest.raises(IntegrityError):
        test_db.flush()
    test_db.rollback()

    # 2. Negative shortfall check
    neg_shortfall_item = GapReportItemRecord(
        item_id=uuid.uuid4(),
        report_id=report.report_id,
        requirement_id=uuid.uuid4(),
        display_order=99,
        category=RequirementCategory.EXPERIENCE,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        source_match_state=MatchResultState.PARTIALLY_MATCHED,
        source_match_value=0.5,
        measurable_shortfall_value=-1.0,  # Violates check constraint
        shortfall_unit="months",
        gap_state=RequirementGapState.PARTIALLY_COVERED,
        review_required=False,
        recruiter_wording="Test wording",
    )
    test_db.add(neg_shortfall_item)
    with pytest.raises(IntegrityError):
        test_db.flush()
    test_db.rollback()


def test_canonical_report_assembly_and_invariants(test_db):
    """Test canonical report assembly derives exact invariants: total == sum(gap_states)."""
    recruiter = _seed_recruiter(test_db)
    _, job_ver, reqs = _seed_job_and_version(test_db, recruiter.user_id)
    _, prof = _seed_candidate(test_db)
    _, res = _seed_match_run_and_result(test_db, recruiter.user_id, job_ver, prof, reqs)

    report, _ = create_gap_report_for_actor(
        actor=recruiter,
        match_result_id=res.result_id,
        idempotency_key="canonical_assembly_key",
        db=test_db,
    )

    canonical = get_canonical_gap_report_for_actor(
        report_id=report.report_id,
        actor=recruiter,
        db=test_db,
    )
    summary = canonical.summary_counts

    assert canonical.report_id == report.report_id
    assert canonical.total_requirements == 2
    assert summary[RequirementGapState.COVERED.value] == 1
    assert summary[RequirementGapState.PARTIALLY_COVERED.value] == 1
    assert summary[RequirementGapState.NOT_EVIDENCED.value] == 0
    assert summary[RequirementGapState.UNVERIFIED.value] == 0
    assert summary[RequirementGapState.CONFLICTING_REVIEW_REQUIRED.value] == 0
    assert summary[RequirementGapState.NOT_APPLICABLE.value] == 0

    assert (
        canonical.total_requirements
        == summary[RequirementGapState.COVERED.value]
        + summary[RequirementGapState.PARTIALLY_COVERED.value]
        + summary[RequirementGapState.NOT_EVIDENCED.value]
        + summary[RequirementGapState.UNVERIFIED.value]
        + summary[RequirementGapState.CONFLICTING_REVIEW_REQUIRED.value]
        + summary[RequirementGapState.NOT_APPLICABLE.value]
    )
