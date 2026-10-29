"""Comprehensive test suite for Phase 3F.3: Candidate Practice Eligibility Diagnostics, Catalog, Requests, and Transactional Release."""
import uuid
from datetime import datetime, timezone
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.auth import UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateBindingStatus,
    CandidateInvitationRecord,
    CandidatePracticeReleaseRecord,
    CandidatePracticeRequestRecord,
    CandidatePracticeRequestStatus,
    JobPracticeCatalogRecord,
)
from app.models.interview import AiInterviewQuestionSetRecord
from app.models.job import JobLifecycleStatus, JobRequisition, JobRequirement, JobVersion, JobVersionStatus
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.mcq_contract import (
    CandidatePracticeRequestCreatePayload,
    CandidatePracticeRequestDeclinePayload,
    PracticeEligibilityReasonCode,
    PublishPracticeCatalogPayload,
)
from app.services.ai_question_set_service import (
    approve_ai_question_set,
    generate_ai_questions_by_job_id,
    prepare_practice_set_for_revision,
)
from app.services.candidate_practice_service import (
    create_candidate_practice_request,
    decline_candidate_practice_request,
    get_candidate_practice_catalog,
    get_candidate_practice_discovery,
    list_candidate_practice_releases_for_recruiter,
    list_candidate_practice_requests_for_candidate,
    list_candidate_practice_requests_for_recruiter,
    list_eligible_jobs_for_candidate_practice,
    publish_job_revision_to_catalog,
    release_practice_from_request,
    release_practice_to_candidate,
    withdraw_candidate_practice_request,
)
from app.services.wording_provider import FakeWordingProvider


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
def test_org_id():
    return uuid.uuid4()


@pytest.fixture
def test_recruiter(test_db: Session, test_org_id: uuid.UUID) -> UserRecord:
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email=f"recruiter_{uuid.uuid4().hex[:8]}@example.com",
        display_name="Test Recruiter",
        password_hash="hashed_secret",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add(recruiter)
    test_db.commit()
    test_db.refresh(recruiter)
    return recruiter


@pytest.fixture
def test_candidate(test_db: Session, test_recruiter: UserRecord, test_org_id: uuid.UUID):
    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="resume.pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=1024,
        sha256_hash="e" * 64,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    test_db.add(artifact)
    test_db.flush()

    cand = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        full_name="Nisha Rao",
        primary_email=f"nisha_{uuid.uuid4().hex[:8]}@example.com",
        identity_payload={"skills": ["Python", "API Development"]},
    )
    test_db.add(cand)
    test_db.flush()

    invitation = CandidateInvitationRecord(
        invitation_id=uuid.uuid4(),
        organization_id=test_org_id,
        candidate_id=cand.identity_id,
        token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc),
        created_by_user_id=test_recruiter.user_id,
    )
    test_db.add(invitation)
    test_db.flush()

    cand_user = UserRecord(
        user_id=uuid.uuid4(),
        email=cand.primary_email,
        display_name="Nisha Rao",
        password_hash="hashed_secret",
        role=UserRole.CANDIDATE,
        is_active=True,
    )
    test_db.add(cand_user)
    test_db.flush()

    binding = CandidateAccountBindingRecord(
        binding_id=uuid.uuid4(),
        user_id=cand_user.user_id,
        candidate_id=cand.identity_id,
        organization_id=test_org_id,
        invitation_id=invitation.invitation_id,
        status=CandidateBindingStatus.ACTIVE.value,
    )
    test_db.add(binding)
    test_db.commit()
    test_db.refresh(cand)
    test_db.refresh(cand_user)
    return cand, cand_user


# =========================================================================
# 1. Eligibility Diagnostic Tests (All Domain Reason Codes)
# =========================================================================

@pytest.mark.anyio
async def test_eligibility_diagnostics_all_reason_codes(
    test_db: Session,
    test_recruiter: UserRecord,
    test_candidate,
    test_org_id: uuid.UUID,
):
    cand, cand_user = test_candidate
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS", reworded_text_override="Explain your experience with Python REST APIs.")

    # 1. Job not ready (DRAFT version)
    job1 = JobRequisition(job_id=uuid.uuid4(), created_by_user_id=test_recruiter.user_id, status=JobLifecycleStatus.ACTIVE)
    test_db.add(job1)
    test_db.flush()
    ver1 = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job1.job_id,
        version_number=1,
        version_status=JobVersionStatus.DRAFT,
        title="Draft Job Without Ready",
        created_by_user_id=test_recruiter.user_id,
    )
    test_db.add(ver1)
    test_db.commit()

    diag1 = list_eligible_jobs_for_candidate_practice(actor=test_recruiter, candidate_id=cand.identity_id, db=test_db)
    inelig_reasons = {item.reason_code for item in diag1.ineligible}
    assert PracticeEligibilityReasonCode.JOB_NOT_READY in inelig_reasons

    # 2. Ready job with NO question set
    ver1.version_status = JobVersionStatus.READY_FOR_MATCHING
    test_db.commit()

    diag2 = list_eligible_jobs_for_candidate_practice(actor=test_recruiter, candidate_id=cand.identity_id, db=test_db)
    inelig_reasons2 = {item.reason_code for item in diag2.ineligible}
    assert PracticeEligibilityReasonCode.NO_PRACTICE_SET in inelig_reasons2

    # 3. Ready job with DRAFT question set
    req1 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver1.job_version_id,
        label="Python Fast API",
        normalized_label="python fast api",
        category="SKILL",
        necessity="MUST_HAVE",
        priority="HIGH",
        display_order=0,
    )
    test_db.add(req1)
    test_db.commit()

    set_detail = await generate_ai_questions_by_job_id(
        actor=test_recruiter,
        job_id=job1.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )
    assert set_detail.lifecycle_status == "DRAFT"

    diag3 = list_eligible_jobs_for_candidate_practice(actor=test_recruiter, candidate_id=cand.identity_id, db=test_db)
    inelig_items3 = [item for item in diag3.ineligible if item.job_id == job1.job_id]
    assert len(inelig_items3) == 1
    assert inelig_items3[0].reason_code == PracticeEligibilityReasonCode.DRAFT_SET_EXISTS
    assert inelig_items3[0].action == "OPEN_QUESTION_SET"

    # 4. Approved question set on current revision -> ELIGIBLE (5 conv, 0 MCQ is valid)
    approve_ai_question_set(actor=test_recruiter, template_id=set_detail.ai_set_id, db=test_db)

    diag4 = list_eligible_jobs_for_candidate_practice(actor=test_recruiter, candidate_id=cand.identity_id, db=test_db)
    elig_items4 = [item for item in diag4.eligible if item.job_id == job1.job_id]
    assert len(elig_items4) == 1
    assert elig_items4[0].status == "READY_TO_RELEASE"
    assert elig_items4[0].conversational_question_count == 1
    assert elig_items4[0].mcq_question_count == 0

    # 5. Job bumped to v2, approved set belongs to v1 -> APPROVED_SET_ON_OLDER_REVISION
    ver2 = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job1.job_id,
        version_number=2,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        title="Junior Python API Developer v2",
        created_by_user_id=test_recruiter.user_id,
    )
    req2 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver2.job_version_id,
        label="FastAPI Microservices",
        normalized_label="fastapi microservices",
        category="SKILL",
        necessity="MUST_HAVE",
        priority="HIGH",
        display_order=0,
    )
    test_db.add_all([ver2, req2])
    test_db.commit()

    diag5 = list_eligible_jobs_for_candidate_practice(actor=test_recruiter, candidate_id=cand.identity_id, db=test_db)
    inelig_items5 = [item for item in diag5.ineligible if item.job_id == job1.job_id]
    assert len(inelig_items5) == 1
    assert inelig_items5[0].reason_code == PracticeEligibilityReasonCode.APPROVED_SET_ON_OLDER_REVISION
    assert inelig_items5[0].action == "PREPARE_PRACTICE_SET"
    assert inelig_items5[0].older_revision == 1

    # 6. Prepare practice set for v2 -> creates DRAFT for v2
    prepared_v2 = prepare_practice_set_for_revision(
        actor=test_recruiter,
        job_id=job1.job_id,
        job_version_id=ver2.job_version_id,
        db=test_db,
    )
    assert prepared_v2.lifecycle_status == "DRAFT"
    assert prepared_v2.job_revision == 2

    # Now diag shows DRAFT_SET_EXISTS for v2
    diag6 = list_eligible_jobs_for_candidate_practice(actor=test_recruiter, candidate_id=cand.identity_id, db=test_db)
    inelig_items6 = [item for item in diag6.ineligible if item.job_id == job1.job_id]
    assert len(inelig_items6) == 1
    assert inelig_items6[0].reason_code == PracticeEligibilityReasonCode.DRAFT_SET_EXISTS

    # Approve v2 -> ELIGIBLE
    approve_ai_question_set(actor=test_recruiter, template_id=prepared_v2.ai_set_id, db=test_db)
    diag7 = list_eligible_jobs_for_candidate_practice(actor=test_recruiter, candidate_id=cand.identity_id, db=test_db)
    elig_items7 = [item for item in diag7.eligible if item.job_id == job1.job_id]
    assert len(elig_items7) == 1

    # Release to candidate -> now ineligibility shows ALREADY_RELEASED
    release_practice_to_candidate(
        actor=test_recruiter,
        candidate_id=cand.identity_id,
        job_id=job1.job_id,
        ai_set_id=prepared_v2.ai_set_id,
        db=test_db,
    )
    diag8 = list_eligible_jobs_for_candidate_practice(actor=test_recruiter, candidate_id=cand.identity_id, db=test_db)
    inelig_items8 = [item for item in diag8.ineligible if item.job_id == job1.job_id]
    assert len(inelig_items8) == 1
    assert inelig_items8[0].reason_code == PracticeEligibilityReasonCode.ALREADY_RELEASED


# =========================================================================
# 2. Candidate Practice Catalog & Request Workflow Tests
# =========================================================================

@pytest.mark.anyio
async def test_practice_catalog_and_request_lifecycle(
    test_db: Session,
    test_recruiter: UserRecord,
    test_candidate,
    test_org_id: uuid.UUID,
):
    cand, cand_user = test_candidate
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS", reworded_text_override="Describe API security and JWT authentication in FastAPI.")

    # 1. Setup Job v3
    job = JobRequisition(job_id=uuid.uuid4(), created_by_user_id=test_recruiter.user_id, status=JobLifecycleStatus.ACTIVE)
    test_db.add(job)
    test_db.flush()

    ver3 = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=3,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        title="Junior Python API Developer v3",
        created_by_user_id=test_recruiter.user_id,
    )
    req = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver3.job_version_id,
        label="FastAPI Security",
        normalized_label="fastapi security",
        category="SKILL",
        necessity="MUST_HAVE",
        priority="HIGH",
        display_order=0,
    )
    test_db.add_all([ver3, req])
    test_db.commit()

    # Create approved question set
    q_set = await generate_ai_questions_by_job_id(
        actor=test_recruiter,
        job_id=job.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )
    approve_ai_question_set(actor=test_recruiter, template_id=q_set.ai_set_id, db=test_db)

    # 2. Recruiter publishes job to candidate practice catalog
    cat_item = publish_job_revision_to_catalog(
        actor=test_recruiter,
        payload=PublishPracticeCatalogPayload(
            job_version_id=ver3.job_version_id,
            safe_title="Junior Python API Developer",
            safe_summary="Hands-on practice covering FastAPI architecture and API endpoints.",
            skill_labels=["Python", "FastAPI"],
        ),
        db=test_db,
    )
    assert cat_item.safe_title == "Junior Python API Developer"

    # 3. Candidate views catalog
    cat_resp = get_candidate_practice_catalog(candidate_user=cand_user, db=test_db)
    assert len(cat_resp.items) == 1
    assert cat_resp.items[0].job_version_id == ver3.job_version_id
    assert cat_resp.items[0].request_status == "NOT_REQUESTED"

    # Strict zero-leakage check
    cat_dict = cat_resp.items[0].model_dump()
    for forbidden in ["correct_option_id", "answer_explanation", "rubric", "criteria", "ranking", "internal_notes"]:
        assert forbidden not in cat_dict

    # 4. Candidate submits practice request
    req_dto = create_candidate_practice_request(
        candidate_user=cand_user,
        job_version_id=ver3.job_version_id,
        db=test_db,
    )
    assert req_dto.status == CandidatePracticeRequestStatus.REQUESTED.value

    # Catalog reflects REQUESTED status
    cat_resp2 = get_candidate_practice_catalog(candidate_user=cand_user, db=test_db)
    assert cat_resp2.items[0].request_status == "REQUESTED"
    assert cat_resp2.items[0].active_request_id == req_dto.request_id

    # Recruiter sees request in eligibility diagnostics (pinned as has_candidate_request)
    diag = list_eligible_jobs_for_candidate_practice(actor=test_recruiter, candidate_id=cand.identity_id, db=test_db)
    assert len(diag.eligible) == 1
    assert diag.eligible[0].has_candidate_request is True
    assert diag.eligible[0].candidate_request_id == req_dto.request_id

    # 5. Candidate withdraws request
    withdrawn = withdraw_candidate_practice_request(
        candidate_user=cand_user,
        request_id=req_dto.request_id,
        db=test_db,
    )
    assert withdrawn["status"] == "WITHDRAWN"

    # Candidate re-requests
    req_dto2 = create_candidate_practice_request(
        candidate_user=cand_user,
        job_version_id=ver3.job_version_id,
        db=test_db,
    )
    assert req_dto2.status == "REQUESTED"

    # 6. Recruiter releases from request
    release_res = release_practice_from_request(
        actor=test_recruiter,
        request_id=req_dto2.request_id,
        db=test_db,
    )
    assert release_res["status"] == "RELEASED"

    # Candidate catalog now reports RELEASED
    cat_resp3 = get_candidate_practice_catalog(candidate_user=cand_user, db=test_db)
    assert cat_resp3.items[0].request_status == "RELEASED"
    assert cat_resp3.items[0].release_id == release_res["release_id"]

    # Candidate discovery contains released practice session
    cand_discovery = get_candidate_practice_discovery(candidate_user=cand_user, db=test_db)
    assert len(cand_discovery.items) == 1
    assert cand_discovery.items[0].release_id == release_res["release_id"]


@pytest.mark.anyio
async def test_cross_organization_practice_isolation(
    test_db: Session,
    test_recruiter: UserRecord,
    test_candidate,
):
    cand, cand_user = test_candidate
    other_org_id = uuid.uuid4()

    # Create job in another organization
    other_job = JobRequisition(job_id=uuid.uuid4(), created_by_user_id=test_recruiter.user_id, status=JobLifecycleStatus.ACTIVE)
    test_db.add(other_job)
    test_db.flush()

    other_ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=other_job.job_id,
        version_number=1,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        title="Other Org Job",
        created_by_user_id=test_recruiter.user_id,
    )
    cat_rec = JobPracticeCatalogRecord(
        catalog_id=uuid.uuid4(),
        organization_id=other_org_id,
        job_id=other_job.job_id,
        job_version_id=other_ver.job_version_id,
        safe_title="Other Org Job",
        safe_summary="Summary",
        skill_labels=["Rust"],
        is_published=True,
        published_by_user_id=test_recruiter.user_id,
    )
    test_db.add(cat_rec)
    test_db.commit()

    # Candidate from test_candidate cannot request other org job
    with pytest.raises(HTTPException) as exc_info:
        create_candidate_practice_request(
            candidate_user=cand_user,
            job_version_id=other_ver.job_version_id,
            db=test_db,
        )
    assert exc_info.value.status_code in (403, 404)
