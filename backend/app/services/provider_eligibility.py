from typing import Optional
from app.schemas.phase3_enums import (
    PrivacyReviewStatus,
    ProviderCompatibilityStatus,
    ProviderEgressClass,
    ProviderType,
)
from app.schemas.provider_profile import (
    ProviderEligibilityResult,
    ProviderProfile,
)
from app.services.destination_policy import StrictDestinationPolicy
from app.services.secret_resolver import EnvironmentSecretResolver, SecretResolutionError


def evaluate_provider_eligibility(
    profile: ProviderProfile,
    destination_policy: StrictDestinationPolicy,
    secret_resolver: Optional[EnvironmentSecretResolver] = None,
    ignore_enabled_state: bool = False,
) -> ProviderEligibilityResult:
    """Pure evaluation of provider profile configuration eligibility.
    
    Verifies that a provider profile satisfies all egress, destination, secret availability,
    and privacy constraints.
    
    NOTE: Eligibility indicates valid, authorized configuration. It does NOT mean the provider
    is online or healthy (which requires active runtime health checks in Phase 3B).
    """
    reasons: list[str] = []
    resolver = secret_resolver or EnvironmentSecretResolver()

    # 1. Enabled check
    if not ignore_enabled_state and not profile.enabled:
        reasons.append("Provider profile is disabled.")

    # 2. Disabled provider type
    if profile.provider_type == ProviderType.DISABLED:
        reasons.append("Provider type is set to DISABLED.")

    # 3. Model identifier check
    if not profile.model_identifier or not profile.model_identifier.strip():
        if profile.provider_type != ProviderType.DISABLED:
            reasons.append("Model identifier is missing or blank.")

    # 4. Compatibility state check (must be verified HEALTHY or COMPATIBLE)
    if profile.compatibility_status == ProviderCompatibilityStatus.INCOMPATIBLE:
        reasons.append("Provider is flagged as INCOMPATIBLE.")
    elif profile.compatibility_status == ProviderCompatibilityStatus.NOT_TESTED:
        reasons.append("Provider has NOT_TESTED compatibility status. Test compatibility before activation.")
    elif profile.compatibility_status not in {ProviderCompatibilityStatus.HEALTHY, ProviderCompatibilityStatus.COMPATIBLE}:
        if profile.provider_type != ProviderType.DISABLED:
            reasons.append(f"Provider compatibility status '{profile.compatibility_status.value}' is not eligible for activation.")

    # 5. Privacy review check for remote providers
    if profile.egress_class == ProviderEgressClass.APPROVED_REMOTE:
        if profile.privacy_review_status != PrivacyReviewStatus.APPROVED:
            reasons.append("Remote provider requires APPROVED privacy_review_status.")

    # 6. Destination URL validation
    if profile.provider_type in {ProviderType.LOCAL_OPENAI_COMPATIBLE, ProviderType.REMOTE_OPENAI_COMPATIBLE}:
        if not profile.base_url or not profile.base_url.strip():
            reasons.append("Base URL is required for OpenAI-compatible providers.")
        else:
            dest_result = destination_policy.evaluate_destination(
                target_url=profile.base_url,
                egress_class=profile.egress_class,
            )
            if not dest_result.is_valid:
                reasons.append(f"Destination policy violation: {dest_result.safe_error_code} - {dest_result.safe_error_message}")

    # 7. Secret reference availability check for remote providers
    if profile.provider_type == ProviderType.REMOTE_OPENAI_COMPATIBLE:
        if not profile.secret_ref:
            reasons.append("Remote provider requires an API key secret_ref.")
        else:
            try:
                secret_str = resolver.resolve_secret_ref(profile.secret_ref)
                if not secret_str:
                    reasons.append("Configured secret_ref was empty.")
            except SecretResolutionError as e:
                reasons.append(f"Secret resolution failure: {e.code} - {e.message}")

    is_eligible = len(reasons) == 0

    return ProviderEligibilityResult(
        is_eligible=is_eligible,
        rejection_reasons=reasons,
        egress_class=profile.egress_class,
        provider_type=profile.provider_type,
        display_name=profile.display_name,
    )
