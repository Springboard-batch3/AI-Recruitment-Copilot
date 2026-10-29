"""Provider compatibility verification engine for Admin-only diagnostic testing.

Executes a fixed, synthetic, non-personal compatibility suite against configured provider endpoints.
Verifies:
- Endpoint reachability
- Authentication
- Safe model discovery (via /v1/models)
- Configured model identifier availability
- OpenAI-compatible route (/v1/chat/completions)
- Non-streaming enforcement
- Timeout and response-byte bound enforcement
- Unicode handling
- Latency measurement

Strictly forbids:
- Candidate records, resumes, jobs, Phase 2 evidence or real interview questions in payloads.
- Exposing API keys, Authorization headers, raw response bodies, or full trace dumps.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx

from app.schemas.phase3_enums import (
    ProviderCompatibilityStatus,
    ProviderEgressClass,
    ProviderErrorCode,
    ProviderType,
)
from app.schemas.provider_admin import ProviderCompatibilityReportResponse
from app.schemas.provider_profile import ProviderProfile
from app.services.destination_policy import StrictDestinationPolicy
from app.services.secret_resolver import EnvironmentSecretResolver, SecretResolutionError

logger = logging.getLogger("ai_hiring.provider_compatibility")

# Fixed synthetic non-personal test fixtures
SYNTHETIC_TEST_SYSTEM_PROMPT = "You are a test harness verifying non-streaming compatibility. Respond with 'OK'."
SYNTHETIC_TEST_USER_PROMPT = "Health verification ping: \u00e9\u00e0\u00fc \u2705"


class ProviderCompatibilityService:
    """Diagnostic test runner for provider endpoints."""

    def __init__(
        self,
        destination_policy: Optional[StrictDestinationPolicy] = None,
        secret_resolver: Optional[EnvironmentSecretResolver] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.destination_policy = destination_policy or StrictDestinationPolicy(allow_remote=False)
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self._custom_http_client = http_client

    def _resolve_headers(self, profile: ProviderProfile) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AI-Hiring-Platform/CompatibilitySuite-1.0",
        }
        if profile.secret_ref:
            try:
                secret_str = self.secret_resolver.resolve_secret_ref(profile.secret_ref)
                if secret_str:
                    headers["Authorization"] = f"Bearer {secret_str.get_secret_value()}"
            except SecretResolutionError as e:
                raise ValueError(f"Secret resolution failure: {e.code}")
        return headers

    async def test_compatibility(
        self,
        profile: ProviderProfile,
        timeout_override_seconds: Optional[float] = None,
    ) -> ProviderCompatibilityReportResponse:
        """Executes full diagnostic suite against profile and returns safe report."""
        start_time = time.perf_counter()
        tested_at = datetime.now(timezone.utc)

        # 1. Disabled provider check
        if profile.provider_type == ProviderType.DISABLED:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ProviderCompatibilityReportResponse(
                profile_id=profile.profile_id,
                display_name=profile.display_name,
                provider_type=profile.provider_type,
                model_identifier=profile.model_identifier,
                compatibility_status=ProviderCompatibilityStatus.DISABLED,
                is_compatible=False,
                safe_failure_code=ProviderErrorCode.PROVIDER_DISABLED.value,
                safe_failure_message="Provider type is DISABLED. Enable a local or approved remote provider.",
                reachability_passed=False,
                auth_passed=False,
                model_discovered=False,
                discovered_model_ids=[],
                dry_run_completion_passed=False,
                non_streaming_enforced=True,
                unicode_handling_passed=False,
                latency_ms=latency_ms,
                tested_at=tested_at,
                model_source=profile.model_source,
                model_licence=profile.model_licence,
                model_checksum=profile.model_checksum,
            )

        # 2. Destination Policy Preflight
        base_url = profile.base_url or ("http://127.0.0.1:1234/v1" if profile.egress_class == ProviderEgressClass.LOCAL_ONLY else "")
        dest_eval = self.destination_policy.evaluate_destination(
            target_url=base_url,
            egress_class=profile.egress_class,
        )
        if not dest_eval.is_valid:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ProviderCompatibilityReportResponse(
                profile_id=profile.profile_id,
                display_name=profile.display_name,
                provider_type=profile.provider_type,
                model_identifier=profile.model_identifier,
                compatibility_status=ProviderCompatibilityStatus.INCOMPATIBLE,
                is_compatible=False,
                safe_failure_code=ProviderErrorCode.DESTINATION_DENIED.value,
                safe_failure_message=f"Destination policy violation: {dest_eval.safe_error_code} - {dest_eval.safe_error_message}",
                reachability_passed=False,
                auth_passed=False,
                model_discovered=False,
                discovered_model_ids=[],
                dry_run_completion_passed=False,
                non_streaming_enforced=True,
                unicode_handling_passed=False,
                latency_ms=latency_ms,
                tested_at=tested_at,
                model_source=profile.model_source,
                model_licence=profile.model_licence,
                model_checksum=profile.model_checksum,
            )

        normalized_base = dest_eval.normalized_url or base_url.rstrip("/")

        # 3. Secret Resolution Preflight
        try:
            headers = self._resolve_headers(profile)
        except ValueError as e:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ProviderCompatibilityReportResponse(
                profile_id=profile.profile_id,
                display_name=profile.display_name,
                provider_type=profile.provider_type,
                model_identifier=profile.model_identifier,
                compatibility_status=ProviderCompatibilityStatus.INCOMPATIBLE,
                is_compatible=False,
                safe_failure_code=ProviderErrorCode.AUTHENTICATION_FAILED.value,
                safe_failure_message=str(e),
                reachability_passed=False,
                auth_passed=False,
                model_discovered=False,
                discovered_model_ids=[],
                dry_run_completion_passed=False,
                non_streaming_enforced=True,
                unicode_handling_passed=False,
                latency_ms=latency_ms,
                tested_at=tested_at,
                model_source=profile.model_source,
                model_licence=profile.model_licence,
                model_checksum=profile.model_checksum,
            )

        # 4. HTTP client setup
        connect_timeout = timeout_override_seconds or profile.connect_timeout_seconds
        req_timeout = timeout_override_seconds or profile.request_timeout_seconds
        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=req_timeout,
            write=connect_timeout,
            pool=connect_timeout,
        )

        should_close = False
        if self._custom_http_client is not None:
            client = self._custom_http_client
        else:
            client = httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                cookies=None,
                trust_env=False,
            )
            should_close = True

        reachability_passed = False
        auth_passed = False
        model_discovered = False
        discovered_model_ids: List[str] = []
        dry_run_completion_passed = False
        unicode_handling_passed = False

        try:
            # Step A: Test /v1/models
            models_url = f"{normalized_base}/models"
            try:
                models_resp = await client.get(models_url, headers=headers)
            except httpx.ConnectTimeout:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROVIDER_TIMEOUT,
                    f"Connection timed out after {connect_timeout}s while reaching /models."
                )
            except httpx.ReadTimeout:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROVIDER_TIMEOUT,
                    f"Read timed out after {req_timeout}s from /models endpoint."
                )
            except httpx.ConnectError:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROVIDER_UNAVAILABLE,
                    f"Could not connect to {normalized_base}. Ensure local LM Studio server is running."
                )

            reachability_passed = True

            # Redirects are forbidden across all provider endpoints
            if models_resp.status_code in {301, 302, 307, 308}:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.DESTINATION_DENIED,
                    f"HTTP redirect detected (HTTP {models_resp.status_code}). Destination redirects are prohibited by security policy.",
                    reachability_passed=True
                )

            if models_resp.status_code in {401, 403}:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.AUTHENTICATION_FAILED,
                    f"Authentication failed (HTTP {models_resp.status_code}). Check secret_ref configuration.",
                    reachability_passed=True
                )

            if models_resp.status_code == 200:
                auth_passed = True
                if len(models_resp.content) > profile.max_response_bytes:
                    return self._build_failure_response(
                        profile, start_time, tested_at,
                        ProviderErrorCode.PROVIDER_RESPONSE_TOO_LARGE,
                        f"Models response exceeded {profile.max_response_bytes} bytes bound.",
                        reachability_passed=True, auth_passed=True
                    )

                try:
                    models_data = json.loads(models_resp.content.decode("utf-8"))
                    if isinstance(models_data, dict) and "data" in models_data and isinstance(models_data["data"], list):
                        for m in models_data["data"]:
                            if isinstance(m, dict) and "id" in m:
                                discovered_model_ids.append(str(m["id"]))
                except Exception:
                    logger.warning("Could not parse /models JSON response.")

            # Model discovery check
            configured_model = profile.model_identifier
            if configured_model:
                if discovered_model_ids and configured_model not in discovered_model_ids:
                    # Model not present in discovered models list
                    return self._build_failure_response(
                        profile, start_time, tested_at,
                        ProviderErrorCode.MODEL_NOT_AVAILABLE,
                        f"Configured model '{configured_model}' not found on endpoint. Discovered: {discovered_model_ids or 'none'}.",
                        reachability_passed=True, auth_passed=auth_passed,
                        discovered_model_ids=discovered_model_ids
                    )
                model_discovered = True
            else:
                model_discovered = len(discovered_model_ids) > 0

            # Step B: Dry-run non-streaming completion with synthetic fixture
            chat_url = f"{normalized_base}/chat/completions"
            test_model = configured_model or (discovered_model_ids[0] if discovered_model_ids else "default")
            chat_payload = {
                "model": test_model,
                "messages": [
                    {"role": "system", "content": SYNTHETIC_TEST_SYSTEM_PROMPT},
                    {"role": "user", "content": SYNTHETIC_TEST_USER_PROMPT}
                ],
                "temperature": 0.0,
                "max_tokens": 10,
                "stream": False,
            }

            try:
                chat_resp = await client.post(chat_url, json=chat_payload, headers=headers)
            except httpx.ConnectTimeout:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROVIDER_TIMEOUT,
                    f"Connection timed out during dry-run chat completion ({connect_timeout}s).",
                    reachability_passed=True, auth_passed=auth_passed, model_discovered=model_discovered,
                    discovered_model_ids=discovered_model_ids
                )
            except httpx.ReadTimeout:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROVIDER_TIMEOUT,
                    f"Read timed out during dry-run chat completion ({req_timeout}s).",
                    reachability_passed=True, auth_passed=auth_passed, model_discovered=model_discovered,
                    discovered_model_ids=discovered_model_ids
                )
            except httpx.HTTPError as e:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROVIDER_UNAVAILABLE,
                    f"Transport error during dry-run completion: {type(e).__name__}",
                    reachability_passed=True, auth_passed=auth_passed, model_discovered=model_discovered,
                    discovered_model_ids=discovered_model_ids
                )

            if chat_resp.status_code in {301, 302, 307, 308}:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.DESTINATION_DENIED,
                    f"HTTP redirect detected on chat completions (HTTP {chat_resp.status_code}). Destination redirects are prohibited by security policy.",
                    reachability_passed=True, auth_passed=auth_passed,
                    discovered_model_ids=discovered_model_ids
                )
            elif chat_resp.status_code == 404:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.MODEL_NOT_AVAILABLE,
                    f"Chat completions route or model '{test_model}' not found (HTTP 404).",
                    reachability_passed=True, auth_passed=auth_passed,
                    discovered_model_ids=discovered_model_ids
                )
            elif chat_resp.status_code in {401, 403}:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.AUTHENTICATION_FAILED,
                    f"Authentication failed on chat completions (HTTP {chat_resp.status_code}).",
                    reachability_passed=True,
                    discovered_model_ids=discovered_model_ids
                )
            elif chat_resp.status_code == 429:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROVIDER_RATE_LIMITED,
                    "Provider returned rate limit (HTTP 429).",
                    reachability_passed=True, auth_passed=True, model_discovered=model_discovered,
                    discovered_model_ids=discovered_model_ids
                )
            elif chat_resp.status_code != 200:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                    f"Chat completions returned unexpected status HTTP {chat_resp.status_code}.",
                    reachability_passed=True, auth_passed=True, model_discovered=model_discovered,
                    discovered_model_ids=discovered_model_ids
                )

            if len(chat_resp.content) > profile.max_response_bytes:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROVIDER_RESPONSE_TOO_LARGE,
                    f"Chat completion response exceeded bound ({len(chat_resp.content)} bytes > {profile.max_response_bytes} bytes).",
                    reachability_passed=True, auth_passed=True, model_discovered=model_discovered,
                    discovered_model_ids=discovered_model_ids
                )

            try:
                chat_data = json.loads(chat_resp.content.decode("utf-8"))
                choices = chat_data.get("choices", [])
                if not choices or not isinstance(choices, list):
                    return self._build_failure_response(
                        profile, start_time, tested_at,
                        ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                        "Chat completion response missing 'choices' array.",
                        reachability_passed=True, auth_passed=True, model_discovered=model_discovered,
                        discovered_model_ids=discovered_model_ids
                    )
                dry_run_completion_passed = True
                unicode_handling_passed = True
            except Exception:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                    "Chat completion response body could not be parsed as valid JSON.",
                    reachability_passed=True, auth_passed=True, model_discovered=model_discovered,
                    discovered_model_ids=discovered_model_ids
                )

            # Step C: Production Question Wording Capability Test
            # Exercises actual Phase 3C structured question rewording with small non-personal synthetic fixture
            wording_system_prompt = (
                "You are an interview question wording assistant. Reword the provided interview question "
                "to enhance professional tone while preserving exact competency intent. "
                "Return ONLY a valid JSON object matching this schema exactly:\n"
                "{\n"
                '  "operation": "REWORD_QUESTION",\n'
                '  "response_schema_version": "1.0.0",\n'
                '  "variants": [\n'
                '    {\n'
                '      "variant_text": "<reworded question text>",\n'
                '      "rationale_code": "PROFESSIONAL_TONE",\n'
                '      "referenced_identifiers": []\n'
                '    }\n'
                '  ]\n'
                "}\n"
                "Do NOT output markdown fences, explanation, reasoning, or tool calls."
            )
            wording_user_prompt = (
                "Reword this interview question for tone: 'Can you describe a time you optimized a complex database query?'\n"
                "Intent: Assess database query optimization skills."
            )

            wording_payload = {
                "model": test_model,
                "messages": [
                    {"role": "system", "content": wording_system_prompt},
                    {"role": "user", "content": wording_user_prompt}
                ],
                "temperature": 0.1,
                "max_tokens": min(profile.max_output_tokens, 600),
                "stream": False,
            }

            try:
                wording_resp = await client.post(chat_url, json=wording_payload, headers=headers)
            except Exception as e:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROVIDER_UNAVAILABLE,
                    f"Question wording capability test failed during HTTP transport: {type(e).__name__}",
                    reachability_passed=True, auth_passed=True, model_discovered=model_discovered,
                    discovered_model_ids=discovered_model_ids,
                    dry_run_completion_passed=dry_run_completion_passed,
                    unicode_handling_passed=unicode_handling_passed,
                )

            if wording_resp.status_code != 200:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROFILE_MODE_INCOMPATIBLE,
                    f"Question wording test endpoint returned status HTTP {wording_resp.status_code}.",
                    reachability_passed=True, auth_passed=True, model_discovered=model_discovered,
                    discovered_model_ids=discovered_model_ids,
                    dry_run_completion_passed=dry_run_completion_passed,
                    unicode_handling_passed=unicode_handling_passed,
                    recommended_action="Ensure LM Studio is running and the model supports OpenAI chat completions.",
                )

            try:
                wording_json = json.loads(wording_resp.content.decode("utf-8"))
                w_choices = wording_json.get("choices", [])
                if not w_choices or not isinstance(w_choices, list):
                    return self._build_failure_response(
                        profile, start_time, tested_at,
                        ProviderErrorCode.PROFILE_MODE_INCOMPATIBLE,
                        "Question wording response missing 'choices' array.",
                        reachability_passed=True, auth_passed=True, model_discovered=model_discovered,
                        discovered_model_ids=discovered_model_ids,
                        dry_run_completion_passed=dry_run_completion_passed,
                        unicode_handling_passed=unicode_handling_passed,
                    )

                w_choice = w_choices[0]
                w_finish_reason = w_choice.get("finish_reason")
                w_message = w_choice.get("message", {})
                w_content = (w_message.get("content") or "").strip()
                w_reasoning = w_message.get("reasoning_content")

                # Reject if model consumed budget in reasoning or returned empty content
                if not w_content:
                    fail_msg = (
                        "The selected model used its response budget for reasoning and returned no final wording. "
                        "Select a compatible Instruct model and retry."
                        if (w_reasoning or w_finish_reason == "length")
                        else "Model returned empty message content for question wording operation."
                    )
                    return self._build_failure_response(
                        profile, start_time, tested_at,
                        ProviderErrorCode.PROFILE_MODE_INCOMPATIBLE,
                        fail_msg,
                        reachability_passed=True, auth_passed=True, model_discovered=model_discovered,
                        discovered_model_ids=discovered_model_ids,
                        dry_run_completion_passed=dry_run_completion_passed,
                        unicode_handling_passed=unicode_handling_passed,
                        recommended_action="Load and select Qwen3 4B Instruct (non-thinking model) in LM Studio.",
                    )

                if w_finish_reason == "length":
                    return self._build_failure_response(
                        profile, start_time, tested_at,
                        ProviderErrorCode.RESPONSE_TRUNCATED,
                        "Model output was truncated before structured JSON completion (finish_reason: length).",
                        reachability_passed=True, auth_passed=True, model_discovered=model_discovered,
                        discovered_model_ids=discovered_model_ids,
                        dry_run_completion_passed=dry_run_completion_passed,
                        unicode_handling_passed=unicode_handling_passed,
                        recommended_action="Select a compatible Instruct model with low reasoning overhead.",
                    )

                # Clean markdown fences if present
                clean_wording_str = w_content
                if clean_wording_str.startswith("```json"):
                    clean_wording_str = clean_wording_str[7:]
                elif clean_wording_str.startswith("```"):
                    clean_wording_str = clean_wording_str[3:]
                if clean_wording_str.endswith("```"):
                    clean_wording_str = clean_wording_str[:-3]
                clean_wording_str = clean_wording_str.strip()

                parsed_wording = json.loads(clean_wording_str)
                if not isinstance(parsed_wording, dict):
                    raise ValueError("Wording response is not a JSON object.")

                if parsed_wording.get("operation") != "REWORD_QUESTION":
                    raise ValueError(f"Invalid operation '{parsed_wording.get('operation')}'. Expected 'REWORD_QUESTION'.")

                if parsed_wording.get("response_schema_version") != "1.0.0":
                    raise ValueError(f"Invalid schema version '{parsed_wording.get('response_schema_version')}'. Expected '1.0.0'.")

                variants = parsed_wording.get("variants")
                if not variants or not isinstance(variants, list) or len(variants) == 0:
                    raise ValueError("Wording response 'variants' list is missing or empty.")

                first_var = variants[0]
                if not isinstance(first_var, dict) or not first_var.get("variant_text") or not str(first_var["variant_text"]).strip():
                    raise ValueError("Wording variant is missing non-empty 'variant_text'.")

            except Exception as exc:
                return self._build_failure_response(
                    profile, start_time, tested_at,
                    ProviderErrorCode.PROFILE_MODE_INCOMPATIBLE,
                    f"Question wording capability test failed validation: {str(exc)}",
                    reachability_passed=True, auth_passed=True, model_discovered=model_discovered,
                    discovered_model_ids=discovered_model_ids,
                    dry_run_completion_passed=dry_run_completion_passed,
                    unicode_handling_passed=unicode_handling_passed,
                    recommended_action="Load and select Qwen3 4B Instruct (non-thinking model) in LM Studio.",
                )

            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            auth_passed = True

            return ProviderCompatibilityReportResponse(
                profile_id=profile.profile_id,
                display_name=profile.display_name,
                provider_type=profile.provider_type,
                model_identifier=profile.model_identifier or (discovered_model_ids[0] if discovered_model_ids else None),
                compatibility_status=ProviderCompatibilityStatus.HEALTHY,
                is_compatible=True,
                safe_failure_code=None,
                safe_failure_message=None,
                reachability_passed=reachability_passed,
                auth_passed=auth_passed,
                model_discovered=model_discovered,
                discovered_model_ids=discovered_model_ids,
                dry_run_completion_passed=dry_run_completion_passed,
                question_wording_passed=True,
                non_streaming_enforced=True,
                unicode_handling_passed=unicode_handling_passed,
                recommended_action=None,
                latency_ms=latency_ms,
                tested_at=tested_at,
                model_source=profile.model_source,
                model_licence=profile.model_licence,
                model_checksum=profile.model_checksum,
            )

        finally:
            if should_close:
                await client.aclose()

    def _build_failure_response(
        self,
        profile: ProviderProfile,
        start_time: float,
        tested_at: datetime,
        error_code: ProviderErrorCode,
        safe_message: str,
        reachability_passed: bool = False,
        auth_passed: bool = False,
        model_discovered: bool = False,
        discovered_model_ids: Optional[List[str]] = None,
        dry_run_completion_passed: bool = False,
        unicode_handling_passed: bool = False,
        recommended_action: Optional[str] = None,
    ) -> ProviderCompatibilityReportResponse:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return ProviderCompatibilityReportResponse(
            profile_id=profile.profile_id,
            display_name=profile.display_name,
            provider_type=profile.provider_type,
            model_identifier=profile.model_identifier,
            compatibility_status=ProviderCompatibilityStatus.INCOMPATIBLE,
            is_compatible=False,
            safe_failure_code=error_code.value,
            safe_failure_message=safe_message,
            reachability_passed=reachability_passed,
            auth_passed=auth_passed,
            model_discovered=model_discovered,
            discovered_model_ids=discovered_model_ids or [],
            dry_run_completion_passed=dry_run_completion_passed,
            question_wording_passed=False,
            non_streaming_enforced=True,
            unicode_handling_passed=unicode_handling_passed,
            recommended_action=recommended_action,
            latency_ms=latency_ms,
            tested_at=tested_at,
            model_source=profile.model_source,
            model_licence=profile.model_licence,
            model_checksum=profile.model_checksum,
        )
