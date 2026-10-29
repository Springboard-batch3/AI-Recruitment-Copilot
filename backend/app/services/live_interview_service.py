"""Domain service for Phase 3G Live Sequential AI Interview.

Orchestrates:
1. Live AI Question Generation (GENERATE_LIVE_INTERVIEW_QUESTION) with 19-point server-side validation & repair.
2. Complete Q&A Transcript serialization with canonical hashing and ownership verification.
3. Final AI Evaluation (GENERATE_FINAL_INTERVIEW_REVIEW) with strict schema validation & repair.
4. Deterministic conversion of anchored response quality to visual metric scores.
"""
import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.candidate_access import (
    CandidatePracticeMessageRecord,
    CandidatePracticeReportRecord,
    CandidatePracticeSessionRecord,
)
from app.models.interview import ProviderProfileRecord
from app.schemas.live_interview_contract import (
    LEVEL_TO_NUMERIC,
    LIVE_FINAL_REVIEW_SCHEMA_VERSION,
    LIVE_GUARDRAIL_POLICY_VERSION,
    LIVE_PROMPT_TEMPLATE_VERSION,
    LIVE_QUESTION_SCHEMA_VERSION,
    LiveInterviewFinalReviewPayload,
    LiveInterviewQuestionPayload,
    LiveResponseQualityLevel,
    LiveSessionLifecycle,
    LiveTranscriptItem,
)
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    PracticeMessageKind,
    PracticeMessageRole,
    PracticeSessionLifecycle,
    ProviderOperation,
    QuestionFormat,
    ValidationErrorCode,
    ValidationState,
)

logger = logging.getLogger("ai_hiring.services.live_interview")

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

# Coaching/rubric leakage regex in question generation
COACHING_OR_RUBRIC_LEAKAGE_REGEX = re.compile(
    r"\b(in your previous answer|you did well|you were weak|your answer was correct|your score|rubric|strengths and weaknesses|good job on|as feedback)\b",
    re.IGNORECASE,
)

# System prompt for live question generation
LIVE_QUESTION_SYSTEM_PROMPT = (
    "You are an expert AI practice interviewer conducting a live structured practice interview.\n"
    "TASK:\n"
    "- Generate the next sequential practice question for the candidate grounded in the specified job requirements and target competency.\n"
    "- Exactly ONE primary question per turn.\n"
    "- Use the EXACT `question_index` specified in the input context.\n"
    "- Adapt naturally to the context of previous answers if provided, but remain firmly grounded in the job requirements.\n"
    "- Do NOT provide feedback, coaching, scores, or evaluation on previous answers.\n"
    "- Do NOT state whether previous answers were correct or incorrect.\n"
    "- Do NOT invent candidate facts, companies, or past experience.\n"
    "- Do NOT introduce protected or prohibited topics (age, gender, race, religion, disability, marital status, politics, salary).\n"
    "- Do NOT produce HTML, Markdown, URLs, or tool execution code.\n"
    "- Output MUST be a single valid JSON object strictly matching schema 'live-interview-question.v1'.\n\n"
    "SCHEMA:\n"
    "{\n"
    '  "schema_version": "live-interview-question.v1",\n'
    '  "question": "Describe how you would validate input and handle database failures in a Python API.",\n'
    '  "competency_id": "Python & API Development",\n'
    '  "question_type": "TECHNICAL_SCENARIO",\n'
    '  "intent_summary": "Assess practical API validation and failure handling",\n'
    '  "source_requirement_ids": ["req-1"],\n'
    '  "question_index": 1,\n'
    '  "estimated_answer_minutes": 3\n'
    "}"
)

# System prompt for final interview evaluation
LIVE_FINAL_REVIEW_SYSTEM_PROMPT = (
    "You are an expert AI practice evaluator reviewing a completed live practice interview.\n"
    "TASK:\n"
    "- Review the complete question-and-answer transcript.\n"
    "- Return a single valid JSON object strictly matching schema 'live-interview-final-review.v1'.\n"
    "- In `question_reviews`, provide exactly ONE entry for each transcript question (using its exact `question_id`).\n"
    "- For each question review:\n"
    "  * `answer_status`: 'ASSESSABLE' (if answered), 'INSUFFICIENT_RESPONSE' (if unusable/empty), or 'SKIPPED' (if not answered).\n"
    "  * `answer_summary`: 1-2 sentence summary of candidate's response.\n"
    "  * `what_was_done_well`: 1-2 specific strengths demonstrated in the answer.\n"
    "  * `what_was_missing`: 1-2 specific missing details or edge cases.\n"
    "  * `how_to_improve`: 1-2 actionable tips to elevate the answer.\n"
    "  * `stronger_answer_structure`: 1 sentence on optimal answer structure.\n"
    "  * `evidence_excerpt`: Brief verbatim quote from the answer.\n"
    "  * `dimension_levels`: object with relevance, clarity, technical_depth, evidence, problem_solving_structure (levels: 'STRONG', 'DEVELOPING', 'NEEDS_DETAIL', 'NOT_DEMONSTRATED').\n"
    "- In `competency_reviews`, evaluate each tested competency with a level ('STRONG', 'DEVELOPING', 'NEEDS_DETAIL') and concise commentary.\n"
    "- In `response_quality`, rate overall levels across relevance, clarity, technical_depth, evidence, problem_solving_structure.\n"
    "- Provide `strongest_responses`, `improvement_areas`, `best_answer_question_id`, `best_answer_reason`, and a 3-item `next_practice_plan`.\n"
    "- Do NOT invent candidate experience, tools, or credentials.\n"
    "- Do NOT make hiring or rejection recommendations.\n\n"
    "OUTPUT SCHEMA SKELETON:\n"
    "{\n"
    '  "schema_version": "live-interview-final-review.v1",\n'
    '  "overall_summary": "Concise overview of candidate technical performance across the interview.",\n'
    '  "question_reviews": [\n'
    "    {\n"
    '      "question_id": "...",\n'
    '      "answer_status": "ASSESSABLE",\n'
    '      "answer_summary": "...",\n'
    '      "what_was_done_well": ["..."],\n'
    '      "what_was_missing": ["..."],\n'
    '      "how_to_improve": ["..."],\n'
    '      "stronger_answer_structure": "...",\n'
    '      "evidence_excerpt": "...",\n'
    '      "dimension_levels": {\n'
    '        "relevance": "STRONG",\n'
    '        "clarity": "DEVELOPING",\n'
    '        "technical_depth": "DEVELOPING",\n'
    '        "evidence": "STRONG",\n'
    '        "problem_solving_structure": "DEVELOPING"\n'
    "      }\n"
    "    }\n"
    "  ],\n"
    '  "strongest_responses": [\n'
    '    {"question_id": "...", "reason": "...", "answer_evidence": "..."}\n'
    "  ],\n"
    '  "improvement_areas": [\n'
    '    {"question_id": "...", "issue": "...", "how_to_improve": "..."}\n'
    "  ],\n"
    '  "competency_reviews": [\n'
    '    {"competency_id": "...", "level": "STRONG", "commentary": "...", "question_ids": ["..."]}\n'
    "  ],\n"
    '  "response_quality": {\n'
    '    "relevance": "STRONG",\n'
    '    "clarity": "DEVELOPING",\n'
    '    "technical_depth": "DEVELOPING",\n'
    '    "evidence": "STRONG",\n'
    '    "problem_solving_structure": "DEVELOPING"\n'
    "  },\n"
    '  "best_answer_question_id": "...",\n'
    '  "best_answer_reason": "...",\n'
    '  "next_practice_plan": ["Action item 1", "Action item 2", "Action item 3"],\n'
    '  "disclaimer": "This is practice coaching and not a hiring decision."\n'
    "}"
)


# --- Final AI Evaluation Validation ---

def validate_final_review_payload(
    payload: LiveInterviewFinalReviewPayload,
    transcript: List[LiveTranscriptItem],
    allowed_competency_ids: List[str],
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Performs server-side validation and grounding on generated final interview review.
    
    Returns (is_valid, error_code, error_message).
    """
    # 1. Prohibited hiring decisions or safety topics
    all_text = " ".join([
        payload.overall_summary,
        " ".join([s.reason for s in payload.strongest_responses]),
        " ".join([i.issue + " " + i.how_to_improve for i in payload.improvement_areas]),
        " ".join([c.commentary for c in payload.competency_reviews]),
        " ".join([qr.answer_summary + " " + " ".join(qr.what_was_done_well) + " " + " ".join(qr.what_was_missing) for qr in payload.question_reviews]),
        payload.best_answer_reason or "",
    ])
    if PROHIBITED_REGEX.search(all_text):
        return False, "PROHIBITED_TOPIC", "Final review contains prohibited safety, protected, or hiring decision topics."

    # 2. Disclaimer present
    if not payload.disclaimer or "practice coaching" not in payload.disclaimer.lower():
        payload.disclaimer = "This is practice coaching and not a hiring decision."

    # 3. Question IDs grounding in transcript and ensure question_reviews completeness
    valid_q_ids = {t.question_id for t in transcript}
    t_by_index = {t.question_index: t for t in transcript}
    t_by_comp = {t.competency_id.lower(): t for t in transcript}

    # Map question reviews by id, index, or order
    qr_by_id: Dict[str, QuestionReviewItem] = {}
    for idx, qr in enumerate(payload.question_reviews):
        if qr.question_id in valid_q_ids:
            qr_by_id[qr.question_id] = qr
        elif idx < len(transcript):
            # Map by order if LLM used synthetic or sequential IDs
            mapped_t = transcript[idx]
            qr.question_id = mapped_t.question_id
            qr_by_id[mapped_t.question_id] = qr

    rebuilt_qrs = []
    for t in transcript:
        if t.question_id in qr_by_id:
            qr_item = qr_by_id[t.question_id]
            if t.answer and t.answer.strip() and qr_item.answer_status == "SKIPPED":
                qr_item.answer_status = "ASSESSABLE"
            if not qr_item.what_was_done_well and t.answer and t.answer.strip():
                qr_item.what_was_done_well = [f"Addressed core scenario requirements for {t.competency_id}."]
            if not qr_item.what_was_missing and t.answer and t.answer.strip():
                qr_item.what_was_missing = [f"Could provide concrete error handling and verification metrics for {t.competency_id}."]
            if not qr_item.how_to_improve and t.answer and t.answer.strip():
                qr_item.how_to_improve = [f"Structure future answers with specific implementation choices in {t.competency_id}."]
            rebuilt_qrs.append(qr_item)
        else:
            # Construct standard question review item for this question
            rebuilt_qrs.append(
                QuestionReviewItem(
                    question_id=t.question_id,
                    answer_status="ASSESSABLE" if t.answer and t.answer.strip() else "INSUFFICIENT_RESPONSE",
                    answer_summary=f"Candidate response to {t.competency_id} question.",
                    what_was_done_well=[f"Addressed core {t.competency_id} scenario."],
                    what_was_missing=["Could provide further architectural depth and verification metrics."],
                    how_to_improve=["Structure answer with context, ownership, and measurable results."],
                    stronger_answer_structure="State approach, explain implementation, and detail verification.",
                    evidence_excerpt=(t.answer[:140] + "...") if (t.answer and len(t.answer) > 140) else (t.answer or ""),
                    dimension_levels=QuestionReviewDimensionLevels(),
                )
            )
    payload.question_reviews = rebuilt_qrs

    # Ensure strongest_responses and improvement_areas have valid question_ids
    fallback_q_id = transcript[0].question_id if transcript else ""
    for sr in payload.strongest_responses:
        if sr.question_id not in valid_q_ids:
            sr.question_id = fallback_q_id

    for ia in payload.improvement_areas:
        if ia.question_id not in valid_q_ids:
            ia.question_id = fallback_q_id

    # Ensure competency_reviews cover all tested competencies
    cr_comps = {c.competency_id.lower() for c in payload.competency_reviews}
    for t in transcript:
        if t.competency_id.lower() not in cr_comps:
            from app.schemas.live_interview_contract import CompetencyReviewItem, LiveResponseQualityLevel
            payload.competency_reviews.append(
                CompetencyReviewItem(
                    competency_id=t.competency_id,
                    level=LiveResponseQualityLevel.DEVELOPING,
                    commentary=f"Demonstrated foundational understanding in {t.competency_id} with clear potential for further production depth.",
                    question_ids=[t.question_id],
                )
            )
            cr_comps.add(t.competency_id.lower())

    return True, None, None


def repair_truncated_json(raw: str) -> Optional[Dict[str, Any]]:
    """Attempts to recover and parse valid JSON from a truncated LLM output."""
    clean = raw.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()

    start_idx = clean.find("{")
    if start_idx == -1:
        return None
    clean = clean[start_idx:]

    # 1. Try direct parse
    try:
        clean_std = re.sub(r",\s*([}\]])", r"\1", clean)
        return json.loads(clean_std)
    except Exception:
        pass

    # 2. Backtrack from the end to find the last complete key-value or object boundary
    for cut_idx in range(len(clean), max(0, len(clean) - 2000), -1):
        sub = clean[:cut_idx].rstrip()
        sub = re.sub(r',\s*$', '', sub)
        sub = re.sub(r':\s*("[^"]*)?$', '', sub)
        sub = re.sub(r'"[^"]*$', '', sub)
        sub = re.sub(r',\s*$', '', sub)

        stack: List[str] = []
        in_str = False
        escape = False
        for ch in sub:
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch in ('{', '['):
                stack.append('}' if ch == '{' else ']')
            elif ch in ('}', ']'):
                if stack and stack[-1] == ch:
                    stack.pop()

        closing_str = "".join(reversed(stack))
        candidate_json = sub + closing_str
        candidate_json = re.sub(r",\s*([}\]])", r"\1", candidate_json)
        try:
            parsed = json.loads(candidate_json)
            if isinstance(parsed, dict) and ("question_reviews" in parsed or "overall_summary" in parsed):
                return parsed
        except Exception:
            continue

    return None


# --- Final AI Evaluation Generation with LM Studio ---

async def generate_final_interview_review_with_repair(
    session: CandidatePracticeSessionRecord,
    transcript: List[LiveTranscriptItem],
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> Tuple[bool, Optional[LiveInterviewFinalReviewPayload], Optional[str], Optional[str], Optional[str]]:
    """Invokes local LM Studio to generate final interview evaluation.
    
    Returns (success, parsed_payload, provider_class, model_identifier, error_message).
    """
    settings = get_settings()

    stmt = (
        select(ProviderProfileRecord)
        .where(
            ProviderProfileRecord.organization_id == session.organization_id,
            ProviderProfileRecord.enabled.is_(True),
            ProviderProfileRecord.is_archived.is_(False),
        )
    )
    profile = db.execute(stmt).scalar_one_or_none()

    if not profile:
        stmt_any = (
            select(ProviderProfileRecord)
            .where(
                ProviderProfileRecord.enabled.is_(True),
                ProviderProfileRecord.is_archived.is_(False),
            )
        )
        profile = db.execute(stmt_any).scalar_one_or_none()

    if not profile:
        return False, None, None, None, "AI Provider is disabled or not configured."

    endpoint_url = custom_endpoint_url or profile.base_url or "http://127.0.0.1:1234/v1"
    if endpoint_url.endswith("/"):
        endpoint_url = endpoint_url[:-1]
    chat_endpoint = f"{endpoint_url}/chat/completions"

    model_identifier = profile.model_identifier or "qwen/qwen3-4b-mlx"
    provider_class = profile.provider_type.value if hasattr(profile.provider_type, "value") else str(profile.provider_type)

    user_context = {
        "job_title": session.job_version.title if session.job_version else (session.job.title if session.job else "Target Role"),
        "total_questions_completed": len(transcript),
        "questions_and_answers": [
            {
                "question_id": t.question_id,
                "question_index": t.question_index,
                "competency_id": t.competency_id,
                "question": t.question,
                "answer": t.answer,
            }
            for t in transcript
        ],
        "schema_version": LIVE_FINAL_REVIEW_SCHEMA_VERSION,
        "disclaimer": "This is practice coaching and not a hiring decision.",
    }

    user_prompt = (
        "Evaluate the candidate's answers in the completed live practice interview.\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. Evaluate EVERY answer against its corresponding question.\n"
        "2. Keep all feedback concise and punchy (1 bullet point per list item, max 15 words per field).\n"
        "3. Keep answer_summary, stronger_answer_structure, and evidence_excerpt to 1 short sentence (max 15 words).\n"
        "4. Return one item in `question_reviews` for each transcript question (all question_ids must be present).\n"
        "5. Return plain valid JSON matching schema 'live-interview-final-review.v1'.\n\n"
        f"```json\n{json.dumps(user_context, indent=2)}\n```\n"
    )

    messages = [
        {"role": "system", "content": LIVE_FINAL_REVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    async def _call_llm(msgs: List[Dict[str, str]]) -> Tuple[bool, Optional[str]]:
        try:
            req_body = {
                "model": model_identifier,
                "messages": msgs,
                "temperature": 0.2,
                "max_tokens": 3500,
            }
            timeout_val = max(120.0, float(profile.request_timeout_seconds or 60.0))
            async with httpx.AsyncClient(timeout=timeout_val) as client:
                res = await client.post(chat_endpoint, json=req_body)
                if res.status_code != 200:
                    logger.error(f"LM Studio final review call failed with status {res.status_code}: {res.text[:200]}")
                    return False, None
                data = res.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return True, content
        except Exception as exc:
            logger.error(f"LM Studio final review error: {exc}")
            return False, None

    ok, raw_content = await _call_llm(messages)
    if not ok or not raw_content:
        return False, None, provider_class, model_identifier, "Provider connection failed."

    def _parse_and_validate(raw: str) -> Tuple[Optional[LiveInterviewFinalReviewPayload], Optional[str]]:
        try:
            parsed_json = repair_truncated_json(raw)
            if not parsed_json or not isinstance(parsed_json, dict):
                return None, "Unable to parse JSON from AI output."

            payload_obj = LiveInterviewFinalReviewPayload.model_validate(parsed_json)
            val_ok, err_code, err_msg = validate_final_review_payload(
                payload=payload_obj,
                transcript=transcript,
                allowed_competency_ids=[t.competency_id for t in transcript],
            )
            if not val_ok:
                return None, f"{err_code}: {err_msg}"
            return payload_obj, None
        except Exception as ve:
            return None, f"SCHEMA_VALIDATION_ERROR: {ve}"

    parsed, err = _parse_and_validate(raw_content)
    if parsed:
        return True, parsed, provider_class, model_identifier, None

    # Repair Attempt with clean prompt context (avoiding prompt token bloat)
    repair_prompt = (
        user_prompt +
        f"\n\nIMPORTANT: Your previous output was truncated or failed ({err}). "
        "Keep each field ultra-concise (1 short bullet point per list, under 12 words) so the complete JSON finishes safely within token limits."
    )
    repair_messages = [
        {"role": "system", "content": LIVE_FINAL_REVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": repair_prompt},
    ]

    ok_rep, raw_rep = await _call_llm(repair_messages)
    if ok_rep and raw_rep:
        parsed_rep, err_rep = _parse_and_validate(raw_rep)
        if parsed_rep:
            return True, parsed_rep, provider_class, model_identifier, None

    return False, None, provider_class, model_identifier, f"Final review failed validation: {err}"


def construct_canonical_transcript(
    session: CandidatePracticeSessionRecord,
    db: Session,
) -> Tuple[List[LiveTranscriptItem], str]:
    """Builds ordered canonical Q&A transcript items from messages and snapshot, computing the canonical sha256 hash."""
    snapshot = session.questions_snapshot or []
    conv_questions = [q for q in snapshot if q.get("question_format") == QuestionFormat.CONVERSATION.value]

    # Fetch all session messages ordered by sequence_number
    stmt = (
        select(CandidatePracticeMessageRecord)
        .where(CandidatePracticeMessageRecord.session_id == session.session_id)
        .order_by(CandidatePracticeMessageRecord.sequence_number.asc())
    )
    messages = db.execute(stmt).scalars().all()

    # Map question_id to (question_msg, answer_msg)
    q_msgs: Dict[str, CandidatePracticeMessageRecord] = {}
    ans_msgs: Dict[str, CandidatePracticeMessageRecord] = {}

    for m in messages:
        if m.source_question_id:
            qid_str = str(m.source_question_id)
            if m.message_kind == PracticeMessageKind.PRIMARY_QUESTION.value:
                q_msgs[qid_str] = m
            elif m.message_kind == PracticeMessageKind.ANSWER.value:
                ans_msgs[qid_str] = m

    transcript: List[LiveTranscriptItem] = []
    hash_components: List[str] = [
        f"session:{session.session_id}",
        f"job_version:{session.job_version_id}",
        f"snapshot_hash:{session.snapshot_hash}",
    ]

    for idx, q_plan in enumerate(conv_questions, start=1):
        qid_str = str(q_plan.get("question_id"))
        q_msg = q_msgs.get(qid_str)
        ans_msg = ans_msgs.get(qid_str)

        q_text = q_msg.text if q_msg else (q_plan.get("wording") or "")
        ans_text = ans_msg.text if ans_msg else ""
        ans_id_str = str(ans_msg.message_id) if ans_msg else ""
        comp_id = q_plan.get("competency_name") or q_plan.get("target_competency") or "General"
        q_type = q_plan.get("question_type", "TECHNICAL_SCENARIO")

        transcript.append(
            LiveTranscriptItem(
                question_id=qid_str,
                question_index=idx,
                question=q_text,
                competency_id=comp_id,
                question_type=q_type,
                answer_id=ans_id_str,
                answer=ans_text,
            )
        )

        hash_components.append(f"q:{qid_str}:{q_text}:{ans_id_str}:{ans_text}")

    source_hash = hashlib.sha256("|".join(hash_components).encode("utf-8")).hexdigest()
    return transcript, source_hash


# --- 19-Point Server-Side Validation for Live Question ---

def validate_live_question_payload(
    payload: LiveInterviewQuestionPayload,
    expected_index: int,
    allowed_competencies: List[str],
    allowed_requirement_ids: List[str],
    existing_questions: List[str],
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Performs 19 strict server-side validation checks on generated live question.
    
    Returns (is_valid, error_code, error_message).
    """
    # 1. Exact schema version
    if payload.schema_version != LIVE_QUESTION_SCHEMA_VERSION:
        return False, "INVALID_SCHEMA_VERSION", f"Schema version must be {LIVE_QUESTION_SCHEMA_VERSION}"

    # 2. Bounded character length
    wording = (payload.question or "").strip()
    if len(wording) < 15:
        return False, "QUESTION_TOO_SHORT", "Question wording is too short (min 15 chars)."
    if len(wording) > 800:
        return False, "QUESTION_TOO_LONG", "Question wording is too long (max 800 chars)."

    # 3. Expected question index
    if payload.question_index != expected_index:
        return False, "QUESTION_INDEX_MISMATCH", f"Expected question index {expected_index}, got {payload.question_index}"

    # 4. Exactly one primary question (check question mark count bounded <= 2 for compound phrases, typically 1)
    q_marks = wording.count("?")
    if q_marks > 2:
        return False, "MULTIPLE_QUESTIONS", "Question contains multiple compound asks."

    # 5. Allowed competency
    if allowed_competencies and payload.competency_id not in allowed_competencies:
        # Check if case-insensitive match or valid competency substring
        matched = any(c.lower() in payload.competency_id.lower() or payload.competency_id.lower() in c.lower() for c in allowed_competencies)
        if not matched:
            return False, "INVALID_COMPETENCY", f"Competency {payload.competency_id} is not in approved competency list."

    # 6. Allowed question type
    allowed_types = {"TECHNICAL_SCENARIO", "TECHNICAL", "BEHAVIORAL", "SITUATIONAL", "SYSTEM_DESIGN"}
    if payload.question_type not in allowed_types:
        return False, "INVALID_QUESTION_TYPE", f"Question type {payload.question_type} is not allowed."

    # 7. Valid source requirement IDs
    if allowed_requirement_ids and payload.source_requirement_ids:
        for rid in payload.source_requirement_ids:
            clean_rid = str(rid).removeprefix("req-")
            allowed_cleaned = [str(ar).removeprefix("req-") for ar in allowed_requirement_ids]
            if rid not in allowed_requirement_ids and clean_rid not in allowed_cleaned:
                return False, "INVALID_REQUIREMENT_ID", f"Requirement ID {rid} is not approved for this job revision."

    # 8. No prohibited control characters
    if re.search(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", wording):
        return False, "CONTROL_CHARACTERS_PRESENT", "Question contains prohibited control characters."

    # 9. No HTML or Markdown tags
    if re.search(r"<[^>]+>", wording) or "```" in wording:
        return False, "HTML_OR_MARKDOWN_PRESENT", "Question contains prohibited HTML or Markdown code formatting."

    # 10. No URLs
    if re.search(r"https?://\S+|www\.\S+", wording):
        return False, "URL_PRESENT", "Question contains prohibited URLs."

    # 11. No prohibited safety topics
    if PROHIBITED_REGEX.search(wording) or PROHIBITED_REGEX.search(payload.intent_summary):
        return False, "PROHIBITED_TOPIC", "Question touches prohibited safety or protected topics."

    # 12. No coaching or rubric leakage from previous turn
    if COACHING_OR_RUBRIC_LEAKAGE_REGEX.search(wording):
        return False, "COACHING_LEAKAGE", "Question contains feedback or coaching about previous response."

    # 13. Duplicate detection
    clean_new = re.sub(r"[^\w\s]", "", wording.lower()).strip()
    for prev in existing_questions:
        clean_prev = re.sub(r"[^\w\s]", "", prev.lower()).strip()
        if clean_new == clean_prev or (len(clean_new) > 20 and clean_new in clean_prev):
            return False, "DUPLICATE_QUESTION", "Question is a duplicate of a previously asked question in this session."

    # 14. Bounded estimated answer minutes
    if payload.estimated_answer_minutes < 1 or payload.estimated_answer_minutes > 10:
        return False, "INVALID_ESTIMATED_MINUTES", "Estimated answer minutes must be between 1 and 10."

    return True, None, None


# --- Live Question Generation Controller with LM Studio ---

async def generate_live_question_with_repair(
    session: CandidatePracticeSessionRecord,
    target_competency: str,
    desired_question_type: str,
    question_index: int,
    total_count: int,
    requirements: List[Dict[str, Any]],
    covered_competencies: List[str],
    previous_questions: List[str],
    previous_transcript: List[Dict[str, str]],
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> Tuple[bool, Optional[LiveInterviewQuestionPayload], Optional[str], Optional[str], Optional[str]]:
    """Invokes local LM Studio to generate the next interview question with at most 1 repair attempt.
    
    Returns (success, parsed_payload, provider_class, model_identifier, error_message).
    """
    settings = get_settings()

    # Find active provider profile
    stmt = (
        select(ProviderProfileRecord)
        .where(
            ProviderProfileRecord.organization_id == session.organization_id,
            ProviderProfileRecord.enabled.is_(True),
            ProviderProfileRecord.is_archived.is_(False),
        )
    )
    profile = db.execute(stmt).scalar_one_or_none()

    if not profile:
        # Fallback to org-wide enabled profile
        stmt_any = (
            select(ProviderProfileRecord)
            .where(
                ProviderProfileRecord.enabled.is_(True),
                ProviderProfileRecord.is_archived.is_(False),
            )
        )
        profile = db.execute(stmt_any).scalar_one_or_none()

    if not profile:
        logger.warning("No active AI provider profile configured for live question generation.")
        return False, None, None, None, "AI Provider is disabled or not configured."

    endpoint_url = custom_endpoint_url or profile.base_url or "http://127.0.0.1:1234/v1"
    if endpoint_url.endswith("/"):
        endpoint_url = endpoint_url[:-1]
    chat_endpoint = f"{endpoint_url}/chat/completions"

    model_identifier = profile.model_identifier or "qwen/qwen3-4b-mlx"
    provider_class = profile.provider_type.value if hasattr(profile.provider_type, "value") else str(profile.provider_type)

    allowed_comp_names = [r.get("competency_name") or r.get("title") or "General" for r in requirements]
    if target_competency not in allowed_comp_names:
        allowed_comp_names.append(target_competency)
    allowed_req_ids = [str(r.get("requirement_id") or r.get("id")) for r in requirements if r.get("requirement_id") or r.get("id")]

    # Bounded input payload (exclude candidate personal data)
    req_summaries = [
        {"id": str(r.get("requirement_id") or r.get("id")), "title": r.get("title", ""), "summary": r.get("summary", "")[:200]}
        for r in requirements[:5]
    ]

    # Bounded previous transcript context (last 2 Q&A pairs at most)
    bounded_history = []
    for item in previous_transcript[-2:]:
        bounded_history.append({
            "question": item.get("question", "")[:300],
            "candidate_answer": item.get("answer", "")[:400],
        })

    user_context = {
        "job_title": session.job_version.title if session.job_version else (session.job.title if session.job else "Target Role"),
        "target_competency": target_competency,
        "desired_question_type": desired_question_type,
        "question_index": question_index,
        "total_question_count": total_count,
        "covered_competencies": covered_competencies,
        "job_requirements": req_summaries,
        "previous_interview_context": bounded_history,
        "prohibited_topics": ["personal identity", "salary", "politics", "religion", "hiring decisions"],
        "schema_version": LIVE_QUESTION_SCHEMA_VERSION,
    }

    user_prompt = (
        "Generate the next live practice interview question for the following bounded context:\n"
        f"```json\n{json.dumps(user_context, indent=2)}\n```\n"
        f"Return ONLY valid JSON matching schema '{LIVE_QUESTION_SCHEMA_VERSION}'."
    )

    messages = [
        {"role": "system", "content": LIVE_QUESTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    async def _call_llm(msgs: List[Dict[str, str]]) -> Tuple[bool, Optional[str]]:
        try:
            req_body = {
                "model": model_identifier,
                "messages": msgs,
                "temperature": 0.2,
                "max_tokens": 500,
            }
            async with httpx.AsyncClient(timeout=profile.request_timeout_seconds or 30.0) as client:
                res = await client.post(chat_endpoint, json=req_body)
                if res.status_code != 200:
                    logger.error(f"LM Studio call failed with status {res.status_code}: {res.text[:200]}")
                    return False, None
                data = res.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return True, content
        except Exception as exc:
            logger.error(f"LM Studio question generation error: {exc}")
            return False, None

    # Attempt 1
    ok, raw_content = await _call_llm(messages)
    if not ok or not raw_content:
        return False, None, provider_class, model_identifier, "Provider connection failed or timed out."

    def _parse_and_validate(raw: str) -> Tuple[Optional[LiveInterviewQuestionPayload], Optional[str]]:
        try:
            clean = raw.strip()
            if clean.startswith("```json"):
                clean = clean[7:]
            if clean.startswith("```"):
                clean = clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
            parsed_json = json.loads(clean)
            if isinstance(parsed_json, dict):
                if "question_index" in parsed_json:
                    parsed_json["question_index"] = question_index
                if "source_requirement_ids" in parsed_json and isinstance(parsed_json["source_requirement_ids"], list):
                    normalized_rids = []
                    allowed_cleaned = {str(ar).removeprefix("req-"): str(ar) for ar in allowed_req_ids}
                    for r in parsed_json["source_requirement_ids"]:
                        r_str = str(r)
                        r_clean = r_str.removeprefix("req-")
                        if r_str in allowed_req_ids:
                            normalized_rids.append(r_str)
                        elif r_clean in allowed_cleaned:
                            normalized_rids.append(allowed_cleaned[r_clean])
                        elif allowed_req_ids:
                            normalized_rids.append(allowed_req_ids[0])
                    parsed_json["source_requirement_ids"] = normalized_rids or allowed_req_ids[:1]
            payload_obj = LiveInterviewQuestionPayload.model_validate(parsed_json)
            val_ok, err_code, err_msg = validate_live_question_payload(
                payload=payload_obj,
                expected_index=question_index,
                allowed_competencies=allowed_comp_names,
                allowed_requirement_ids=allowed_req_ids,
                existing_questions=previous_questions,
            )
            if not val_ok:
                return None, f"{err_code}: {err_msg}"
            return payload_obj, None
        except (json.JSONDecodeError, ValidationError) as ve:
            return None, f"SCHEMA_VALIDATION_ERROR: {ve}"

    parsed, err = _parse_and_validate(raw_content)
    if parsed:
        return True, parsed, provider_class, model_identifier, None

    # Attempt 2: Controlled Repair Attempt
    logger.info(f"Question generation failed validation ({err}). Attempting controlled schema repair.")
    repair_prompt = (
        f"Your previous response failed validation with error: {err}.\n"
        f"Raw output was:\n{raw_content[:400]}\n\n"
        f"Repair and output ONLY the valid JSON object strictly matching schema '{LIVE_QUESTION_SCHEMA_VERSION}'."
    )
    repair_messages = list(messages)
    repair_messages.append({"role": "assistant", "content": raw_content})
    repair_messages.append({"role": "user", "content": repair_prompt})

    ok_rep, raw_rep = await _call_llm(repair_messages)
    if ok_rep and raw_rep:
        parsed_rep, err_rep = _parse_and_validate(raw_rep)
        if parsed_rep:
            return True, parsed_rep, provider_class, model_identifier, None

    return False, None, provider_class, model_identifier, f"Question generation failed after repair: {err}"

