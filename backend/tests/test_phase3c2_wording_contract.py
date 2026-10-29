import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import hash_password, hash_token
from app.config import Settings
from app.database import get_db
from app.main import app
from app.models.auth import (
    AuditEventType,
    AuditReasonCode,
    AuditResult,
    Base,
    SecurityAuditEventRecord,
    UserRecord,
    UserRole,
    UserSessionRecord,
)
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.gap_report import GapReportItemRecord, GapReportRecord
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
from app.models.matching import MatchResultRecord, MatchRunRecord
from app.models.resume import ResumeArtifact
from app.schemas.requirement_match import MatchResultState
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    PrivacyReviewStatus,
    ProviderCompatibilityStatus,
    ProviderEgressClass,
    ProviderErrorCode,
    ProviderOperation,
    ProviderType,
    QuestionType,
    ValidationState,
)
from app.schemas.provider_profile import ProviderProfile
from app.schemas.safety_policy import (
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_ID,
    SAFETY_POLICY_VERSION,
)
from app.schemas.wording_contract import (
    PROMPT_TEMPLATE_VERSION,
    WORDING_REQUEST_SCHEMA_VERSION,
    WORDING_RESPONSE_SCHEMA_VERSION,
    BoundedWordingRequest,
    BoundedWordingResponse,
    CandidateWordingResult,
    RecruiterWordingAssistRequest,
    StructuredWordingVariant,
    WordingRationaleCode,
)
from app.services.provider_registry import ProviderRegistry, get_provider_registry
from app.services.wording_assistance_service import (
    QuestionNotDraftError,
    QuestionNotFoundError,
    request_question_wording_assistance,
)
from app.services.wording_prompt_package import (
    assemble_wording_messages,
    render_wording_user_prompt,
)
from app.services.wording_provider import (
    DisabledWordingProvider,
    FakeWordingProvider,
    LocalOpenAICompatibleProvider,
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
def test_client(test_db):
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def seeded_env(test_db):
    org_id = uuid.uuid4()
    other_org_id = uuid.uuid4()

    # Users
    recruiter_user = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter@example.com",
        display_name="Recruiter Jane",
        password_hash=hash_password("Password123!"),
        role=UserRole.RECRUITER,
        is_active=True,
    )
    unassigned_recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email="unassigned.recruiter@example.com",
        display_name="Unassigned Recruiter",
        password_hash=hash_password("Password123!"),
        role=UserRole.RECRUITER,
        is_active=True,
    )
    admin_user = UserRecord(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        display_name="Admin Bob",
        password_hash=hash_password("Password123!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    reviewer_user = UserRecord(
        user_id=uuid.uuid4(),
        email="reviewer@example.com",
        display_name="Reviewer Alice",
        password_hash=hash_password("Password123!"),
        role=UserRole.REVIEWER,
        is_active=True,
    )
    test_db.add_all([recruiter_user, unassigned_recruiter, admin_user, reviewer_user])

    # Job
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=recruiter_user.user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)

    job_version = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        title="Senior Database Engineer",
        created_by_user_id=recruiter_user.user_id,
        version_status=JobVersionStatus.APPROVED,
    )
    test_db.add(job_version)

    # Job Requirement
    req = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=job_version.job_version_id,
        category=RequirementCategory.SKILL,
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.CRITICAL,
        label="PostgreSQL Query Optimization",
        normalized_label="postgresql query optimization",
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    test_db.add(req)

    # Template (DRAFT)
    template_draft = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=job_version.job_version_id,
        revision=1,
        template_version=1,
        lifecycle_status=PlanLifecycle.DRAFT,
        guardrail_policy_version=SAFETY_POLICY_VERSION,
        guardrail_policy_hash=SAFETY_POLICY_HASH,
        deterministic_builder_version="1.0.0",
        question_catalogue_version="1.0.0",
        rubric_catalogue_version="1.0.0",
        input_snapshot_hash="a" * 64,
        content_hash="b" * 64,
        created_by_user_id=recruiter_user.user_id,
        record_version=1,
    )
    test_db.add(template_draft)

    # Draft Question
    draft_question = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template_draft.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        display_order=0,
        competency_id="DB-PERF-01",
        requirement_ids=[str(req.requirement_id)],
        question_text="How do you identify and optimize slow queries in PostgreSQL?",
        question_intent="Assess systematic query performance profiling and index design in PostgreSQL.",
        estimated_time_minutes=5,
        included_in_common_score=True,
        validation_state=ValidationState.PASSED,
    )
    test_db.add(draft_question)

    rubric = InterviewRubricRecord(
        rubric_id=uuid.uuid4(),
        question_id=draft_question.question_id,
        rubric_version=1,
        competency_definition="Demonstrated ability to diagnose, profile, and optimize PostgreSQL query plans.",
        question_intent="Assess systematic query performance profiling and index design.",
        observable_indicators=["Mentions EXPLAIN ANALYZE", "Discusses indexing strategies"],
        rating_anchors={1: "Poor anchor description", 3: "Solid anchor description", 5: "Expert anchor description"},
        sufficient_evidence_guidance="Clear explanation of query planning.",
        insufficient_evidence_guidance="Vague answers without practical knowledge.",
        validation_state=ValidationState.PASSED,
    )
    test_db.add(rubric)

    # Template (APPROVED)
    template_approved = JobInterviewTemplateRecord(
        template_id=uuid.uuid4(),
        organization_id=org_id,
        job_id=job.job_id,
        job_version_id=job_version.job_version_id,
        revision=1,
        template_version=2,
        lifecycle_status=PlanLifecycle.APPROVED,
        guardrail_policy_version=SAFETY_POLICY_VERSION,
        guardrail_policy_hash=SAFETY_POLICY_HASH,
        deterministic_builder_version="1.0.0",
        question_catalogue_version="1.0.0",
        rubric_catalogue_version="1.0.0",
        input_snapshot_hash="c" * 64,
        content_hash="d" * 64,
        created_by_user_id=recruiter_user.user_id,
        record_version=1,
    )
    test_db.add(template_approved)

    approved_question = InterviewQuestionRecord(
        question_id=uuid.uuid4(),
        template_id=template_approved.template_id,
        question_version=1,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        authoring_origin=AuthoringOrigin.DETERMINISTIC,
        display_order=0,
        competency_id="DB-PERF-01",
        requirement_ids=[str(req.requirement_id)],
        question_text="How do you optimize slow queries in an approved template?",
        question_intent="Immutable approved question intent.",
        estimated_time_minutes=5,
        included_in_common_score=True,
        validation_state=ValidationState.PASSED,
    )
    test_db.add(approved_question)

    test_db.commit()

    return {
        "org_id": org_id,
        "other_org_id": other_org_id,
        "recruiter": recruiter_user,
        "unassigned_recruiter": unassigned_recruiter,
        "admin": admin_user,
        "reviewer": reviewer_user,
        "job": job,
        "job_version": job_version,
        "requirement": req,
        "template_draft": template_draft,
        "draft_question": draft_question,
        "template_approved": template_approved,
        "approved_question": approved_question,
    }


def create_session(db: Session, user: UserRecord) -> str:
    token = f"test_session_token_{uuid.uuid4()}"
    csrf = f"test_csrf_token_{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    sess = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=user.user_id,
        token_hash=hash_token(token),
        csrf_token_hash=hash_token(csrf),
        idle_expires_at=now + datetime.resolution * 100000000000,
        absolute_expires_at=now + datetime.resolution * 100000000000,
    )
    db.add(sess)
    db.commit()
    return token


# =========================================================================
# 1. Authorization & Role Boundary Tests
# =========================================================================

@pytest.mark.anyio
async def test_only_authorized_recruiter_can_request_wording(seeded_env, test_db):
    env = seeded_env
    reg = ProviderRegistry()
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")

    # Authorized Recruiter: SUCCESS
    result = await request_question_wording_assistance(
        actor=env["recruiter"],
        question_id=env["draft_question"].question_id,
        request_dto=RecruiterWordingAssistRequest(max_variants=1),
        db=test_db,
        provider_registry=reg,
        custom_provider=fake_provider,
    )
    assert isinstance(result, CandidateWordingResult)
    assert len(result.proposed_variants) == 1
    assert result.provider_fallback_used is False


@pytest.mark.anyio
async def test_admin_cannot_use_recruiter_wording_operation(seeded_env, test_db):
    env = seeded_env
    reg = ProviderRegistry()
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")

    # Admin: DENIED
    with pytest.raises(Exception) as exc_info:
        await request_question_wording_assistance(
            actor=env["admin"],
            question_id=env["draft_question"].question_id,
            request_dto=RecruiterWordingAssistRequest(max_variants=1),
            db=test_db,
            provider_registry=reg,
            custom_provider=fake_provider,
        )
    assert "Only authorized Recruiters" in str(exc_info.value)


@pytest.mark.anyio
async def test_unassigned_recruiter_denied(seeded_env, test_db):
    env = seeded_env
    reg = ProviderRegistry()
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")

    # Unassigned Recruiter: DENIED
    with pytest.raises(Exception) as exc_info:
        await request_question_wording_assistance(
            actor=env["unassigned_recruiter"],
            question_id=env["draft_question"].question_id,
            request_dto=RecruiterWordingAssistRequest(max_variants=1),
            db=test_db,
            provider_registry=reg,
            custom_provider=fake_provider,
        )
    assert "Recruiter is not assigned" in str(exc_info.value)


# =========================================================================
# 2. Lifecycle Boundary: Only DRAFT Questions Accepted
# =========================================================================

@pytest.mark.anyio
async def test_approved_question_rejected(seeded_env, test_db):
    env = seeded_env
    reg = ProviderRegistry()
    fake_provider = FakeWordingProvider(simulated_mode="SUCCESS")

    with pytest.raises(QuestionNotDraftError):
        await request_question_wording_assistance(
            actor=env["recruiter"],
            question_id=env["approved_question"].question_id,
            request_dto=RecruiterWordingAssistRequest(max_variants=1),
            db=test_db,
            provider_registry=reg,
            custom_provider=fake_provider,
        )


# =========================================================================
# 3. Contract Invariants & Minimum Allowlisted Data
# =========================================================================

def test_bounded_wording_request_contract_invariants():
    # Verify strict schema validation and forbidden extra fields
    req = BoundedWordingRequest(
        source_question_id=uuid.uuid4(),
        source_question_version=1,
        deterministic_question_text="How do you handle database failover?",
        question_intent="Assess failover and high availability expertise.",
        question_type=QuestionType.TECHNICAL,
        layer=InterviewLayer.COMMON_CORE,
    )
    assert req.request_schema_version == WORDING_REQUEST_SCHEMA_VERSION
    assert req.prompt_template_version == PROMPT_TEMPLATE_VERSION
    assert req.policy_version == SAFETY_POLICY_VERSION
    assert req.require_single_primary_question is True

    # Forbids PII / prohibited keys
    with pytest.raises(ValueError):
        BoundedWordingRequest(
            source_question_id=uuid.uuid4(),
            source_question_version=1,
            deterministic_question_text="How do you handle database failover?",
            question_intent="Assess failover expertise.",
            question_type=QuestionType.TECHNICAL,
            layer=InterviewLayer.COMMON_CORE,
            candidate_name="Jane Doe",  # Prohibited!
        )


def test_bounded_wording_response_schema_validation():
    # Valid response
    valid_resp = BoundedWordingResponse(
        operation="REWORD_QUESTION",
        response_schema_version=WORDING_RESPONSE_SCHEMA_VERSION,
        variants=[
            StructuredWordingVariant(
                variant_text="Could you describe your approach to handling database failover?",
                rationale_code=WordingRationaleCode.PROFESSIONAL_TONE,
            )
        ],
    )
    assert len(valid_resp.variants) == 1

    # Extra fields forbidden
    with pytest.raises(Exception):
        BoundedWordingResponse(
            operation="REWORD_QUESTION",
            response_schema_version=WORDING_RESPONSE_SCHEMA_VERSION,
            variants=[
                StructuredWordingVariant(variant_text="Test question text here?")
            ],
            score_candidate=95,  # Prohibited!
        )

    # Wrong operation forbidden
    with pytest.raises(ValueError):
        BoundedWordingResponse(
            operation="UNAUTHORIZED_OPERATION",
            response_schema_version=WORDING_RESPONSE_SCHEMA_VERSION,
            variants=[
                StructuredWordingVariant(variant_text="Test question text here?")
            ],
        )


# =========================================================================
# 4. Prompt Package & Template Rendering
# =========================================================================

def test_prompt_package_rendering():
    req = BoundedWordingRequest(
        source_question_id=uuid.uuid4(),
        source_question_version=1,
        deterministic_question_text="Explain SQL query indexing.",
        question_intent="Assess indexing knowledge.",
        question_type=QuestionType.TECHNICAL,
        layer=InterviewLayer.COMMON_CORE,
    )
    messages = assemble_wording_messages(req)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "You are a bounded wording assistant" in messages[0]["content"]
    assert "UNTRUSTED DATA" in messages[1]["content"]
    assert "Explain SQL query indexing." in messages[1]["content"]


# =========================================================================
# 5. Provider Failure Posture & Safe Fallback
# =========================================================================

@pytest.mark.anyio
async def test_provider_disabled_fallback_to_deterministic_wording(seeded_env, test_db):
    env = seeded_env
    disabled_provider = DisabledWordingProvider()

    result = await request_question_wording_assistance(
        actor=env["recruiter"],
        question_id=env["draft_question"].question_id,
        request_dto=RecruiterWordingAssistRequest(max_variants=1),
        db=test_db,
        custom_provider=disabled_provider,
    )
    assert result.provider_fallback_used is True
    assert result.provider_error_code == ProviderErrorCode.PROVIDER_DISABLED.value
    assert result.proposed_variants[0].variant_text == env["draft_question"].question_text


@pytest.mark.anyio
async def test_provider_timeout_fallback_to_deterministic_wording(seeded_env, test_db):
    env = seeded_env
    timeout_provider = FakeWordingProvider(simulated_mode="TIMEOUT")

    result = await request_question_wording_assistance(
        actor=env["recruiter"],
        question_id=env["draft_question"].question_id,
        request_dto=RecruiterWordingAssistRequest(max_variants=1),
        db=test_db,
        custom_provider=timeout_provider,
    )
    assert result.provider_fallback_used is True
    assert result.provider_error_code == ProviderErrorCode.PROVIDER_TIMEOUT.value
    assert result.proposed_variants[0].variant_text == env["draft_question"].question_text


# =========================================================================
# 6. End-to-End API Router Endpoint Integration
# =========================================================================

def test_api_endpoint_wording_assistance(seeded_env, test_db, test_client):
    env = seeded_env
    token = create_session(test_db, env["recruiter"])

    # Configure active local profile in global registry
    reg = get_provider_registry()
    profile = ProviderProfile(
        profile_id=uuid.uuid4(),
        organization_id=env["org_id"],
        display_name="Local Fake Adapter",
        provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:1234/v1",
        model_identifier="qwen3-4b",
        enabled=True,
        compatibility_status=ProviderCompatibilityStatus.COMPATIBLE,
    )
    reg._profiles[profile.profile_id] = profile

    # Inject mock FakeWordingProvider into registry instance factory
    fake_prov = FakeWordingProvider(simulated_mode="SUCCESS", reworded_text_override="Can you detail your experience optimizing PostgreSQL queries?")
    reg.get_provider_instance = lambda p: fake_prov

    url = f"/api/v1/jobs/{env['job'].job_id}/interview-templates/{env['template_draft'].template_id}/questions/{env['draft_question'].question_id}/assist-wording"
    
    response = test_client.post(
        url,
        json={"max_variants": 1, "target_tone": "PROFESSIONAL"},
        headers={"Cookie": f"ai_hiring_session={token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["operation"] == "REWORD_QUESTION"
    assert data["request_schema_version"] == "1.0.0"
    assert data["prompt_template_version"] == "1.0.0"
    assert data["provider_fallback_used"] is False
    assert len(data["proposed_variants"]) == 1
    assert data["proposed_variants"][0]["variant_text"] == "Can you detail your experience optimizing PostgreSQL queries?"


def test_api_endpoint_rejects_admin_role(seeded_env, test_db, test_client):
    env = seeded_env
    admin_token = create_session(test_db, env["admin"])

    url = f"/api/v1/jobs/{env['job'].job_id}/interview-templates/{env['template_draft'].template_id}/questions/{env['draft_question'].question_id}/assist-wording"
    response = test_client.post(
        url,
        json={"max_variants": 1},
        headers={"Cookie": f"ai_hiring_session={admin_token}"},
    )
    assert response.status_code == 403


def test_api_endpoint_wording_assistance_after_draft_creation_e2e(seeded_env, test_db, test_client):
    """Verifies that questions returned by POST /draft can immediately be targeted by /assist-wording without 404."""
    env = seeded_env
    token = create_session(test_db, env["recruiter"])

    # Configure fake provider
    reg = get_provider_registry()
    profile = ProviderProfile(
        profile_id=uuid.uuid4(),
        organization_id=env["org_id"],
        display_name="Local Fake Adapter",
        provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:1234/v1",
        model_identifier="qwen3-4b",
        enabled=True,
        compatibility_status=ProviderCompatibilityStatus.COMPATIBLE,
    )
    reg._profiles[profile.profile_id] = profile
    fake_prov = FakeWordingProvider(simulated_mode="SUCCESS", reworded_text_override="Can you detail your experience optimizing PostgreSQL queries?")
    reg.get_provider_instance = lambda p: fake_prov

    # Call POST /draft
    draft_res = test_client.post(
        f"/api/v1/jobs/{env['job'].job_id}/interview-templates/draft",
        headers={"Cookie": f"ai_hiring_session={token}"},
    )
    assert draft_res.status_code == 201
    draft_data = draft_res.json()
    template_id = draft_data["template_id"]
    assert len(draft_data["common_core_questions"]) > 0

    # Call /assist-wording on each question returned by the draft endpoint
    for q in draft_data["common_core_questions"]:
        q_id = q["question_id"]
        assist_url = f"/api/v1/jobs/{env['job'].job_id}/interview-templates/{template_id}/questions/{q_id}/assist-wording"
        wording_res = test_client.post(
            assist_url,
            json={"max_variants": 1, "target_tone": "PROFESSIONAL"},
            headers={"Cookie": f"ai_hiring_session={token}"},
        )
        assert wording_res.status_code == 200, f"Failed for question_id {q_id}: {wording_res.text}"
        wording_data = wording_res.json()
        assert len(wording_data["proposed_variants"]) >= 1

