import ipaddress
import logging
import os
import socket
import uuid
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas.phase3_enums import (
    PrivacyReviewStatus,
    ProviderApiMode,
    ProviderCompatibilityStatus,
    ProviderEgressClass,
    ProviderOperation,
    ProviderType,
)
from app.schemas.provider_profile import (
    ProviderEligibilityResult,
    ProviderProfile,
)
from app.services.destination_policy import StrictDestinationPolicy
from app.services.provider_eligibility import evaluate_provider_eligibility
from app.services.secret_resolver import (
    EnvironmentSecretResolver,
    SecretResolutionError,
)


def test_default_disabled_configuration():
    """Verifies that AI and provider profiles are disabled by default."""
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:pass@localhost:5432/db")
    assert settings.llm_enabled is False
    assert settings.llm_provider == "disabled"
    assert settings.llm_allow_remote is False
    assert settings.llm_max_concurrency == 1
    assert settings.llm_retry_limit == 0

    profile = ProviderProfile(
        display_name="Default Disabled Profile",
        provider_type=ProviderType.DISABLED,
        enabled=False,
    )
    assert profile.enabled is False
    assert profile.provider_type == ProviderType.DISABLED
    assert profile.compatibility_status == ProviderCompatibilityStatus.NOT_TESTED

    policy = StrictDestinationPolicy(allow_remote=False)
    eligibility = evaluate_provider_eligibility(profile, destination_policy=policy)
    assert eligibility.is_eligible is False
    assert "Provider profile is disabled." in eligibility.rejection_reasons
    assert "Provider type is set to DISABLED." in eligibility.rejection_reasons


def test_local_provider_configuration_and_destination_policy(monkeypatch):
    """Verifies local provider profile setup and strict loopback validation."""
    # Mock DNS getaddrinfo to return 127.0.0.1 for localhost
    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host in {"127.0.0.1", "localhost"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        elif host == "::1":
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0))]
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    policy = StrictDestinationPolicy(allow_remote=False, local_allowed_ports={8080})

    # 1. Valid local loopback URL (127.0.0.1:8080)
    res_valid = policy.evaluate_destination("http://127.0.0.1:8080/v1", egress_class=ProviderEgressClass.LOCAL_ONLY)
    assert res_valid.is_valid is True
    assert res_valid.normalized_url == "http://127.0.0.1:8080/v1"
    assert "127.0.0.1" in res_valid.resolved_ips

    # 2. Deny non-approved local port (9999)
    res_invalid_port = policy.evaluate_destination("http://127.0.0.1:9999/v1", egress_class=ProviderEgressClass.LOCAL_ONLY)
    assert res_invalid_port.is_valid is False
    assert res_invalid_port.safe_error_code == "LOCAL_PORT_UNAPPROVED"

    # 3. Deny embedded credentials (http://user:pass@127.0.0.1:8080)
    res_creds = policy.evaluate_destination("http://user:pass@127.0.0.1:8080/v1", egress_class=ProviderEgressClass.LOCAL_ONLY)
    assert res_creds.is_valid is False
    assert res_creds.safe_error_code == "EMBEDDED_CREDENTIALS_FORBIDDEN"

    # 4. Deny external remote host on local profile (http://api.openai.com:8080)
    res_external = policy.evaluate_destination("http://api.openai.com:8080/v1", egress_class=ProviderEgressClass.LOCAL_ONLY)
    assert res_external.is_valid is False
    assert res_external.safe_error_code == "LOCAL_DESTINATION_FORBIDDEN"

    # 5. Local profile eligibility check
    local_profile = ProviderProfile(
        display_name="Local Qwen3",
        provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:8080/v1",
        model_identifier="qwen3-4b-instruct",
        egress_class=ProviderEgressClass.LOCAL_ONLY,
        compatibility_status=ProviderCompatibilityStatus.HEALTHY,
        enabled=True,
    )
    eligibility = evaluate_provider_eligibility(local_profile, destination_policy=policy)
    assert eligibility.is_eligible is True
    assert eligibility.rejection_reasons == []


def test_remote_destination_policy_and_ssrf_blocking(monkeypatch):
    """Verifies remote HTTPS requirement, hostname allowlists, and SSRF/metadata blocking."""
    # Mock DNS resolution for allowed remote host vs private/metadata hosts
    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host == "api.approved-llm.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        elif host == "malicious-internal.approved-llm.com":
            # DNS rebinding returning internal RFC1918 address
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.100", port))]
        elif host == "metadata.approved-llm.com":
            # AWS metadata IP
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    # 1. Remote disabled by default
    policy_disabled = StrictDestinationPolicy(allow_remote=False, remote_allowed_hosts=["api.approved-llm.com"])
    res_disabled = policy_disabled.evaluate_destination("https://api.approved-llm.com/v1", egress_class=ProviderEgressClass.APPROVED_REMOTE)
    assert res_disabled.is_valid is False
    assert res_disabled.safe_error_code == "REMOTE_EGRESS_DISABLED"

    # 2. Remote enabled with allowlist
    policy_enabled = StrictDestinationPolicy(allow_remote=True, remote_allowed_hosts=["api.approved-llm.com", "malicious-internal.approved-llm.com", "metadata.approved-llm.com"])
    
    # Valid remote HTTPS
    res_valid = policy_enabled.evaluate_destination("https://api.approved-llm.com/v1", egress_class=ProviderEgressClass.APPROVED_REMOTE)
    assert res_valid.is_valid is True
    assert res_valid.normalized_url == "https://api.approved-llm.com/v1"
    assert "93.184.216.34" in res_valid.resolved_ips

    # Deny plain HTTP on remote
    res_http = policy_enabled.evaluate_destination("http://api.approved-llm.com/v1", egress_class=ProviderEgressClass.APPROVED_REMOTE)
    assert res_http.is_valid is False
    assert res_http.safe_error_code == "REMOTE_HTTPS_REQUIRED"

    # Deny non-allowlisted host
    res_unlisted = policy_enabled.evaluate_destination("https://unapproved-llm.com/v1", egress_class=ProviderEgressClass.APPROVED_REMOTE)
    assert res_unlisted.is_valid is False
    assert res_unlisted.safe_error_code == "REMOTE_HOST_NOT_ALLOWLISTED"

    # Deny host resolving to private RFC1918 address (SSRF protection)
    res_private = policy_enabled.evaluate_destination("https://malicious-internal.approved-llm.com/v1", egress_class=ProviderEgressClass.APPROVED_REMOTE)
    assert res_private.is_valid is False
    assert res_private.safe_error_code == "SSRF_PROHIBITED_IP"

    # Deny cloud metadata endpoint (169.254.169.254)
    res_metadata = policy_enabled.evaluate_destination("https://metadata.approved-llm.com/v1", egress_class=ProviderEgressClass.APPROVED_REMOTE)
    assert res_metadata.is_valid is False
    assert res_metadata.safe_error_code in {"SSRF_PROHIBITED_IP", "SSRF_METADATA_IP"}


def test_secret_resolver_masking_and_non_serialization(monkeypatch, caplog):
    """Verifies that secrets are resolved strictly via references and never serialized or leaked into logs."""
    fake_secret = "test_fake_api_key_ABC123XYZ"
    monkeypatch.setenv("LLM_TEST_KEY", fake_secret)

    resolver = EnvironmentSecretResolver()

    # 1. Resolve valid secret reference
    secret_str = resolver.resolve_secret_ref("env:LLM_TEST_KEY")
    assert secret_str is not None
    assert secret_str.get_secret_value() == fake_secret
    # String representation must mask the secret
    assert str(secret_str) == "**********"
    assert repr(secret_str) == "SecretStr('**********')"

    # 2. Reject missing secret
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve_secret_ref("env:NON_EXISTENT_KEY")
    assert exc.value.code == "SECRET_NOT_FOUND"

    # 3. Reject invalid format (without env:)
    with pytest.raises(SecretResolutionError) as exc:
        resolver.resolve_secret_ref("raw_password_string")
    assert exc.value.code == "INVALID_SECRET_REF_FORMAT"

    # 4. Profile model serialization must NEVER contain the secret value
    profile = ProviderProfile(
        display_name="Remote Profile",
        provider_type=ProviderType.REMOTE_OPENAI_COMPATIBLE,
        base_url="https://api.approved-llm.com/v1",
        model_identifier="gpt-4o-mini",
        secret_ref="env:LLM_TEST_KEY",
        egress_class=ProviderEgressClass.APPROVED_REMOTE,
        enabled=True,
    )
    dumped = profile.model_dump()
    dumped_json = profile.model_dump_json()
    assert fake_secret not in str(dumped)
    assert fake_secret not in dumped_json
    assert dumped["secret_ref"] == "env:LLM_TEST_KEY"

    # 5. Logging check: prove fake secret value was not logged
    logger = logging.getLogger("test_logger")
    with caplog.at_level(logging.DEBUG):
        logger.info("Created profile for testing: %s", profile.display_name)
    assert fake_secret not in caplog.text


def test_provider_eligibility_vs_health_separation(monkeypatch):
    """Verifies that configuration eligibility remains separated from runtime health status."""
    monkeypatch.setenv("LLM_REMOTE_KEY", "fake_key_999")
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))])

    policy = StrictDestinationPolicy(allow_remote=True, remote_allowed_hosts=["api.approved-llm.com"])

    # Remote profile without privacy approval
    profile = ProviderProfile(
        display_name="Unapproved Remote Profile",
        provider_type=ProviderType.REMOTE_OPENAI_COMPATIBLE,
        base_url="https://api.approved-llm.com/v1",
        model_identifier="remote-model-v1",
        secret_ref="env:LLM_REMOTE_KEY",
        egress_class=ProviderEgressClass.APPROVED_REMOTE,
        privacy_review_status=PrivacyReviewStatus.PENDING_REVIEW,  # Not approved yet
        enabled=True,
        compatibility_status=ProviderCompatibilityStatus.NOT_TESTED,
    )

    eligibility = evaluate_provider_eligibility(profile, destination_policy=policy)
    assert eligibility.is_eligible is False
    assert "Remote provider requires APPROVED privacy_review_status." in eligibility.rejection_reasons
    # Compatibility status remains NOT_TESTED (never marked healthy without real call)
    assert profile.compatibility_status == ProviderCompatibilityStatus.NOT_TESTED


def test_application_self_call_rejection(monkeypatch):
    """Verifies that localhost:8000 / 127.0.0.1:8000 / [::1]:8000 are rejected with clear safe message."""
    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host in {"127.0.0.1", "localhost"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        elif host == "::1":
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0))]
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    policy = StrictDestinationPolicy(allow_remote=False)

    # 1. Port 8000 self call rejection
    res_8000 = policy.evaluate_destination("http://127.0.0.1:8000/v1", egress_class=ProviderEgressClass.LOCAL_ONLY)
    assert res_8000.is_valid is False
    assert res_8000.safe_error_code == "APPLICATION_SELF_CALL_FORBIDDEN"
    assert res_8000.safe_error_message == "This address points to the application server, not an AI provider."

    # 2. Port 8000 localhost
    res_local_8000 = policy.evaluate_destination("http://localhost:8000/v1", egress_class=ProviderEgressClass.LOCAL_ONLY)
    assert res_local_8000.is_valid is False
    assert res_local_8000.safe_error_code == "APPLICATION_SELF_CALL_FORBIDDEN"

    # 3. Port 8000 IPv6 loopback
    res_ipv6_8000 = policy.evaluate_destination("http://[::1]:8000/v1", egress_class=ProviderEgressClass.LOCAL_ONLY)
    assert res_ipv6_8000.is_valid is False
    assert res_ipv6_8000.safe_error_code == "APPLICATION_SELF_CALL_FORBIDDEN"

    # 4. Port 1234 LM Studio approved
    res_1234 = policy.evaluate_destination("http://127.0.0.1:1234/v1", egress_class=ProviderEgressClass.LOCAL_ONLY)
    assert res_1234.is_valid is True
    assert res_1234.normalized_url == "http://127.0.0.1:1234/v1"


def test_activation_blocked_for_incompatible_or_untested():
    """Verifies that evaluate_provider_eligibility blocks activation when compatibility is NOT_TESTED or INCOMPATIBLE."""
    policy = StrictDestinationPolicy(allow_remote=False)

    # 1. NOT_TESTED profile
    untested_profile = ProviderProfile(
        display_name="Untested Local Qwen",
        provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:1234/v1",
        model_identifier="qwen/qwen3-4b-thinking-2507",
        egress_class=ProviderEgressClass.LOCAL_ONLY,
        compatibility_status=ProviderCompatibilityStatus.NOT_TESTED,
        enabled=True,
    )
    elig_untested = evaluate_provider_eligibility(untested_profile, destination_policy=policy)
    assert elig_untested.is_eligible is False
    assert any("NOT_TESTED" in r for r in elig_untested.rejection_reasons)

    # 2. INCOMPATIBLE profile
    incompat_profile = ProviderProfile(
        display_name="Incompatible Local Qwen",
        provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:1234/v1",
        model_identifier="qwen/qwen3-4b-thinking-2507",
        egress_class=ProviderEgressClass.LOCAL_ONLY,
        compatibility_status=ProviderCompatibilityStatus.INCOMPATIBLE,
        enabled=True,
    )
    elig_incompat = evaluate_provider_eligibility(incompat_profile, destination_policy=policy)
    assert elig_incompat.is_eligible is False
    assert any("INCOMPATIBLE" in r for r in elig_incompat.rejection_reasons)

    # 3. HEALTHY profile is eligible
    healthy_profile = ProviderProfile(
        display_name="Healthy Local Qwen",
        provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:1234/v1",
        model_identifier="qwen/qwen3-4b-thinking-2507",
        egress_class=ProviderEgressClass.LOCAL_ONLY,
        compatibility_status=ProviderCompatibilityStatus.HEALTHY,
        enabled=True,
    )
    elig_healthy = evaluate_provider_eligibility(healthy_profile, destination_policy=policy)
    assert elig_healthy.is_eligible is True

