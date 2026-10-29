"""Domain service for answer segmentation, deterministic insufficient check, local AI review generation, validation and repair."""
import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.candidate_access import (
    CandidatePracticeAnswerReviewRecord,
    CandidatePracticeAnswerSegmentRecord,
    CandidatePracticeMessageRecord,
    CandidatePracticeSessionRecord,
)
from app.models.interview import ProviderProfileRecord
from app.schemas.phase3_enums import (
    PracticeMessageKind,
    PracticeMessageRole,
    PracticeSessionLifecycle,
)
from app.schemas.practice_review_contract import (
    COACHING_POLICY_VERSION,
    COACHING_PROMPT_TEMPLATE_VERSION,
    PRACTICE_ANSWER_REVIEW_SCHEMA_VERSION,
    AnswerStatus,
    AnswerStrengthItem,
    ConversationalReviewPayload,
    DimensionLevel,
    FollowUpItem,
    ImprovementAreaItem,
    ReviewLifecycleStatus,
)

logger = logging.getLogger("ai_hiring.services.practice_review")

# Segment bounds
MIN_MEANINGFUL_ANSWER_LENGTH = 8
LONG_PARAGRAPH_THRESHOLD = 300
SENTENCE_SPLIT_REGEX = re.compile(r"([.!?]+(?:\s+|$))")

# Prohibited semantic patterns for strict safety
PROHIBITED_TERMS = [
    r"\b(salary|compensation|equity|wage|hourly rate|stock options)\b",
    r"\b(race|ethnicity|black|white|asian|hispanic|latino|caucasian|caste)\b",
    r"\b(gender|male|female|non-binary|sexual orientation|lgbtq|transgender)\b",
    r"\b(religion|religious|christian|muslim|jewish|hindu|atheist|synagogue|mosque|church)\b",
    r"\b(disability|disabled|handicap|medical condition|pregnant|pregnancy|illness)\b",
    r"\b(age|elderly|young|retirement|birth year|graduation year)\b",
    r"\b(culture fit|vibes|personality|party|drink|social life)\b",
    r"\b(hire|do not hire|reject|advance|pass candidate|fail candidate|hired|verdict)\b",
    r"\b(system prompt|ignore previous instructions|DAN mode|jailbreak)\b",
]
PROHIBITED_REGEX = re.compile("|".join(PROHIBITED_TERMS), re.IGNORECASE)

# Known insufficient / non-answer patterns
INSUFFICIENT_PATTERNS = [
    r"^(idk|i don'?t know|no idea|nothing|none|n/a|na|skip|pass|\.|\?|\!|-|_)$",
    r"^(asdf|qwerty|test|testing|abc|xyz|blah|foo|bar)$",
    r"\b(idk|i don'?t know|no idea|nothing|none)\b",
]
INSUFFICIENT_REGEX = re.compile("|".join(INSUFFICIENT_PATTERNS), re.IGNORECASE)



def extract_answer_segments(
    text: str,
    answer_id: uuid.UUID,
    session_id: uuid.UUID,
    question_id: uuid.UUID,
) -> List[CandidatePracticeAnswerSegmentRecord]:
    """Deterministically extracts ordered, opaque text segments with exact character offsets."""
    if not text or len(text.strip()) == 0:
        return []

    # 1. Identify raw paragraph spans [start, end]
    raw_paragraphs: List[Tuple[int, int]] = []
    para_pattern = re.compile(r"[^\r\n]+(?:\r?\n[^\r\n]+)*")
    for match in para_pattern.finditer(text):
        p_start, p_end = match.span()
        if text[p_start:p_end].strip():
            raw_paragraphs.append((p_start, p_end))

    if not raw_paragraphs:
        return []

    candidate_spans: List[Tuple[int, int]] = []
    for p_start, p_end in raw_paragraphs:
        para_text = text[p_start:p_end]
        if len(para_text) > LONG_PARAGRAPH_THRESHOLD:
            last_idx = 0
            for sent_match in SENTENCE_SPLIT_REGEX.finditer(para_text):
                sent_end_rel = sent_match.end()
                sent_text = para_text[last_idx:sent_end_rel].strip()
                if sent_text:
                    rel_start = para_text.find(sent_text, last_idx)
                    abs_start = p_start + rel_start
                    abs_end = abs_start + len(sent_text)
                    candidate_spans.append((abs_start, abs_end))
                last_idx = sent_end_rel
            remaining = para_text[last_idx:].strip()
            if remaining:
                rel_start = para_text.find(remaining, last_idx)
                abs_start = p_start + rel_start
                abs_end = abs_start + len(remaining)
                candidate_spans.append((abs_start, abs_end))
        else:
            trimmed = para_text.strip()
            if trimmed:
                rel_start = para_text.find(trimmed)
                abs_start = p_start + rel_start
                abs_end = abs_start + len(trimmed)
                candidate_spans.append((abs_start, abs_end))

    records: List[CandidatePracticeAnswerSegmentRecord] = []
    for seq_idx, (c_start, c_end) in enumerate(candidate_spans, start=1):
        seg_text = text[c_start:c_end]
        if not seg_text.strip():
            continue
        seed = f"{answer_id}:{seq_idx}:{c_start}:{c_end}:1.0.0"
        seg_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, seed)
        records.append(
            CandidatePracticeAnswerSegmentRecord(
                segment_id=seg_uuid,
                session_id=session_id,
                answer_id=answer_id,
                question_id=question_id,
                sequence_order=seq_idx,
                start_char=c_start,
                end_char=c_end,
                segment_text=seg_text,
                segmentation_version="1.0.0",
            )
        )
    return records


def is_deterministic_insufficient_response(answer_text: str, question_wording: Optional[str] = None) -> bool:
    """Detects clearly unusable/insufficient responses deterministically without penalizing concise answers."""
    cleaned = answer_text.strip()
    if not cleaned:
        return True

    # Punctuation or whitespace only
    if not re.search(r"[a-zA-Z0-9]", cleaned):
        return True

    # Check known non-answers
    if INSUFFICIENT_REGEX.match(cleaned):
        return True

    # Repeated characters like "aaaaaa" or "....."
    if len(set(cleaned.replace(" ", "").lower())) <= 2 and len(cleaned) > 5:
        return True

    # Verbatim copy of question without answer
    if question_wording and cleaned.lower() == question_wording.strip().lower():
        return True

    return False


def validate_conversational_review(
    payload: Dict[str, Any],
    source_question_id: str,
    valid_segment_ids: Set[str],
) -> Tuple[bool, List[str], Optional[ConversationalReviewPayload]]:
    """Strictly validates untrusted model response against all 24 grounding rules."""
    errors: List[str] = []

    if not isinstance(payload, dict):
        return False, ["INVALID_JSON_OBJECT"], None

    # Check extra/unexpected fields
    expected_top_keys = {
        "schema_version",
        "source_question_id",
        "answer_status",
        "acknowledgement",
        "strengths",
        "improvement_areas",
        "next_steps",
        "stronger_structure",
        "dimension_levels",
        "follow_up",
        "uncertainty_codes",
    }
    extra_keys = set(payload.keys()) - expected_top_keys
    if extra_keys:
        errors.append(f"UNEXPECTED_FIELDS: {sorted(list(extra_keys))}")

    # Schema version
    if payload.get("schema_version") != PRACTICE_ANSWER_REVIEW_SCHEMA_VERSION:
        errors.append(f"INVALID_SCHEMA_VERSION: {payload.get('schema_version')}")

    # Question ID match
    if str(payload.get("source_question_id", "")) != str(source_question_id):
        errors.append("SOURCE_QUESTION_ID_MISMATCH")

    # Answer status
    status_str = payload.get("answer_status")
    if status_str not in [e.value for e in AnswerStatus]:
        errors.append(f"INVALID_ANSWER_STATUS: {status_str}")

    # Strengths bounds
    strengths = payload.get("strengths", [])
    if not isinstance(strengths, list) or len(strengths) > 2:
        errors.append("STRENGTHS_COUNT_EXCEEDED")
    else:
        for st in strengths:
            if not isinstance(st, dict):
                errors.append("INVALID_STRENGTH_ITEM")
                continue
            seg_ids = st.get("answer_segment_ids", [])
            for sid in seg_ids:
                if str(sid) not in valid_segment_ids:
                    errors.append(f"UNKNOWN_SEGMENT_ID_IN_STRENGTH: {sid}")

    # Improvement areas bounds
    improvement_areas = payload.get("improvement_areas", [])
    if not isinstance(improvement_areas, list) or len(improvement_areas) > 3:
        errors.append("IMPROVEMENT_AREAS_COUNT_EXCEEDED")
    else:
        for imp in improvement_areas:
            if not isinstance(imp, dict):
                errors.append("INVALID_IMPROVEMENT_ITEM")
                continue
            seg_ids = imp.get("answer_segment_ids", [])
            for sid in seg_ids:
                if str(sid) not in valid_segment_ids:
                    errors.append(f"UNKNOWN_SEGMENT_ID_IN_IMPROVEMENT: {sid}")

    # Next steps count
    next_steps = payload.get("next_steps", [])
    if not isinstance(next_steps, list) or not (2 <= len(next_steps) <= 3):
        errors.append("NEXT_STEPS_COUNT_INVALID")

    # Dimension levels
    dim_levels = payload.get("dimension_levels", {})
    if not isinstance(dim_levels, dict):
        errors.append("DIMENSION_LEVELS_INVALID")
    else:
        for k, v in dim_levels.items():
            if v not in [e.value for e in DimensionLevel]:
                errors.append(f"INVALID_DIMENSION_LEVEL: {k}={v}")

    # Text safety & prohibited topics
    all_text = " ".join([
        str(payload.get("acknowledgement", "")),
        str(payload.get("stronger_structure", "")),
        " ".join([str(st.get("observation", "")) for st in strengths if isinstance(st, dict)]),
        " ".join([str(imp.get("observation", "")) for imp in improvement_areas if isinstance(imp, dict)]),
        " ".join([str(ns) for ns in next_steps if isinstance(ns, str)]),
        str(payload.get("follow_up", {}).get("question", "") if isinstance(payload.get("follow_up"), dict) else ""),
    ])

    if PROHIBITED_REGEX.search(all_text):
        errors.append("PROHIBITED_TOPIC_OR_INFERENCE_DETECTED")

    # Check for HTML or scripts
    if re.search(r"<(script|html|iframe|a\s|img\s)", all_text, re.IGNORECASE):
        errors.append("MARKUP_OR_SCRIPT_DETECTED")

    # Follow up bounds
    fu_data = payload.get("follow_up")
    if isinstance(fu_data, dict):
        if fu_data.get("needed") and fu_data.get("question"):
            fq = str(fu_data.get("question", "")).strip()
            if fq.count("?") > 1:
                errors.append("MULTIPLE_QUESTIONS_IN_FOLLOW_UP")

    if errors:
        return False, errors, None

    try:
        parsed = ConversationalReviewPayload.model_validate(payload)
        return True, [], parsed
    except Exception as exc:
        return False, [f"PYDANTIC_VALIDATION_ERROR: {str(exc)}"], None


async def generate_conversational_review_with_repair(
    source_question_id: uuid.UUID,
    question_wording: str,
    competency_name: str,
    question_type: str,
    answer_text: str,
    segments: List[CandidatePracticeAnswerSegmentRecord],
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> Tuple[ReviewLifecycleStatus, Optional[ConversationalReviewPayload], Optional[str], Optional[str]]:
    """Calls local LM Studio model, executes strict validation, and runs at most one repair if needed.
    
    Returns:
    (lifecycle_status, parsed_payload, provider_class, model_identifier)
    """
    settings = get_settings()

    # 1. Resolve Provider Profile
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
    provider_class = active_profile.provider_type if active_profile else "LOCAL_OPENAI_COMPATIBLE"
    timeout_sec = min(
        active_profile.request_timeout_seconds if active_profile and active_profile.request_timeout_seconds else 20.0,
        30.0,
    )

    valid_segment_ids = {str(seg.segment_id) for seg in segments}
    segment_payloads = [
        {"segment_id": str(seg.segment_id), "text": seg.segment_text}
        for seg in segments
    ]

    # 2. Build system and user prompt
    system_prompt = (
        "You are an objective, encouraging technical interview coaching assistant. "
        "Review the candidate's response to the practice question on the stated competency.\n\n"
        "STRICT REVIEW RULES:\n"
        "1. Explain what the candidate demonstrated (at most 2 strengths).\n"
        "2. Explain what was missing or weak (at most 3 improvement areas).\n"
        "3. Provide exactly 2 or 3 actionable next steps.\n"
        "4. Provide a concise stronger response structure without inventing candidate experience.\n"
        "5. Assign dimension levels from ['STRONG', 'DEVELOPING', 'NEEDS_DETAIL', 'NOT_DEMONSTRATED', 'NOT_APPLICABLE'] for relevance, clarity, technical_depth, evidence.\n"
        "6. In 'strengths' and 'improvement_areas', cite ONLY valid segment IDs from the provided answer_segments.\n"
        "7. If a targeted clarification would materially help practice, set follow_up.needed=true and supply at most 1 single question; otherwise needed=false and question=null.\n"
        "8. Return plain JSON matching schema_version 'practice-answer-review.v1'. No markdown, no code fences, no chain-of-thought.\n"
        "9. NEVER evaluate hiring decisions, scores, ranks, personality, emotion, or protected attributes."
    )

    user_payload = {
        "schema_version": PRACTICE_ANSWER_REVIEW_SCHEMA_VERSION,
        "source_question_id": str(source_question_id),
        "competency": competency_name,
        "question_type": question_type,
        "question_wording": question_wording,
        "candidate_answer_full": answer_text,
        "answer_segments": segment_payloads,
    }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload)},
    ]

    req_body = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 600,
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
            logger.warning("Provider returned HTTP %d on initial review call.", response.status_code)
            return ReviewLifecycleStatus.REVIEW_FAILED_RETRYABLE, None, provider_class, model_name

        resp_json = response.json()
        raw_content = resp_json["choices"][0]["message"]["content"]
        clean_content = raw_content.strip()
        if clean_content.startswith("```"):
            clean_content = re.sub(r"^```(?:json)?\s*", "", clean_content)
            clean_content = re.sub(r"\s*```$", "", clean_content)

        parsed_data = json.loads(clean_content)
        is_valid, val_errors, valid_payload = validate_conversational_review(
            parsed_data, str(source_question_id), valid_segment_ids
        )
        if is_valid and valid_payload:
            return ReviewLifecycleStatus.REVIEW_READY, valid_payload, provider_class, model_name

        logger.info("First review output invalid (%s). Attempting 1 controlled repair.", val_errors)

        # 3. Controlled Repair (at most 1)
        repair_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload)},
            {"role": "assistant", "content": clean_content},
            {
                "role": "user",
                "content": json.dumps({
                    "action": "SCHEMA_AND_GROUNDING_REPAIR",
                    "validation_errors": val_errors,
                    "instruction": "Fix validation errors. Ensure citations reference provided segment_ids only. Return plain JSON only.",
                }),
            },
        ]

        repair_body = {
            "model": model_name,
            "messages": repair_messages,
            "temperature": 0.1,
            "max_tokens": 600,
            "stream": False,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            repair_resp = await client.post(
                endpoint_url,
                json=repair_body,
                headers={"Content-Type": "application/json"},
            )

        if repair_resp.status_code != 200:
            logger.warning("Provider returned HTTP %d on review repair call.", repair_resp.status_code)
            return ReviewLifecycleStatus.REVIEW_FAILED_RETRYABLE, None, provider_class, model_name

        repair_json = repair_resp.json()
        repair_raw = repair_json["choices"][0]["message"]["content"].strip()
        if repair_raw.startswith("```"):
            repair_raw = re.sub(r"^```(?:json)?\s*", "", repair_raw)
            repair_raw = re.sub(r"\s*```$", "", repair_raw)

        repair_parsed = json.loads(repair_raw)
        rep_valid, rep_errors, rep_payload = validate_conversational_review(
            repair_parsed, str(source_question_id), valid_segment_ids
        )
        if rep_valid and rep_payload:
            return ReviewLifecycleStatus.REVIEW_READY, rep_payload, provider_class, model_name

        logger.warning("Repair also failed validation: %s", rep_errors)
        return ReviewLifecycleStatus.REVIEW_FAILED_RETRYABLE, None, provider_class, model_name

    except Exception as exc:
        logger.warning("Local AI review generation exception: %s", type(exc).__name__)
        return ReviewLifecycleStatus.REVIEW_FAILED_RETRYABLE, None, provider_class, model_name


def create_insufficient_response_review(
    source_question_id: uuid.UUID,
    session_id: uuid.UUID,
    answer_id: uuid.UUID,
    idempotency_key: str,
) -> CandidatePracticeAnswerReviewRecord:
    """Creates a deterministic honest review record for an unusable/insufficient candidate response."""
    now = datetime.now(timezone.utc)
    return CandidatePracticeAnswerReviewRecord(
        review_id=uuid.uuid4(),
        session_id=session_id,
        question_id=source_question_id,
        answer_id=answer_id,
        idempotency_key=idempotency_key,
        schema_version=PRACTICE_ANSWER_REVIEW_SCHEMA_VERSION,
        lifecycle_status=ReviewLifecycleStatus.REVIEW_READY.value,
        answer_status=AnswerStatus.INSUFFICIENT_RESPONSE.value,
        acknowledgement="Your response was too brief or incomplete to provide fine-grained technical coaching.",
        strengths=[],
        improvement_areas=[
            {
                "dimension": "evidence_depth",
                "observation": "Response did not provide technical details or demonstrable context for this scenario.",
                "answer_segment_ids": [],
            }
        ],
        next_steps=[
            "Provide specific technical actions you personally executed.",
            "Describe the measurable outcome or verification process.",
        ],
        stronger_structure="State the problem situation, your direct technical contribution, and how you validated the outcome.",
        dimension_levels={
            "relevance": DimensionLevel.NOT_DEMONSTRATED.value,
            "clarity": DimensionLevel.NOT_DEMONSTRATED.value,
            "technical_depth": DimensionLevel.NOT_DEMONSTRATED.value,
            "evidence": DimensionLevel.NOT_DEMONSTRATED.value,
        },
        follow_up_needed=False,
        follow_up_question=None,
        uncertainty_codes=["INSUFFICIENT_RESPONSE_EVIDENCE"],
        provider_class="DETERMINISTIC_INSUFFICIENT_CLASSIFIER",
        model_identifier="deterministic-rule",
        prompt_template_version=COACHING_PROMPT_TEMPLATE_VERSION,
        guardrail_policy_version=COACHING_POLICY_VERSION,
        validation_status="PASSED",
        retry_count=0,
        created_at=now,
        completed_at=now,
    )
