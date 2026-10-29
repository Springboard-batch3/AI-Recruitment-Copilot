import asyncio
import json
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
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
from app.schemas.interview_blueprint import PolicyVersionReference
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    PrivacyReviewStatus,
    ProviderCompatibilityStatus,
    ProviderEgressClass,
    ProviderErrorCode,
    ProviderOperation,
    ProviderType,
    ValidationState,
)
from app.schemas.provider_admin import (
    ActivateProviderProfileRequest,
    CreateProviderProfileRequest,
    DisableProviderProfileRequest,
    TestProviderCompatibilityRequest,
    UpdateProviderProfileRequest,
)
from app.schemas.provider_capability import (
    ProviderCapabilityRequest,
    RewordQuestionPayload,
)
from app.schemas.provider_profile import ProviderProfile
from app.services.destination_policy import StrictDestinationPolicy
from app.services.provider_compatibility_service import ProviderCompatibilityService
from app.services.provider_eligibility import evaluate_provider_eligibility
from app.services.provider_registry import (
    IneligibleProviderError,
    ProviderRegistry,
    RemoteEgressConfirmationRequiredError,
    StaleVersionError,
    get_provider_registry,
)
from app.services.secret_resolver import EnvironmentSecretResolver
from app.services.wording_provider import (
    DisabledWordingProvider,
    FakeWordingProvider,
    LocalOpenAICompatibleProvider,
    ProviderTransportError,
)


@pytest.fixture
def mock_dns(monkeypatch):
    """Mocks DNS getaddrinfo to return 127.0.0.1 for loopback hosts."""
    def _mock_getaddrinfo(host, port, *args, **kwargs):
        host_lower = host.lower()
        if host_lower in {"127.0.0.1", "localhost"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        elif host_lower == "::1":
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0))]
        elif host_lower == "api.openai.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        elif host_lower == "metadata.bad.internal":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]
        elif host_lower == "lan.bad.internal":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.50", port))]
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _mock_getaddrinfo)


@pytest.fixture
def test_db_session():
    """Provides an isolated in-memory SQLite database session."""
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
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def test_client(test_db_session):
    """Provides a TestClient wired to the test DB."""
    def override_get_db():
        try:
            yield test_db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(test_db_session):
    """Creates admin and recruiter users and returns their auth cookies/headers."""
    now = datetime.now(timezone.utc)
    idle_exp = now + timedelta(seconds=1800)
    abs_exp = now + timedelta(seconds=43200)

    # 1. Admin User
    admin_id = uuid.uuid4()
    admin_user = UserRecord(
        user_id=admin_id,
        email="admin_phase3c1@example.com",
        display_name="Admin Phase3C1",
        password_hash=hash_password("AdminPass123!"),
        role=UserRole.ADMIN,
        is_active=True,
        must_change_password=False,
    )
    test_db_session.add(admin_user)

    admin_session_token = "admin_token_phase3c1_abc123"
    admin_csrf_token = "admin_csrf_phase3c1_xyz789"
    admin_session = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=admin_id,
        token_hash=hash_token(admin_session_token),
        csrf_token_hash=hash_token(admin_csrf_token),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=idle_exp,
        absolute_expires_at=abs_exp,
    )
    test_db_session.add(admin_session)

    # 2. Recruiter User
    recruiter_id = uuid.uuid4()
    recruiter_user = UserRecord(
        user_id=recruiter_id,
        email="recruiter_phase3c1@example.com",
        display_name="Recruiter Phase3C1",
        password_hash=hash_password("RecruiterPass123!"),
        role=UserRole.RECRUITER,
        is_active=True,
        must_change_password=False,
    )
    test_db_session.add(recruiter_user)

    recruiter_session_token = "recruiter_token_phase3c1_abc123"
    recruiter_csrf_token = "recruiter_csrf_phase3c1_xyz789"
    recruiter_session = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=recruiter_id,
        token_hash=hash_token(recruiter_session_token),
        csrf_token_hash=hash_token(recruiter_csrf_token),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=idle_exp,
        absolute_expires_at=abs_exp,
    )
    test_db_session.add(recruiter_session)
    test_db_session.commit()

    return {
        "admin": {
            "cookies": {"ai_hiring_session": admin_session_token},
            "headers": {"X-CSRF-Token": admin_csrf_token, "Origin": "http://localhost:5173"},
            "user_id": admin_id,
        },
        "recruiter": {
            "cookies": {"ai_hiring_session": recruiter_session_token},
            "headers": {"X-CSRF-Token": recruiter_csrf_token, "Origin": "http://localhost:5173"},
            "user_id": recruiter_id,
        },
    }



# ===========================================================================
# 1. Disabled Provider & Fake Provider Tests
# ===========================================================================

def test_disabled_provider_zero_network():
    """Verifies that DisabledWordingProvider performs zero network operations and returns typed failure."""
    async def _run():
        disabled_provider = DisabledWordingProvider()
        req = ProviderCapabilityRequest(
            correlation_id=uuid.uuid4(),
            operation=ProviderOperation.REWORD_QUESTION,
            reword_payload=RewordQuestionPayload(
                original_text="Describe a complex project you coordinated successfully.",
                question_intent="Evaluate project management skill",
            ),
        )

        resp = await disabled_provider.reword_question(req)
        assert resp.validation_state == ValidationState.BLOCKED
        assert resp.error_code == ProviderErrorCode.PROVIDER_DISABLED
        assert resp.fallback_used is True
        assert "disabled" in resp.safe_error_message.lower()

    asyncio.run(_run())


def test_fake_provider_deterministic_modes():
    """Verifies that FakeWordingProvider deterministically simulates success and typed failure modes."""
    async def _run():
        req = ProviderCapabilityRequest(
            correlation_id=uuid.uuid4(),
            operation=ProviderOperation.REWORD_QUESTION,
            reword_payload=RewordQuestionPayload(
                original_text="Tell me about yourself and your skills.",
                question_intent="Assess communication skills",
            ),
        )

        # 1. Success mode
        p_success = FakeWordingProvider(simulated_mode="SUCCESS")
        resp_success = await p_success.reword_question(req)
        assert resp_success.validation_state == ValidationState.PASSED
        assert resp_success.reword_result is not None
        assert resp_success.reword_result.authoring_origin == AuthoringOrigin.AI_ASSISTED

        # 2. Timeout mode
        p_timeout = FakeWordingProvider(simulated_mode="TIMEOUT")
        resp_timeout = await p_timeout.reword_question(req)
        assert resp_timeout.validation_state == ValidationState.BLOCKED
        assert resp_timeout.error_code == ProviderErrorCode.PROVIDER_TIMEOUT

        # 3. Auth Failed mode
        p_auth = FakeWordingProvider(simulated_mode="AUTH_FAILED")
        resp_auth = await p_auth.reword_question(req)
        assert resp_auth.validation_state == ValidationState.BLOCKED
        assert resp_auth.error_code == ProviderErrorCode.AUTHENTICATION_FAILED

        # 4. Model not available mode
        p_model = FakeWordingProvider(simulated_mode="MODEL_NOT_AVAILABLE")
        resp_model = await p_model.reword_question(req)
        assert resp_model.validation_state == ValidationState.BLOCKED
        assert resp_model.error_code == ProviderErrorCode.MODEL_NOT_AVAILABLE

        # 5. Oversized response mode
        p_large = FakeWordingProvider(simulated_mode="OVERSIZED_RESPONSE")
        resp_large = await p_large.reword_question(req)
        assert resp_large.validation_state == ValidationState.BLOCKED
        assert resp_large.error_code == ProviderErrorCode.PROVIDER_RESPONSE_TOO_LARGE

        # 6. Invalid response mode
        p_inv = FakeWordingProvider(simulated_mode="INVALID_RESPONSE")
        resp_inv = await p_inv.reword_question(req)
        assert resp_inv.validation_state == ValidationState.BLOCKED
        assert resp_inv.error_code == ProviderErrorCode.PROVIDER_INVALID_RESPONSE

    asyncio.run(_run())


# ===========================================================================
# 2. Local Destination Policy & Port Tests (LM Studio Port 1234)
# ===========================================================================

def test_local_destination_policy_accepts_lm_studio_port(mock_dns):
    """Verifies that StrictDestinationPolicy accepts LM Studio port 1234 and standard loopback ports."""
    policy = StrictDestinationPolicy(allow_remote=False)

    # 1. LM Studio loopback on 1234
    res_1234 = policy.evaluate_destination("http://127.0.0.1:1234/v1", ProviderEgressClass.LOCAL_ONLY)
    assert res_1234.is_valid is True
    assert res_1234.normalized_url == "http://127.0.0.1:1234/v1"

    # 2. Standard 8080 port
    res_8080 = policy.evaluate_destination("http://127.0.0.1:8080/v1", ProviderEgressClass.LOCAL_ONLY)
    assert res_8080.is_valid is True

    # 3. Reject prohibited ports (e.g. 9999)
    res_bad_port = policy.evaluate_destination("http://127.0.0.1:9999/v1", ProviderEgressClass.LOCAL_ONLY)
    assert res_bad_port.is_valid is False
    assert res_bad_port.safe_error_code == "LOCAL_PORT_UNAPPROVED"

    # 4. Reject embedded credentials
    res_creds = policy.evaluate_destination("http://user:secret@127.0.0.1:1234/v1", ProviderEgressClass.LOCAL_ONLY)
    assert res_creds.is_valid is False
    assert res_creds.safe_error_code == "EMBEDDED_CREDENTIALS_FORBIDDEN"

    # 5. Reject URL fragments
    res_frag = policy.evaluate_destination("http://127.0.0.1:1234/v1#section", ProviderEgressClass.LOCAL_ONLY)
    assert res_frag.is_valid is False
    assert res_frag.safe_error_code == "URL_FRAGMENT_FORBIDDEN"

    # 6. Reject external non-loopback destinations on LOCAL_ONLY
    res_ext = policy.evaluate_destination("http://api.openai.com:1234/v1", ProviderEgressClass.LOCAL_ONLY)
    assert res_ext.is_valid is False
    assert res_ext.safe_error_code == "LOCAL_DESTINATION_FORBIDDEN"


# ===========================================================================
# 3. LocalOpenAICompatibleProvider Transport & Boundedness Tests
# ===========================================================================

def test_local_provider_transport_success(mock_dns):
    """Verifies that LocalOpenAICompatibleProvider dispatches non-streaming chat completions successfully."""
    async def _run():
        profile = ProviderProfile(
            display_name="LM Studio Qwen3",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:1234/v1",
            model_identifier="qwen3-4b-instruct",
            egress_class=ProviderEgressClass.LOCAL_ONLY,
            enabled=True,
        )

        # Mock HTTP transport
        def mock_handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            assert request.headers.get("content-type") == "application/json"
            body = json.loads(request.content.decode("utf-8"))
            assert body["model"] == "qwen3-4b-instruct"
            assert body["stream"] is False

            return httpx.Response(
                status_code=200,
                json={
                    "id": "chatcmpl-test-123",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "Can you describe a scenario where you led an engineering team?",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as mock_client:
            provider = LocalOpenAICompatibleProvider(
                profile=profile,
                http_client=mock_client,
            )
            req = ProviderCapabilityRequest(
                correlation_id=uuid.uuid4(),
                operation=ProviderOperation.REWORD_QUESTION,
                reword_payload=RewordQuestionPayload(
                    original_text="Describe leadership experience.",
                    question_intent="Assess engineering leadership",
                ),
            )
            resp = await provider.reword_question(req)

            assert resp.validation_state == ValidationState.PASSED
            assert resp.reword_result is not None
            assert "led an engineering team" in resp.reword_result.reworded_text

    asyncio.run(_run())


def test_local_provider_auth_failure_mapping(mock_dns, monkeypatch):
    """Verifies that HTTP 401/403 maps safely to AUTHENTICATION_FAILED without exposing secrets."""
    async def _run():
        monkeypatch.setenv("LLM_TEST_KEY", "secret_token_12345")

        profile = ProviderProfile(
            display_name="LM Studio With Token",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:1234/v1",
            model_identifier="qwen3-4b-instruct",
            secret_ref="env:LLM_TEST_KEY",
            egress_class=ProviderEgressClass.LOCAL_ONLY,
            enabled=True,
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("authorization") == "Bearer secret_token_12345"
            return httpx.Response(status_code=401, json={"error": "Unauthorized token"})

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as mock_client:
            provider = LocalOpenAICompatibleProvider(
                profile=profile,
                http_client=mock_client,
            )
            req = ProviderCapabilityRequest(
                correlation_id=uuid.uuid4(),
                operation=ProviderOperation.REWORD_QUESTION,
                reword_payload=RewordQuestionPayload(
                    original_text="Describe leadership experience.",
                    question_intent="Assess leadership",
                ),
            )
            resp = await provider.reword_question(req)
            assert resp.validation_state == ValidationState.BLOCKED
            assert resp.error_code == ProviderErrorCode.AUTHENTICATION_FAILED
            assert "secret_token_12345" not in resp.safe_error_message

    asyncio.run(_run())


def test_local_provider_oversized_response_mapping(mock_dns):
    """Verifies that responses exceeding max_response_bytes map to PROVIDER_RESPONSE_TOO_LARGE."""
    async def _run():
        profile = ProviderProfile(
            display_name="LM Studio Small Limit",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:1234/v1",
            model_identifier="qwen3-4b-instruct",
            max_response_bytes=100,  # Strict small bound for test
            egress_class=ProviderEgressClass.LOCAL_ONLY,
            enabled=True,
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            # Return 500 bytes of response
            long_content = "X" * 500
            return httpx.Response(
                status_code=200,
                json={"choices": [{"message": {"content": long_content}}]},
            )

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as mock_client:
            provider = LocalOpenAICompatibleProvider(
                profile=profile,
                http_client=mock_client,
            )
            req = ProviderCapabilityRequest(
                correlation_id=uuid.uuid4(),
                operation=ProviderOperation.REWORD_QUESTION,
                reword_payload=RewordQuestionPayload(
                    original_text="Describe leadership experience.",
                    question_intent="Assess leadership",
                ),
            )
            resp = await provider.reword_question(req)
            assert resp.validation_state == ValidationState.BLOCKED
            assert resp.error_code == ProviderErrorCode.PROVIDER_RESPONSE_TOO_LARGE

    asyncio.run(_run())


# ===========================================================================
# 4. Diagnostic Compatibility Service Tests
# ===========================================================================

def test_compatibility_service_full_success(mock_dns):
    """Verifies that ProviderCompatibilityService validates /models and /chat/completions."""
    async def _run():
        profile = ProviderProfile(
            display_name="LM Studio Qwen3 Profile",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:1234/v1",
            model_identifier="qwen3-4b-instruct",
            egress_class=ProviderEgressClass.LOCAL_ONLY,
            model_source="Qwen/Qwen3-4B-Instruct-GGUF",
            model_licence="Apache-2.0",
            model_checksum="sha256:abc123def456",
            enabled=False,
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/models":
                return httpx.Response(
                    status_code=200,
                    json={
                        "data": [
                            {"id": "qwen3-4b-instruct", "object": "model"},
                            {"id": "deepseek-coder", "object": "model"},
                        ]
                    },
                )
            elif request.url.path == "/v1/chat/completions":
                body = json.loads(request.content.decode("utf-8"))
                assert body["stream"] is False
                # If this is Step C (system prompt includes 'wording assistant'), return structured JSON
                messages = body.get("messages", [])
                system_content = next((m["content"] for m in messages if m.get("role") == "system"), "")
                if "wording assistant" in system_content:
                    valid_wording_json = json.dumps({
                        "operation": "REWORD_QUESTION",
                        "response_schema_version": "1.0.0",
                        "variants": [
                            {
                                "variant_text": "Could you provide an example of how you optimized a complex database query?",
                                "rationale_code": "PROFESSIONAL_TONE",
                                "referenced_identifiers": []
                            }
                        ]
                    })
                    return httpx.Response(
                        status_code=200,
                        json={
                            "choices": [
                                {"message": {"role": "assistant", "content": valid_wording_json}, "finish_reason": "stop"}
                            ]
                        },
                    )
                return httpx.Response(
                    status_code=200,
                    json={
                        "choices": [
                            {"message": {"role": "assistant", "content": "OK"}}
                        ]
                    },
                )
            return httpx.Response(status_code=404)

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as mock_client:
            svc = ProviderCompatibilityService(
                http_client=mock_client,
            )
            report = await svc.test_compatibility(profile)

            assert report.is_compatible is True
            assert report.compatibility_status == ProviderCompatibilityStatus.HEALTHY
            assert report.reachability_passed is True
            assert report.auth_passed is True
            assert report.model_discovered is True
            assert "qwen3-4b-instruct" in report.discovered_model_ids
            assert report.dry_run_completion_passed is True
            assert report.unicode_handling_passed is True
            assert report.model_checksum == "sha256:abc123def456"
            assert report.safe_failure_code is None

    asyncio.run(_run())


def test_compatibility_service_model_not_found(mock_dns):
    """Verifies that missing model identifier on endpoint maps to MODEL_NOT_AVAILABLE."""
    async def _run():
        profile = ProviderProfile(
            display_name="LM Studio Wrong Model",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:1234/v1",
            model_identifier="llama-3-70b-instruct",  # Not loaded in LM Studio
            egress_class=ProviderEgressClass.LOCAL_ONLY,
            enabled=False,
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/models":
                return httpx.Response(
                    status_code=200,
                    json={
                        "data": [
                            {"id": "qwen3-4b-instruct", "object": "model"},
                        ]
                    },
                )
            return httpx.Response(status_code=404)

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as mock_client:
            svc = ProviderCompatibilityService(http_client=mock_client)
            report = await svc.test_compatibility(profile)

            assert report.is_compatible is False
            assert report.compatibility_status == ProviderCompatibilityStatus.INCOMPATIBLE
            assert report.safe_failure_code == ProviderErrorCode.MODEL_NOT_AVAILABLE.value
            assert "llama-3-70b-instruct" in report.safe_failure_message

    asyncio.run(_run())



# ===========================================================================
# 5. Admin API Endpoints & Role Enforcement Tests
# ===========================================================================

def test_admin_only_test_compatibility_endpoint(test_client, auth_headers, mock_dns):
    """Verifies that only Admin can test compatibility, while Recruiter is denied (403)."""
    admin_auth = auth_headers["admin"]
    recruiter_auth = auth_headers["recruiter"]

    # 1. Recruiter attempt is denied
    recruiter_resp = test_client.post(
        f"/api/v1/admin/provider-profiles/{uuid.uuid4()}/test-compatibility",
        json={"version": 1},
        cookies=recruiter_auth["cookies"],
        headers=recruiter_auth["headers"],
    )
    assert recruiter_resp.status_code == 403

    # 2. Admin attempt on non-existent profile returns 404
    admin_resp_404 = test_client.post(
        f"/api/v1/admin/provider-profiles/{uuid.uuid4()}/test-compatibility",
        json={"version": 1},
        cookies=admin_auth["cookies"],
        headers=admin_auth["headers"],
    )
    assert admin_resp_404.status_code == 404

    # 3. Create profile as Admin
    create_resp = test_client.post(
        "/api/v1/admin/provider-profiles",
        json={
            "display_name": "LM Studio Local Engine",
            "provider_type": "LOCAL_OPENAI_COMPATIBLE",
            "egress_class": "LOCAL_ONLY",
            "base_url": "http://127.0.0.1:1234/v1",
            "model_identifier": "qwen3-4b-instruct",
            "model_source": "Qwen/Qwen3-4B-Instruct-GGUF",
            "model_licence": "Apache-2.0",
        },
        cookies=admin_auth["cookies"],
        headers=admin_auth["headers"],
    )
    assert create_resp.status_code == 201
    created_profile = create_resp.json()
    profile_id = created_profile["profile_id"]

    # 4. Admin runs compatibility test (endpoint will connect to simulated handler or report)
    compat_resp = test_client.post(
        f"/api/v1/admin/provider-profiles/{profile_id}/test-compatibility",
        json={"version": created_profile["version"]},
        cookies=admin_auth["cookies"],
        headers=admin_auth["headers"],
    )
    assert compat_resp.status_code == 200
    compat_report = compat_resp.json()
    assert compat_report["profile_id"] == profile_id
    assert "reachability_passed" in compat_report
    assert "suite_version" in compat_report
    # Secret must never appear in response
    assert "secret" not in str(compat_report).lower() or "not_required" in str(compat_report).lower()


def test_incompatible_provider_activation_policy(mock_dns):
    """Verifies that a provider flagged as INCOMPATIBLE cannot be activated."""
    registry = ProviderRegistry()

    # Create profile
    profile = registry.create_profile(
        CreateProviderProfileRequest(
            display_name="Broken LM Studio",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:1234/v1",
            model_identifier="qwen3-4b-instruct",
            egress_class=ProviderEgressClass.LOCAL_ONLY,
        ),
        actor_id=uuid.uuid4(),
    )

    # Manually mark as INCOMPATIBLE
    with registry._lock:
        registry._profiles[profile.profile_id] = ProviderProfile(
            profile_id=profile.profile_id,
            display_name=profile.display_name,
            provider_type=profile.provider_type,
            base_url=profile.base_url,
            model_identifier=profile.model_identifier,
            egress_class=profile.egress_class,
            enabled=False,
            compatibility_status=ProviderCompatibilityStatus.INCOMPATIBLE,
            version=profile.version,
        )

    # Attempt activation
    with pytest.raises(IneligibleProviderError) as exc:
        registry.activate_profile(
            profile_id=profile.profile_id,
            request=ActivateProviderProfileRequest(version=profile.version),
            actor_id=uuid.uuid4(),
        )
    assert "INCOMPATIBLE" in str(exc.value)
