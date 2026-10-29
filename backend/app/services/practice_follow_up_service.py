"""Bounded domain service for candidate practice conversational follow-up evaluation and generation."""
import asyncio
import json
import logging
import re
from typing import Any, Dict, Optional
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.interview import ProviderProfileRecord
from app.schemas.phase3_enums import (
    ProviderCompatibilityStatus,
    ProviderOperation,
    ProviderType,
)
from app.schemas.practice_follow_up_contract import (
    BoundedPracticeFollowUpRequest,
    BoundedPracticeFollowUpResponse,
)

logger = logging.getLogger("ai_hiring.services.practice_follow_up")

# Prohibited semantic patterns for safety
PROHIBITED_TERMS = [
    r"\b(salary|compensation|equity|wage|hourly rate|stock options)\b",
    r"\b(race|ethnicity|black|white|asian|hispanic|latino|caucasian)\b",
    r"\b(gender|male|female|non-binary|sexual orientation|lgbtq|transgender)\b",
    r"\b(religion|religious|christian|muslim|jewish|hindu|atheist)\b",
    r"\b(disability|disabled|handicap|medical condition|pregnant|pregnancy)\b",
    r"\b(age|elderly|young|retirement|birth year)\b",
    r"\b(culture fit|vibes|personality|party|drink)\b",
    r"\b(you pass|you failed|score|scored|scoring|rubric|points out of|rating|hired|rejected|verdict)\b",
    r"\b(system prompt|ignore previous instructions|DAN mode|jailbreak)\b",
]

PROHIBITED_REGEX = re.compile("|".join(PROHIBITED_TERMS), re.IGNORECASE)

DEFAULT_FALLBACK_ACKNOWLEDGEMENT = "Thank you for explaining that."


def validate_follow_up_semantics(
    res: BoundedPracticeFollowUpResponse,
    req: BoundedPracticeFollowUpRequest,
) -> bool:
    """Performs strict semantic and safety validation on model output."""
    if res.source_question_id != req.source_question_id:
        logger.warning("Follow-up validation failed: source_question_id mismatch.")
        return False

    if res.action == "FOLLOW_UP":
        if not res.follow_up_question or not res.follow_up_question.strip():
            logger.warning("Follow-up validation failed: empty follow-up question for FOLLOW_UP action.")
            return False

        fq = res.follow_up_question.strip()
        # Check prohibited terms
        if PROHIBITED_REGEX.search(fq) or PROHIBITED_REGEX.search(res.acknowledgement):
            logger.warning("Follow-up validation failed: prohibited topic/term detected.")
            return False

        # Must not contain multiple question marks (at most 1 question)
        if fq.count("?") > 1:
            logger.warning("Follow-up validation failed: multiple questions detected.")
            return False

        # Must not contain raw code blocks or html
        if "<script" in fq.lower() or "```" in fq or "<html" in fq.lower():
            logger.warning("Follow-up validation failed: markup or code blocks detected.")
            return False

    return True


async def request_practice_conversation_follow_up(
    request: BoundedPracticeFollowUpRequest,
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> BoundedPracticeFollowUpResponse:
    """Calls local AI provider for conversational follow-up or falls back to deterministic neutral acknowledgement."""
    # 1. If follow-up is not available, immediately return deterministic continuation
    if not request.follow_up_available:
        return BoundedPracticeFollowUpResponse(
            action="ACKNOWLEDGE_AND_CONTINUE",
            source_question_id=request.source_question_id,
            acknowledgement=DEFAULT_FALLBACK_ACKNOWLEDGEMENT,
            follow_up_question=None,
            validation_passed=True,
            safe_fallback_used=False,
        )

    # 2. Check if active provider profile is enabled
    from app.config import get_settings
    settings = get_settings()

    stmt = (
        select(ProviderProfileRecord)
        .where(
            ProviderProfileRecord.enabled.is_(True),
            ProviderProfileRecord.is_archived.is_(False),
        )
        .order_by(ProviderProfileRecord.created_at.desc())
    )
    active_profile = db.execute(stmt).scalars().first()

    endpoint_url = (
        custom_endpoint_url
        or (active_profile.base_url if active_profile and active_profile.base_url else None)
        or (settings.llm_base_url if settings.llm_base_url else "http://localhost:1234/v1")
    )
    if not endpoint_url.endswith("/chat/completions"):
        endpoint_url = endpoint_url.rstrip("/") + "/chat/completions"

    model_name = (
        (active_profile.model_identifier if active_profile and active_profile.model_identifier else None)
        or (settings.llm_model if settings.llm_model else "local-model")
    )
    timeout_sec = min(
        active_profile.request_timeout_seconds if active_profile and active_profile.request_timeout_seconds else 15.0,
        30.0,
    )

    # 3. Assemble prompt
    system_prompt = (
        "You are an objective, encouraging technical interview assistant conducting a practice session. "
        "Review the candidate's answer to the primary question on the given competency. "
        "If probing deeper on a specific technical detail or design trade-off will help them practice further, "
        "respond with action 'FOLLOW_UP' and a single, concise follow-up question. "
        "If their answer is complete, brief, or off-topic, respond with action 'ACKNOWLEDGE_AND_CONTINUE'.\n\n"
        "STRICT OUTPUT REQUIREMENTS:\n"
        "Return ONLY a JSON object with this exact structure:\n"
        "{\n"
        '  "action": "FOLLOW_UP" | "ACKNOWLEDGE_AND_CONTINUE",\n'
        '  "source_question_id": "<exact input source_question_id>",\n'
        '  "acknowledgement": "<1-2 sentence neutral acknowledgement>",\n'
        '  "follow_up_question": "<single targeted question or null>"\n'
        "}\n\n"
        "PROHIBITIONS:\n"
        "- Do NOT score, rate, evaluate, pass/fail, or make hiring decisions.\n"
        "- Do NOT mention protected attributes, salary, culture fit, or other candidates.\n"
        "- Do NOT include markdown, code blocks, or HTML. Plain text only."
    )

    user_payload = {
        "source_question_id": request.source_question_id,
        "competency_name": request.competency_name,
        "primary_question_wording": request.question_wording,
        "candidate_answer": request.candidate_answer,
    }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload)},
    ]

    req_body = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 250,
        "stream": False,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            response = await client.post(
                endpoint_url,
                json=req_body,
                headers={"Content-Type": "application/json"},
            )

        if response.status_code != 200:
            logger.warning(f"Provider returned HTTP {response.status_code}. Using fallback.")
            return BoundedPracticeFollowUpResponse(
                action="ACKNOWLEDGE_AND_CONTINUE",
                source_question_id=request.source_question_id,
                acknowledgement=DEFAULT_FALLBACK_ACKNOWLEDGEMENT,
                follow_up_question=None,
                validation_passed=True,
                safe_fallback_used=True,
            )

        resp_data = response.json()
        raw_content = resp_data["choices"][0]["message"]["content"]
        
        # Clean potential markdown fences
        clean_json_str = raw_content.strip()
        if clean_json_str.startswith("```"):
            clean_json_str = re.sub(r"^```(?:json)?\s*", "", clean_json_str)
            clean_json_str = re.sub(r"\s*```$", "", clean_json_str)

        parsed = json.loads(clean_json_str)
        action = parsed.get("action", "ACKNOWLEDGE_AND_CONTINUE")
        if action not in ["FOLLOW_UP", "ACKNOWLEDGE_AND_CONTINUE"]:
            action = "ACKNOWLEDGE_AND_CONTINUE"

        parsed_resp = BoundedPracticeFollowUpResponse(
            action=action,
            source_question_id=str(parsed.get("source_question_id", request.source_question_id)),
            acknowledgement=str(parsed.get("acknowledgement", DEFAULT_FALLBACK_ACKNOWLEDGEMENT))[:200],
            follow_up_question=str(parsed.get("follow_up_question", ""))[:500] if parsed.get("follow_up_question") else None,
            validation_passed=True,
            safe_fallback_used=False,
        )

        # 4. Semantic Validation
        if not validate_follow_up_semantics(parsed_resp, request):
            logger.info("Follow-up semantic validation failed. Falling back to deterministic continuation.")
            return BoundedPracticeFollowUpResponse(
                action="ACKNOWLEDGE_AND_CONTINUE",
                source_question_id=request.source_question_id,
                acknowledgement=DEFAULT_FALLBACK_ACKNOWLEDGEMENT,
                follow_up_question=None,
                validation_passed=False,
                safe_fallback_used=True,
            )

        return parsed_resp

    except Exception as exc:
        logger.warning(f"Provider execution failed ({type(exc).__name__}). Using deterministic fallback.")
        return BoundedPracticeFollowUpResponse(
            action="ACKNOWLEDGE_AND_CONTINUE",
            source_question_id=request.source_question_id,
            acknowledgement=DEFAULT_FALLBACK_ACKNOWLEDGEMENT,
            follow_up_question=None,
            validation_passed=True,
            safe_fallback_used=True,
        )
