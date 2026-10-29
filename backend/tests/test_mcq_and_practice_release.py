"""Comprehensive test suite for Phase 3F.2: MCQ contracts, validation, recruiter management, and candidate practice release/discovery."""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

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
)
from app.models.interview import AiInterviewQuestionSetRecord, JobInterviewTemplateRecord
from app.models.job import JobLifecycleStatus, JobRequisition, JobRequirement, JobVersion
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.ai_question_set import AiQuestionSlotUpdateRequest
from app.schemas.mcq_contract import (
    McqCreateRequest,
    McqGenerateRequest,
    McqOptionDto,
    McqReorderRequest,
    McqUpdateRequest,
    CandidatePracticeDiscoveryResponse,
)
from app.schemas.phase3_enums import PracticeReleaseStatus, QuestionDifficulty, QuestionFormat
from app.services.ai_question_set_service import (
    add_manual_mcq_slot,
    approve_ai_question_set,
    generate_ai_questions_by_job_id,
    generate_mcq_slots_for_template,
    get_ai_question_set_by_template,
    remove_mcq_slot,
    reorder_mcq_slots,
    update_ai_question_slot,
    update_mcq_slot,
    validate_ai_question_set,
)
from app.services.candidate_practice_service import (
    get_candidate_practice_discovery,
    list_candidate_practice_releases_for_recruiter,
    release_practice_to_candidate,
    revoke_candidate_practice_release,
)
from app.services.mcq_validation_service import validate_mcq_item
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
def test_recruiter(test_db: Session) -> UserRecord:
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
def test_job_and_version(test_db: Session, test_recruiter: UserRecord):
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=test_recruiter.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        title="Senior Python Backend Engineer",
        created_by_user_id=test_recruiter.user_id,
    )
    test_db.add(ver)
    test_db.flush()

    req1 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        label="PostgreSQL Optimization",
        normalized_label="postgresql optimization",
        category="SKILL",
        necessity="MUST_HAVE",
        priority="HIGH",
        display_order=0,
    )
    req2 = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=ver.job_version_id,
        label="FastAPI & Async Architecture",
        normalized_label="fastapi & async architecture",
        category="SKILL",
        necessity="MUST_HAVE",
        priority="HIGH",
        display_order=1,
    )
    test_db.add_all([req1, req2])
    test_db.commit()
    test_db.refresh(job)
    test_db.refresh(ver)
    return job, ver, [req1, req2]


@pytest.fixture
def test_candidate(test_db: Session, test_recruiter: UserRecord):
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
        full_name="Alex Mercer",
        primary_email=f"candidate_{uuid.uuid4().hex[:8]}@example.com",
        identity_payload={"skills": ["Python", "PostgreSQL"]},
    )
    test_db.add(cand)
    test_db.flush()

    org_id = uuid.uuid4()
    invitation = CandidateInvitationRecord(
        invitation_id=uuid.uuid4(),
        organization_id=org_id,
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
        display_name="Alex Mercer",
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
        organization_id=org_id,
        invitation_id=invitation.invitation_id,
        status=CandidateBindingStatus.ACTIVE.value,
    )
    test_db.add(binding)
    test_db.commit()
    test_db.refresh(cand)
    test_db.refresh(cand_user)
    return cand, cand_user


# =========================================================================
# 1. MCQ Validation Unit Tests
# =========================================================================

def test_mcq_validation_valid_item():
    res = validate_mcq_item(
        question_text="What is the primary purpose of an index in PostgreSQL?",
        options=[
            {"option_id": "A", "text": "Accelerate data retrieval operations at the cost of write performance."},
            {"option_id": "B", "text": "Encrypt table columns on disk."},
            {"option_id": "C", "text": "Automatically generate foreign key relationships."},
            {"option_id": "D", "text": "Compress tabular memory allocations."},
        ],
        correct_option_id="A",
        answer_explanation="Indexes create helper data structures (such as B-Trees) allowing logarithmic time lookups.",
        competency_name="Database Optimization",
    )
    assert res.is_valid is True
    assert res.error_code is None


def test_mcq_validation_rejects_option_count_bounds():
    # 1 option rejected
    res_one = validate_mcq_item(
        question_text="Valid question text with at least ten characters?",
        options=[{"option_id": "A", "text": "Only one option"}],
        correct_option_id="A",
        answer_explanation="Explanation.",
        competency_name="Testing",
    )
    assert res_one.is_valid is False
    assert res_one.error_code == "TOO_FEW_OPTIONS"

    # 6 options rejected
    res_six = validate_mcq_item(
        question_text="Valid question text with at least ten characters?",
        options=[{"option_id": str(i), "text": f"Option {i}"} for i in range(6)],
        correct_option_id="0",
        answer_explanation="Explanation.",
        competency_name="Testing",
    )
    assert res_six.is_valid is False
    assert res_six.error_code == "TOO_MANY_OPTIONS"


def test_mcq_validation_rejects_duplicate_options_and_ids():
    # Duplicate ID
    res_dup_id = validate_mcq_item(
        question_text="Valid question text with at least ten characters?",
        options=[
            {"option_id": "A", "text": "First option text"},
            {"option_id": "A", "text": "Second option text"},
        ],
        correct_option_id="A",
        answer_explanation="Explanation.",
        competency_name="Testing",
    )
    assert res_dup_id.is_valid is False
    assert res_dup_id.error_code == "DUPLICATE_OPTION_ID"

    # Duplicate normalized text
    res_dup_text = validate_mcq_item(
        question_text="Valid question text with at least ten characters?",
        options=[
            {"option_id": "A", "text": "Accelerate data lookups"},
            {"option_id": "B", "text": "accelerate data lookups!"},
        ],
        correct_option_id="A",
        answer_explanation="Explanation.",
        competency_name="Testing",
    )
    assert res_dup_text.is_valid is False
    assert res_dup_text.error_code == "DUPLICATE_OPTION_CONTENT"


def test_mcq_validation_rejects_prohibited_meta_and_negative_patterns():
    # "All of the above"
    res_meta = validate_mcq_item(
        question_text="Which features are supported by PostgreSQL?",
        options=[
            {"option_id": "A", "text": "JSONB storage"},
            {"option_id": "B", "text": "All of the above"},
        ],
        correct_option_id="B",
        answer_explanation="PostgreSQL supports both.",
        competency_name="PostgreSQL Features",
    )
    assert res_meta.is_valid is False
    assert res_meta.error_code == "PROHIBITED_META_OPTION"

    # Negative phrasing
    res_neg = validate_mcq_item(
        question_text="Which of the following is NOT a valid HTTP method?",
        options=[
            {"option_id": "A", "text": "FETCH_ALL"},
            {"option_id": "B", "text": "GET"},
        ],
        correct_option_id="A",
        answer_explanation="FETCH_ALL is not an HTTP method.",
        competency_name="HTTP Protocol",
    )
    assert res_neg.is_valid is False
    assert res_neg.error_code == "PROHIBITED_NEGATIVE_PHRASING"


def test_mcq_validation_rejects_candidate_leakage_and_culture_fit():
    # Resume leak
    res_leak = validate_mcq_item(
        question_text="According to your resume, you used Redis at your last company. How does Redis persist data?",
        options=[
            {"option_id": "A", "text": "RDB snapshots and AOF logs."},
            {"option_id": "B", "text": "Relational schema tables."},
        ],
        correct_option_id="A",
        answer_explanation="Redis supports RDB and AOF.",
        competency_name="Redis Persistence",
    )
    assert res_leak.is_valid is False
    assert res_leak.error_code == "PROHIBITED_CANDIDATE_LEAK"

    # Culture fit
    res_culture = validate_mcq_item(
        question_text="What personality type best fits our engineering team culture fit?",
        options=[
            {"option_id": "A", "text": "Introvert"},
            {"option_id": "B", "text": "Extrovert"},
        ],
        correct_option_id="A",
        answer_explanation="Culture fit is important.",
        competency_name="Team Dynamics",
    )
    assert res_culture.is_valid is False
    assert res_culture.error_code == "PROHIBITED_CULTURE_FIT"


# =========================================================================
# 2. Recruiter MCQ Management and Question Set Composition
# =========================================================================

@pytest.mark.anyio
async def test_recruiter_add_and_update_manual_mcq(test_db: Session, test_recruiter: UserRecord, test_job_and_version):
    job, ver, reqs = test_job_and_version
    fake_provider = FakeWordingProvider(
        simulated_mode="SUCCESS",
        reworded_text_override="Can you detail your practical experience designing RESTful APIs with FastAPI?",
    )

    # 1. Generate base question set
    detail = await generate_ai_questions_by_job_id(
        actor=test_recruiter,
        job_id=job.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )
    assert detail.lifecycle_status == "DRAFT"
    assert detail.conversation_count == 2
    assert detail.mcq_count == 0

    # 2. Add manual MCQ
    mcq_payload = McqCreateRequest(
        question_text="What does the HTTP 409 Conflict status code indicate?",
        options=[
            McqOptionDto(option_id="A", text="The request could not be completed due to a conflict with the current state of the resource."),
            McqOptionDto(option_id="B", text="The server encountered an unhandled internal exception."),
            McqOptionDto(option_id="C", text="The client lacks authentication credentials."),
            McqOptionDto(option_id="D", text="The requested URI does not exist on the server."),
        ],
        correct_option_id="A",
        answer_explanation="HTTP 409 Conflict indicates that the request conflicts with current server state.",
        competency_name="REST Architecture",
        difficulty=QuestionDifficulty.INTERMEDIATE,
    )
    updated_detail = add_manual_mcq_slot(
        actor=test_recruiter,
        template_id=detail.ai_set_id,
        payload=mcq_payload,
        db=test_db,
    )
    assert updated_detail.mcq_count == 1
    assert updated_detail.total_slots_count == 3
    mcq_slot = [s for s in updated_detail.slots if s.question_format == "MCQ_SINGLE_ANSWER"][0]
    assert mcq_slot.question_text == "What does the HTTP 409 Conflict status code indicate?"
    assert mcq_slot.correct_option_id == "A"
    assert mcq_slot.authoring_origin in ("LEGACY_MANUAL", "RECRUITER_AUTHORED")

    # 3. Update MCQ
    update_payload = McqUpdateRequest(
        question_text="What does HTTP 409 Conflict represent in RESTful API design?",
        correct_option_id="A",
        difficulty=QuestionDifficulty.ADVANCED,
    )
    updated_detail2 = update_mcq_slot(
        actor=test_recruiter,
        template_id=detail.ai_set_id,
        slot_id=mcq_slot.slot_id,
        payload=update_payload,
        db=test_db,
    )
    updated_slot = [s for s in updated_detail2.slots if s.slot_id == mcq_slot.slot_id][0]
    assert updated_slot.question_text == "What does HTTP 409 Conflict represent in RESTful API design?"
    assert updated_slot.difficulty == "ADVANCED"
    assert updated_slot.review_state == "EDITED"


@pytest.mark.anyio
async def test_mcq_generation_and_reordering(test_db: Session, test_recruiter: UserRecord, test_job_and_version):
    job, ver, reqs = test_job_and_version
    fake_provider = FakeWordingProvider(
        simulated_mode="SUCCESS",
        reworded_text_override="Explain your approach to database indexing and query optimization.",
    )

    detail = await generate_ai_questions_by_job_id(
        actor=test_recruiter,
        job_id=job.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )

    # Generate 3 MCQs
    gen_payload = McqGenerateRequest(count=3, difficulty=QuestionDifficulty.INTERMEDIATE)
    gen_detail = await generate_mcq_slots_for_template(
        actor=test_recruiter,
        template_id=detail.ai_set_id,
        payload=gen_payload,
        db=test_db,
    )
    assert gen_detail.mcq_count == 3
    mcq_slots = [s for s in gen_detail.slots if s.question_format == "MCQ_SINGLE_ANSWER"]
    assert len(mcq_slots) == 3

    # Reorder MCQs (reverse order)
    reversed_ids = [s.slot_id for s in reversed(mcq_slots)]
    reorder_payload = McqReorderRequest(slot_ids=reversed_ids)
    reordered_detail = reorder_mcq_slots(
        actor=test_recruiter,
        template_id=detail.ai_set_id,
        payload=reorder_payload,
        db=test_db,
    )
    new_mcq_slots = [s for s in reordered_detail.slots if s.question_format == "MCQ_SINGLE_ANSWER"]
    assert [s.slot_id for s in new_mcq_slots] == reversed_ids


@pytest.mark.anyio
async def test_mcq_limit_and_successor_draft_immutability(test_db: Session, test_recruiter: UserRecord, test_job_and_version):
    job, ver, reqs = test_job_and_version
    fake_provider = FakeWordingProvider(
        simulated_mode="SUCCESS",
        reworded_text_override="Explain your approach to database indexing.",
    )

    detail = await generate_ai_questions_by_job_id(
        actor=test_recruiter,
        job_id=job.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )

    # Edit second slot so both conversational slots have distinct wording
    update_ai_question_slot(
        actor=test_recruiter,
        template_id=detail.ai_set_id,
        slot_id=detail.slots[1].slot_id,
        payload=AiQuestionSlotUpdateRequest(
            decision="EDIT",
            wording="Can you discuss how you handle async event loops and query execution in FastAPI?",
        ),
        db=test_db,
    )

    # Approve question set (v1)
    approve_res = approve_ai_question_set(
        actor=test_recruiter,
        template_id=detail.ai_set_id,
        db=test_db,
    )
    assert approve_res.lifecycle_status == "APPROVED"
    assert approve_res.is_immutable is True
    assert approve_res.content_hash is not None

    # Adding an MCQ to an approved question set automatically branches successor draft (v2)
    mcq_payload = McqCreateRequest(
        question_text="What is the time complexity of a B-Tree index search?",
        options=[
            McqOptionDto(option_id="A", text="O(log N) where N is the number of keys."),
            McqOptionDto(option_id="B", text="O(N^2) quadratic polynomial time."),
            McqOptionDto(option_id="C", text="O(1) constant hash lookup time."),
        ],
        correct_option_id="A",
        answer_explanation="B-Trees maintain sorted keys allowing logarithmic lookups.",
    )
    v2_detail = add_manual_mcq_slot(
        actor=test_recruiter,
        template_id=detail.ai_set_id,
        payload=mcq_payload,
        db=test_db,
    )
    assert v2_detail.set_version == 2
    assert v2_detail.lifecycle_status == "DRAFT"
    assert v2_detail.is_immutable is False
    assert v2_detail.mcq_count == 1

    # Verify approved v1 remains untouched in database
    v1_rec = test_db.query(AiInterviewQuestionSetRecord).filter(
        AiInterviewQuestionSetRecord.ai_set_id == detail.ai_set_id
    ).first()
    assert v1_rec.lifecycle_status == "APPROVED"
    assert v1_rec.is_immutable is True


# =========================================================================
# 3. Candidate Practice Release and Zero-Leakage Discovery
# =========================================================================

@pytest.mark.anyio
async def test_practice_release_and_candidate_discovery(
    test_db: Session,
    test_recruiter: UserRecord,
    test_candidate,
    test_job_and_version,
):
    job, ver, reqs = test_job_and_version
    cand, cand_user = test_candidate
    fake_provider = FakeWordingProvider(
        simulated_mode="SUCCESS",
        reworded_text_override="Describe how you design scalable systems in Python.",
    )

    # 1. Create and populate question set
    detail = await generate_ai_questions_by_job_id(
        actor=test_recruiter,
        job_id=job.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )
    
    # Edit second slot to ensure distinct wording
    update_ai_question_slot(
        actor=test_recruiter,
        template_id=detail.ai_set_id,
        slot_id=detail.slots[1].slot_id,
        payload=AiQuestionSlotUpdateRequest(
            decision="EDIT",
            wording="Explain how you handle database connection pooling and query optimization in Python.",
        ),
        db=test_db,
    )

    # Generate MCQs with AI for Phase 3G.1 release eligibility
    gen_payload = McqGenerateRequest(count=1, difficulty=QuestionDifficulty.INTERMEDIATE)
    await generate_mcq_slots_for_template(
        actor=test_recruiter,
        template_id=detail.ai_set_id,
        payload=gen_payload,
        db=test_db,
    )
    
    # 2. Approve set
    approve_ai_question_set(actor=test_recruiter, template_id=detail.ai_set_id, db=test_db)

    # 3. Recruiter releases practice set to candidate
    release_resp = release_practice_to_candidate(
        actor=test_recruiter,
        candidate_id=cand.identity_id,
        job_id=job.job_id,
        ai_set_id=detail.ai_set_id,
        db=test_db,
    )
    assert release_resp["status"] == PracticeReleaseStatus.RELEASED.value
    assert release_resp["candidate_id"] == cand.identity_id
    assert release_resp["job_id"] == job.job_id

    # 4. Candidate discovers released practice interview
    disc = get_candidate_practice_discovery(candidate_user=cand_user, db=test_db)
    assert len(disc.items) == 1
    p_job = disc.items[0]
    assert p_job.job_id == job.job_id
    assert p_job.job_title == "Senior Python Backend Engineer"
    assert p_job.conversation_question_count == 2
    assert p_job.mcq_count == 1
    assert p_job.status_label == "Practice content ready"

    # STRICT ZERO-LEAKAGE SECURITY BOUNDARY VERIFICATION
    raw_dict = p_job.model_dump()
    
    # Prohibited fields must never exist on candidate-safe discovery DTO
    assert "correct_option_id" not in raw_dict
    assert "answer_explanation" not in raw_dict
    assert "is_correct" not in raw_dict
    assert "rubric" not in raw_dict
    assert "prompt" not in raw_dict
    assert "score" not in raw_dict
    assert "rank" not in raw_dict

    # 5. Revoke practice release
    revoke_resp = revoke_candidate_practice_release(
        actor=test_recruiter,
        release_id=release_resp["release_id"],
        db=test_db,
    )
    assert revoke_resp["status"] == PracticeReleaseStatus.REVOKED.value

    # 6. Candidate discovery after revocation
    disc_after = get_candidate_practice_discovery(candidate_user=cand_user, db=test_db)
    assert len(disc_after.items) == 0
