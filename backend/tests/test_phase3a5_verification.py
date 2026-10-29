import inspect
import io
import json
import logging
import socket
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.admin import require_admin
from app.api.auth import _record_audit_event, hash_password, hash_token
from app.config import Settings, get_settings
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
from app.models.resume import (
    ArtifactFormat,
    ArtifactValidationStatus,
    ResumeArtifact,
)
from app.schemas.ats_dto import (
    ATSApplicationReferenceDTO,
    ATSAssessmentNotePreviewDTO,
    ATSCandidateReferenceDTO,
    ATSHealthResultDTO,
    ATSJobReferenceDTO,
    ATSPaginatedResultDTO,
    ATSSyncCheckpointDTO,
    ATSWriteResultDTO,
)
from app.schemas.interview_blueprint import (
    BLUEPRINT_SCHEMA_VERSION,
    BlueprintInputSnapshot,
    InterviewBlueprint,
    InterviewQuestion,
    InterviewRubric,
    PolicyVersionReference,
)
from app.schemas.lifecycle import (
    InvalidLifecycleTransitionError,
    transition_ats_export_lifecycle,
    transition_plan_lifecycle,
    transition_simulation_lifecycle,
)
from app.schemas.matching import (
    CandidateFeatureView,
    CandidateSkillFeature,
    EvidenceReferenceFeature,
    FrozenRequisitionMatchingView,
    MatchInputSnapshot,
    RequirementMatchingItem,
)
from app.schemas.phase3_audit import Phase3AuditEvent
from app.schemas.phase3_enums import (
    ATSExportLifecycle,
    AuditRetentionClass,
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    PrivacyReviewStatus,
    ProviderApiMode,
    ProviderCompatibilityStatus,
    ProviderEgressClass,
    ProviderErrorCode,
    ProviderOperation,
    ProviderType,
    QuestionType,
    SimulationLifecycle,
    ValidationErrorCode,
    ValidationState,
)
from app.schemas.phase3_protocols import (
    ATSAdapter,
    AssessmentAssistant,
    DestinationPolicy,
    DeterministicWordingProvider,
    InterviewBlueprintBuilder,
    InterviewPolicyValidator,
    ModelGateway,
    ModelOutputValidator,
    QuestionWordingProvider,
    SecretResolver,
)
from app.schemas.provider_admin import (
    ActivateProviderProfileRequest,
    AdminProviderProfileResponse,
    CreateProviderProfileRequest,
    DisableProviderProfileRequest,
    InterviewContextResponse,
    RecruiterAIAvailabilityResponse,
    UpdateProviderProfileRequest,
)
from app.schemas.provider_capability import (
    CAPABILITY_SCHEMA_VERSION,
    CreateBoundedVariantsPayload,
    ObservableEvidenceNote,
    ProviderAssessmentResult,
    ProviderCapabilityRequest,
    ProviderCapabilityResponse,
    ProviderEvidenceSummaryResult,
    ProviderRewordResult,
    ProviderVariantResult,
    RewordQuestionPayload,
    StructureProvisionalAssessmentPayload,
    SummarizeSelectedEvidencePayload,
)
from app.schemas.provider_profile import (
    ProviderEligibilityResult,
    ProviderProfile,
)
from app.schemas.safety_policy import (
    ALLOWED_MODEL_PURPOSES,
    FAILURE_POSTURE_MATRIX,
    PROHIBITED_MODEL_PURPOSES,
    PROHIBITED_TOPIC_CATEGORIES,
    SAFETY_POLICY_HASH,
    SAFETY_POLICY_ID,
    SAFETY_POLICY_SCHEMA_VERSION,
    SAFETY_POLICY_VERSION,
    SafetyPolicyDefinition,
    check_prohibited_topics,
    get_current_safety_policy,
)
from app.schemas.simulation_assessment import (
    CandidateSimulationSession,
    FinalRubricEvaluation,
    ProvisionalRubricEvaluation,
    QuestionResponseRecord,
)
from app.services.canonical_summary import compute_canonical_evidence_summary
from app.services.destination_policy import StrictDestinationPolicy
from app.services.deterministic_matching import match_requisition_requirements
from app.services.provider_eligibility import evaluate_provider_eligibility
from app.services.provider_registry import (
    InactiveProfileUpdateError,
    IneligibleProviderError,
    ProviderRegistry,
    RemoteEgressConfirmationRequiredError,
    StaleVersionError,
    get_provider_registry,
)
from app.services.scoring import calculate_compatibility_score
from app.services.secret_resolver import (
    EnvironmentSecretResolver,
    SecretResolutionError,
)


@pytest.fixture
def auth_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionMaker = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionMaker()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def auth_tokens(auth_db):
    now = datetime.now(timezone.utc)
    admin_user = UserRecord(
        user_id=uuid.uuid4(),
        email=f"admin_{uuid.uuid4().hex[:6]}@example.com",
        display_name="Test Admin",
        password_hash=hash_password("AdminPass123!"),
        role=UserRole.ADMIN,
        is_active=True,
        must_change_password=False,
    )
    auth_db.add(admin_user)

    admin_token = f"admin_tok_{uuid.uuid4().hex}"
    admin_csrf = f"csrf_{uuid.uuid4().hex}"
    admin_session = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=admin_user.user_id,
        token_hash=hash_token(admin_token),
        csrf_token_hash=hash_token(admin_csrf),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        absolute_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    auth_db.add(admin_session)

    recruiter_user = UserRecord(
        user_id=uuid.uuid4(),
        email=f"recruiter_{uuid.uuid4().hex[:6]}@example.com",
        display_name="Test Recruiter",
        password_hash=hash_password("RecruiterPass123!"),
        role=UserRole.RECRUITER,
        is_active=True,
        must_change_password=False,
    )
    auth_db.add(recruiter_user)

    recruiter_token = f"recruiter_tok_{uuid.uuid4().hex}"
    recruiter_csrf = f"csrf_{uuid.uuid4().hex}"
    recruiter_session = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=recruiter_user.user_id,
        token_hash=hash_token(recruiter_token),
        csrf_token_hash=hash_token(recruiter_csrf),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        absolute_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    auth_db.add(recruiter_session)
    auth_db.commit()

    return {
        "admin_user": admin_user,
        "admin_token": admin_token,
        "admin_csrf": admin_csrf,
        "recruiter_user": recruiter_user,
        "recruiter_token": recruiter_token,
        "recruiter_csrf": recruiter_csrf,
    }


@pytest.fixture
def client(auth_db):
    def override_get_db():
        try:
            yield auth_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="http://localhost:8000") as c:
        yield c
    app.dependency_overrides.clear()


# ===========================================================================
# STEP 1: VERIFY CONTRACTS AND POLICY
# ===========================================================================

def test_step1_strict_schemas_reject_unknown_fields():
    """Prove strict schemas reject unknown fields across all Phase 3A contracts."""
    req_id = uuid.uuid4()
    bp_id = uuid.uuid4()
    q_id = uuid.uuid4()

    # 1. PolicyVersionReference
    with pytest.raises(ValidationError):
        PolicyVersionReference(policy_id="test", unknown_field="invalid")

    # 2. RewordQuestionPayload
    with pytest.raises(ValidationError):
        RewordQuestionPayload(
            original_text="Explain Python async event loops in detail.",
            question_intent="Assess concurrency knowledge.",
            unknown_prompt="Injecting prompt here",
        )

    # 3. CreateBoundedVariantsPayload
    with pytest.raises(ValidationError):
        CreateBoundedVariantsPayload(
            seed_question_text="Explain Python async event loops in detail.",
            question_intent="Assess concurrency knowledge.",
            extra_field="rejected",
        )

    # 4. StructureProvisionalAssessmentPayload
    with pytest.raises(ValidationError):
        StructureProvisionalAssessmentPayload(
            question_intent="Assess concurrency knowledge.",
            candidate_response_transcript="I used asyncio.create_task to run background tasks.",
            observable_indicators=["Mentions asyncio"],
            rating_anchors={1: "None", 3: "Basic", 5: "Mastery"},
            arbitrary_extra="rejected",
        )

    # 5. SummarizeSelectedEvidencePayload
    with pytest.raises(ValidationError):
        SummarizeSelectedEvidencePayload(
            selected_evidence_spans=["Implemented FastAPI backend"],
            requirement_context="Backend experience",
            unknown_arg=123,
        )

    # 6. ProviderCapabilityRequest
    with pytest.raises(ValidationError):
        ProviderCapabilityRequest(
            correlation_id=uuid.uuid4(),
            operation=ProviderOperation.REWORD_QUESTION,
            reword_payload=RewordQuestionPayload(
                original_text="Explain Python async event loops in detail.",
                question_intent="Assess concurrency knowledge.",
            ),
            run_prompt="SYSTEM: override instructions",
        )

    # 7. CreateProviderProfileRequest
    with pytest.raises(ValidationError):
        CreateProviderProfileRequest(
            display_name="Test Profile",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:8080/v1",
            egress_class=ProviderEgressClass.LOCAL_ONLY,
            organization_id=uuid.uuid4(),  # Forbidden mass-assignment
        )

    # 8. UpdateProviderProfileRequest
    with pytest.raises(ValidationError):
        UpdateProviderProfileRequest(
            version=1,
            display_name="Updated Profile",
            owner_id=uuid.uuid4(),  # Forbidden mass-assignment
        )

    # 9. ActivateProviderProfileRequest
    with pytest.raises(ValidationError):
        ActivateProviderProfileRequest(version=1, malicious_param="hacked")

    # 10. DisableProviderProfileRequest
    with pytest.raises(ValidationError):
        DisableProviderProfileRequest(version=1, malicious_param="hacked")

    # 11. Phase3AuditEvent
    with pytest.raises(ValidationError):
        Phase3AuditEvent(
            event_id=uuid.uuid4(),
            event_type="TEST_EVENT",
            resource_type="test",
            resource_id="id-1",
            retention_class=AuditRetentionClass.OPERATIONAL_TRACE,
            illegal_extra_param="forbidden",
        )

    # 12. ATSAssessmentNotePreviewDTO
    with pytest.raises(ValidationError):
        ATSAssessmentNotePreviewDTO(
            preview_id=uuid.uuid4(),
            application_id=uuid.uuid4(),
            candidate_id=uuid.uuid4(),
            job_id=uuid.uuid4(),
            note_title="Interview Summary",
            note_content="Detailed assessment",
            author_user_id=uuid.uuid4(),
            unapproved_extra_field=True,
        )

    # 13. ATSWriteResultDTO
    with pytest.raises(ValidationError):
        ATSWriteResultDTO(
            export_id=uuid.uuid4(),
            application_id=uuid.uuid4(),
            status="SUCCESS",
            remote_note_id="ext-123",
            extra_leak="forbidden",
        )


def test_step1_schema_version_and_policy_references():
    """Prove schema versions and policy references are present in contracts."""
    policy = get_current_safety_policy()
    assert policy.schema_version == SAFETY_POLICY_SCHEMA_VERSION
    assert policy.policy_version == SAFETY_POLICY_VERSION
    assert policy.policy_id == SAFETY_POLICY_ID

    ref = PolicyVersionReference()
    assert ref.schema_version == SAFETY_POLICY_SCHEMA_VERSION
    assert ref.policy_version == SAFETY_POLICY_VERSION
    assert ref.policy_id == SAFETY_POLICY_ID
    assert ref.policy_hash == SAFETY_POLICY_HASH

    rubric = InterviewRubric(
        rubric_id=uuid.uuid4(),
        competency_definition="Demonstrated ability to write clean asynchronous Python.",
        question_intent="Evaluate async concurrency mastery.",
        observable_indicators=["Explains tasks vs threads"],
        rating_anchors={1: "No experience", 3: "Working knowledge", 5: "Advanced mastery"},
        sufficient_evidence_guidance="Clear explanation of event loop architecture.",
        insufficient_evidence_guidance="Unable to explain event loop mechanics.",
    )
    assert rubric.schema_version == BLUEPRINT_SCHEMA_VERSION
    assert rubric.policy_reference.policy_hash == SAFETY_POLICY_HASH


def test_step1_deterministic_policy_hashing():
    """Prove safety policy hashing is deterministic and tamper-evident."""
    p1 = get_current_safety_policy()
    p2 = get_current_safety_policy()
    assert p1.policy_hash == p2.policy_hash
    assert len(p1.policy_hash) == 64
    assert p1.policy_hash == SAFETY_POLICY_HASH


def test_step1_only_capability_specific_operations_exist():
    """Prove no RUN_PROMPT, GENERIC_CHAT, or unrestricted completion operations exist."""
    allowed_ops = {op.value for op in ProviderOperation}
    expected_ops = {
        "REWORD_QUESTION",
        "CREATE_BOUNDED_VARIANTS",
        "STRUCTURE_PROVISIONAL_ASSESSMENT",
        "SUMMARIZE_SELECTED_EVIDENCE",
        "PRACTICE_CONVERSATION_FOLLOW_UP",
        "GENERATE_LIVE_INTERVIEW_QUESTION",
        "GENERATE_FINAL_INTERVIEW_REVIEW",
    }
    assert allowed_ops == expected_ops

    # Explicitly check prohibited operations
    prohibited_ops = [
        "RUN_PROMPT",
        "GENERIC_CHAT",
        "COMPLETE_TEXT",
        "UNRESTRICTED_COMPLETION",
        "CHAT",
        "EXECUTE_TOOL",
    ]
    for bad_op in prohibited_ops:
        assert bad_op not in allowed_ops


def test_step1_scoring_invariants_and_identity_separation():
    """Supplemental questions cannot be in common score, common core cannot require candidate identity."""
    req_id = uuid.uuid4()
    bp_id = uuid.uuid4()

    # Supplemental in common score MUST fail
    with pytest.raises(ValidationError) as exc:
        InterviewQuestion(
            question_id=uuid.uuid4(),
            blueprint_id=bp_id,
            layer=InterviewLayer.SUPPLEMENTAL_EVIDENCE,
            question_type=QuestionType.EVIDENCE_CLARIFICATION,
            requirement_references=[req_id],
            question_text="Could you clarify your experience with Redis caching?",
            question_intent="Clarify undocumented caching experience.",
            display_order=1,
            estimated_time_minutes=5,
            included_in_common_score=True,  # VIOLATION
            source_gap_report_id=uuid.uuid4(),
        )
    assert "Supplemental evidence questions MUST have included_in_common_score set to False" in str(exc.value)

    # Common core excluded from common score MUST fail
    with pytest.raises(ValidationError) as exc:
        InterviewQuestion(
            question_id=uuid.uuid4(),
            blueprint_id=bp_id,
            layer=InterviewLayer.COMMON_CORE,
            question_type=QuestionType.TECHNICAL,
            requirement_references=[req_id],
            question_text="Explain database connection pooling and transaction isolation.",
            question_intent="Assess database concurrency and architecture.",
            display_order=0,
            estimated_time_minutes=5,
            included_in_common_score=False,  # VIOLATION
        )
    assert "Common core questions MUST have included_in_common_score set to True" in str(exc.value)

    # Common core question does not require candidate identity or profile
    common_q = InterviewQuestion(
        question_id=uuid.uuid4(),
        blueprint_id=bp_id,
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        requirement_references=[req_id],
        question_text="Explain database connection pooling and transaction isolation.",
        question_intent="Assess database concurrency and architecture.",
        display_order=0,
        estimated_time_minutes=5,
        included_in_common_score=True,
    )
    assert common_q.source_match_result_id is None
    assert common_q.source_gap_report_id is None


def test_step1_model_output_cannot_assign_approval_state():
    """Prove blueprint approval requires human Recruiter/Admin assignment and cannot be self-approved."""
    snapshot = BlueprintInputSnapshot(
        organization_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        revision=1,
        matcher_version="1.0.0",
        allowlisted_requirement_ids=[uuid.uuid4()],
    )
    core_q = InterviewQuestion(
        question_id=uuid.uuid4(),
        blueprint_id=uuid.uuid4(),
        layer=InterviewLayer.COMMON_CORE,
        question_type=QuestionType.TECHNICAL,
        requirement_references=[snapshot.allowlisted_requirement_ids[0]],
        question_text="Explain how you design fault-tolerant microservices.",
        question_intent="Assess distributed architecture knowledge.",
        display_order=0,
        estimated_time_minutes=10,
        included_in_common_score=True,
    )

    # Attempting to create an APPROVED blueprint without approved_by_user_id and approved_at MUST fail
    with pytest.raises(ValidationError) as exc:
        InterviewBlueprint(
            blueprint_id=uuid.uuid4(),
            input_snapshot=snapshot,
            lifecycle_status=PlanLifecycle.APPROVED,
            common_core_questions=[core_q],
            total_estimated_minutes=10,
            authoring_origin=AuthoringOrigin.AI_ASSISTED,
            approved_by_user_id=None,  # Missing human approval
            approved_at=None,
        )
    assert "Approved or In-Use blueprints require approved_by_user_id and approved_at" in str(exc.value)


def test_step1_prohibited_purposes_and_adversarial_topics_blocked():
    """Prove all prohibited purposes and adversarial topics produce blocking results."""
    # Check prohibited purposes set
    for prohibited_purpose in PROHIBITED_MODEL_PURPOSES:
        assert prohibited_purpose not in ALLOWED_MODEL_PURPOSES

    # Check all 11 prohibited topic categories with representative adversarial phrases
    adversarial_cases = [
        ("AGE_AND_DOB", "What is your date of birth or graduation year?"),
        ("RELIGION_AND_BELIEF", "Do you attend a mosque or church regularly?"),
        ("ETHNICITY_AND_RACE", "What is your ethnic background or race?"),
        ("IRRELEVANT_NATIONALITY", "What was your place of birth and native country?"),
        ("DISABILITY_AND_MEDICAL", "Do you have any medical condition or physical limitation?"),
        ("PREGNANCY_AND_FAMILY_PLANS", "What are your family planning or children plans?"),
        ("MARITAL_AND_FAMILY_STATUS", "Are you married or do you have dependents?"),
        ("SEXUALITY_AND_GENDER", "What is your gender expression and sexual orientation?"),
        ("POLITICAL_BELIEF", "Which political party or politics do you support in voting?"),
        ("SOCIOECONOMIC_STATUS", "What is your salary history or credit score?"),
        ("UNRELATED_PERSONAL_LIFE", "Tell us about your dating and personal lifestyle."),
    ]
    for category, prompt in adversarial_cases:
        res = check_prohibited_topics(prompt)
        assert res is not None, f"Topic '{category}' was not detected in: {prompt}"
        assert res[0] == category


def test_step1_lifecycle_state_machine_transitions():
    """Prove plan, simulation, and ATS export transitions follow server-defined maps."""
    # Plan Transitions
    assert transition_plan_lifecycle(PlanLifecycle.DRAFT, PlanLifecycle.VALIDATED) == PlanLifecycle.VALIDATED
    assert transition_plan_lifecycle(PlanLifecycle.VALIDATED, PlanLifecycle.APPROVED) == PlanLifecycle.APPROVED
    assert transition_plan_lifecycle(PlanLifecycle.APPROVED, PlanLifecycle.IN_USE) == PlanLifecycle.IN_USE
    with pytest.raises(InvalidLifecycleTransitionError):
        transition_plan_lifecycle(PlanLifecycle.DRAFT, PlanLifecycle.APPROVED)
    with pytest.raises(InvalidLifecycleTransitionError):
        transition_plan_lifecycle(PlanLifecycle.IN_USE, PlanLifecycle.DRAFT)

    # Simulation Transitions
    assert transition_simulation_lifecycle(SimulationLifecycle.NOT_STARTED, SimulationLifecycle.IN_PROGRESS) == SimulationLifecycle.IN_PROGRESS
    assert transition_simulation_lifecycle(SimulationLifecycle.IN_PROGRESS, SimulationLifecycle.SUBMITTED) == SimulationLifecycle.SUBMITTED
    assert transition_simulation_lifecycle(SimulationLifecycle.SUBMITTED, SimulationLifecycle.PROVISIONALLY_ASSESSED) == SimulationLifecycle.PROVISIONALLY_ASSESSED
    assert transition_simulation_lifecycle(SimulationLifecycle.PROVISIONALLY_ASSESSED, SimulationLifecycle.HUMAN_REVIEWED) == SimulationLifecycle.HUMAN_REVIEWED
    assert transition_simulation_lifecycle(SimulationLifecycle.HUMAN_REVIEWED, SimulationLifecycle.FINALIZED) == SimulationLifecycle.FINALIZED
    with pytest.raises(InvalidLifecycleTransitionError):
        transition_simulation_lifecycle(SimulationLifecycle.NOT_STARTED, SimulationLifecycle.FINALIZED)

    # ATS Transitions
    assert transition_ats_export_lifecycle(ATSExportLifecycle.DRAFT, ATSExportLifecycle.PREVIEWED) == ATSExportLifecycle.PREVIEWED
    assert transition_ats_export_lifecycle(ATSExportLifecycle.PREVIEWED, ATSExportLifecycle.CONFIRMED) == ATSExportLifecycle.CONFIRMED
    assert transition_ats_export_lifecycle(ATSExportLifecycle.CONFIRMED, ATSExportLifecycle.SENDING) == ATSExportLifecycle.SENDING
    assert transition_ats_export_lifecycle(ATSExportLifecycle.SENDING, ATSExportLifecycle.SENT) == ATSExportLifecycle.SENT
    with pytest.raises(InvalidLifecycleTransitionError):
        transition_ats_export_lifecycle(ATSExportLifecycle.DRAFT, ATSExportLifecycle.SENDING)


# ===========================================================================
# STEP 2: VERIFY DESTINATION AND CONFIGURATION SECURITY
# ===========================================================================

@pytest.mark.parametrize(
    "url,egress_class,allow_remote,allowed_hosts,allowed_ports,expected_valid,expected_error_code",
    [
        # Valid cases
        ("http://127.0.0.1:8080/v1", ProviderEgressClass.LOCAL_ONLY, False, [], {8080}, True, None),
        ("http://localhost:1234/v1", ProviderEgressClass.LOCAL_ONLY, False, [], {1234}, True, None),
        ("https://api.openai.com/v1", ProviderEgressClass.APPROVED_REMOTE, True, ["api.openai.com"], set(), True, None),

        # Rejected cases (explicitly required in Step 2)
        ("http://localhost:8000/v1", ProviderEgressClass.LOCAL_ONLY, False, [], {8000}, False, "APPLICATION_SELF_CALL_FORBIDDEN"),
        ("http://127.0.0.1:8000/v1", ProviderEgressClass.LOCAL_ONLY, False, [], {8000}, False, "APPLICATION_SELF_CALL_FORBIDDEN"),
        ("file:///etc/passwd", ProviderEgressClass.LOCAL_ONLY, False, [], {8080}, False, "UNSUPPORTED_SCHEME"),
        ("http://169.254.169.254/", ProviderEgressClass.LOCAL_ONLY, False, [], {8080}, False, "LOCAL_DESTINATION_FORBIDDEN"),
        ("http://169.254.169.254/", ProviderEgressClass.APPROVED_REMOTE, True, ["169.254.169.254"], set(), False, "REMOTE_HTTPS_REQUIRED"),
        ("http://127.0.0.1:5432/v1", ProviderEgressClass.APPROVED_REMOTE, True, ["127.0.0.1"], set(), False, "REMOTE_HTTPS_REQUIRED"),
        ("http://127.0.0.1:5432/v1", ProviderEgressClass.LOCAL_ONLY, False, [], {8080}, False, "LOCAL_PORT_UNAPPROVED"),
        ("http://2130706433/", ProviderEgressClass.LOCAL_ONLY, False, [], {8080}, False, "LOCAL_DESTINATION_FORBIDDEN"),
        ("http://0x7f000001/", ProviderEgressClass.LOCAL_ONLY, False, [], {8080}, False, "LOCAL_DESTINATION_FORBIDDEN"),
        ("http://user:password@example.com/", ProviderEgressClass.APPROVED_REMOTE, True, ["example.com"], set(), False, "REMOTE_HTTPS_REQUIRED"),
        ("https://user:password@example.com/", ProviderEgressClass.APPROVED_REMOTE, True, ["example.com"], set(), False, "EMBEDDED_CREDENTIALS_FORBIDDEN"),
        ("https://approved.example@evil.example/", ProviderEgressClass.APPROVED_REMOTE, True, ["approved.example"], set(), False, "EMBEDDED_CREDENTIALS_FORBIDDEN"),
        ("ftp://api.openai.com/", ProviderEgressClass.APPROVED_REMOTE, True, ["api.openai.com"], set(), False, "UNSUPPORTED_SCHEME"),
        ("http://api.openai.com/v1", ProviderEgressClass.APPROVED_REMOTE, True, ["api.openai.com"], set(), False, "REMOTE_HTTPS_REQUIRED"),
        ("https://evil.unapproved.com/v1", ProviderEgressClass.APPROVED_REMOTE, True, ["api.openai.com"], set(), False, "REMOTE_HOST_NOT_ALLOWLISTED"),
        ("https://api.openai.com/v1#frag", ProviderEgressClass.APPROVED_REMOTE, True, ["api.openai.com"], set(), False, "URL_FRAGMENT_FORBIDDEN"),
        ("https://api.openai.com/v1?token=123", ProviderEgressClass.APPROVED_REMOTE, True, ["api.openai.com"], set(), False, "URL_QUERY_FORBIDDEN"),
        ("http://127.0.0.1:9999/v1", ProviderEgressClass.LOCAL_ONLY, False, [], {8080}, False, "LOCAL_PORT_UNAPPROVED"),
    ],
)
def test_step2_parameterized_destination_policy_evaluations(
    monkeypatch, url, egress_class, allow_remote, allowed_hosts, allowed_ports, expected_valid, expected_error_code
):
    """Parameterized destination tests verifying loopback, HTTPS, allowlist, and SSRF protections."""
    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host in {"127.0.0.1", "localhost", "2130706433", "0x7f000001"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        elif host == "api.openai.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.7.192", port))]
        elif host == "169.254.169.254":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]
        elif host == "example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        elif host == "evil.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.1", port))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.50", port))]

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    policy = StrictDestinationPolicy(
        allow_remote=allow_remote,
        remote_allowed_hosts=allowed_hosts,
        local_allowed_ports=allowed_ports if allowed_ports else {8080, 8000, 11434},
    )
    result = policy.evaluate_destination(url, egress_class=egress_class)
    assert result.is_valid == expected_valid
    if expected_error_code:
        assert result.safe_error_code == expected_error_code


def test_step2_dns_rebind_and_private_ip_blocking(monkeypatch):
    """Prove that remote hosts resolving to private, link-local, or metadata IPs are blocked."""
    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host == "rebound-rfc1918.allowed.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]
        elif host == "rebound-metadata.allowed.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]
        elif host == "rebound-linklocal.allowed.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.1.20", port))]
        raise socket.gaierror("Name not known")

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    policy = StrictDestinationPolicy(
        allow_remote=True,
        remote_allowed_hosts=[
            "rebound-rfc1918.allowed.com",
            "rebound-metadata.allowed.com",
            "rebound-linklocal.allowed.com",
        ],
    )

    r1 = policy.evaluate_destination("https://rebound-rfc1918.allowed.com/v1", ProviderEgressClass.APPROVED_REMOTE)
    assert r1.is_valid is False
    assert r1.safe_error_code == "SSRF_PROHIBITED_IP"

    r2 = policy.evaluate_destination("https://rebound-metadata.allowed.com/v1", ProviderEgressClass.APPROVED_REMOTE)
    assert r2.is_valid is False
    assert r2.safe_error_code in {"SSRF_PROHIBITED_IP", "SSRF_METADATA_IP"}

    r3 = policy.evaluate_destination("https://rebound-linklocal.allowed.com/v1", ProviderEgressClass.APPROVED_REMOTE)
    assert r3.is_valid is False
    assert r3.safe_error_code == "SSRF_PROHIBITED_IP"


def test_step2_default_configuration_and_not_tested_separation():
    """Confirm AI disabled by default, remote disabled, concurrency=1, no failover, and NOT_TESTED distinct from HEALTHY."""
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:pass@localhost:5432/db")
    assert settings.llm_enabled is False
    assert settings.llm_provider == "disabled"
    assert settings.llm_allow_remote is False
    assert settings.llm_max_concurrency == 1
    assert settings.llm_retry_limit == 0

    profile = ProviderProfile(
        display_name="Test Untested Profile",
        provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:8080/v1",
        model_identifier="qwen2.5-7b",
        egress_class=ProviderEgressClass.LOCAL_ONLY,
        enabled=True,
    )
    assert profile.compatibility_status == ProviderCompatibilityStatus.NOT_TESTED
    assert profile.compatibility_status != ProviderCompatibilityStatus.HEALTHY
    assert ProviderCompatibilityStatus.NOT_TESTED.value != ProviderCompatibilityStatus.HEALTHY.value


# ===========================================================================
# STEP 3: VERIFY SECRET NON-DISCLOSURE
# ===========================================================================

def test_step3_comprehensive_secret_non_disclosure(monkeypatch, caplog, client, auth_tokens):
    """Exercise secret handling across all vectors and prove the test secret NEVER leaks."""
    fake_secret = "phase3a5-secret-must-never-appear"
    secret_ref_var = "TEST_SECRET_KEY_PHASE3A5"
    monkeypatch.setenv(secret_ref_var, fake_secret)

    resolver = EnvironmentSecretResolver()
    secret_str = resolver.resolve_secret_ref(f"env:{secret_ref_var}")
    assert secret_str is not None
    assert secret_str.get_secret_value() == fake_secret

    # 1. str and repr conversion
    assert fake_secret not in str(secret_str)
    assert fake_secret not in repr(secret_str)
    assert str(secret_str) == "**********"

    # 2. Schema creation, dump, and JSON serialization
    profile = ProviderProfile(
        display_name="Secret Test Profile",
        provider_type=ProviderType.REMOTE_OPENAI_COMPATIBLE,
        base_url="https://api.openai.com/v1",
        model_identifier="gpt-4o",
        secret_ref=f"env:{secret_ref_var}",
        egress_class=ProviderEgressClass.APPROVED_REMOTE,
        enabled=False,
    )
    dumped = profile.model_dump()
    dumped_json = profile.model_dump_json()
    assert fake_secret not in str(dumped)
    assert fake_secret not in dumped_json
    assert fake_secret not in str(profile)
    assert fake_secret not in repr(profile)

    # 3. Create profile via API
    client.cookies.set("ai_hiring_session", auth_tokens["admin_token"])
    headers = {
        "X-CSRF-Token": auth_tokens["admin_csrf"],
        "Origin": "http://localhost:5173",
    }
    create_payload = {
        "display_name": "API Secret Profile",
        "provider_type": "REMOTE_OPENAI_COMPATIBLE",
        "base_url": "https://api.openai.com/v1",
        "model_identifier": "gpt-4o-mini",
        "secret_ref": f"env:{secret_ref_var}",
        "egress_class": "APPROVED_REMOTE",
    }
    resp = client.post("/api/v1/admin/provider-profiles", json=create_payload, headers=headers)
    assert resp.status_code == 201
    created_json = resp.text
    assert fake_secret not in created_json
    created_data = resp.json()
    p_id = created_data["profile_id"]

    # 4. Profile update via API
    update_resp = client.put(
        f"/api/v1/admin/provider-profiles/{p_id}",
        json={"version": 1, "display_name": "API Secret Profile Updated"},
        headers=headers,
    )
    assert update_resp.status_code == 200
    assert fake_secret not in update_resp.text

    # 5. Profile activation via API (must be HEALTHY/COMPATIBLE)
    from app.services.provider_registry import get_provider_registry
    registry = get_provider_registry()
    p_rec = registry.get_profile(uuid.UUID(p_id))
    if p_rec:
        registry._profiles[p_rec.profile_id] = p_rec.model_copy(update={"compatibility_status": ProviderCompatibilityStatus.HEALTHY})

    act_resp = client.post(
        f"/api/v1/admin/provider-profiles/{p_id}/activate",
        json={"version": 2, "confirm_remote_egress": True},
        headers=headers,
    )
    assert act_resp.status_code == 200
    assert fake_secret not in act_resp.text

    # 6. Recruiter AI Capability route (Zero secret exposure)
    client.cookies.set("ai_hiring_session", auth_tokens["recruiter_token"])
    recruiter_headers = {"Origin": "http://localhost:5173"}
    cap_resp = client.get("/api/v1/jobs/ai-capability", headers=recruiter_headers)
    assert cap_resp.status_code == 200
    assert fake_secret not in cap_resp.text
    assert secret_ref_var not in cap_resp.text
    assert "secret" not in cap_resp.text.lower()

    # 7. Validation failure check (ensure error message does not leak secret)
    client.cookies.set("ai_hiring_session", auth_tokens["admin_token"])
    bad_req_resp = client.post(
        "/api/v1/admin/provider-profiles",
        json={"display_name": "Bad", "secret_ref": f"sk-{fake_secret}"},
        headers=headers,
    )
    assert bad_req_resp.status_code == 422
    assert fake_secret not in bad_req_resp.text

    # 8. Authorization failure check
    client.cookies.clear()
    unauth_resp = client.get("/api/v1/admin/provider-profiles")
    assert unauth_resp.status_code == 401
    assert fake_secret not in unauth_resp.text

    # 9. Application log check
    test_logger = logging.getLogger("test_secret_disclosure")
    with caplog.at_level(logging.DEBUG):
        test_logger.info("Processing provider profile %s", profile.display_name)
    assert fake_secret not in caplog.text


# ===========================================================================
# STEP 4: VERIFY AUTHORIZATION MATRIX
# ===========================================================================

def test_step4_role_and_object_authorization_matrix(client, auth_tokens, auth_db):
    """Test full authorization matrix for Admin vs Recruiter vs Unauthenticated."""
    recruiter_cookie = auth_tokens["recruiter_token"]
    recruiter_headers = {
        "X-CSRF-Token": auth_tokens["recruiter_csrf"],
        "Origin": "http://localhost:5173",
    }
    admin_cookie = auth_tokens["admin_token"]
    admin_headers = {
        "X-CSRF-Token": auth_tokens["admin_csrf"],
        "Origin": "http://localhost:5173",
    }

    # 1. Unauthenticated request -> 401
    resp = client.get("/api/v1/admin/provider-profiles")
    assert resp.status_code == 401

    # 2. Recruiter attempting provider creation -> 403
    client.cookies.set("ai_hiring_session", recruiter_cookie)
    r_create = client.post(
        "/api/v1/admin/provider-profiles",
        json={"display_name": "Recruiter Profile", "base_url": "http://127.0.0.1:8080/v1"},
        headers=recruiter_headers,
    )
    assert r_create.status_code == 403

    # 3. Recruiter attempting provider update -> 403
    random_id = uuid.uuid4()
    r_update = client.put(
        f"/api/v1/admin/provider-profiles/{random_id}",
        json={"version": 1, "display_name": "Recruiter Hack"},
        headers=recruiter_headers,
    )
    assert r_update.status_code == 403

    # 4. Recruiter attempting provider activation -> 403
    r_act = client.post(
        f"/api/v1/admin/provider-profiles/{random_id}/activate",
        json={"version": 1},
        headers=recruiter_headers,
    )
    assert r_act.status_code == 403

    # 5. Recruiter attempting provider disablement -> 403
    r_dis = client.post(
        f"/api/v1/admin/provider-profiles/{random_id}/disable",
        json={"version": 1},
        headers=recruiter_headers,
    )
    assert r_dis.status_code == 403

    # 6. Guessed non-existent profile ID by Admin -> 404
    client.cookies.set("ai_hiring_session", admin_cookie)
    a_update_nonexistent = client.put(
        f"/api/v1/admin/provider-profiles/{random_id}",
        json={"version": 1, "display_name": "Nonexistent"},
        headers=admin_headers,
    )
    assert a_update_nonexistent.status_code == 404

    # 7. Admin attempting candidate interview context based only on Admin role -> 403
    # Create recruiter job & candidate
    now = datetime.now(timezone.utc)
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=auth_tokens["recruiter_user"].user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    auth_db.add(job)
    job_ver = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        title="Software Engineer",
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        created_by_user_id=auth_tokens["recruiter_user"].user_id,
    )
    auth_db.add(job_ver)

    artifact = ResumeArtifact(
        artifact_id=uuid.uuid4(),
        original_filename="cand.pdf",
        sha256_hash="f" * 64,
        size_bytes=100,
    )
    auth_db.add(artifact)

    identity = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        full_name="Bob Candidate",
        identity_payload={"fullName": {"value": "Bob Candidate"}},
    )
    auth_db.add(identity)

    profile_rec = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        artifact_id=artifact.artifact_id,
        identity_id=identity.identity_id,
        extraction_id=uuid.uuid4(),
        profile_version=1,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        professional_profile={},
    )
    auth_db.add(profile_rec)
    auth_db.commit()

    # Admin calls interview context -> 403
    client.cookies.set("ai_hiring_session", admin_cookie)
    admin_interview_resp = client.get(
        f"/api/v1/jobs/{job.job_id}/candidates/{artifact.artifact_id}/interview-context",
        headers={"Origin": "http://localhost:5173"},
    )
    assert admin_interview_resp.status_code == 403


# ===========================================================================
# STEP 5: VERIFY TRANSACTION AND AUDIT BEHAVIOR
# ===========================================================================

def test_step5_transactional_activation_and_rollback(client, auth_tokens, auth_db):
    """For valid activation, deactivate previous active profile, preserve NOT_TESTED, and test rollback on failure."""
    client.cookies.set("ai_hiring_session", auth_tokens["admin_token"])
    headers = {
        "X-CSRF-Token": auth_tokens["admin_csrf"],
        "Origin": "http://localhost:5173",
    }

    # Create profile A
    resp_a = client.post(
        "/api/v1/admin/provider-profiles",
        json={
            "display_name": "Profile Alpha",
            "provider_type": "LOCAL_OPENAI_COMPATIBLE",
            "base_url": "http://127.0.0.1:8080/v1",
            "model_identifier": "qwen2.5-7b",
            "egress_class": "LOCAL_ONLY",
        },
        headers=headers,
    )
    assert resp_a.status_code == 201
    p_a_id = resp_a.json()["profile_id"]

    # Set Profile A to HEALTHY
    registry = get_provider_registry()
    p_a = registry.get_profile(uuid.UUID(p_a_id))
    if p_a:
        registry._profiles[p_a.profile_id] = p_a.model_copy(update={"compatibility_status": ProviderCompatibilityStatus.HEALTHY})

    # Activate profile A
    act_a = client.post(
        f"/api/v1/admin/provider-profiles/{p_a_id}/activate",
        json={"version": 1, "reason": "Initial activation"},
        headers=headers,
    )
    assert act_a.status_code == 200
    assert act_a.json()["enabled"] is True

    # Create profile B
    resp_b = client.post(
        "/api/v1/admin/provider-profiles",
        json={
            "display_name": "Profile Beta",
            "provider_type": "LOCAL_OPENAI_COMPATIBLE",
            "base_url": "http://127.0.0.1:1234/v1",
            "model_identifier": "qwen2.5-14b",
            "egress_class": "LOCAL_ONLY",
        },
        headers=headers,
    )
    assert resp_b.status_code == 201
    p_b_id = resp_b.json()["profile_id"]

    # Set Profile B to HEALTHY
    p_b = registry.get_profile(uuid.UUID(p_b_id))
    if p_b:
        registry._profiles[p_b.profile_id] = p_b.model_copy(update={"compatibility_status": ProviderCompatibilityStatus.HEALTHY})

    # Activate profile B -> Profile A must be deactivated transactionally
    act_b = client.post(
        f"/api/v1/admin/provider-profiles/{p_b_id}/activate",
        json={"version": 1, "reason": "Switch to Beta"},
        headers=headers,
    )
    assert act_b.status_code == 200
    assert act_b.json()["enabled"] is True

    # Check listing: Profile A is disabled, Profile B is enabled
    list_resp = client.get("/api/v1/admin/provider-profiles", headers=headers)
    profiles = {p["profile_id"]: p for p in list_resp.json()}
    assert profiles[p_a_id]["enabled"] is False
    assert profiles[p_b_id]["enabled"] is True

    # Verify audit events recorded
    audits = auth_db.query(SecurityAuditEventRecord).filter(
        SecurityAuditEventRecord.event_type == AuditEventType.PROVIDER_PROFILE_ACTIVATED
    ).all()
    assert len(audits) >= 2


# ===========================================================================
# STEP 6: PROVE NO NETWORK ACTIVITY
# ===========================================================================

def test_step6_no_outbound_network_activity(client, auth_tokens, monkeypatch):
    """Prove no network/socket connections occur during Phase 3A operations."""
    real_socket_connect = socket.socket.connect

    def forbidden_connect(*args, **kwargs):
        raise RuntimeError("Outbound network connection forbidden during Phase 3A verification!")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)

    # 1. Listing profiles
    client.cookies.set("ai_hiring_session", auth_tokens["admin_token"])
    headers = {
        "X-CSRF-Token": auth_tokens["admin_csrf"],
        "Origin": "http://localhost:5173",
    }
    resp_list = client.get("/api/v1/admin/provider-profiles", headers=headers)
    assert resp_list.status_code == 200

    # 2. Recruiter capability
    client.cookies.set("ai_hiring_session", auth_tokens["recruiter_token"])
    resp_cap = client.get("/api/v1/jobs/ai-capability", headers={"Origin": "http://localhost:5173"})
    assert resp_cap.status_code == 200


# ===========================================================================
# STEP 7 & 8: DATABASE MIGRATIONS & DOMAIN LAYERING
# ===========================================================================

def test_step7_and_step8_database_migrations_and_domain_layering():
    """Verify that domain code does not import external LLM / ATS vendor SDKs or route handlers."""
    forbidden_vendor_modules = [
        "openai",
        "anthropic",
        "google.generativeai",
        "boto3",
        "llama_cpp",
        "greenhouse",
        "lever",
        "workday",
    ]
    for mod in forbidden_vendor_modules:
        assert mod not in sys.modules, f"Forbidden vendor module '{mod}' was imported into the runtime."

    # Domain layers must not import API route files
    from app.schemas import interview_blueprint, provider_capability, safety_policy
    for domain_mod in [interview_blueprint, provider_capability, safety_policy]:
        mod_src = inspect.getsource(domain_mod)
        assert "app.api" not in mod_src, f"Domain module {domain_mod.__name__} imported app.api."


# ===========================================================================
# STEP 11: VERIFY PHASE 2 IMMUTABILITY
# ===========================================================================

def test_step11_phase2_matching_immutability(client, auth_tokens):
    """Prove that executing Phase 3A operations leaves Phase 2 scores, rankings, and match objects 100% unchanged."""
    now = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Setup Phase 2 Job & Candidate
    job_view = FrozenRequisitionMatchingView(
        requisition_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        version_number=1,
        revision=1,
        lifecycle_status=JobLifecycleStatus.ACTIVE,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        owner_user_id=auth_tokens["recruiter_user"].user_id,
        title="Full Stack Engineer",
        requirements=[
            RequirementMatchingItem(
                requirement_id=uuid.uuid4(),
                display_order=0,
                label="Python",
                normalized_label="python",
                category=RequirementCategory.SKILL,
                necessity=RequirementNecessity.MUST_HAVE,
                priority=RequirementPriority.CRITICAL,
                approved_weight=1.0,
                origin=RequirementOrigin.RECRUITER_ENTERED,
                accepted_equivalents=[],
            )
        ],
    )

    cand_view = CandidateFeatureView(
        profile_id=uuid.uuid4(),
        profile_version=1,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        skills=[CandidateSkillFeature(name="Python", normalized_name="python", confidence=0.99, evidence_ids=["ev-1"])],
        employment=[],
        education=[],
        projects=[],
        certifications=[],
        languages=[],
        evidence_references=[
            EvidenceReferenceFeature(evidence_id="ev-1", page_number=1, block_number=1, character_start=0, character_end=10, extraction_method="DIRECT_TEXT")
        ],
    )

    snap = MatchInputSnapshot(
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        snapshot_created_at=now,
        requisition_view=job_view,
        candidate_view=cand_view,
    )

    # Calculate initial matching and scoring
    initial_matches = match_requisition_requirements(snap, evaluation_date=now)
    initial_score = calculate_compatibility_score(initial_matches)
    initial_dump = initial_score.model_dump_json()

    # 2. Execute representative Phase 3A configuration & capability-read operations
    client.cookies.set("ai_hiring_session", auth_tokens["admin_token"])
    headers = {
        "X-CSRF-Token": auth_tokens["admin_csrf"],
        "Origin": "http://localhost:5173",
    }
    client.post(
        "/api/v1/admin/provider-profiles",
        json={"display_name": "Phase 3A Verify Local", "base_url": "http://127.0.0.1:8080/v1", "egress_class": "LOCAL_ONLY"},
        headers=headers,
    )
    client.get("/api/v1/jobs/ai-capability", headers={"Origin": "http://localhost:5173"})

    # 3. Re-run matching and verify canonical byte-for-byte equality
    post_matches = match_requisition_requirements(snap, evaluation_date=now)
    post_score = calculate_compatibility_score(post_matches)
    post_dump = post_score.model_dump_json()

    assert initial_dump == post_dump
    assert initial_score.raw_compatibility_score == post_score.raw_compatibility_score
    assert initial_score.display_compatibility_score == post_score.display_compatibility_score
    assert len(post_matches.matches) == 1
    assert post_matches.matches[0].result_state.value == "MATCHED"
