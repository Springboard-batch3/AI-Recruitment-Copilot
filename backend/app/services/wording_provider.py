"""Provider-neutral internal contract and implementations for wording providers.

Defines:
- BaseWordingProvider: Narrow, bounded domain interface.
- DisabledWordingProvider: Zero-network disabled default.
- FakeWordingProvider: Deterministic test fixture simulation.
- LocalOpenAICompatibleProvider: Secure, bounded local OpenAI-compatible HTTP adapter for LM Studio.

Strictly enforces:
- No generic chat/prompt/generation endpoints.
- No model tools, function calling, browser, ATS, filesystem or database access.
- Non-streaming transport only.
- Strict connection/read timeouts and maximum response size bounds.
- Rejection of redirects and URL credentials.
- Local-only loopback transport; zero automatic remote fallback.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import httpx

from app.schemas.interview_blueprint import PolicyVersionReference
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    ProviderCompatibilityStatus,
    ProviderEgressClass,
    ProviderErrorCode,
    ProviderOperation,
    ProviderType,
    ValidationErrorCode,
    ValidationState,
)
from app.schemas.provider_capability import (
    ProviderCapabilityRequest,
    ProviderCapabilityResponse,
    ProviderRewordResult,
)
from app.schemas.provider_profile import ProviderProfile
from app.services.destination_policy import StrictDestinationPolicy
from app.services.secret_resolver import EnvironmentSecretResolver, SecretResolutionError

from app.schemas.wording_contract import (
    PROMPT_TEMPLATE_VERSION,
    WORDING_RESPONSE_SCHEMA_VERSION,
    BoundedWordingRequest,
    BoundedWordingResponse,
    StructuredWordingVariant,
    WordingRationaleCode,
)
from app.schemas.assessment_contract import (
    BoundedAssessmentRequest,
    BoundedAssessmentResponse,
    StructuredCriterionObservation,
)
from app.schemas.mcq_contract import (
    StructuredAiMcqBatchResponse,
    StructuredAiMcqOption,
    StructuredAiMcqQuestion,
)
from app.services.wording_prompt_package import (
    assemble_wording_messages,
    assemble_assessment_messages,
)

logger = logging.getLogger("ai_hiring.wording_provider")



class ProviderTransportError(Exception):
    """Base exception for provider transport failures."""
    def __init__(self, error_code: ProviderErrorCode, safe_message: str):
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message


class BaseWordingProvider(ABC):
    """Narrow provider-neutral interface separating interview domain from provider transport."""

    @abstractmethod
    async def reword_question(
        self,
        request: ProviderCapabilityRequest,
    ) -> ProviderCapabilityResponse:
        """Executes bounded question rewording or returns typed failure."""
        pass

    @abstractmethod
    async def request_wording_assistance(
        self,
        request: BoundedWordingRequest,
    ) -> BoundedWordingResponse:
        """Executes strict structured wording assistance for one authorized question."""
        pass

    @abstractmethod
    async def request_provisional_assessment(
        self,
        request: Any,
    ) -> Any:
        """Executes strict structured provisional assessment for one submitted question answer."""
        pass

    async def request_mcq_generation(
        self,
        request: Any,
    ) -> StructuredAiMcqBatchResponse:
        """Executes strict structured multiple-choice question generation for job requirements."""
        raise NotImplementedError("Provider does not support MCQ generation.")

    async def request_job_grounded_generation(
        self,
        job_context: Dict[str, Any],
        slots_context: List[Dict[str, Any]],
        interview_mode: str = "TEXT",
        existing_text_stems: Optional[List[str]] = None,
    ) -> Any:
        """Executes batch job-grounded question generation for planned slots."""
        raise NotImplementedError("Provider does not support job-grounded question generation.")



class DisabledWordingProvider(BaseWordingProvider):
    """Default provider mode when AI assistance is disabled.
    
    Performs zero network operations, requires no secrets, and never blocks Phase 3B.
    """

    def __init__(self, profile: Optional[ProviderProfile] = None):
        self.profile = profile

    async def request_job_grounded_generation(
        self,
        job_context: Dict[str, Any],
        slots_context: List[Dict[str, Any]],
        interview_mode: str = "TEXT",
        existing_text_stems: Optional[List[str]] = None,
    ) -> Any:
        raise ProviderTransportError(
            error_code=ProviderErrorCode.PROVIDER_DISABLED,
            safe_message="Model provider is disabled by system policy.",
        )

    async def reword_question(
        self,
        request: ProviderCapabilityRequest,
    ) -> ProviderCapabilityResponse:
        return ProviderCapabilityResponse(
            correlation_id=request.correlation_id,
            operation=request.operation,
            validation_state=ValidationState.BLOCKED,
            error_code=ProviderErrorCode.PROVIDER_DISABLED,
            safe_error_message="Model provider is disabled by system policy.",
            fallback_used=True,
            policy_reference=request.policy_reference,
            completed_at=datetime.now(timezone.utc),
        )

    async def request_wording_assistance(
        self,
        request: BoundedWordingRequest,
    ) -> BoundedWordingResponse:
        raise ProviderTransportError(
            error_code=ProviderErrorCode.PROVIDER_DISABLED,
            safe_message="Model provider is disabled by system policy.",
        )

    async def request_provisional_assessment(
        self,
        request: BoundedAssessmentRequest,
    ) -> BoundedAssessmentResponse:
        raise ProviderTransportError(
            error_code=ProviderErrorCode.PROVIDER_DISABLED,
            safe_message="Model provider is disabled by system policy.",
        )

    async def request_mcq_generation(
        self,
        request: Any,
    ) -> StructuredAiMcqBatchResponse:
        raise ProviderTransportError(
            error_code=ProviderErrorCode.PROVIDER_DISABLED,
            safe_message="Model provider is disabled by system policy.",
        )



class FakeWordingProvider(BaseWordingProvider):
    """Deterministic fake provider for automated unit and integration tests.
    
    Simulates successful rewording, timeout, connection failures, authentication errors,
    invalid schema responses, oversized responses, and rate limits without network traffic.
    """

    def __init__(
        self,
        simulated_mode: str = "SUCCESS",
        reworded_text_override: Optional[str] = None,
        custom_error_code: Optional[ProviderErrorCode] = None,
        custom_error_message: Optional[str] = None,
        simulated_variants_override: Optional[List[StructuredWordingVariant]] = None,
        simulated_raw_json_override: Optional[Dict[str, Any]] = None,
    ):
        self.simulated_mode = simulated_mode
        self.reworded_text_override = reworded_text_override
        self.custom_error_code = custom_error_code
        self.custom_error_message = custom_error_message
        self.simulated_variants_override = simulated_variants_override
        self.simulated_raw_json_override = simulated_raw_json_override

    async def reword_question(
        self,
        request: ProviderCapabilityRequest,
    ) -> ProviderCapabilityResponse:
        now = datetime.now(timezone.utc)

        if self.simulated_mode == "SUCCESS":
            orig_text = (
                request.reword_payload.original_text
                if request.reword_payload
                else "Default question text"
            )
            reworded = self.reworded_text_override or f"Can you describe a situation where: {orig_text}"
            return ProviderCapabilityResponse(
                correlation_id=request.correlation_id,
                operation=request.operation,
                validation_state=ValidationState.PASSED,
                error_code=None,
                safe_error_message=None,
                fallback_used=False,
                reword_result=ProviderRewordResult(
                    reworded_text=reworded,
                    intent_preserved=True,
                    authoring_origin=AuthoringOrigin.AI_ASSISTED,
                ),
                policy_reference=request.policy_reference,
                completed_at=now,
            )

        elif self.simulated_mode == "TIMEOUT":
            return ProviderCapabilityResponse(
                correlation_id=request.correlation_id,
                operation=request.operation,
                validation_state=ValidationState.BLOCKED,
                error_code=ProviderErrorCode.PROVIDER_TIMEOUT,
                safe_error_message="Simulated request timeout.",
                fallback_used=True,
                policy_reference=request.policy_reference,
                completed_at=now,
            )

        elif self.simulated_mode == "UNAVAILABLE":
            return ProviderCapabilityResponse(
                correlation_id=request.correlation_id,
                operation=request.operation,
                validation_state=ValidationState.BLOCKED,
                error_code=ProviderErrorCode.PROVIDER_UNAVAILABLE,
                safe_error_message="Simulated provider endpoint unavailable.",
                fallback_used=True,
                policy_reference=request.policy_reference,
                completed_at=now,
            )

        elif self.simulated_mode == "AUTH_FAILED":
            return ProviderCapabilityResponse(
                correlation_id=request.correlation_id,
                operation=request.operation,
                validation_state=ValidationState.BLOCKED,
                error_code=ProviderErrorCode.AUTHENTICATION_FAILED,
                safe_error_message="Simulated authentication failure.",
                fallback_used=True,
                policy_reference=request.policy_reference,
                completed_at=now,
            )

        elif self.simulated_mode == "MODEL_NOT_AVAILABLE":
            return ProviderCapabilityResponse(
                correlation_id=request.correlation_id,
                operation=request.operation,
                validation_state=ValidationState.BLOCKED,
                error_code=ProviderErrorCode.MODEL_NOT_AVAILABLE,
                safe_error_message="Simulated configured model not found on endpoint.",
                fallback_used=True,
                policy_reference=request.policy_reference,
                completed_at=now,
            )

        elif self.simulated_mode == "OVERSIZED_RESPONSE":
            return ProviderCapabilityResponse(
                correlation_id=request.correlation_id,
                operation=request.operation,
                validation_state=ValidationState.BLOCKED,
                error_code=ProviderErrorCode.PROVIDER_RESPONSE_TOO_LARGE,
                safe_error_message="Simulated response exceeded maximum allowed bytes.",
                fallback_used=True,
                policy_reference=request.policy_reference,
                completed_at=now,
            )

        elif self.simulated_mode == "INVALID_RESPONSE":
            return ProviderCapabilityResponse(
                correlation_id=request.correlation_id,
                operation=request.operation,
                validation_state=ValidationState.BLOCKED,
                error_code=ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                safe_error_message="Simulated response was malformed or failed schema.",
                fallback_used=True,
                policy_reference=request.policy_reference,
                completed_at=now,
            )

        else:
            return ProviderCapabilityResponse(
                correlation_id=request.correlation_id,
                operation=request.operation,
                validation_state=ValidationState.BLOCKED,
                error_code=self.custom_error_code or ProviderErrorCode.PROVIDER_UNAVAILABLE,
                safe_error_message=self.custom_error_message or "Simulated provider error.",
                fallback_used=True,
                policy_reference=request.policy_reference,
                completed_at=now,
            )

    async def request_wording_assistance(
        self,
        request: BoundedWordingRequest,
    ) -> BoundedWordingResponse:
        if self.simulated_mode == "SUCCESS":
            if self.simulated_variants_override is not None:
                variants = self.simulated_variants_override
            else:
                orig_text = request.deterministic_question_text
                reworded = self.reworded_text_override or f"Could you provide an example of when: {orig_text}"
                variants = [
                    StructuredWordingVariant(
                        variant_text=reworded,
                        rationale_code=WordingRationaleCode.PROFESSIONAL_TONE,
                        referenced_identifiers=list(request.allowed_reference_ids),
                    )
                ]
            return BoundedWordingResponse(
                operation="REWORD_QUESTION",
                response_schema_version=WORDING_RESPONSE_SCHEMA_VERSION,
                variants=variants[: request.max_variants],
            )
        elif self.simulated_mode == "TIMEOUT":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_TIMEOUT,
                safe_message="Simulated request timeout.",
            )
        elif self.simulated_mode == "UNAVAILABLE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_UNAVAILABLE,
                safe_message="Simulated provider endpoint unavailable.",
            )
        elif self.simulated_mode == "AUTH_FAILED":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.AUTHENTICATION_FAILED,
                safe_message="Simulated authentication failure.",
            )
        elif self.simulated_mode == "MODEL_NOT_AVAILABLE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.MODEL_NOT_AVAILABLE,
                safe_message="Simulated configured model not found on endpoint.",
            )
        elif self.simulated_mode == "OVERSIZED_RESPONSE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_RESPONSE_TOO_LARGE,
                safe_message="Simulated response exceeded maximum allowed bytes.",
            )
        elif self.simulated_mode == "INVALID_RESPONSE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                safe_message="Simulated response was malformed or failed schema.",
            )
        else:
            raise ProviderTransportError(
                error_code=self.custom_error_code or ProviderErrorCode.PROVIDER_UNAVAILABLE,
                safe_message=self.custom_error_message or "Simulated provider error.",
            )

    async def request_provisional_assessment(
        self,
        request: BoundedAssessmentRequest,
    ) -> BoundedAssessmentResponse:
        if self.simulated_mode == "SUCCESS":
            if self.simulated_raw_json_override is not None:
                return BoundedAssessmentResponse.model_validate(self.simulated_raw_json_override)
            
            # Default success response: Map first criterion to first segment
            obs = []
            cited_ids = list(request.citation_allowlist[:1]) if request.citation_allowlist else []
            crit_ids = list(request.rubric.rating_anchors.keys())
            provisional_level = crit_ids[0] if crit_ids else 3
            
            if request.citation_allowlist:
                obs.append(
                    StructuredCriterionObservation(
                        criterion_id=f"CRIT-{provisional_level}",
                        observation="Candidate demonstrated structured reasoning matching rubric criteria.",
                        cited_segment_ids=cited_ids,
                    )
                )

            return BoundedAssessmentResponse(
                operation="PROVISIONAL_ASSESSMENT",
                response_schema_version="1.0.0",
                provisional_level=provisional_level,
                insufficient_evidence_reason=None,
                criterion_observations=obs,
                cited_segment_ids=cited_ids,
                uncertainty_codes=[],
                safe_validation_metadata={"mock": True},
            )
        elif self.simulated_mode == "TIMEOUT":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_TIMEOUT,
                safe_message="Simulated request timeout.",
            )
        elif self.simulated_mode == "UNAVAILABLE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_UNAVAILABLE,
                safe_message="Simulated provider endpoint unavailable.",
            )
        elif self.simulated_mode == "AUTH_FAILED":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.AUTHENTICATION_FAILED,
                safe_message="Simulated authentication failure.",
            )
        elif self.simulated_mode == "MODEL_NOT_AVAILABLE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.MODEL_NOT_AVAILABLE,
                safe_message="Simulated configured model not found on endpoint.",
            )
        elif self.simulated_mode == "OVERSIZED_RESPONSE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_RESPONSE_TOO_LARGE,
                safe_message="Simulated response exceeded maximum allowed bytes.",
            )
        elif self.simulated_mode == "INVALID_RESPONSE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                safe_message="Simulated response was malformed or failed schema.",
            )
        else:
            raise ProviderTransportError(
                error_code=self.custom_error_code or ProviderErrorCode.PROVIDER_UNAVAILABLE,
                safe_message=self.custom_error_message or "Simulated provider error.",
            )

    async def request_mcq_generation(
        self,
        request: Any,
    ) -> StructuredAiMcqBatchResponse:
        if self.simulated_mode == "SUCCESS":
            count = getattr(request, "count", 5) if hasattr(request, "count") else (request.get("count", 5) if isinstance(request, dict) else 5)
            difficulty = getattr(request, "difficulty", "FOUNDATIONAL")
            diff_str = difficulty.value if hasattr(difficulty, "value") else str(difficulty)
            questions: List[StructuredAiMcqQuestion] = []
            for i in range(count):
                questions.append(
                    StructuredAiMcqQuestion(
                        question_text=f"When designing scalable production systems, what is standard best practice #{i+1}?",
                        options=[
                            StructuredAiMcqOption(option_id="A", text="Implement structured input validation and robust error recovery."),
                            StructuredAiMcqOption(option_id="B", text="Bypass all validation steps to reduce processing latency."),
                            StructuredAiMcqOption(option_id="C", text="Store all secrets unencrypted in public log streams."),
                            StructuredAiMcqOption(option_id="D", text="Disable database transactions during concurrent writes."),
                        ],
                        correct_option_id="A",
                        answer_explanation="Standard engineering principles require rigorous input validation and comprehensive error handling.",
                        difficulty=diff_str,
                        competency_id=f"COMP-{i+1}",
                    )
                )
            return StructuredAiMcqBatchResponse(questions=questions)
        elif self.simulated_mode == "TIMEOUT":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_TIMEOUT,
                safe_message="Simulated request timeout.",
            )
        elif self.simulated_mode == "UNAVAILABLE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_UNAVAILABLE,
                safe_message="Simulated provider endpoint unavailable.",
            )
        elif self.simulated_mode == "AUTH_FAILED":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.AUTHENTICATION_FAILED,
                safe_message="Simulated authentication failure.",
            )
        elif self.simulated_mode == "MODEL_NOT_AVAILABLE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.MODEL_NOT_AVAILABLE,
                safe_message="Simulated configured model not found on endpoint.",
            )
        elif self.simulated_mode == "OVERSIZED_RESPONSE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_RESPONSE_TOO_LARGE,
                safe_message="Simulated response exceeded maximum allowed bytes.",
            )
        elif self.simulated_mode == "INVALID_RESPONSE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                safe_message="Simulated response was malformed or failed schema.",
            )
        else:
            raise ProviderTransportError(
                error_code=self.custom_error_code or ProviderErrorCode.PROVIDER_UNAVAILABLE,
                safe_message=self.custom_error_message or "Simulated provider error.",
            )

    async def request_job_grounded_generation(
        self,
        job_context: Dict[str, Any],
        slots_context: List[Dict[str, Any]],
        interview_mode: str = "TEXT",
        existing_text_stems: Optional[List[str]] = None,
    ) -> Any:
        if self.simulated_mode == "SUCCESS":
            if self.simulated_raw_json_override is not None:
                from app.schemas.question_generation_contract import StrictJobGroundedGenerationBatchResponse
                return StrictJobGroundedGenerationBatchResponse.model_validate(self.simulated_raw_json_override)
            
            from app.schemas.question_generation_contract import (
                QUESTION_GENERATION_SCHEMA_VERSION,
                StrictGeneratedQuestionItem,
                StrictJobGroundedGenerationBatchResponse,
            )
            items = []
            for slot in slots_context:
                sid = slot.get("slot_id")
                req_id = slot.get("primary_requirement_id") or (slot.get("allowed_requirement_ids", ["req-1"])[0])
                comp = slot.get("competency_name", "Technical Competency")
                obj = slot.get("question_objective", "Assess practical engineering problem-solving")
                q_type = slot.get("question_type", "TECHNICAL")
                diff = slot.get("difficulty", "INTERMEDIATE")
                if interview_mode == "AUDIO":
                    q_text = f"Can you walk me through how you verbally communicate and solve {comp} challenges?"
                else:
                    q_text = f"Can you describe how you architect and validate {comp} in high-scale production systems?"
                items.append(
                    StrictGeneratedQuestionItem(
                        slot_id=str(sid),
                        question_text=q_text,
                        primary_requirement_id=str(req_id),
                        supporting_requirement_ids=[],
                        competency_name=str(comp),
                        question_objective=str(obj),
                        question_type=str(q_type),
                        difficulty=str(diff),
                        estimated_answer_minutes=3 if interview_mode == "AUDIO" else 5,
                        permitted_probes=[
                            f"How do you monitor and measure latency in {comp}?",
                            "What failure modes or edge cases did you encounter?",
                        ],
                    )
                )
            return StrictJobGroundedGenerationBatchResponse(
                schema_version=QUESTION_GENERATION_SCHEMA_VERSION,
                questions=items,
            )
        elif self.simulated_mode == "TIMEOUT":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_TIMEOUT,
                safe_message="Simulated request timeout.",
            )
        elif self.simulated_mode == "UNAVAILABLE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_UNAVAILABLE,
                safe_message="Simulated provider endpoint unavailable.",
            )
        elif self.simulated_mode == "INVALID_RESPONSE":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                safe_message="Simulated response was malformed or failed schema.",
            )
        else:
            raise ProviderTransportError(
                error_code=self.custom_error_code or ProviderErrorCode.PROVIDER_UNAVAILABLE,
                safe_message=self.custom_error_message or "Simulated provider error.",
            )



class LocalOpenAICompatibleProvider(BaseWordingProvider):
    """Production local OpenAI-compatible HTTP adapter suitable for LM Studio.
    
    Properties:
    - Backend-only dispatch (never connects directly from client browser).
    - Enforces local loopback boundary via StrictDestinationPolicy.
    - Uses non-streaming HTTP requests with strict connect, read, and total timeouts.
    - Limits response read size to max_response_bytes.
    - Disallows HTTP redirects (follow_redirects=False).
    - Disallows cookies and raw credential URL embedding.
    - Supports optional API key bearer authentication from secret_resolver.
    - Limits concurrency via internal asyncio.Semaphore.
    - Never triggers automatic remote fallback on failure.
    """

    def __init__(
        self,
        profile: ProviderProfile,
        destination_policy: Optional[StrictDestinationPolicy] = None,
        secret_resolver: Optional[EnvironmentSecretResolver] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.profile = profile
        self.destination_policy = destination_policy or StrictDestinationPolicy(allow_remote=False)
        self.secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self._custom_http_client = http_client
        self._semaphore = asyncio.Semaphore(max(1, profile.max_concurrency))

    def _resolve_headers(self) -> Dict[str, str]:
        """Constructs safe headers, injecting resolved secret if configured."""
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AI-Hiring-Platform/Phase3C",
        }
        if self.profile.secret_ref:
            try:
                secret_str = self.secret_resolver.resolve_secret_ref(self.profile.secret_ref)
                if secret_str:
                    headers["Authorization"] = f"Bearer {secret_str.get_secret_value()}"
            except SecretResolutionError as e:
                logger.warning("Failed to resolve secret_ref '%s': %s", self.profile.secret_ref, e.code)
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.AUTHENTICATION_FAILED,
                    safe_message=f"Secret resolution error for configured secret reference: {e.code}",
                )
        return headers

    def _validate_local_endpoint(self) -> str:
        """Validates base URL against local destination policy."""
        base_url = self.profile.base_url or "http://127.0.0.1:1234/v1"
        dest_eval = self.destination_policy.evaluate_destination(
            target_url=base_url,
            egress_class=ProviderEgressClass.LOCAL_ONLY,
        )
        if not dest_eval.is_valid:
            logger.warning("Local destination rejected by policy: %s", dest_eval.safe_error_code)
            raise ProviderTransportError(
                error_code=ProviderErrorCode.DESTINATION_DENIED,
                safe_message=f"Destination rejected: {dest_eval.safe_error_code} - {dest_eval.safe_error_message}",
            )
        return dest_eval.normalized_url or base_url.rstrip("/")

    async def _send_bounded_request(
        self,
        endpoint_url: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Transmits non-streaming payload with strict timeout and byte limit enforcement."""
        headers = self._resolve_headers()
        timeout = httpx.Timeout(
            connect=self.profile.connect_timeout_seconds,
            read=self.profile.request_timeout_seconds,
            write=self.profile.connect_timeout_seconds,
            pool=self.profile.connect_timeout_seconds,
        )

        async with self._semaphore:
            if self._custom_http_client is not None:
                client = self._custom_http_client
                should_close = False
            else:
                client = httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=False,
                    cookies=None,
                    trust_env=False,  # Prevent rogue proxy env vars from redirecting loopback
                )
                should_close = True

            try:
                # Use non-streaming POST
                response = await client.post(
                    endpoint_url,
                    json=payload,
                    headers=headers,
                )

                # Check status codes
                if response.status_code == 401 or response.status_code == 403:
                    raise ProviderTransportError(
                        error_code=ProviderErrorCode.AUTHENTICATION_FAILED,
                        safe_message=f"Provider returned authentication failure (HTTP {response.status_code}).",
                    )
                elif response.status_code == 404:
                    raise ProviderTransportError(
                        error_code=ProviderErrorCode.MODEL_NOT_AVAILABLE,
                        safe_message=f"Provider endpoint route or model not found (HTTP {response.status_code}).",
                    )
                elif response.status_code == 429:
                    raise ProviderTransportError(
                        error_code=ProviderErrorCode.PROVIDER_RATE_LIMITED,
                        safe_message="Provider returned rate limit (HTTP 429).",
                    )
                elif response.status_code >= 500:
                    raise ProviderTransportError(
                        error_code=ProviderErrorCode.PROVIDER_UNAVAILABLE,
                        safe_message=f"Provider returned server error (HTTP {response.status_code}).",
                    )
                elif response.status_code != 200:
                    raise ProviderTransportError(
                        error_code=ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                        safe_message=f"Provider returned unexpected status HTTP {response.status_code}.",
                    )

                # Enforce response byte limit
                content_bytes = response.content
                if len(content_bytes) > self.profile.max_response_bytes:
                    raise ProviderTransportError(
                        error_code=ProviderErrorCode.PROVIDER_RESPONSE_TOO_LARGE,
                        safe_message=f"Provider response ({len(content_bytes)} bytes) exceeded bound of {self.profile.max_response_bytes} bytes.",
                    )

                try:
                    parsed_json = json.loads(content_bytes.decode("utf-8"))
                except Exception:
                    raise ProviderTransportError(
                        error_code=ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                        safe_message="Provider response could not be parsed as valid JSON.",
                    )

                return parsed_json

            except httpx.ConnectTimeout:
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.PROVIDER_TIMEOUT,
                    safe_message=f"Connection timeout to local provider ({self.profile.connect_timeout_seconds}s).",
                )
            except httpx.ReadTimeout:
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.PROVIDER_TIMEOUT,
                    safe_message=f"Read timeout from local provider ({self.profile.request_timeout_seconds}s).",
                )
            except httpx.ConnectError:
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.PROVIDER_UNAVAILABLE,
                    safe_message="Could not connect to local provider endpoint. Ensure LM Studio server is running.",
                )
            except httpx.HTTPError as e:
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.PROVIDER_UNAVAILABLE,
                    safe_message=f"Transport error connecting to provider: {type(e).__name__}",
                )
            finally:
                if should_close:
                    await client.aclose()

    async def request_wording_assistance(
        self,
        request: BoundedWordingRequest,
    ) -> BoundedWordingResponse:
        """Executes strict structured wording assistance using the active profile."""
        if not self.profile.enabled:
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_DISABLED,
                safe_message="Target provider profile is currently disabled.",
            )

        normalized_base = self._validate_local_endpoint()
        chat_url = f"{normalized_base}/chat/completions"

        messages = assemble_wording_messages(request)

        payload = {
            "model": self.profile.model_identifier or "default",
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": min(self.profile.max_output_tokens, 600),
            "stream": False,
        }

        raw_response = await self._send_bounded_request(chat_url, payload)

        choices = raw_response.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                safe_message="OpenAI-compatible response missing 'choices' array.",
            )

        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        message = choice.get("message", {})
        raw_content = message.get("content")
        raw_reasoning = message.get("reasoning_content")

        # Normalize content
        content_str = (raw_content or "").strip()

        # Reject reasoning-only output or empty content
        if not content_str:
            if raw_reasoning or finish_reason == "length":
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.RESPONSE_CONTENT_EMPTY,
                    safe_message="The selected model used its response budget for reasoning and returned no final wording. Select a compatible Instruct model and retry. Your deterministic questions were retained.",
                )
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_CONTENT_EMPTY,
                safe_message="OpenAI-compatible response contained null, empty, or whitespace message content.",
            )

        # Check for truncation before completing structured output
        if finish_reason == "length":
            # If the response was truncated at length boundary, it cannot be guaranteed valid
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_TRUNCATED,
                safe_message="Model response was truncated before completing structured output (finish_reason: length).",
            )

        # Parse JSON from content (stripping markdown fences if present)
        # NEVER parse reasoning_content as final answer; only parse message.content
        clean_json_str = content_str
        if clean_json_str.startswith("```json"):
            clean_json_str = clean_json_str[7:]
        elif clean_json_str.startswith("```"):
            clean_json_str = clean_json_str[3:]
        if clean_json_str.endswith("```"):
            clean_json_str = clean_json_str[:-3]
        clean_json_str = clean_json_str.strip()

        try:
            parsed_data = json.loads(clean_json_str)
        except Exception as e:
            logger.warning("Failed to parse JSON from provider wording response: %s", e)
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_SCHEMA_INVALID,
                safe_message="Provider response content could not be parsed into valid JSON structure.",
            )

        if not isinstance(parsed_data, dict):
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_SCHEMA_INVALID,
                safe_message="Provider response root is not a JSON object.",
            )

        try:
            structured_response = BoundedWordingResponse.model_validate(parsed_data)
            return structured_response
        except Exception as e:
            logger.warning("Provider wording response failed schema validation: %s", e)
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_SCHEMA_INVALID,
                safe_message=f"Provider response schema validation failed: {str(e)}",
            )

    async def request_provisional_assessment(
        self,
        request: BoundedAssessmentRequest,
    ) -> BoundedAssessmentResponse:
        """Executes strict structured provisional assessment using the active profile."""
        if not self.profile.enabled:
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_DISABLED,
                safe_message="Target provider profile is currently disabled.",
            )

        normalized_base = self._validate_local_endpoint()
        chat_url = f"{normalized_base}/chat/completions"

        messages = assemble_assessment_messages(request)

        payload = {
            "model": self.profile.model_identifier or "default",
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": min(self.profile.max_output_tokens, 800),
            "stream": False,
        }

        raw_response = await self._send_bounded_request(chat_url, payload)

        choices = raw_response.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                safe_message="OpenAI-compatible response missing 'choices' array.",
            )

        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        message = choice.get("message", {})
        raw_content = message.get("content")
        raw_reasoning = message.get("reasoning_content")

        content_str = (raw_content or "").strip()

        if not content_str:
            if raw_reasoning or finish_reason == "length":
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.RESPONSE_CONTENT_EMPTY,
                    safe_message="The selected model used its response budget for reasoning and returned no final assessment.",
                )
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_CONTENT_EMPTY,
                safe_message="OpenAI-compatible response contained null, empty, or whitespace message content.",
            )

        if finish_reason == "length":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_TRUNCATED,
                safe_message="Model assessment response was truncated before completing structured output (finish_reason: length).",
            )

        clean_json_str = content_str
        if clean_json_str.startswith("```json"):
            clean_json_str = clean_json_str[7:]
        elif clean_json_str.startswith("```"):
            clean_json_str = clean_json_str[3:]
        if clean_json_str.endswith("```"):
            clean_json_str = clean_json_str[:-3]
        clean_json_str = clean_json_str.strip()

        try:
            parsed_data = json.loads(clean_json_str)
        except Exception as e:
            logger.warning("Failed to parse JSON from provider assessment response: %s", e)
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_SCHEMA_INVALID,
                safe_message="Provider response content could not be parsed into valid JSON structure.",
            )

        if not isinstance(parsed_data, dict):
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_SCHEMA_INVALID,
                safe_message="Provider response root is not a JSON object.",
            )

        try:
            structured_response = BoundedAssessmentResponse.model_validate(parsed_data)
            return structured_response
        except Exception as e:
            logger.warning("Provider assessment response failed schema validation: %s", e)
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_SCHEMA_INVALID,
                safe_message=f"Provider response schema validation failed: {str(e)}",
            )

    async def request_mcq_generation(
        self,
        request: Any,
    ) -> StructuredAiMcqBatchResponse:
        """Executes structured MCQ generation via local OpenAI-compatible endpoint."""
        if not self.profile.enabled:
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_DISABLED,
                safe_message="Target provider profile is currently disabled.",
            )

        normalized_base = self._validate_local_endpoint()
        chat_url = f"{normalized_base}/chat/completions"

        req_texts = []
        if hasattr(request, "requirements") and request.requirements:
            for r in request.requirements:
                lbl = getattr(r, "label", getattr(r, "name", "Requirement"))
                req_texts.append(f"- Requirement ({getattr(r, 'requirement_id', '')}): {lbl}")
        req_context = "\n".join(req_texts) if req_texts else "- General Technical & Software Engineering Fundamentals"

        count = getattr(request, "count", 5) if hasattr(request, "count") else (request.get("count", 5) if isinstance(request, dict) else 5)
        difficulty = getattr(request, "difficulty", "FOUNDATIONAL")
        diff_str = difficulty.value if hasattr(difficulty, "value") else str(difficulty)

        system_prompt = (
            "You are an expert technical interviewer creating structured Multiple Choice Questions (MCQs) for candidate practice. "
            "You must output ONLY valid JSON matching the exact schema provided. Do not use tools or Markdown outside JSON fences.\n"
            "Schema:\n"
            "{\n"
            '  "questions": [\n'
            "    {\n"
            '      "question_text": "...",\n'
            '      "options": [\n'
            '        {"option_id": "A", "text": "..."},\n'
            '        {"option_id": "B", "text": "..."},\n'
            '        {"option_id": "C", "text": "..."},\n'
            '        {"option_id": "D", "text": "..."}\n'
            "      ],\n"
            '      "correct_option_id": "A",\n'
            '      "answer_explanation": "...",\n'
            '      "requirement_id": "optional-id",\n'
            '      "competency_id": "optional-id",\n'
            f'      "difficulty": "{diff_str}"\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        user_prompt = (
            f"Generate exactly {count} distinct multiple-choice questions ({diff_str} level) grounded strictly in these job requirements:\n"
            f"{req_context}\n\n"
            "Rules:\n"
            "1. Each question must have 2 to 5 options with unique option_ids ('A', 'B', 'C', 'D').\n"
            "2. Exactly one option must be the correct_option_id.\n"
            "3. Provide a clear, factual answer_explanation.\n"
            "4. Return strictly valid JSON."
        )

        payload = {
            "model": self.profile.model_identifier or "default",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": max(self.profile.max_output_tokens, 2048),
            "stream": False,
        }

        raw_response = await self._send_bounded_request(chat_url, payload)

        choices = raw_response.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                safe_message="OpenAI-compatible response missing 'choices' array.",
            )

        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        message = choice.get("message", {})
        raw_content = message.get("content")
        raw_reasoning = message.get("reasoning_content")

        content_str = (raw_content or "").strip()

        if not content_str:
            if raw_reasoning or finish_reason == "length":
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.RESPONSE_CONTENT_EMPTY,
                    safe_message="The selected model used its response budget for reasoning and returned no final MCQ batch.",
                )
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_CONTENT_EMPTY,
                safe_message="OpenAI-compatible response contained null, empty, or whitespace message content.",
            )

        if finish_reason == "length":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_TRUNCATED,
                safe_message="Model MCQ generation response was truncated before completing structured output.",
            )

        clean_json_str = content_str
        if clean_json_str.startswith("```json"):
            clean_json_str = clean_json_str[7:]
        elif clean_json_str.startswith("```"):
            clean_json_str = clean_json_str[3:]
        if clean_json_str.endswith("```"):
            clean_json_str = clean_json_str[:-3]
        clean_json_str = clean_json_str.strip()

        parsed_data = None
        try:
            parsed_data = json.loads(clean_json_str)
        except Exception:
            # Fallback regex extraction for json object or array
            import re
            match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", content_str)
            if match:
                try:
                    parsed_data = json.loads(match.group(1).strip())
                except Exception as e:
                    logger.warning("Failed to parse regex-extracted JSON from provider MCQ response: %s", e)
            
            if parsed_data is None:
                logger.warning("Failed to parse JSON from provider MCQ response: %s", content_str[:200])
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.RESPONSE_SCHEMA_INVALID,
                    safe_message="Provider MCQ response content could not be parsed into valid JSON structure.",
                )

        if isinstance(parsed_data, list):
            parsed_data = {"questions": parsed_data}
        elif not isinstance(parsed_data, dict):
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_SCHEMA_INVALID,
                safe_message="Provider MCQ response root is not a JSON object or array.",
            )

        if "questions" not in parsed_data and any(k in parsed_data for k in ("mcqs", "items", "data")):
            for alt_key in ("mcqs", "items", "data"):
                if alt_key in parsed_data and isinstance(parsed_data[alt_key], list):
                    parsed_data["questions"] = parsed_data[alt_key]
                    break

        if isinstance(parsed_data.get("questions"), list):
            for q_dict in parsed_data["questions"]:
                if isinstance(q_dict, dict) and "options" in q_dict:
                    raw_opts = q_dict.get("options")
                    if isinstance(raw_opts, list):
                        norm_opts = []
                        for opt_idx, o in enumerate(raw_opts):
                            if isinstance(o, str):
                                default_id = chr(ord("A") + opt_idx)
                                norm_opts.append({"option_id": default_id, "text": o.strip()})
                            elif isinstance(o, dict):
                                o_id = str(o.get("option_id") or o.get("id") or o.get("key") or chr(ord("A") + opt_idx)).strip().upper()
                                o_text = str(o.get("text") or o.get("label") or o.get("value") or "").strip()
                                norm_opts.append({"option_id": o_id, "text": o_text})
                        q_dict["options"] = norm_opts
                    
                    # Normalize correct_option_id
                    corr_id = str(q_dict.get("correct_option_id") or q_dict.get("correct_option") or q_dict.get("answer") or "A").strip().upper()
                    # If model returned text instead of ID (e.g. "A" vs the option text)
                    matched_id = None
                    for opt in q_dict.get("options", []):
                        if opt["option_id"] == corr_id:
                            matched_id = opt["option_id"]
                            break
                        elif opt["text"].strip().lower() == corr_id.strip().lower():
                            matched_id = opt["option_id"]
                            break
                    q_dict["correct_option_id"] = matched_id or (q_dict["options"][0]["option_id"] if q_dict.get("options") else "A")

        try:
            structured_response = StructuredAiMcqBatchResponse.model_validate(parsed_data)
            return structured_response
        except Exception as e:
            logger.warning("Provider MCQ response failed schema validation: %s", e)
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_SCHEMA_INVALID,
                safe_message=f"Provider MCQ response schema validation failed: {str(e)}",
            )

    async def request_job_grounded_generation(
        self,
        job_context: Dict[str, Any],
        slots_context: List[Dict[str, Any]],
        interview_mode: str = "TEXT",
        existing_text_stems: Optional[List[str]] = None,
    ) -> Any:
        """Executes structured batch job-grounded question generation for planned slots."""
        if not self.profile.enabled:
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_DISABLED,
                safe_message="Target provider profile is currently disabled.",
            )

        normalized_base = self._validate_local_endpoint()
        chat_url = f"{normalized_base}/chat/completions"

        from app.services.wording_prompt_package import (
            assemble_job_grounded_generation_messages,
            assemble_job_grounded_repair_messages,
        )
        from app.schemas.question_generation_contract import StrictJobGroundedGenerationBatchResponse

        messages = assemble_job_grounded_generation_messages(
            job_context,
            slots_context,
            interview_mode=interview_mode,
            existing_text_stems=existing_text_stems,
        )

        payload = {
            "model": self.profile.model_identifier or "default",
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": max(self.profile.max_output_tokens, 2048),
            "stream": False,
        }

        raw_response = await self._send_bounded_request(chat_url, payload)

        choices = raw_response.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise ProviderTransportError(
                error_code=ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                safe_message="OpenAI-compatible response missing 'choices' array.",
            )

        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        message = choice.get("message", {})
        raw_content = message.get("content")
        raw_reasoning = message.get("reasoning_content")

        content_str = (raw_content or "").strip()

        if not content_str:
            if raw_reasoning or finish_reason == "length":
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.RESPONSE_CONTENT_EMPTY,
                    safe_message="The selected model used its response budget for reasoning and returned no final questions.",
                )
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_CONTENT_EMPTY,
                safe_message="OpenAI-compatible response contained null or whitespace message content.",
            )

        if finish_reason == "length":
            raise ProviderTransportError(
                error_code=ProviderErrorCode.RESPONSE_TRUNCATED,
                safe_message="Model generation response was truncated before completing structured output.",
            )

        clean_json_str = content_str
        if clean_json_str.startswith("```json"):
            clean_json_str = clean_json_str[7:]
        elif clean_json_str.startswith("```"):
            clean_json_str = clean_json_str[3:]
        if clean_json_str.endswith("```"):
            clean_json_str = clean_json_str[:-3]
        clean_json_str = clean_json_str.strip()

        try:
            parsed_data = json.loads(clean_json_str)
            return StrictJobGroundedGenerationBatchResponse.model_validate(parsed_data)
        except Exception as first_err:
            logger.warning("First generation output failed validation (%s). Attempting 1 controlled repair...", first_err)
            
            # Attempt 1 controlled repair
            repair_msgs = assemble_job_grounded_repair_messages(
                invalid_raw_content=clean_json_str,
                validation_error_codes=[str(first_err)],
                slots_context=slots_context,
            )
            repair_payload = {
                "model": self.profile.model_identifier or "default",
                "messages": repair_msgs,
                "temperature": 0.1,
                "max_tokens": max(self.profile.max_output_tokens, 2048),
                "stream": False,
            }
            repair_resp = await self._send_bounded_request(chat_url, repair_payload)
            r_choices = repair_resp.get("choices", [])
            if not r_choices:
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.RESPONSE_SCHEMA_INVALID,
                    safe_message="Controlled repair failed: empty choices array.",
                )
            r_content = (r_choices[0].get("message", {}).get("content") or "").strip()
            if r_content.startswith("```json"):
                r_content = r_content[7:]
            elif r_content.startswith("```"):
                r_content = r_content[3:]
            if r_content.endswith("```"):
                r_content = r_content[:-3]
            r_content = r_content.strip()

            try:
                r_parsed = json.loads(r_content)
                return StrictJobGroundedGenerationBatchResponse.model_validate(r_parsed)
            except Exception as repair_err:
                logger.warning("Controlled repair failed schema validation: %s", repair_err)
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.RESPONSE_SCHEMA_INVALID,
                    safe_message=f"Job-grounded generation output failed schema validation after controlled repair: {str(repair_err)}",
                )


    async def reword_question(
        self,
        request: ProviderCapabilityRequest,
    ) -> ProviderCapabilityResponse:
        """Legacy capability dispatch seam maintained for backwards compatibility."""
        now = datetime.now(timezone.utc)

        if not self.profile.enabled:
            return ProviderCapabilityResponse(
                correlation_id=request.correlation_id,
                operation=request.operation,
                validation_state=ValidationState.BLOCKED,
                error_code=ProviderErrorCode.PROVIDER_DISABLED,
                safe_error_message="Target provider profile is currently disabled.",
                fallback_used=True,
                policy_reference=request.policy_reference,
                completed_at=now,
            )

        try:
            normalized_base = self._validate_local_endpoint()
            chat_url = f"{normalized_base}/chat/completions"

            orig_text = request.reword_payload.original_text if request.reword_payload else ""
            intent = request.reword_payload.question_intent if request.reword_payload else ""

            payload = {
                "model": self.profile.model_identifier or "default",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a professional hiring assistant. Reword interview questions clearly and concisely while preserving exact intent. No tools. No markdown."
                    },
                    {
                        "role": "user",
                        "content": f"Reword this interview question for tone: {orig_text}\nIntent: {intent}"
                    }
                ],
                "temperature": 0.2,
                "max_tokens": min(self.profile.max_output_tokens, 600),
                "stream": False,
            }

            raw_response = await self._send_bounded_request(chat_url, payload)

            choices = raw_response.get("choices")
            if not choices or not isinstance(choices, list) or len(choices) == 0:
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.PROVIDER_INVALID_RESPONSE,
                    safe_message="OpenAI-compatible response missing 'choices' array.",
                )

            choice = choices[0]
            finish_reason = choice.get("finish_reason")
            message = choice.get("message", {})
            content = (message.get("content") or "").strip()
            raw_reasoning = message.get("reasoning_content")

            if not content:
                if raw_reasoning or finish_reason == "length":
                    raise ProviderTransportError(
                        error_code=ProviderErrorCode.RESPONSE_CONTENT_EMPTY,
                        safe_message="The selected model used its response budget for reasoning and returned no final wording. Select a compatible Instruct model and retry. Your deterministic questions were retained.",
                    )
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.RESPONSE_CONTENT_EMPTY,
                    safe_message="OpenAI-compatible response contained empty message content.",
                )

            if finish_reason == "length":
                raise ProviderTransportError(
                    error_code=ProviderErrorCode.RESPONSE_TRUNCATED,
                    safe_message="Model response was truncated before completion.",
                )

            return ProviderCapabilityResponse(
                correlation_id=request.correlation_id,
                operation=request.operation,
                validation_state=ValidationState.PASSED,
                error_code=None,
                safe_error_message=None,
                fallback_used=False,
                reword_result=ProviderRewordResult(
                    reworded_text=content,
                    intent_preserved=True,
                    authoring_origin=AuthoringOrigin.AI_ASSISTED,
                ),
                policy_reference=request.policy_reference,
                completed_at=datetime.now(timezone.utc),
            )

        except ProviderTransportError as e:
            return ProviderCapabilityResponse(
                correlation_id=request.correlation_id,
                operation=request.operation,
                validation_state=ValidationState.BLOCKED,
                error_code=e.error_code,
                safe_error_message=e.safe_message,
                fallback_used=True,
                policy_reference=request.policy_reference,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.exception("Unexpected error in local provider dispatch: %s", e)
            return ProviderCapabilityResponse(
                correlation_id=request.correlation_id,
                operation=request.operation,
                validation_state=ValidationState.BLOCKED,
                error_code=ProviderErrorCode.PROVIDER_UNAVAILABLE,
                safe_error_message="Unexpected internal error during local provider dispatch.",
                fallback_used=True,
                policy_reference=request.policy_reference,
                completed_at=datetime.now(timezone.utc),
            )
