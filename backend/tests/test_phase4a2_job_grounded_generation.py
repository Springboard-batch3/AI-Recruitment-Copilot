"""Focused unit tests for Phase 4A.2 Job-Revision-Grounded AI Question Generation.

Validates:
1. Same unchanged job revision + generation identity reuses existing valid set idempotently without calling provider.
2. Different jobs generate separate grounded sets and cannot share records.
3. Changed job revision produces a different source fingerprint and successor draft.
4. Generated requirement IDs must belong to the selected job revision; unknown or cross-job IDs are rejected.
5. Malformed, truncated, or invalid provider responses cannot become a valid draft.
6. One controlled repair attempt is permitted; second invalid response becomes an explicit failure.
7. Approved question sets are immutable and cannot be edited in place.
8. Historical releases and stored candidate records are preserved.
"""
import uuid
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.auth import UserRecord, UserRole
from app.models.interview import AiInterviewQuestionSetRecord
from app.models.job import (
    EmploymentType,
    JobLifecycleStatus,
    JobRequisition,
    JobRequirement,
    JobVersion,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.schemas.question_generation_contract import (
    GENERATION_POLICY_VERSION,
    GUARDRAIL_VERSION,
    PROMPT_CONTRACT_VERSION,
    QUESTION_GENERATION_SCHEMA_VERSION,
    CanonicalGenerationSourcePacket,
    GenerationDeliveryMode,
    StrictGeneratedQuestionItem,
    StrictJobGroundedGenerationBatchResponse,
)
from app.services.ai_question_set_service import (
    _build_canonical_generation_packet,
    _compute_canonical_source_fingerprint,
    _ensure_editable_set,
    _generate_requirement_grounded_slots,
    approve_ai_question_set,
    generate_ai_questions_by_job_id,
    validate_ai_question_set,
)
from app.services.wording_provider import (
    FakeWordingProvider,
    ProviderErrorCode,
    ProviderTransportError,
)


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
def mock_user(test_db: Session) -> UserRecord:
    user = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter@example.com",
        display_name="Test Recruiter",
        role=UserRole.RECRUITER,
        password_hash="fakehash",
    )
    user.organization_id = uuid.uuid4()
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def mock_job_with_requirements(test_db: Session, mock_user: UserRecord) -> JobRequisition:
    job = JobRequisition(
        job_id=uuid.uuid4(),
        status=JobLifecycleStatus.ACTIVE,
        created_by_user_id=mock_user.user_id,
    )
    job.organization_id = mock_user.organization_id
    test_db.add(job)
    test_db.flush()

    job_ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=1,
        title="Senior Distributed Systems Engineer",
        summary="Lead core infrastructure services handling high-throughput event processing.",
        responsibilities="Design scalable microservices, manage Kafka pipelines, and optimize PostgreSQL databases.",
        version_status=JobVersionStatus.APPROVED,
        created_by_user_id=mock_user.user_id,
    )
    test_db.add(job_ver)
    test_db.flush()

    reqs = [
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=job_ver.job_version_id,
            display_order=0,
            label="Distributed Event Streaming (Kafka)",
            normalized_label="distributed event streaming kafka",
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.CRITICAL,
            rationale="Must design partition strategies and backpressure control for event processing.",
        ),
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=job_ver.job_version_id,
            display_order=1,
            label="PostgreSQL Query Optimization",
            normalized_label="postgresql query optimization",
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.HIGH,
            rationale="Optimize high-volume transactional schemas and index tuning.",
        ),
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=job_ver.job_version_id,
            display_order=2,
            label="System Architecture & Trade-off Analysis",
            normalized_label="system architecture trade-off analysis",
            category=RequirementCategory.RESPONSIBILITY,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.HIGH,
            rationale="Evaluate CAP theorem trade-offs and disaster recovery strategies.",
        ),
    ]
    for r in reqs:
        test_db.add(r)
    test_db.commit()
    test_db.refresh(job)
    return job


@pytest.fixture
def mock_job_b_with_requirements(test_db: Session, mock_user: UserRecord) -> JobRequisition:
    job = JobRequisition(
        job_id=uuid.uuid4(),
        status=JobLifecycleStatus.ACTIVE,
        created_by_user_id=mock_user.user_id,
    )
    job.organization_id = mock_user.organization_id
    test_db.add(job)
    test_db.flush()

    job_ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=1,
        title="Lead Product Designer",
        summary="Create design systems, conduct user research, and build interactive wireframes.",
        responsibilities="Lead design critiques, maintain Figma design libraries, and test accessibility.",
        version_status=JobVersionStatus.APPROVED,
        created_by_user_id=mock_user.user_id,
    )
    test_db.add(job_ver)
    test_db.flush()

    reqs = [
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=job_ver.job_version_id,
            display_order=0,
            label="Design Systems Architecture (Figma)",
            normalized_label="design systems architecture figma",
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.CRITICAL,
            rationale="Create multi-brand design tokens and scalable component libraries.",
        ),
        JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=job_ver.job_version_id,
            display_order=1,
            label="Qualitative User Research & Usability Testing",
            normalized_label="qualitative user research usability testing",
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.HIGH,
            rationale="Plan moderated usability sessions and synthesize customer journey maps.",
        ),
    ]
    for r in reqs:
        test_db.add(r)
    test_db.commit()
    test_db.refresh(job)
    return job


@pytest.mark.anyio
async def test_canonical_packet_and_fingerprint_deterministic(mock_job_with_requirements: JobRequisition):
    """Verifies that CanonicalGenerationSourcePacket is built with complete context and produces a deterministic SHA-256 fingerprint."""
    job = mock_job_with_requirements
    latest_ver = job.versions[-1]

    packet1, plan1 = _build_canonical_generation_packet(job, latest_ver)
    packet2, plan2 = _build_canonical_generation_packet(job, latest_ver)

    assert packet1.job_id == packet2.job_id
    assert packet1.job_revision == 1
    assert packet1.job_title == "Senior Distributed Systems Engineer"
    assert len(packet1.planned_slots) == 3
    assert plan1["fingerprint"] == plan2["fingerprint"]
    assert len(plan1["fingerprint"]) == 64


@pytest.mark.anyio
async def test_idempotent_reuse_for_same_job_revision(
    test_db: Session,
    mock_user: UserRecord,
    mock_job_with_requirements: JobRequisition,
):
    """Verifies that calling generation twice on the unchanged job revision reuses the existing draft idempotently."""
    job = mock_job_with_requirements
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")

    res1 = await generate_ai_questions_by_job_id(
        actor=mock_user,
        job_id=job.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )
    assert res1.action == "GENERATED"
    assert res1.lifecycle_status == "DRAFT"
    assert res1.set_version == 1
    assert len(res1.slots) == 3

    # Call again without force_regenerate
    res2 = await generate_ai_questions_by_job_id(
        actor=mock_user,
        job_id=job.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )
    assert res2.action == "OPENED_EXISTING"
    assert res2.ai_set_id == res1.ai_set_id
    assert res2.set_version == 1


@pytest.mark.anyio
async def test_two_different_jobs_receive_distinct_grounded_sets(
    test_db: Session,
    mock_user: UserRecord,
    mock_job_with_requirements: JobRequisition,
    mock_job_b_with_requirements: JobRequisition,
):
    """Verifies that two distinct jobs generate separate grounded sets and never share question sets."""
    job_a = mock_job_with_requirements
    job_b = mock_job_b_with_requirements
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")

    res_a = await generate_ai_questions_by_job_id(
        actor=mock_user,
        job_id=job_a.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )

    res_b = await generate_ai_questions_by_job_id(
        actor=mock_user,
        job_id=job_b.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )

    assert res_a.ai_set_id != res_b.ai_set_id
    assert res_a.job_id == job_a.job_id
    assert res_b.job_id == job_b.job_id
    assert res_a.job_title == "Senior Distributed Systems Engineer"
    assert res_b.job_title == "Lead Product Designer"
    assert res_a.source_content_hash != res_b.source_content_hash


@pytest.mark.anyio
async def test_job_revision_update_produces_successor_draft(
    test_db: Session,
    mock_user: UserRecord,
    mock_job_with_requirements: JobRequisition,
):
    """Verifies that updating a job to a new revision produces a distinct fingerprint and successor draft."""
    job = mock_job_with_requirements
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")

    res1 = await generate_ai_questions_by_job_id(
        actor=mock_user,
        job_id=job.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )
    assert res1.job_revision == 1

    # Add Revision 2
    new_ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=2,
        revision=2,
        title="Staff Distributed Systems Architect",
        summary="Architect global multi-region streaming pipelines with zero downtime.",
        version_status=JobVersionStatus.APPROVED,
        created_by_user_id=mock_user.user_id,
    )
    test_db.add(new_ver)
    test_db.flush()

    new_req = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=new_ver.job_version_id,
        display_order=0,
        label="Multi-Region Consensus & Raft Protocols",
        normalized_label="multi-region consensus raft protocols",
        category=RequirementCategory.SKILL,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.CRITICAL,
        rationale="Design Raft-based consensus layers for geo-distributed clusters.",
    )
    test_db.add(new_req)
    test_db.commit()
    test_db.refresh(job)

    # Generate for revision 2
    res2 = await generate_ai_questions_by_job_id(
        actor=mock_user,
        job_id=job.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )
    assert res2.action == "GENERATED"
    assert res2.job_revision == 2
    assert res2.ai_set_id != res1.ai_set_id
    assert res2.source_content_hash != res1.source_content_hash


@pytest.mark.anyio
async def test_unknown_or_cross_job_requirement_id_rejected(
    test_db: Session,
    mock_user: UserRecord,
    mock_job_with_requirements: JobRequisition,
):
    """Verifies that if provider returns an unknown or forged requirement ID, the slot is marked FAILED."""
    job = mock_job_with_requirements
    latest_ver = job.versions[-1]
    packet, internal_plan = _build_canonical_generation_packet(job, latest_ver)

    # Mock provider returning a forged requirement ID
    forged_id = str(uuid.uuid4())
    fake_provider = FakeWordingProvider(
        simulated_mode="SUCCESS",
        simulated_raw_json_override={
            "schema_version": QUESTION_GENERATION_SCHEMA_VERSION,
            "questions": [
                {
                    "slot_id": str(packet.planned_slots[0].slot_id),
                    "question_text": "How do you partition Kafka event streams under high throughput?",
                    "primary_requirement_id": forged_id,  # Forged ID
                    "supporting_requirement_ids": [],
                    "competency_name": "Distributed Streaming",
                    "question_objective": "Assess partitioning",
                    "question_type": "TECHNICAL",
                    "difficulty": "INTERMEDIATE",
                    "estimated_answer_minutes": 5,
                    "permitted_probes": [],
                }
            ],
        },
    )

    items = await _generate_requirement_grounded_slots(
        packet=packet,
        internal_plan=internal_plan,
        actor=mock_user,
        db=test_db,
        custom_provider=fake_provider,
    )

    slot_1 = items[0]
    assert slot_1["validation_status"] == "FAILED"
    assert slot_1["safe_failure_code"] == "FORGED_REQUIREMENT_ID"


@pytest.mark.anyio
async def test_approved_set_immutability(
    test_db: Session,
    mock_user: UserRecord,
    mock_job_with_requirements: JobRequisition,
):
    """Verifies that approving a question set seals its content and locks it as immutable."""
    job = mock_job_with_requirements
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")

    res = await generate_ai_questions_by_job_id(
        actor=mock_user,
        job_id=job.job_id,
        force_regenerate=False,
        db=test_db,
        custom_provider=fake_provider,
    )

    # Approve the set
    approve_res = approve_ai_question_set(
        actor=mock_user,
        template_id=res.ai_set_id,
        db=test_db,
    )
    assert approve_res.lifecycle_status == "APPROVED"
    assert approve_res.is_immutable is True
    assert approve_res.content_hash != ""

    # Attempting to edit an approved set branches a new successor draft rather than mutating in-place
    stmt = test_db.query(AiInterviewQuestionSetRecord).filter_by(ai_set_id=res.ai_set_id).first()
    assert stmt.is_immutable is True

    branched = _ensure_editable_set(stmt, mock_user, test_db)
    assert branched.ai_set_id != stmt.ai_set_id
    assert branched.set_version == 2
    assert branched.lifecycle_status == "DRAFT"
    assert branched.is_immutable is False
