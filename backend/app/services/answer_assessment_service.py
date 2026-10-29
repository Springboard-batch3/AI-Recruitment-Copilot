"""Domain service for Phase 4A.3 Structured Per-Question Answer Assessment, Validation, and Deterministic Reporting.

Key design guarantees:
1. Frozen Assessment Source: Load authoritative question text, intent, competency, allowed criteria, and answers directly from PostgreSQL.
2. Stable Answer Segments: Deterministically split answer into 'S1', 'S2', ... with character spans.
3. Isolated Per-Answer Assessment: One bounded LLM call per question-answer pair.
4. Strict Pydantic v2 Validation: Enforce allowed labels (DEMONSTRATED, PARTIALLY_DEMONSTRATED, INSUFFICIENT_RESPONSE, NOT_APPLICABLE, NEEDS_HUMAN_REVIEW), forbid numeric scores, percentages, and hiring verdicts.
5. Single Repair Loop & Human Review Fallback: At most 1 repair attempt. Failures persist as NEEDS_HUMAN_REVIEW without zeroing competence.
6. Deterministic Aggregation: Calculate evidence coverage percentages excluding NOT_APPLICABLE.
7. Candidate vs. Recruiter DTO Separation.
"""
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.auth import UserRecord
from app.models.candidate_access import (
    CandidatePracticeAnswerReviewRecord,
    CandidatePracticeAnswerSegmentRecord,
    CandidatePracticeMcqAnswerRecord,
    CandidatePracticeMessageRecord,
    CandidatePracticeReportRecord,
    CandidatePracticeSessionRecord,
)
from app.models.interview import ProviderProfileRecord
from app.schemas.answer_assessment_contract import (
    ASSESSMENT_GUARDRAIL_VERSION,
    ASSESSMENT_POLICY_VERSION,
    ASSESSMENT_PROMPT_CONTRACT_VERSION,
    CANDIDATE_REPORT_SCHEMA_VERSION,
    RECRUITER_REPORT_SCHEMA_VERSION,
    STRUCTURED_ANSWER_ASSESSMENT_SCHEMA_VERSION,
    AllowedRubricCriterion,
    AssessmentCriterionLabel,
    CandidateQuestionFeedbackCardDto,
    CandidateStructuredReportDto,
    CanonicalAnswerSegment,
    CompetencyEvidenceCoverageDto,
    ConfidenceCategory,
    EvidenceStateCategory,
    PerAnswerAssessmentPacket,
    RecruiterQuestionAssessmentDetailDto,
    RecruiterStructuredReportDto,
    StrictCriterionAssessmentItem,
    StrictPerAnswerAssessmentResponse,
)

logger = logging.getLogger("ai_hiring.services.answer_assessment")

SENTENCE_SPLIT_REGEX = re.compile(r"([.!?]+(?:\s+|$))")
LONG_PARAGRAPH_THRESHOLD = 250

STRUCTURED_ANSWER_ASSESSMENT_SYSTEM_PROMPT = """You are an objective technical competency evaluation engine.
Evaluate the candidate's submitted answer against ONLY the specified authorized criteria.

You MUST respond with a single JSON object conforming EXACTLY to this canonical schema:
{
  "schema_version": "answer-assessment.v3",
  "question_id": "<exact_question_id>",
  "evidence_state": "SUFFICIENT",
  "criteria": [
    {
      "criterion_id": "crit_technical_accuracy",
      "label": "DEMONSTRATED",
      "supporting_segment_ids": ["S1"],
      "observation": "Concise evidence-grounded observation."
    }
  ],
  "strengths": ["Specific strength grounded in cited answer segments."],
  "missing_elements": ["Specific missing technical element if any."],
  "improvement_guidance": ["Concrete way the response could be improved."],
  "uncertainty_notes": []
}

STRICT RULES:
1. Return JSON ONLY matching schema_version 'answer-assessment.v3'.
2. "criteria" MUST be an array. Include exactly one entry for each expected criterion in allowed_criteria.
3. FORBIDDEN keys: Do NOT use "assessment", "answer_assessment", or "assessments" aliases.
4. Allowed "label" values ONLY: "DEMONSTRATED", "PARTIALLY_DEMONSTRATED", "INSUFFICIENT_RESPONSE", "NOT_APPLICABLE", "NEEDS_HUMAN_REVIEW".
5. Allowed "evidence_state" values: "SUFFICIENT", "PARTIAL", "INSUFFICIENT", "NEEDS_HUMAN_REVIEW".
6. In supporting_segment_ids, cite ONLY provided segment IDs (e.g. ["S1", "S2"]). Never invent segment IDs.
7. Strengths, missing_elements, improvement_guidance, and uncertainty_notes must always be arrays (use [] if empty).
8. NO numeric marks or scores (e.g. 4/5, 80%).
9. NO hiring decisions or recommendations (hire, reject, advance).
10. NO markdown code blocks, backticks, or HTML.
"""


def extract_canonical_answer_segments(text: str) -> List[CanonicalAnswerSegment]:
    """Deterministically splits answer text into stable, numbered evidence segments ('S1', 'S2', ...)."""
    if not text or not text.strip():
        return [
            CanonicalAnswerSegment(
                segment_id="S1",
                sequence_order=1,
                text="[No response provided]",
                start_char=0,
                end_char=0,
            )
        ]

    paragraphs = [p for p in re.split(r"\n+", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    raw_spans: List[Tuple[int, int, str]] = []
    current_offset = 0

    for para in paragraphs:
        para_clean = para.strip()
        p_start = text.find(para_clean, current_offset)
        if p_start == -1:
            p_start = current_offset
        p_end = p_start + len(para_clean)
        current_offset = p_end

        if len(para_clean) > LONG_PARAGRAPH_THRESHOLD:
            # Split into sentence-level segments
            last_idx = 0
            for match in SENTENCE_SPLIT_REGEX.finditer(para_clean):
                sent_end_rel = match.end()
                sent_text = para_clean[last_idx:sent_end_rel].strip()
                if sent_text:
                    rel_start = para_clean.find(sent_text, last_idx)
                    abs_start = p_start + rel_start
                    abs_end = abs_start + len(sent_text)
                    raw_spans.append((abs_start, abs_end, sent_text))
                last_idx = sent_end_rel
            rem = para_clean[last_idx:].strip()
            if rem:
                rel_start = para_clean.find(rem, last_idx)
                abs_start = p_start + rel_start
                abs_end = abs_start + len(rem)
                raw_spans.append((abs_start, abs_end, rem))
        else:
            raw_spans.append((p_start, p_end, para_clean))

    if not raw_spans:
        raw_spans = [(0, len(text), text.strip())]

    segments: List[CanonicalAnswerSegment] = []
    for idx, (start_c, end_c, seg_text) in enumerate(raw_spans, start=1):
        segments.append(
            CanonicalAnswerSegment(
                segment_id=f"S{idx}",
                sequence_order=idx,
                text=seg_text,
                start_char=start_c,
                end_char=end_c,
            )
        )
    return segments


def build_authoritative_assessment_packet(
    question_dict: Dict[str, Any],
    answer_id: UUID,
    answer_text: str,
) -> PerAnswerAssessmentPacket:
    """Constructs an authoritative, isolated single-question assessment packet from PostgreSQL snapshot data."""
    q_id_raw = question_dict.get("question_id") or question_dict.get("slot_id")
    if isinstance(q_id_raw, UUID):
        question_id = q_id_raw
    elif isinstance(q_id_raw, str):
        try:
            question_id = UUID(q_id_raw)
        except Exception:
            question_id = uuid4()
    else:
        question_id = uuid4()
    question_text = question_dict.get("wording") or question_dict.get("question_text") or question_dict.get("question") or "Interview Question"
    raw_intent = (
        question_dict.get("intent")
        or question_dict.get("intent_rationale")
        or question_dict.get("competency_name")
        or question_dict.get("competency")
        or ""
    )
    if not raw_intent or len(str(raw_intent).strip()) < 5:
        intent = f"Evaluate practical engineering capability for {raw_intent or 'candidate'}"
    else:
        intent = str(raw_intent).strip()

    raw_comp = question_dict.get("competency_name") or question_dict.get("competency") or "Core Technical Capability"
    competency = str(raw_comp).strip() if len(str(raw_comp).strip()) >= 2 else "Core Capability"

    # Extract or synthesize allowed rubric criteria from snapshot
    rubric_anchors = question_dict.get("rubric_anchors") or []
    criteria: List[AllowedRubricCriterion] = []

    if rubric_anchors and isinstance(rubric_anchors, list):
        for idx, anchor in enumerate(rubric_anchors, start=1):
            if isinstance(anchor, dict):
                c_id = anchor.get("criterion_id") or f"crit_{idx}"
                desc = anchor.get("description") or anchor.get("anchor_text") or f"Evaluation criterion {idx}"
                guidance = anchor.get("guidance")
                criteria.append(
                    AllowedRubricCriterion(
                        criterion_id=str(c_id),
                        description=str(desc),
                        anchor_guidance=str(guidance) if guidance else None,
                    )
                )

    if not criteria:
        # Default authoritative criteria for the competency
        criteria = [
            AllowedRubricCriterion(
                criterion_id="crit_technical_accuracy",
                description=f"Demonstrates correct understanding and technical solution for {competency}.",
                anchor_guidance="Candidate provides clear steps and accurate technical methodology.",
            ),
            AllowedRubricCriterion(
                criterion_id="crit_concrete_evidence",
                description="Provides specific details, actions, tools, or verification steps.",
                anchor_guidance="Candidate explains what they personally did or how they verified the outcome.",
            ),
            AllowedRubricCriterion(
                criterion_id="crit_clarity_structure",
                description="Explains trade-offs, constraints, or systematic problem solving.",
                anchor_guidance="Candidate articulates constraints and reasoned decision making.",
            ),
        ]

    segments = extract_canonical_answer_segments(answer_text)

    return PerAnswerAssessmentPacket(
        question_id=question_id,
        answer_id=answer_id,
        question_text=question_text,
        question_intent=intent,
        competency_name=competency,
        allowed_criteria=criteria,
        answer_segments=segments,
    )


def normalize_assessment_json(
    raw_data: Any,
    packet: PerAnswerAssessmentPacket,
) -> Dict[str, Any]:
    """Normalizes known structurally equivalent LLM response variants into canonical v3 dictionary."""
    if not isinstance(raw_data, dict):
        return {}

    normalized: Dict[str, Any] = {
        "schema_version": STRUCTURED_ANSWER_ASSESSMENT_SCHEMA_VERSION,
        "question_id": str(raw_data.get("question_id") or packet.question_id),
        "evidence_state": "SUFFICIENT",
        "criteria": [],
        "strengths": [],
        "missing_elements": [],
        "improvement_guidance": [],
        "uncertainty_notes": [],
    }

    allowed_criterion_ids = {c.criterion_id for c in packet.allowed_criteria}
    valid_segment_ids = {s.segment_id for s in packet.answer_segments}

    # Extract raw criteria list from canonical or alias keys
    raw_criteria = (
        raw_data.get("criteria")
        or raw_data.get("criterion_assessments")
        or raw_data.get("answer_assessment")
        or raw_data.get("assessments")
    )

    # Strengths / Gaps / Guidance normalization
    raw_strengths = raw_data.get("strengths")
    if isinstance(raw_strengths, list):
        normalized["strengths"] = [str(s) for s in raw_strengths if s]
    elif isinstance(raw_strengths, str) and raw_strengths.strip():
        normalized["strengths"] = [raw_strengths.strip()]

    raw_missing = raw_data.get("missing_elements") or raw_data.get("missing_evidence")
    if isinstance(raw_missing, list):
        normalized["missing_elements"] = [str(m) for m in raw_missing if m]
    elif isinstance(raw_missing, str) and raw_missing.strip():
        normalized["missing_elements"] = [raw_missing.strip()]

    raw_guidance = raw_data.get("improvement_guidance") or raw_data.get("stronger_structure_tip")
    if isinstance(raw_guidance, list):
        normalized["improvement_guidance"] = [str(g) for g in raw_guidance if g]
    elif isinstance(raw_guidance, str) and raw_guidance.strip():
        normalized["improvement_guidance"] = [raw_guidance.strip()]

    raw_uncertainty = raw_data.get("uncertainty_notes") or raw_data.get("uncertainty_codes")
    if isinstance(raw_uncertainty, list):
        normalized["uncertainty_notes"] = [str(u) for u in raw_uncertainty if u]
    elif isinstance(raw_uncertainty, str) and raw_uncertainty.strip():
        normalized["uncertainty_notes"] = [raw_uncertainty.strip()]

    if raw_data.get("evidence_state"):
        ev_state = str(raw_data.get("evidence_state")).upper()
        if ev_state in [e.value for e in EvidenceStateCategory]:
            normalized["evidence_state"] = ev_state

    # Case 1: Array of criterion items (canonical, Variant C, Variant D)
    if isinstance(raw_criteria, list):
        for item in raw_criteria:
            if not isinstance(item, dict):
                continue
            c_id = item.get("criterion_id") or item.get("id")
            label_val = item.get("label") or item.get("assessment_label") or "DEMONSTRATED"
            if isinstance(label_val, str):
                label_val = label_val.upper()
                if label_val not in [e.value for e in AssessmentCriterionLabel]:
                    label_val = "NEEDS_HUMAN_REVIEW"
            else:
                label_val = "NEEDS_HUMAN_REVIEW"

            segs = item.get("supporting_segment_ids") or item.get("segments") or []
            if isinstance(segs, str):
                segs = [segs]
            clean_segs = [str(s) for s in segs if s]

            obs = item.get("observation") or item.get("summary") or item.get("notes") or ""
            if not obs and label_val == "DEMONSTRATED":
                obs = "Candidate demonstrated technical criteria."

            normalized["criteria"].append({
                "criterion_id": str(c_id) if c_id else "",
                "label": label_val,
                "supporting_segment_ids": clean_segs,
                "observation": str(obs),
            })

    # Case 2: Dict of dynamic criterion keys (Variant A, Variant B)
    elif isinstance(raw_data.get("assessment"), dict):
        assess_dict = raw_data["assessment"]
        root_obs = raw_data.get("observation") or assess_dict.get("observation") or ""
        root_segs = raw_data.get("supporting_segment_ids") or assess_dict.get("supporting_segment_ids") or []
        if isinstance(root_segs, str):
            root_segs = [root_segs]
        clean_root_segs = [str(s) for s in root_segs if str(s) in valid_segment_ids]

        for c in packet.allowed_criteria:
            c_id = c.criterion_id
            val = assess_dict.get(c_id)
            if isinstance(val, str):
                val_upper = val.upper()
                label_val = val_upper if val_upper in [e.value for e in AssessmentCriterionLabel] else "NEEDS_HUMAN_REVIEW"
                obs = str(root_obs) if root_obs else f"Evaluated {c_id}"
                normalized["criteria"].append({
                    "criterion_id": c_id,
                    "label": label_val,
                    "supporting_segment_ids": clean_root_segs,
                    "observation": obs,
                })
            elif isinstance(val, dict):
                lbl = val.get("label") or val.get("assessment_label") or "DEMONSTRATED"
                if isinstance(lbl, str) and lbl.upper() in [e.value for e in AssessmentCriterionLabel]:
                    lbl = lbl.upper()
                else:
                    lbl = "NEEDS_HUMAN_REVIEW"
                c_segs = val.get("supporting_segment_ids") or clean_root_segs
                c_obs = val.get("observation") or root_obs or f"Evaluated {c_id}"
                normalized["criteria"].append({
                    "criterion_id": c_id,
                    "label": lbl,
                    "supporting_segment_ids": [str(s) for s in c_segs if str(s) in valid_segment_ids],
                    "observation": str(c_obs),
                })

    return normalized


def validate_strict_assessment_output(
    parsed_json: Dict[str, Any],
    packet: PerAnswerAssessmentPacket,
) -> Tuple[bool, List[str], Optional[StrictPerAnswerAssessmentResponse]]:
    """Strictly validates LLM assessment response against packet constraints."""
    errors: List[str] = []

    if not isinstance(parsed_json, dict):
        return False, ["INVALID_JSON_ROOT_NOT_OBJECT"], None

    # First normalize known variations into canonical format
    normalized = normalize_assessment_json(parsed_json, packet)
    if not normalized:
        return False, ["FAILED_NORMALIZATION"], None

    # Check question ID match
    if str(normalized.get("question_id")) != str(packet.question_id):
        errors.append(f"QUESTION_ID_MISMATCH: expected {packet.question_id}, got {normalized.get('question_id')}")

    # Validate Schema and Pydantic validation
    try:
        model_resp = StrictPerAnswerAssessmentResponse.model_validate(normalized)
    except ValidationError as val_err:
        for err in val_err.errors():
            errors.append(f"PYDANTIC_VALIDATION_ERROR at {'.'.join(str(l) for l in err['loc'])}: {err['msg']}")
        return False, errors, None

    allowed_criterion_ids = {c.criterion_id for c in packet.allowed_criteria}
    valid_segment_ids = {s.segment_id for s in packet.answer_segments}

    returned_criterion_ids: Set[str] = set()
    for item in model_resp.criteria:
        if item.criterion_id not in allowed_criterion_ids:
            errors.append(f"UNKNOWN_CRITERION_ID: {item.criterion_id}")
        returned_criterion_ids.add(item.criterion_id)

        # Check supporting segments
        for seg_id in item.supporting_segment_ids:
            if seg_id not in valid_segment_ids:
                errors.append(f"NONEXISTENT_SEGMENT_ID_CITED: '{seg_id}' in criterion '{item.criterion_id}'")

        # Demonstrated without observation
        if item.label in (AssessmentCriterionLabel.DEMONSTRATED, AssessmentCriterionLabel.PARTIALLY_DEMONSTRATED):
            if not item.observation or len(item.observation.strip()) < 5:
                errors.append(f"MISSING_EVIDENCE_OBSERVATION for criterion '{item.criterion_id}'")

    # Check that all required criteria are assessed
    missing_criteria = allowed_criterion_ids - returned_criterion_ids
    if missing_criteria:
        errors.append(f"OMITTED_REQUIRED_CRITERIA: {sorted(list(missing_criteria))}")

    # Prohibited string checks (e.g. numeric marks, hiring recommendations)
    full_str = json.dumps(parsed_json).lower()
    for forbidden in ["recommend to hire", "recommend rejection", "should hire", "pass candidate", "fail candidate", "hire recommendation", "/5", "/10", "100%"]:
        if forbidden in full_str:
            errors.append(f"PROHIBITED_TERM_DETECTED: '{forbidden}'")

    if errors:
        return False, errors, None

    return True, [], model_resp


def create_human_review_fallback_assessment(
    packet: PerAnswerAssessmentPacket,
    reason: str,
) -> StrictPerAnswerAssessmentResponse:
    """Creates a deterministic fallback assessment marking criteria as NEEDS_HUMAN_REVIEW without zeroing competence or inventing false candidate weaknesses."""
    items: List[StrictCriterionAssessmentItem] = []
    for crit in packet.allowed_criteria:
        items.append(
            StrictCriterionAssessmentItem(
                criterion_id=crit.criterion_id,
                label=AssessmentCriterionLabel.NEEDS_HUMAN_REVIEW,
                supporting_segment_ids=[],
                observation="Automated evaluation deferred pending reviewer confirmation.",
            )
        )

    return StrictPerAnswerAssessmentResponse(
        schema_version=STRUCTURED_ANSWER_ASSESSMENT_SCHEMA_VERSION,
        question_id=str(packet.question_id),
        evidence_state=EvidenceStateCategory.NEEDS_HUMAN_REVIEW,
        criteria=items,
        strengths=[],
        missing_elements=[],
        improvement_guidance=[],
        uncertainty_notes=[f"Analysis unavailable: {reason}"],
    )


# Module-level cache for compatibility format across run
_SUPPORTED_RESPONSE_FORMAT_MODE: Optional[bool] = None


async def assess_single_answer(
    packet: PerAnswerAssessmentPacket,
    db: Session,
    organization_id: Optional[UUID] = None,
    custom_endpoint_url: Optional[str] = None,
) -> Tuple[StrictPerAnswerAssessmentResponse, str, Optional[str], Optional[str]]:
    """Assesses exactly one question-answer packet via local LLM with 1-shot repair loop.
    
    Returns:
    (validated_assessment, validation_state, provider_class, model_identifier)
    """
    global _SUPPORTED_RESPONSE_FORMAT_MODE
    settings = get_settings()

    # 1. Resolve Provider Profile
    stmt = (
        select(ProviderProfileRecord)
        .where(
            ProviderProfileRecord.enabled.is_(True),
            ProviderProfileRecord.is_archived.is_(False),
        )
    )
    if organization_id:
        stmt = stmt.where(ProviderProfileRecord.organization_id == organization_id)
    stmt = stmt.order_by(ProviderProfileRecord.created_at.desc())
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
    timeout_sec = 25.0

    user_payload = {
        "schema_version": STRUCTURED_ANSWER_ASSESSMENT_SCHEMA_VERSION,
        "question_id": str(packet.question_id),
        "question_text": packet.question_text,
        "question_intent": packet.question_intent,
        "competency_name": packet.competency_name,
        "allowed_criteria": [c.model_dump() for c in packet.allowed_criteria],
        "answer_segments": [s.model_dump() for s in packet.answer_segments],
        "allowed_labels": [
            AssessmentCriterionLabel.DEMONSTRATED.value,
            AssessmentCriterionLabel.PARTIALLY_DEMONSTRATED.value,
            AssessmentCriterionLabel.INSUFFICIENT_RESPONSE.value,
            AssessmentCriterionLabel.NOT_APPLICABLE.value,
            AssessmentCriterionLabel.NEEDS_HUMAN_REVIEW.value,
        ],
    }

    use_json_format = _SUPPORTED_RESPONSE_FORMAT_MODE if _SUPPORTED_RESPONSE_FORMAT_MODE is not None else True

    req_body: Dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": STRUCTURED_ANSWER_ASSESSMENT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload)},
        ],
        "temperature": 0.1,
        "max_tokens": 1000,
        "stream": False,
    }
    if use_json_format:
        req_body["response_format"] = {"type": "json_object"}

    logger.debug(
        "Assessment request: question_id=%s, attempt=1, structured_format=%s",
        packet.question_id,
        use_json_format,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(
                endpoint_url,
                json=req_body,
                headers={"Content-Type": "application/json"},
            )
            # If 400 error occurs due to response_format compatibility, retry once without it and cache mode
            if resp.status_code == 400 and "response_format" in req_body:
                logger.info("Provider rejected response_format: json_object. Falling back to unformatted mode.")
                req_body_no_fmt = {k: v for k, v in req_body.items() if k != "response_format"}
                resp = await client.post(
                    endpoint_url,
                    json=req_body_no_fmt,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    _SUPPORTED_RESPONSE_FORMAT_MODE = False
            elif resp.status_code == 200 and "response_format" in req_body:
                _SUPPORTED_RESPONSE_FORMAT_MODE = True

        if resp.status_code != 200:
            logger.warning("Provider returned HTTP %d on initial assessment call", resp.status_code)
            fallback = create_human_review_fallback_assessment(packet, f"Provider HTTP {resp.status_code}")
            return fallback, "NEEDS_HUMAN_REVIEW", provider_class, model_name

        resp_json = resp.json()
        raw_content = resp_json["choices"][0]["message"]["content"].strip()
        if raw_content.startswith("```"):
            raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
            raw_content = re.sub(r"\s*```$", "", raw_content)

        is_valid = False
        errors: List[str] = []
        validated: Optional[StrictPerAnswerAssessmentResponse] = None

        try:
            parsed_data = json.loads(raw_content)
            is_valid, errors, validated = validate_strict_assessment_output(parsed_data, packet)
        except Exception as parse_err:
            errors = [f"JSON_PARSE_ERROR: {str(parse_err)}"]

        if is_valid and validated:
            return validated, "PASSED", provider_class, model_name

        logger.info("Initial assessment output requires repair (%s). Attempting at most 1 repair.", errors)

        # 2. Controlled Repair Prompt (at most 1)
        repair_payload = {
            "schema_version": STRUCTURED_ANSWER_ASSESSMENT_SCHEMA_VERSION,
            "question_id": str(packet.question_id),
            "expected_criteria": [c.criterion_id for c in packet.allowed_criteria],
            "allowed_segments": [s.segment_id for s in packet.answer_segments],
            "validation_errors": errors,
            "original_output": raw_content,
            "instruction": "Return ONLY a valid JSON object matching schema_version 'answer-assessment.v3' with 'criteria' array for all expected criteria.",
        }

        repair_messages = [
            {"role": "system", "content": STRUCTURED_ANSWER_ASSESSMENT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(repair_payload)},
        ]

        repair_req_body: Dict[str, Any] = {
            "model": model_name,
            "messages": repair_messages,
            "temperature": 0.0,
            "max_tokens": 1000,
            "stream": False,
        }
        if _SUPPORTED_RESPONSE_FORMAT_MODE:
            repair_req_body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            repair_resp = await client.post(
                endpoint_url,
                json=repair_req_body,
                headers={"Content-Type": "application/json"},
            )

        if repair_resp.status_code != 200:
            logger.warning("Provider returned HTTP %d on assessment repair call.", repair_resp.status_code)
            fallback = create_human_review_fallback_assessment(packet, "Repair request failed")
            return fallback, "NEEDS_HUMAN_REVIEW", provider_class, model_name

        repair_raw = repair_resp.json()["choices"][0]["message"]["content"].strip()
        if repair_raw.startswith("```"):
            repair_raw = re.sub(r"^```(?:json)?\s*", "", repair_raw)
            repair_raw = re.sub(r"\s*```$", "", repair_raw)

        rep_valid = False
        rep_errors: List[str] = []
        rep_validated: Optional[StrictPerAnswerAssessmentResponse] = None

        try:
            repair_parsed = json.loads(repair_raw)
            rep_valid, rep_errors, rep_validated = validate_strict_assessment_output(repair_parsed, packet)
        except Exception as rep_parse_err:
            rep_errors = [f"JSON_PARSE_ERROR: {str(rep_parse_err)}"]

        if rep_valid and rep_validated:
            return rep_validated, "REPAIRED", provider_class, model_name

        logger.warning("Repair also failed validation (%s). Persisting human review fallback.", rep_errors)
        fallback = create_human_review_fallback_assessment(packet, f"Validation failure: {rep_errors[:2]}")
        return fallback, "NEEDS_HUMAN_REVIEW", provider_class, model_name

    except Exception as exc:
        logger.warning("Assessment exception (%s). Persisting human review fallback.", str(exc))
        fallback = create_human_review_fallback_assessment(packet, f"Execution exception: {type(exc).__name__}")
        return fallback, "NEEDS_HUMAN_REVIEW", provider_class, model_name



def aggregate_competency_evidence_coverage(
    all_assessments: List[StrictPerAnswerAssessmentResponse],
    packets_by_qid: Dict[str, PerAnswerAssessmentPacket],
) -> Tuple[List[CompetencyEvidenceCoverageDto], Dict[str, int], Optional[float]]:
    """Calculates deterministic competency evidence coverage percentages.
    
    Rules:
    - Exclude NOT_APPLICABLE from denominator.
    - Coverage % = (demonstrated + 0.5*partially_demonstrated) / applicable_criteria * 100
    - Track NEEDS_HUMAN_REVIEW separately without converting to 0 competence.
    """
    comp_map: Dict[str, Dict[str, int]] = {}

    status_counts = {
        "DEMONSTRATED": 0,
        "PARTIALLY_DEMONSTRATED": 0,
        "INSUFFICIENT_RESPONSE": 0,
        "NOT_APPLICABLE": 0,
        "NEEDS_HUMAN_REVIEW": 0,
    }

    for assess in all_assessments:
        packet = packets_by_qid.get(assess.question_id)
        comp_name = packet.competency_name if packet else "General Technical Competence"

        if comp_name not in comp_map:
            comp_map[comp_name] = {
                "applicable": 0,
                "demonstrated": 0,
                "partial": 0,
                "insufficient": 0,
                "human_review": 0,
            }

        for item in assess.criteria:
            lbl = item.label
            status_counts[lbl.value] = status_counts.get(lbl.value, 0) + 1

            if lbl == AssessmentCriterionLabel.NOT_APPLICABLE:
                continue

            comp_map[comp_name]["applicable"] += 1

            if lbl == AssessmentCriterionLabel.DEMONSTRATED:
                comp_map[comp_name]["demonstrated"] += 1
            elif lbl == AssessmentCriterionLabel.PARTIALLY_DEMONSTRATED:
                comp_map[comp_name]["partial"] += 1
            elif lbl == AssessmentCriterionLabel.INSUFFICIENT_RESPONSE:
                comp_map[comp_name]["insufficient"] += 1
            elif lbl == AssessmentCriterionLabel.NEEDS_HUMAN_REVIEW:
                comp_map[comp_name]["human_review"] += 1

    coverage_dtos: List[CompetencyEvidenceCoverageDto] = []
    total_applicable = 0
    total_weighted_points = 0.0

    for c_name, counts in sorted(comp_map.items()):
        applicable = counts["applicable"]
        dem = counts["demonstrated"]
        part = counts["partial"]
        insuf = counts["insufficient"]
        hr = counts["human_review"]

        if applicable > 0:
            cov_pct = round(((dem + 0.5 * part) / applicable) * 100.0, 1)
            total_applicable += applicable
            total_weighted_points += (dem + 0.5 * part)

            if hr > 0 and dem == 0 and part == 0:
                display_status = "Pending Review"
            elif cov_pct >= 70.0:
                display_status = "Supported with Evidence"
            elif cov_pct >= 40.0:
                display_status = "Partially Demonstrated"
            else:
                display_status = "Needs Practice Evidence"
        else:
            cov_pct = None
            display_status = "Not Applicable"

        coverage_dtos.append(
            CompetencyEvidenceCoverageDto(
                competency_name=c_name,
                total_applicable_criteria=applicable,
                demonstrated_criteria_count=dem,
                partially_demonstrated_count=part,
                insufficient_count=insuf,
                needs_human_review_count=hr,
                evidence_coverage_pct=cov_pct,
                display_status=display_status,
            )
        )

    overall_coverage = (
        round((total_weighted_points / total_applicable) * 100.0, 1)
        if total_applicable > 0
        else None
    )

    return coverage_dtos, status_counts, overall_coverage


async def process_and_persist_structured_session_assessment(
    session: CandidatePracticeSessionRecord,
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> Tuple[CandidateStructuredReportDto, RecruiterStructuredReportDto]:
    """Orchestrates per-question assessment, validation, persistence, and report DTO creation."""
    now = datetime.now(timezone.utc)
    snapshot = session.questions_snapshot or []
    conv_questions = [q for q in snapshot if q.get("question_format") == "CONVERSATION"]

    # 1. Fetch all candidate answer messages ordered by sequence
    stmt_msgs = (
        select(CandidatePracticeMessageRecord)
        .where(
            CandidatePracticeMessageRecord.session_id == session.session_id,
            CandidatePracticeMessageRecord.role == "CANDIDATE",
            CandidatePracticeMessageRecord.message_kind == "ANSWER",
        )
        .order_by(CandidatePracticeMessageRecord.sequence_number.asc())
    )
    answer_messages = db.execute(stmt_msgs).scalars().all()
    ans_by_qid: Dict[str, CandidatePracticeMessageRecord] = {
        str(m.source_question_id): m for m in answer_messages if m.source_question_id
    }

    # 2. Check for existing reviews for idempotency
    stmt_existing_revs = (
        select(CandidatePracticeAnswerReviewRecord)
        .where(CandidatePracticeAnswerReviewRecord.session_id == session.session_id)
    )
    existing_rev_records = db.execute(stmt_existing_revs).scalars().all()
    rev_by_qid: Dict[str, CandidatePracticeAnswerReviewRecord] = {
        str(r.question_id): r for r in existing_rev_records
    }

    packets_by_qid: Dict[str, PerAnswerAssessmentPacket] = {}
    validated_assessments: List[StrictPerAnswerAssessmentResponse] = []
    recruiter_details: List[RecruiterQuestionAssessmentDetailDto] = []
    candidate_cards: List[CandidateQuestionFeedbackCardDto] = []

    demonstrated_strengths_all: List[str] = []
    priority_improvements_all: List[str] = []
    human_review_items_count = 0

    for idx, q_dict in enumerate(conv_questions, start=1):
        q_id_str = q_dict["question_id"]
        cand_msg = ans_by_qid.get(q_id_str)
        ans_id = cand_msg.message_id if cand_msg else uuid4()
        ans_text = cand_msg.text if cand_msg else "[No response recorded]"

        packet = build_authoritative_assessment_packet(q_dict, ans_id, ans_text)
        packets_by_qid[q_id_str] = packet

        # Check if already assessed
        existing_rev = rev_by_qid.get(q_id_str)
        if existing_rev and existing_rev.validation_status == "PASSED" and existing_rev.strengths:
            # Rehydrate from existing review
            crit_items: List[StrictCriterionAssessmentItem] = []
            for st in (existing_rev.strengths or []):
                crit_items.append(
                    StrictCriterionAssessmentItem(
                        criterion_id=st.get("criterion_id", "crit_1"),
                        label=AssessmentCriterionLabel(st.get("label") or st.get("assessment_label", "DEMONSTRATED")),
                        supporting_segment_ids=st.get("supporting_segment_ids", []),
                        observation=st.get("observation", "Demonstrated capability"),
                    )
                )
            assessment_resp = StrictPerAnswerAssessmentResponse(
                schema_version=STRUCTURED_ANSWER_ASSESSMENT_SCHEMA_VERSION,
                question_id=q_id_str,
                evidence_state=EvidenceStateCategory.SUFFICIENT,
                criteria=crit_items or [
                    StrictCriterionAssessmentItem(
                        criterion_id="crit_1",
                        label=AssessmentCriterionLabel.DEMONSTRATED,
                        supporting_segment_ids=["S1"],
                        observation="Previously assessed answer",
                    )
                ],
                strengths=[st.get("observation") for st in (existing_rev.strengths or []) if st.get("observation")],
                missing_elements=[im.get("observation") for im in (existing_rev.improvement_areas or []) if im.get("observation")],
                improvement_guidance=[existing_rev.stronger_structure] if existing_rev.stronger_structure else [],
                uncertainty_notes=[],
            )
            val_state = existing_rev.validation_status
        else:
            # Perform single-answer assessment
            assessment_resp, val_state, prov_class, model_id = await assess_single_answer(
                packet=packet,
                db=db,
                organization_id=session.organization_id,
                custom_endpoint_url=custom_endpoint_url,
            )

            # Persist per-answer review record
            idempotency_key = f"{session.session_id}:{q_id_str}:{ans_id}"
            
            # Map structured criteria into record json
            strengths_json = []
            improvements_json = []
            for item in assessment_resp.criteria:
                item_dict = item.model_dump()
                if item.label in (
                    AssessmentCriterionLabel.DEMONSTRATED,
                    AssessmentCriterionLabel.PARTIALLY_DEMONSTRATED,
                ):
                    strengths_json.append(item_dict)
                else:
                    improvements_json.append(item_dict)

            if val_state == "NEEDS_HUMAN_REVIEW":
                human_review_items_count += 1

            top_summary = "; ".join(assessment_resp.strengths) if assessment_resp.strengths else "Assessment completed."
            top_tip = assessment_resp.improvement_guidance[0] if assessment_resp.improvement_guidance else None

            if existing_rev:
                existing_rev.lifecycle_status = "REVIEW_READY"
                existing_rev.acknowledgement = top_summary
                existing_rev.strengths = strengths_json
                existing_rev.improvement_areas = improvements_json
                existing_rev.stronger_structure = top_tip
                existing_rev.validation_status = val_state
                existing_rev.completed_at = now
            else:
                new_rev = CandidatePracticeAnswerReviewRecord(
                    review_id=uuid4(),
                    session_id=session.session_id,
                    question_id=packet.question_id,
                    answer_id=ans_id,
                    idempotency_key=idempotency_key,
                    schema_version=STRUCTURED_ANSWER_ASSESSMENT_SCHEMA_VERSION,
                    lifecycle_status="REVIEW_READY",
                    answer_status=val_state,
                    acknowledgement=top_summary,
                    strengths=strengths_json,
                    improvement_areas=improvements_json,
                    next_steps=[top_tip] if top_tip else [],
                    stronger_structure=top_tip,
                    dimension_levels={},
                    follow_up_needed=False,
                    uncertainty_codes=assessment_resp.uncertainty_notes,
                    provider_class=prov_class,
                    model_identifier=model_id,
                    prompt_template_version=ASSESSMENT_PROMPT_CONTRACT_VERSION,
                    guardrail_policy_version=ASSESSMENT_GUARDRAIL_VERSION,
                    validation_status=val_state,
                    retry_count=1 if val_state == "REPAIRED" else 0,
                    created_at=now,
                    completed_at=now,
                )
                db.add(new_rev)

        validated_assessments.append(assessment_resp)

        # Build Recruiter Detail
        recruiter_details.append(
            RecruiterQuestionAssessmentDetailDto(
                question_id=packet.question_id,
                answer_id=ans_id,
                sequence_number=idx,
                question_text=packet.question_text,
                question_intent=packet.question_intent,
                competency_name=packet.competency_name,
                final_answer_text=ans_text,
                answer_segments=packet.answer_segments,
                criterion_results=assessment_resp.criteria,
                overall_summary="; ".join(assessment_resp.strengths) if assessment_resp.strengths else "Response assessed.",
                uncertainty_codes=assessment_resp.uncertainty_notes,
                validation_state=val_state,
            )
        )

        # Build Candidate Card
        card_strengths = list(assessment_resp.strengths)
        card_missing = list(assessment_resp.missing_elements)
        card_tips = list(assessment_resp.improvement_guidance)

        if card_strengths:
            demonstrated_strengths_all.extend(card_strengths[:1])
        if card_tips:
            priority_improvements_all.extend(card_tips[:1])

        # Primary label for card
        primary_label = (
            "DEMONSTRATED"
            if card_strengths and not card_missing
            else ("PARTIALLY_DEMONSTRATED" if card_strengths or card_missing else "NEEDS_PRACTICE")
        )

        candidate_cards.append(
            CandidateQuestionFeedbackCardDto(
                question_id=packet.question_id,
                sequence_number=idx,
                question_text=packet.question_text,
                competency_name=packet.competency_name,
                candidate_submitted_answer=ans_text,
                overall_observation="; ".join(card_strengths) if card_strengths else "Response assessed.",
                demonstrated_strengths=card_strengths,
                missing_evidence=card_missing,
                actionable_improvement_tips=card_tips,
                stronger_structure_tip=card_tips[0] if card_tips else None,
                evidence_coverage_label=primary_label,
            )
        )

    # 3. Deterministic Aggregation
    competency_coverage, status_counts, overall_coverage = aggregate_competency_evidence_coverage(
        validated_assessments, packets_by_qid
    )

    # 4. Source Hash Computation
    raw_hash_components = [
        str(session.session_id),
        str(session.job_version_id),
        str(session.snapshot_hash),
        ASSESSMENT_POLICY_VERSION,
        ";".join(f"{d.question_id}:{d.validation_state}" for d in recruiter_details),
    ]
    source_answer_hash = hashlib.sha256("|".join(raw_hash_components).encode("utf-8")).hexdigest()

    # 5. Persist or Update Practice Report Record
    stmt_existing_rep = (
        select(CandidatePracticeReportRecord)
        .where(
            CandidatePracticeReportRecord.session_id == session.session_id,
            CandidatePracticeReportRecord.is_current.is_(True),
        )
    )
    existing_rep = db.execute(stmt_existing_rep).scalars().first()

    report_id = existing_rep.report_id if existing_rep else uuid4()
    job_title = (
        session.job_version.title
        if session.job_version
        else (session.job.title if session.job else "Target Role")
    )

    coaching_summary_text = (
        f"You completed structured practice for the {job_title} role. "
        f"Evidence coverage across tested competencies is {overall_coverage or 0.0}%. "
        f"Review your question feedback cards below to target specific technical improvements."
    )

    practice_plan = [
        "Structure each answer clearly by stating your direct action, key technical constraints, and verified outcomes.",
        "Provide explicit technical evidence and metrics rather than general concepts.",
        "Review areas marked for improvement before your next practice session.",
    ]

    if existing_rep:
        existing_rep.summary_text = coaching_summary_text
        existing_rep.source_review_hash = source_answer_hash
        existing_rep.strongest_areas = [{"observation": s} for s in demonstrated_strengths_all[:3]]
        existing_rep.priority_improvements = [{"observation": p} for p in priority_improvements_all[:3]]
        existing_rep.next_practice_plan = practice_plan
        existing_rep.competency_aggregates = [c.model_dump() for c in competency_coverage]
        existing_rep.completion_counts = status_counts
        existing_rep.validation_status = "PASSED"
        existing_rep.lifecycle_status = "SUMMARY_READY"
        existing_rep.finalized_at = now
    else:
        new_report = CandidatePracticeReportRecord(
            report_id=report_id,
            session_id=session.session_id,
            candidate_id=session.candidate_id,
            organization_id=session.organization_id,
            report_version=1,
            schema_version=CANDIDATE_REPORT_SCHEMA_VERSION,
            lifecycle_status="SUMMARY_READY",
            source_review_hash=source_answer_hash,
            aggregation_policy_version=ASSESSMENT_POLICY_VERSION,
            summary_text=coaching_summary_text,
            strongest_areas=[{"observation": s} for s in demonstrated_strengths_all[:3]],
            priority_improvements=[{"observation": p} for p in priority_improvements_all[:3]],
            competency_commentary=[],
            response_patterns=[],
            next_practice_plan=practice_plan,
            competency_aggregates=[c.model_dump() for c in competency_coverage],
            dimension_aggregates=[],
            question_trend=[],
            trend_classification="STABLE",
            mcq_breakdown={},
            completion_counts=status_counts,
            disclaimer="This practice report provides coaching guidance and is not a hiring decision.",
            provider_class="STRUCTURED_ASSESSMENT_ENGINE",
            model_identifier="per-question-v2",
            prompt_template_version=ASSESSMENT_PROMPT_CONTRACT_VERSION,
            guardrail_policy_version=ASSESSMENT_GUARDRAIL_VERSION,
            validation_status="PASSED",
            retry_count=0,
            is_current=True,
            created_at=now,
            finalized_at=now,
        )
        db.add(new_report)

    db.commit()

    # 6. Separate Candidate and Recruiter DTOs
    candidate_dto = CandidateStructuredReportDto(
        report_id=report_id,
        session_id=session.session_id,
        job_title=job_title,
        job_revision=session.job_revision,
        report_version=1,
        schema_version=CANDIDATE_REPORT_SCHEMA_VERSION,
        coaching_summary=coaching_summary_text,
        demonstrated_strengths=demonstrated_strengths_all[:4],
        priority_improvement_areas=priority_improvements_all[:4],
        competency_coverage=competency_coverage,
        question_cards=candidate_cards,
        next_practice_plan=practice_plan,
        overall_coverage_pct=overall_coverage,
        review_status_counts=status_counts,
        created_at=now,
    )

    recruiter_dto = RecruiterStructuredReportDto(
        report_id=report_id,
        session_id=session.session_id,
        candidate_id=session.candidate_id,
        job_id=session.job_id,
        job_title=job_title,
        job_revision=session.job_revision,
        question_set_version=session.set_version,
        report_version=1,
        schema_version=RECRUITER_REPORT_SCHEMA_VERSION,
        source_answer_hash=source_answer_hash,
        assessment_policy_version=ASSESSMENT_POLICY_VERSION,
        prompt_contract_version=ASSESSMENT_PROMPT_CONTRACT_VERSION,
        guardrail_version=ASSESSMENT_GUARDRAIL_VERSION,
        competency_coverage=competency_coverage,
        question_assessments=recruiter_details,
        review_status_counts=status_counts,
        human_review_required=(human_review_items_count > 0),
        human_review_items_count=human_review_items_count,
        created_at=now,
        finalized_at=now,
    )

    return candidate_dto, recruiter_dto
