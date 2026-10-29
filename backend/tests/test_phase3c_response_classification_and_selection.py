import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.auth import Base, UserRecord, UserRole
from app.models.interview import (
    CandidateInterviewPlanRecord,
    InterviewQuestionRecord,
    InterviewRubricRecord,
    JobInterviewTemplateRecord,
)
from app.models.job import JobLifecycleStatus, JobRequisition, JobVersion, JobVersionStatus
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    PlanLifecycle,
    ProviderCompatibilityStatus,
    ProviderEgressClass,
    ProviderErrorCode,
    ProviderOperation,
    ProviderType,
    QuestionType,
    ValidationState,
)
from app.schemas.provider_admin import SelectProviderModelRequest
from app.schemas.provider_capability import ProviderCapabilityRequest, RewordQuestionPayload
from app.schemas.provider_profile import ProviderProfile
from app.schemas.wording_contract import BoundedWordingRequest, BoundedWordingResponse
from app.services.provider_compatibility_service import ProviderCompatibilityService
from app.services.provider_registry import ProviderRegistry
from app.services.wording_assistance_service import generate_template_ai_wording
from app.services.wording_provider import LocalOpenAICompatibleProvider


# =========================================================================
# 1. Response Classification Tests
# =========================================================================

def test_response_classification_empty_content():
    """HTTP 200 with empty string or whitespace content must be rejected with RESPONSE_CONTENT_EMPTY."""
    async def _run():
        profile = ProviderProfile(
            display_name="LM Studio Empty Content Test",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:1234/v1",
            model_identifier="qwen/qwen3-4b-thinking-2507",
            egress_class=ProviderEgressClass.LOCAL_ONLY,
            enabled=True,
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={
                    "id": "chatcmpl-empty-1",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "   ",
                                "reasoning_content": "Thinking tokens generated here...",
                            },
                            "finish_reason": "length",
                        }
                    ],
                },
            )

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as mock_client:
            provider = LocalOpenAICompatibleProvider(profile=profile, http_client=mock_client)
            req = ProviderCapabilityRequest(
                correlation_id=uuid.uuid4(),
                operation=ProviderOperation.REWORD_QUESTION,
                reword_payload=RewordQuestionPayload(
                    original_text="Explain database index optimizations.",
                    question_intent="Assess database indexing skill",
                ),
            )
            resp = await provider.reword_question(req)
            assert resp.validation_state == ValidationState.BLOCKED
            assert resp.error_code == ProviderErrorCode.RESPONSE_CONTENT_EMPTY
            assert resp.fallback_used is True
            # Assert reasoning content was never leaked into safe_error_message or reword_result
            assert "Thinking tokens" not in (resp.safe_error_message or "")
            assert resp.reword_result is None

    asyncio.run(_run())


def test_response_classification_truncated_finish_reason():
    """HTTP 200 with finish_reason 'length' before valid JSON completion must be rejected with RESPONSE_TRUNCATED."""
    async def _run():
        profile = ProviderProfile(
            display_name="LM Studio Truncated Test",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:1234/v1",
            model_identifier="qwen/qwen3-4b-thinking-2507",
            egress_class=ProviderEgressClass.LOCAL_ONLY,
            enabled=True,
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={
                    "id": "chatcmpl-trunc-1",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": '{"operation": "REWORD_QUESTION", "variants": [{"variant_text": "Incompl',
                            },
                            "finish_reason": "length",
                        }
                    ],
                },
            )

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as mock_client:
            provider = LocalOpenAICompatibleProvider(profile=profile, http_client=mock_client)
            req = ProviderCapabilityRequest(
                correlation_id=uuid.uuid4(),
                operation=ProviderOperation.REWORD_QUESTION,
                reword_payload=RewordQuestionPayload(
                    original_text="Explain database index optimizations.",
                    question_intent="Assess database indexing skill",
                ),
            )
            resp = await provider.reword_question(req)
            assert resp.validation_state == ValidationState.BLOCKED
            assert resp.error_code == ProviderErrorCode.RESPONSE_TRUNCATED
            assert resp.fallback_used is True

    asyncio.run(_run())


def test_response_classification_invalid_json_schema():
    """HTTP 200 with invalid JSON schema must be rejected with RESPONSE_SCHEMA_INVALID."""
    async def _run():
        profile = ProviderProfile(
            display_name="LM Studio Schema Invalid Test",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:1234/v1",
            model_identifier="qwen3-4b-instruct",
            egress_class=ProviderEgressClass.LOCAL_ONLY,
            enabled=True,
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                json={
                    "id": "chatcmpl-invalid-1",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "I would reword this question by asking: Can you describe your database tuning experience?",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as mock_client:
            provider = LocalOpenAICompatibleProvider(profile=profile, http_client=mock_client)
            wording_req = BoundedWordingRequest(
                source_question_id=uuid.uuid4(),
                source_question_version=1,
                deterministic_question_text="Explain database index optimizations.",
                question_intent="Assess database indexing skill.",
                question_type=QuestionType.TECHNICAL,
                layer=InterviewLayer.COMMON_CORE,
                competency_label="Database Indexing",
            )
            with pytest.raises(Exception) as exc_info:
                await provider.request_wording_assistance(wording_req)
            assert exc_info.value.error_code == ProviderErrorCode.RESPONSE_SCHEMA_INVALID

    asyncio.run(_run())


# =========================================================================
# 2. Compatibility Service Synthetic Test Suite
# =========================================================================

def test_compatibility_service_rejects_reasoning_only_model():
    """ProviderCompatibilityService marks profile INCOMPATIBLE if Step C returns empty content or finish_reason: length."""
    async def _run():
        profile = ProviderProfile(
            display_name="LM Studio Thinking Model Profile",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:1234/v1",
            model_identifier="qwen/qwen3-4b-thinking-2507",
            egress_class=ProviderEgressClass.LOCAL_ONLY,
            enabled=False,
        )

        def mock_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/models":
                return httpx.Response(
                    status_code=200,
                    json={"data": [{"id": "qwen/qwen3-4b-thinking-2507", "object": "model"}]},
                )
            elif request.url.path == "/v1/chat/completions":
                body = json.loads(request.content.decode("utf-8"))
                messages = body.get("messages", [])
                system_content = next((m["content"] for m in messages if m.get("role") == "system"), "")
                if "wording assistant" in system_content:
                    # Simulate Thinking model behavior: empty content, finish_reason: length
                    return httpx.Response(
                        status_code=200,
                        json={
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": "",
                                        "reasoning_content": "Thinking tokens consumed budget...",
                                    },
                                    "finish_reason": "length",
                                }
                            ]
                        },
                    )
                # Step B dry run returns standard OK
                return httpx.Response(status_code=200, json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
            return httpx.Response(status_code=404)

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as mock_client:
            svc = ProviderCompatibilityService(http_client=mock_client)
            report = await svc.test_compatibility(profile)

            assert report.is_compatible is False
            assert report.compatibility_status == ProviderCompatibilityStatus.INCOMPATIBLE
            assert report.reachability_passed is True
            assert report.model_discovered is True
            assert report.dry_run_completion_passed is True
            assert report.question_wording_passed is False
            assert report.safe_failure_code == ProviderErrorCode.PROFILE_MODE_INCOMPATIBLE.value
            assert "Instruct" in (report.recommended_action or "")

    asyncio.run(_run())


def test_compatibility_service_passes_instruct_model():
    """ProviderCompatibilityService marks profile HEALTHY and is_compatible=True when Step C returns valid JSON schema."""
    async def _run():
        profile = ProviderProfile(
            display_name="LM Studio Instruct Model Profile",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:1234/v1",
            model_identifier="qwen/qwen3-4b-2507",
            egress_class=ProviderEgressClass.LOCAL_ONLY,
            enabled=False,
        )

        valid_json = json.dumps({
            "operation": "REWORD_QUESTION",
            "response_schema_version": "1.0.0",
            "variants": [
                {
                    "variant_text": "Could you provide an example of how you optimized a database query?",
                    "rationale_code": "PROFESSIONAL_TONE",
                    "referenced_identifiers": [],
                }
            ],
        })

        def mock_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/models":
                return httpx.Response(
                    status_code=200,
                    json={"data": [{"id": "qwen/qwen3-4b-2507", "object": "model"}]},
                )
            elif request.url.path == "/v1/chat/completions":
                body = json.loads(request.content.decode("utf-8"))
                messages = body.get("messages", [])
                system_content = next((m["content"] for m in messages if m.get("role") == "system"), "")
                if "wording assistant" in system_content:
                    return httpx.Response(
                        status_code=200,
                        json={
                            "choices": [
                                {
                                    "message": {"role": "assistant", "content": valid_json},
                                    "finish_reason": "stop",
                                }
                            ]
                        },
                    )
                return httpx.Response(status_code=200, json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
            return httpx.Response(status_code=404)

        transport = httpx.MockTransport(mock_handler)
        async with httpx.AsyncClient(transport=transport) as mock_client:
            svc = ProviderCompatibilityService(http_client=mock_client)
            report = await svc.test_compatibility(profile)

            assert report.is_compatible is True
            assert report.compatibility_status == ProviderCompatibilityStatus.HEALTHY
            assert report.reachability_passed is True
            assert report.model_discovered is True
            assert report.question_wording_passed is True
            assert report.safe_failure_code is None

    asyncio.run(_run())


# =========================================================================
# 3. Batch Fast-Fail & Deterministic Fallback Retention Tests
# =========================================================================

def test_batch_fast_fail_on_provider_wide_failure():
    """When question 1 encounters a provider-wide failure, remaining questions fast-fail and retain deterministic text."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()

    try:
        # Seed test user, job, template, questions
        recruiter = UserRecord(
            user_id=uuid.uuid4(),
            email="recruiter@example.com",
            display_name="Recruiter",
            password_hash="hash",
            role=UserRole.RECRUITER,
            is_active=True,
        )
        db.add(recruiter)

        job = JobRequisition(
            job_id=uuid.uuid4(),
            created_by_user_id=recruiter.user_id,
            status=JobLifecycleStatus.ACTIVE,
        )
        db.add(job)
        db.flush()

        job_version = JobVersion(
            job_version_id=uuid.uuid4(),
            job_id=job.job_id,
            version_number=1,
            title="Backend Engineer",
            version_status=JobVersionStatus.APPROVED,
            created_by_user_id=recruiter.user_id,
        )
        db.add(job_version)
        db.flush()

        template = JobInterviewTemplateRecord(
            template_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            job_id=job.job_id,
            job_version_id=job_version.job_version_id,
            revision=1,
            template_version=1,
            lifecycle_status=PlanLifecycle.DRAFT,
            guardrail_policy_version="1.0.0",
            guardrail_policy_hash="a" * 64,
            deterministic_builder_version="1.0.0",
            question_catalogue_version="1.0.0",
            rubric_catalogue_version="1.0.0",
            input_snapshot_hash="b" * 64,
            content_hash="c" * 64,
            created_by_user_id=recruiter.user_id,
            record_version=1,
        )
        db.add(template)
        db.flush()

        questions = []
        for i in range(5):
            q = InterviewQuestionRecord(
                question_id=uuid.uuid4(),
                template_id=template.template_id,
                question_version=1,
                layer=InterviewLayer.COMMON_CORE,
                question_type=QuestionType.TECHNICAL,
                authoring_origin=AuthoringOrigin.DETERMINISTIC,
                display_order=i,
                competency_id=f"COMP-{i}",
                requirement_ids=[],
                question_text=f"Original deterministic question {i + 1} text",
                question_intent=f"Intent {i + 1}",
                estimated_time_minutes=5,
                included_in_common_score=True,
                validation_state=ValidationState.PASSED,
            )
            db.add(q)
            questions.append(q)
        db.commit()

        # Mock provider that fails with RESPONSE_CONTENT_EMPTY
        call_count = 0

        def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                status_code=200,
                json={
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": ""},
                            "finish_reason": "length",
                        }
                    ]
                },
            )

        profile = ProviderProfile(
            profile_id=uuid.uuid4(),
            organization_id=template.organization_id,
            display_name="LM Studio Thinking",
            provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:1234/v1",
            model_identifier="qwen/qwen3-4b-thinking-2507",
            enabled=True,
            compatibility_status=ProviderCompatibilityStatus.COMPATIBLE,
        )

        transport = httpx.MockTransport(mock_handler)
        mock_client = httpx.AsyncClient(transport=transport)
        provider = LocalOpenAICompatibleProvider(profile=profile, http_client=mock_client)

        async def _run():
            from app.schemas.interview_api import GenerateTemplateAiWordingRequest

            req_dto = GenerateTemplateAiWordingRequest(expected_template_version=1)
            res = await generate_template_ai_wording(
                actor=recruiter,
                template_id=template.template_id,
                request=req_dto,
                db=db,
                custom_provider=provider,
            )
            # Batch fast-fail must make exactly 1 network call, not 5
            assert call_count == 1
            assert res.ai_generated_count == 0
            assert res.fallback_count == 5
            assert "The selected model used its response budget for reasoning" in res.safe_message

            # Verify all 5 questions retain deterministic text in database
            db_questions = db.query(InterviewQuestionRecord).filter_by(template_id=template.template_id).order_by(InterviewQuestionRecord.display_order).all()
            for idx, q_rec in enumerate(db_questions):
                assert q_rec.question_text == f"Original deterministic question {idx + 1} text"
                assert q_rec.authoring_origin == AuthoringOrigin.DETERMINISTIC

        asyncio.run(_run())

    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


# =========================================================================
# 4. Model Selection & Persistence Tests
# =========================================================================

def test_registry_set_profile_model_persists():
    """ProviderRegistry.set_profile_model updates model identifier and increments version."""
    registry = ProviderRegistry()
    actor_id = uuid.uuid4()

    profile = ProviderProfile(
        profile_id=uuid.uuid4(),
        display_name="Local LM Studio",
        provider_type=ProviderType.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:1234/v1",
        model_identifier="qwen/qwen3-4b-thinking-2507",
        egress_class=ProviderEgressClass.LOCAL_ONLY,
        enabled=True,
        version=1,
    )
    registry._profiles[profile.profile_id] = profile

    updated = registry.set_profile_model(
        profile_id=profile.profile_id,
        model_identifier="qwen/qwen3-4b-2507",
        actor_id=actor_id,
    )

    assert updated.model_identifier == "qwen/qwen3-4b-2507"
    assert updated.version == 2
    assert updated.compatibility_status == ProviderCompatibilityStatus.NOT_TESTED

    # Verify lookup from registry
    retrieved = registry.get_profile(profile.profile_id)
    assert retrieved is not None
    assert retrieved.model_identifier == "qwen/qwen3-4b-2507"
