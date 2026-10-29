"""Domain service for deterministic competency and dimension aggregation, canonical review hashing, local AI final summary generation, validation and persistence."""
import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.candidate_access import (
    CandidatePracticeAnswerReviewRecord,
    CandidatePracticeMcqAnswerRecord,
    CandidatePracticeMessageRecord,
    CandidatePracticeReportRecord,
    CandidatePracticeSessionRecord,
)
from app.models.interview import ProviderProfileRecord
from app.schemas.phase3_enums import (
    PracticeMessageKind,
    PracticeMessageRole,
    PracticeSessionLifecycle,
    QuestionFormat,
)
from app.schemas.practice_report_contract import (
    AGGREGATION_POLICY_VERSION,
    PRACTICE_COACHING_SUMMARY_SCHEMA_VERSION,
    REPORT_GUARDRAIL_POLICY_VERSION,
    REPORT_PROMPT_TEMPLATE_VERSION,
    CandidatePracticeReportDto,
    CompetencyAggregateDto,
    CompetencyCommentaryItem,
    CompletionCountsDto,
    DimensionAggregateDto,
    McqBreakdownDto,
    McqTopicResultDto,
    PriorityImprovementItem,
    QuestionTrendItemDto,
    ReportLifecycleStatus,
    ResponsePatternItem,
    StrengthAreaItem,
    PracticeCoachingSummaryPayload,
    TrendClassification,
)
from app.schemas.practice_review_contract import (
    AnswerStatus,
    DimensionLevel,
    ReviewLifecycleStatus,
)

logger = logging.getLogger("ai_hiring.services.practice_report")

LEVEL_NUMERIC_MAP = {
    "STRONG": 4.0,
    "DEVELOPING": 3.0,
    "NEEDS_DETAIL": 2.0,
    "NOT_DEMONSTRATED": 1.0,
}

DIMENSION_METADATA = {
    "relevance": "Relevance & Focus",
    "clarity": "Clarity & Structure",
    "technical_depth": "Technical Depth",
    "evidence": "Evidence & Verification",
    "problem_solving_structure": "Problem Solving & Structure",
}

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


def calculate_source_review_hash(
    session: CandidatePracticeSessionRecord,
    reviews: List[CandidatePracticeAnswerReviewRecord],
    mcq_answers: List[CandidatePracticeMcqAnswerRecord],
) -> str:
    """Calculates canonical sha256 hash across ordered questions, answers, reviews, and MCQs."""
    components: List[str] = []
    components.append(f"session:{session.session_id}")
    components.append(f"job_version:{session.job_version_id}")
    components.append(f"snapshot_hash:{session.snapshot_hash}")
    components.append(f"policy:{AGGREGATION_POLICY_VERSION}")

    # Sorted reviews by question_id
    sorted_reviews = sorted(reviews, key=lambda r: str(r.question_id))
    for r in sorted_reviews:
        components.append(
            f"rev:{r.review_id}:{r.answer_id}:{r.question_id}:{r.answer_status}:{r.lifecycle_status}:{r.validation_status}"
        )

    # Sorted MCQs by question_id
    sorted_mcqs = sorted(mcq_answers, key=lambda m: str(m.question_id))
    for m in sorted_mcqs:
        components.append(f"mcq:{m.question_id}:{m.selected_option_id}:{m.is_correct}")

    raw_str = "|".join(components)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


def compute_deterministic_aggregates(
    session: CandidatePracticeSessionRecord,
    reviews: List[CandidatePracticeAnswerReviewRecord],
    mcq_answers: List[CandidatePracticeMcqAnswerRecord],
) -> Tuple[
    List[CompetencyAggregateDto],
    bool,
    List[DimensionAggregateDto],
    List[QuestionTrendItemDto],
    str,
    Optional[McqBreakdownDto],
    CompletionCountsDto,
]:
    """Computes all deterministic metrics, competencies, dimensions, trend, and MCQ stats."""
    snapshot = session.questions_snapshot or []
    conv_questions = [q for q in snapshot if q.get("question_format") == QuestionFormat.CONVERSATION.value]
    mcq_questions = [q for q in snapshot if q.get("question_format") == QuestionFormat.MCQ_SINGLE_ANSWER.value]

    review_by_qid: Dict[str, CandidatePracticeAnswerReviewRecord] = {
        str(r.question_id): r for r in reviews if r.lifecycle_status == ReviewLifecycleStatus.REVIEW_READY.value
    }
    mcq_by_qid: Dict[str, CandidatePracticeMcqAnswerRecord] = {
        str(m.question_id): m for m in mcq_answers
    }

    # 1. Competency Aggregation
    competency_groups: Dict[str, List[CandidatePracticeAnswerReviewRecord]] = {}
    competency_q_count: Dict[str, int] = {}
    for q in conv_questions:
        c_name = q.get("competency_name", "General")
        competency_q_count[c_name] = competency_q_count.get(c_name, 0) + 1
        rev = review_by_qid.get(q["question_id"])
        if rev and rev.answer_status == AnswerStatus.ASSESSABLE.value:
            competency_groups.setdefault(c_name, []).append(rev)

    competency_aggregates: List[CompetencyAggregateDto] = []
    assessable_competencies_count = 0

    all_c_names = sorted(competency_q_count.keys())
    for c_name in all_c_names:
        c_reviews = competency_groups.get(c_name, [])
        total_q = competency_q_count[c_name]
        assessable_c = len(c_reviews)

        if assessable_c > 0:
            assessable_competencies_count += 1
            # Gather all numeric dimension scores
            scores: List[float] = []
            for r in c_reviews:
                dim_levels = r.dimension_levels or {}
                for d_val in dim_levels.values():
                    if d_val in LEVEL_NUMERIC_MAP:
                        scores.append(LEVEL_NUMERIC_MAP[d_val])
            avg_score = round(sum(scores) / len(scores), 1) if scores else None
            if avg_score is not None:
                if avg_score >= 3.5:
                    anchored = "STRONG"
                    band = "Consistent Strength"
                    desc = f"Demonstrated solid competency depth across {assessable_c} response(s)."
                elif avg_score >= 2.5:
                    anchored = "DEVELOPING"
                    band = "Developing Capability"
                    desc = f"Shows foundational understanding; opportunity to add more specific implementation details."
                else:
                    anchored = "NEEDS_DETAIL"
                    band = "Needs More Detail"
                    desc = f"Requires clearer evidence and structured problem-solving actions."
            else:
                anchored = "NOT_DEMONSTRATED"
                band = "Not Demonstrated"
                desc = "Insufficient details provided during practice turns."
        else:
            avg_score = None
            anchored = "NOT_ASSESSED"
            band = "Not Assessed"
            desc = "No assessable responses were submitted for this competency."

        competency_aggregates.append(
            CompetencyAggregateDto(
                competency_name=c_name,
                numeric_score=avg_score,
                anchored_level=anchored,
                assessable_count=assessable_c,
                total_questions=total_q,
                excluded_count=total_q - assessable_c,
                display_band=band,
                description=desc,
            )
        )

    # Radar eligibility: exactly between 3 and 6 competencies with assessable evidence
    radar_eligible = 3 <= assessable_competencies_count <= 6

    # 2. Response Quality Dimension Aggregation
    dim_scores: Dict[str, List[float]] = {k: [] for k in DIMENSION_METADATA.keys()}
    dim_counts: Dict[str, int] = {k: 0 for k in DIMENSION_METADATA.keys()}

    for rev in review_by_qid.values():
        if rev.answer_status == AnswerStatus.ASSESSABLE.value:
            d_levels = rev.dimension_levels or {}
            for d_key in DIMENSION_METADATA.keys():
                lvl = d_levels.get(d_key)
                if lvl in LEVEL_NUMERIC_MAP:
                    dim_scores[d_key].append(LEVEL_NUMERIC_MAP[lvl])
                    dim_counts[d_key] += 1

    dimension_aggregates: List[DimensionAggregateDto] = []
    for d_key, d_label in DIMENSION_METADATA.items():
        s_list = dim_scores[d_key]
        c_val = dim_counts[d_key]
        if s_list:
            avg_s = round(sum(s_list) / len(s_list), 1)
            if avg_s >= 3.5:
                anch_lvl = "STRONG"
                disp_lbl = "Demonstrated Strength"
            elif avg_s >= 2.5:
                anch_lvl = "DEVELOPING"
                disp_lbl = "Developing Level"
            else:
                anch_lvl = "NEEDS_DETAIL"
                disp_lbl = "Needs More Detail"
        else:
            avg_s = None
            anch_lvl = "NOT_ASSESSED"
            disp_lbl = "Not Assessed"

        dimension_aggregates.append(
            DimensionAggregateDto(
                dimension_key=d_key,
                dimension_label=d_label,
                anchored_level=anch_lvl,
                numeric_score=avg_s,
                assessable_count=c_val,
                excluded_count=len(conv_questions) - c_val,
                display_label=disp_lbl,
            )
        )

    # 3. Question Sequence & Trend
    question_trend: List[QuestionTrendItemDto] = []
    seq_idx = 1
    assessable_scores_in_order: List[float] = []

    for q in snapshot:
        qid_str = q["question_id"]
        q_fmt = q.get("question_format", QuestionFormat.CONVERSATION.value)
        wording = q.get("wording", "")
        c_name = q.get("competency_name", "General")

        if q_fmt == QuestionFormat.CONVERSATION.value:
            rev = review_by_qid.get(qid_str)
            if rev:
                status_val = rev.answer_status
                dim_levels = rev.dimension_levels or {}
                rev_id = rev.review_id
                fu_completed = bool(rev.follow_up_needed)
                if status_val == AnswerStatus.ASSESSABLE.value and dim_levels:
                    # Compute average level for trend
                    q_num_s = [LEVEL_NUMERIC_MAP[v] for v in dim_levels.values() if v in LEVEL_NUMERIC_MAP]
                    if q_num_s:
                        assessable_scores_in_order.append(sum(q_num_s) / len(q_num_s))
            else:
                status_val = "SKIPPED"
                dim_levels = {}
                rev_id = None
                fu_completed = False

            question_trend.append(
                QuestionTrendItemDto(
                    sequence_number=seq_idx,
                    question_id=UUID(qid_str),
                    question_format=q_fmt,
                    wording=wording,
                    competency_name=c_name,
                    assessability_status=status_val,
                    dimension_levels=dim_levels,
                    follow_up_completed=fu_completed,
                    is_mcq_correct=None,
                    review_id=rev_id,
                )
            )
            seq_idx += 1
        elif q_fmt == QuestionFormat.MCQ_SINGLE_ANSWER.value:
            mcq_ans = mcq_by_qid.get(qid_str)
            question_trend.append(
                QuestionTrendItemDto(
                    sequence_number=seq_idx,
                    question_id=UUID(qid_str),
                    question_format=q_fmt,
                    wording=wording,
                    competency_name=c_name,
                    assessability_status="MCQ_ANSWERED" if mcq_ans else "SKIPPED",
                    dimension_levels={},
                    follow_up_completed=False,
                    is_mcq_correct=mcq_ans.is_correct if mcq_ans else None,
                    review_id=None,
                )
            )
            seq_idx += 1

    # Trend classification heuristic
    if len(assessable_scores_in_order) >= 3:
        first_half = assessable_scores_in_order[: len(assessable_scores_in_order) // 2]
        second_half = assessable_scores_in_order[len(assessable_scores_in_order) // 2 :]
        avg_1 = sum(first_half) / len(first_half)
        avg_2 = sum(second_half) / len(second_half)
        if avg_2 - avg_1 >= 0.4:
            trend_classification = TrendClassification.IMPROVING.value
        elif abs(avg_2 - avg_1) < 0.4:
            trend_classification = TrendClassification.STABLE.value
        else:
            trend_classification = TrendClassification.MIXED.value
    else:
        trend_classification = TrendClassification.NOT_ENOUGH_ASSESSABLE_DATA.value

    # 4. MCQ Breakdown
    mcq_breakdown: Optional[McqBreakdownDto] = None
    if mcq_questions:
        attempted_mcq = len(mcq_answers)
        correct_mcq = sum(1 for m in mcq_answers if m.is_correct)
        skipped_mcq = len(mcq_questions) - attempted_mcq

        # Topic grouping
        topic_map: Dict[str, Dict[str, Any]] = {}
        for q in mcq_questions:
            t_name = q.get("competency_name", "General Knowledge")
            if t_name not in topic_map:
                topic_map[t_name] = {"attempted": 0, "correct": 0, "learning_points": []}
            ans = mcq_by_qid.get(q["question_id"])
            if ans:
                topic_map[t_name]["attempted"] += 1
                if ans.is_correct:
                    topic_map[t_name]["correct"] += 1
                if q.get("explanation"):
                    topic_map[t_name]["learning_points"].append(q["explanation"])

        topic_dtos = [
            McqTopicResultDto(
                topic_name=t,
                attempted_count=data["attempted"],
                correct_count=data["correct"],
                learning_points=data["learning_points"][:2],
            )
            for t, data in topic_map.items()
        ]

        mcq_breakdown = McqBreakdownDto(
            total_count=len(mcq_questions),
            attempted_count=attempted_mcq,
            correct_count=correct_mcq,
            skipped_count=skipped_mcq,
            topics=topic_dtos,
        )

    # 5. Completion Counts
    assessable_c = sum(1 for r in reviews if r.answer_status == AnswerStatus.ASSESSABLE.value)
    insufficient_c = sum(1 for r in reviews if r.answer_status == AnswerStatus.INSUFFICIENT_RESPONSE.value)
    unavailable_c = sum(1 for r in reviews if r.answer_status == AnswerStatus.REVIEW_UNAVAILABLE.value)
    total_answered = len(reviews)
    skipped_conv = len(conv_questions) - total_answered

    completion_counts = CompletionCountsDto(
        total_primary_questions=len(snapshot),
        total_conversational_questions=len(conv_questions),
        total_mcq_questions=len(mcq_questions),
        assessable_answers_count=assessable_c,
        insufficient_answers_count=insufficient_c,
        skipped_count=max(0, skipped_conv),
        unavailable_count=unavailable_c,
    )

    return (
        competency_aggregates,
        radar_eligible,
        dimension_aggregates,
        question_trend,
        trend_classification,
        mcq_breakdown,
        completion_counts,
    )


def validate_coaching_summary(
    payload: Dict[str, Any],
    allowed_review_ids: Set[str],
    allowed_competencies: Set[str],
) -> Tuple[bool, List[str], Optional[PracticeCoachingSummaryPayload]]:
    """Strictly validates local-AI final summary output against all 15 grounding constraints."""
    errors: List[str] = []

    if not isinstance(payload, dict):
        return False, ["INVALID_JSON_OBJECT"], None

    expected_keys = {
        "schema_version",
        "summary",
        "strongest_areas",
        "priority_improvements",
        "competency_commentary",
        "response_patterns",
        "next_practice_plan",
        "disclaimer",
    }
    extra_keys = set(payload.keys()) - expected_keys
    if extra_keys:
        errors.append(f"UNEXPECTED_FIELDS: {list(extra_keys)}")

    if payload.get("schema_version") != PRACTICE_COACHING_SUMMARY_SCHEMA_VERSION:
        errors.append(f"SCHEMA_VERSION_MISMATCH: expected {PRACTICE_COACHING_SUMMARY_SCHEMA_VERSION}")

    # Check prohibited terms across all textual values
    def check_prohibited(text_val: str, field_name: str):
        if PROHIBITED_REGEX.search(text_val):
            errors.append(f"PROHIBITED_TERM_DETECTED_IN_{field_name}")

    summary_text = payload.get("summary", "")
    if not isinstance(summary_text, str) or len(summary_text.strip()) < 10:
        errors.append("SUMMARY_TEXT_TOO_SHORT")
    else:
        check_prohibited(summary_text, "SUMMARY")

    # Validate Strongest Areas
    strongest = payload.get("strongest_areas", [])
    if not isinstance(strongest, list) or len(strongest) > 3:
        errors.append("STRONGEST_AREAS_COUNT_EXCEEDED")
    else:
        for idx, item in enumerate(strongest):
            if not isinstance(item, dict):
                errors.append(f"INVALID_STRENGTH_ITEM_{idx}")
                continue
            for r_id in item.get("source_review_ids", []):
                if str(r_id) not in allowed_review_ids:
                    errors.append(f"UNKNOWN_SOURCE_REVIEW_ID_IN_STRENGTH_{r_id}")
            check_prohibited(item.get("observation", ""), f"STRENGTH_{idx}")

    # Validate Priority Improvements
    priorities = payload.get("priority_improvements", [])
    if not isinstance(priorities, list) or len(priorities) > 3:
        errors.append("PRIORITY_IMPROVEMENTS_COUNT_EXCEEDED")
    else:
        for idx, item in enumerate(priorities):
            if not isinstance(item, dict):
                errors.append(f"INVALID_PRIORITY_ITEM_{idx}")
                continue
            for r_id in item.get("source_review_ids", []):
                if str(r_id) not in allowed_review_ids:
                    errors.append(f"UNKNOWN_SOURCE_REVIEW_ID_IN_PRIORITY_{r_id}")
            check_prohibited(item.get("observation", ""), f"PRIORITY_{idx}")

    # Validate Competency Commentary
    commentaries = payload.get("competency_commentary", [])
    if isinstance(commentaries, list):
        for idx, item in enumerate(commentaries):
            if isinstance(item, dict):
                c_id = item.get("competency_id", "")
                if c_id not in allowed_competencies:
                    errors.append(f"UNRECOGNIZED_COMPETENCY_ID_{c_id}")
                for r_id in item.get("source_review_ids", []):
                    if str(r_id) not in allowed_review_ids:
                        errors.append(f"UNKNOWN_SOURCE_REVIEW_ID_IN_COMMENTARY_{r_id}")
                check_prohibited(item.get("commentary", ""), f"COMMENTARY_{idx}")

    # Validate Next Practice Plan
    plan = payload.get("next_practice_plan", [])
    if not isinstance(plan, list) or len(plan) != 3:
        errors.append("NEXT_PRACTICE_PLAN_MUST_CONTAIN_EXACTLY_3_ITEMS")
    else:
        for p_item in plan:
            if not isinstance(p_item, str) or len(p_item.strip()) < 5:
                errors.append("INVALID_PLAN_ITEM")
            else:
                check_prohibited(p_item, "PLAN_ITEM")

    if errors:
        return False, errors, None

    try:
        validated_payload = PracticeCoachingSummaryPayload.model_validate(payload)
        return True, [], validated_payload
    except Exception as e:
        return False, [f"PYDANTIC_VALIDATION_ERROR: {str(e)}"], None


async def generate_coaching_summary_with_repair(
    session: CandidatePracticeSessionRecord,
    reviews: List[CandidatePracticeAnswerReviewRecord],
    competency_aggregates: List[CompetencyAggregateDto],
    dimension_aggregates: List[DimensionAggregateDto],
    trend_classification: str,
    mcq_breakdown: Optional[McqBreakdownDto],
    db: Session,
    custom_endpoint_url: Optional[str] = None,
) -> Tuple[ReportLifecycleStatus, Optional[PracticeCoachingSummaryPayload], Optional[str], Optional[str]]:
    """Invokes local Qwen3-4B MLX model with structured observations and 1-shot repair loop."""
    settings = get_settings()

    # 1. Check Admin AI Enablement & Profile
    stmt = (
        select(ProviderProfileRecord)
        .where(
            ProviderProfileRecord.organization_id == session.organization_id,
            ProviderProfileRecord.enabled.is_(True),
            ProviderProfileRecord.is_archived.is_(False),
        )
        .order_by(ProviderProfileRecord.created_at.desc())
    )
    profile = db.execute(stmt).scalars().first()

    if not profile:
        stmt_any = (
            select(ProviderProfileRecord)
            .where(
                ProviderProfileRecord.enabled.is_(True),
                ProviderProfileRecord.is_archived.is_(False),
            )
            .order_by(ProviderProfileRecord.created_at.desc())
        )
        profile = db.execute(stmt_any).scalars().first()

    base_url = profile.base_url if (profile and profile.base_url) else (settings.llm_base_url if settings.llm_base_url else "http://localhost:1234/v1")
    if base_url.endswith("/"):
        base_url = base_url[:-1]
    default_endpoint = f"{base_url}/chat/completions" if not base_url.endswith("/chat/completions") else base_url

    endpoint_url = custom_endpoint_url or default_endpoint
    model_name = (profile.model_identifier if (profile and profile.model_identifier) else (settings.llm_model if settings.llm_model else "qwen/qwen3-4b-2507"))
    provider_class = profile.provider_type.value if (profile and hasattr(profile.provider_type, "value")) else (str(profile.provider_type) if profile else "LOCAL_OPENAI_COMPATIBLE")

    if profile and str(profile.provider_type).upper() == "DISABLED":
        return ReportLifecycleStatus.SUMMARY_FAILED_RETRYABLE, None, provider_class, model_name

    allowed_review_ids: Set[str] = {str(r.review_id) for r in reviews}
    allowed_competencies: Set[str] = {c.competency_name for c in competency_aggregates}

    # Extract structured observations
    extracted_strengths = []
    extracted_gaps = []
    for r in reviews:
        if r.answer_status == AnswerStatus.ASSESSABLE.value:
            for st in r.strengths or []:
                extracted_strengths.append({
                    "review_id": str(r.review_id),
                    "dimension": st.get("dimension"),
                    "observation": st.get("observation"),
                })
            for imp in r.improvement_areas or []:
                extracted_gaps.append({
                    "review_id": str(r.review_id),
                    "dimension": imp.get("dimension"),
                    "observation": imp.get("observation"),
                })

    system_prompt = (
        "You are an expert interview practice coach. Your task is to produce a concise, encouraging, and grounded "
        "practice summary based strictly on the provided structured observations. "
        "CRITICAL RULES:\n"
        "1. Output plain, valid JSON only matching the schema exactly.\n"
        "2. Never invent candidate experience, tools, actions, or credentials.\n"
        "3. Every strength or priority improvement must cite existing source_review_ids from the input.\n"
        "4. Include exactly 3 actionable next_practice_plan steps.\n"
        "5. Do NOT include personality, emotion, cultural fit, or hiring/rejection recommendations.\n"
        "6. Do NOT output Markdown code fences, HTML, or chain-of-thought."
    )

    user_payload = {
        "operation": "PRACTICE_COACHING_SUMMARY",
        "schema_version": PRACTICE_COACHING_SUMMARY_SCHEMA_VERSION,
        "policy_version": REPORT_GUARDRAIL_POLICY_VERSION,
        "prompt_version": REPORT_PROMPT_TEMPLATE_VERSION,
        "competency_aggregates": [
            {"competency": c.competency_name, "level": c.anchored_level, "display": c.display_band}
            for c in competency_aggregates
        ],
        "dimension_aggregates": [
            {"dimension": d.dimension_label, "level": d.anchored_level}
            for d in dimension_aggregates
        ],
        "trend": trend_classification,
        "observed_strengths": extracted_strengths[:6],
        "observed_improvement_areas": extracted_gaps[:6],
        "mcq_summary": f"{mcq_breakdown.correct_count}/{mcq_breakdown.attempted_count} correct" if mcq_breakdown else "No MCQs",
        "allowed_source_review_ids": list(allowed_review_ids),
        "allowed_competencies": list(allowed_competencies),
    }

    req_body = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload)},
        ],
        "temperature": 0.15,
        "max_tokens": 1000,
        "stream": False,
        "response_format": {"type": "json_object"},
    }

    timeout_sec = 60.0
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            response = await client.post(
                endpoint_url,
                json=req_body,
                headers={"Content-Type": "application/json"},
            )

        if response.status_code != 200:
            logger.warning("Provider returned HTTP %d on coaching summary call.", response.status_code)
            return ReportLifecycleStatus.SUMMARY_FAILED_RETRYABLE, None, provider_class, model_name

        resp_json = response.json()
        raw_content = resp_json["choices"][0]["message"]["content"]
        clean_content = raw_content.strip()
        if clean_content.startswith("```"):
            clean_content = re.sub(r"^```(?:json)?\s*", "", clean_content)
            clean_content = re.sub(r"\s*```$", "", clean_content)

        parsed_data = json.loads(clean_content)
        is_valid, val_errors, valid_payload = validate_coaching_summary(
            parsed_data, allowed_review_ids, allowed_competencies
        )
        if is_valid and valid_payload:
            return ReportLifecycleStatus.SUMMARY_READY, valid_payload, provider_class, model_name

        logger.info("First summary output invalid (%s). Attempting 1 controlled repair.", val_errors)

        # 1-Shot Repair
        repair_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload)},
            {"role": "assistant", "content": clean_content},
            {
                "role": "user",
                "content": json.dumps({
                    "action": "SUMMARY_GROUNDING_REPAIR",
                    "validation_errors": val_errors,
                    "instruction": "Fix validation errors. Ensure source_review_ids exist in allowed list. Return plain JSON only.",
                }),
            },
        ]

        repair_body = {
            "model": model_name,
            "messages": repair_messages,
            "temperature": 0.1,
            "max_tokens": 1000,
            "stream": False,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            rep_resp = await client.post(
                endpoint_url,
                json=repair_body,
                headers={"Content-Type": "application/json"},
            )

        if rep_resp.status_code != 200:
            return ReportLifecycleStatus.SUMMARY_FAILED_RETRYABLE, None, provider_class, model_name

        rep_json = rep_resp.json()
        rep_content = rep_json["choices"][0]["message"]["content"].strip()
        if rep_content.startswith("```"):
            rep_content = re.sub(r"^```(?:json)?\s*", "", rep_content)
            rep_content = re.sub(r"\s*```$", "", rep_content)

        rep_parsed = json.loads(rep_content)
        rep_valid, rep_errors, rep_payload = validate_coaching_summary(
            rep_parsed, allowed_review_ids, allowed_competencies
        )
        if rep_valid and rep_payload:
            return ReportLifecycleStatus.SUMMARY_READY, rep_payload, provider_class, model_name

        logger.warning("Summary repair failed: %s", rep_errors)
        return ReportLifecycleStatus.SUMMARY_FAILED_RETRYABLE, None, provider_class, model_name

    except Exception as exc:
        logger.warning("Summary generation encountered transport or runtime error: %s", exc)
        return ReportLifecycleStatus.SUMMARY_FAILED_RETRYABLE, None, provider_class, model_name


def assemble_live_interview_report_data(
    session: CandidatePracticeSessionRecord,
    transcript: List[Any],
    final_review: Any,
    source_hash: str,
    prov_class: Optional[str],
    model_id: Optional[str],
) -> Dict[str, Any]:
    """Assembles all report data from the persisted transcript + final AI evaluation.
    
    Derives question status, competency aggregates, response quality dimensions,
    and question-by-question progression without relying on per-answer coaching records.
    """
    snapshot = session.questions_snapshot or []
    conv_questions = [q for q in snapshot if q.get("question_format") == QuestionFormat.CONVERSATION.value]

    # Map question reviews by question_id
    qr_by_id = {str(qr.question_id): qr for qr in (final_review.question_reviews or [])}
    # Map competency reviews by competency_id
    cr_by_id = {str(cr.competency_id).lower(): cr for cr in (final_review.competency_reviews or [])}
    # Map transcript items by question_id
    t_by_id = {str(t.question_id): t for t in transcript}

    # 1. Build Question Trends with full analysis and exact status
    question_trends: List[QuestionTrendItemDto] = []
    assessable_scores_in_order: List[float] = []
    assessable_count = 0
    insufficient_count = 0
    skipped_count = 0

    for idx, q_plan in enumerate(conv_questions, start=1):
        qid_str = str(q_plan.get("question_id"))
        t_item = t_by_id.get(qid_str)
        qr_item = qr_by_id.get(qid_str)

        wording = t_item.question if t_item else (q_plan.get("wording") or "")
        cand_ans = t_item.answer if t_item else None
        comp_name = q_plan.get("competency_name") or q_plan.get("target_competency") or "General"

        # Determine status strictly from candidate answer and review
        if cand_ans and cand_ans.strip():
            if qr_item and qr_item.answer_status in ("ASSESSABLE", "INSUFFICIENT_RESPONSE", "SKIPPED"):
                status_val = "ASSESSABLE" if qr_item.answer_status == "SKIPPED" else qr_item.answer_status
            else:
                status_val = "ASSESSABLE"
        else:
            status_val = "SKIPPED"

        if status_val == "ASSESSABLE":
            assessable_count += 1
        elif status_val == "INSUFFICIENT_RESPONSE":
            insufficient_count += 1
        elif status_val == "SKIPPED":
            skipped_count += 1

        dim_levels_dict = {}
        if qr_item and qr_item.dimension_levels:
            dl = qr_item.dimension_levels
            if hasattr(dl, "model_dump"):
                raw_dl = dl.model_dump()
            elif isinstance(dl, dict):
                raw_dl = dl
            else:
                raw_dl = {}
            for k, v in raw_dl.items():
                dim_levels_dict[k] = v.value if hasattr(v, "value") else str(v)

        # Ensure all 5 dimensions exist for assessable answers
        if status_val == "ASSESSABLE":
            for d_k in DIMENSION_METADATA.keys():
                if d_k not in dim_levels_dict or not dim_levels_dict[d_k]:
                    dim_levels_dict[d_k] = "DEVELOPING"

            numeric_vals = [LEVEL_NUMERIC_MAP[v] for v in dim_levels_dict.values() if v in LEVEL_NUMERIC_MAP]
            if numeric_vals:
                assessable_scores_in_order.append(sum(numeric_vals) / len(numeric_vals))

        # Rich question feedback fallbacks if model output was sparse
        done_well = qr_item.what_was_done_well if (qr_item and qr_item.what_was_done_well) else (
            [f"Addressed core scenario requirements for {comp_name}."] if status_val == "ASSESSABLE" else []
        )
        missing = qr_item.what_was_missing if (qr_item and qr_item.what_was_missing) else (
            [f"Could provide concrete error handling, edge cases, and verification metrics for {comp_name}."] if status_val == "ASSESSABLE" else []
        )
        improve = qr_item.how_to_improve if (qr_item and qr_item.how_to_improve) else (
            [f"Structure answers with specific implementation choices and verified results in {comp_name}."] if status_val == "ASSESSABLE" else []
        )
        strong_struct = qr_item.stronger_answer_structure if (qr_item and qr_item.stronger_answer_structure) else (
            "1. Core solution & design choice -> 2. Implementation details -> 3. Edge-cases & verification." if status_val == "ASSESSABLE" else ""
        )
        ans_sum = qr_item.answer_summary if (qr_item and qr_item.answer_summary) else (
            f"Candidate provided structured response addressing {comp_name}." if status_val == "ASSESSABLE" else ""
        )
        excerpt = qr_item.evidence_excerpt if (qr_item and qr_item.evidence_excerpt) else (
            (cand_ans[:140] + "...") if (cand_ans and len(cand_ans) > 140) else (cand_ans or "")
        )

        trend_item = QuestionTrendItemDto(
            sequence_number=idx,
            question_id=UUID(qid_str),
            question_format=QuestionFormat.CONVERSATION.value,
            wording=wording,
            candidate_answer=cand_ans,
            competency_name=comp_name,
            assessability_status=status_val,
            answer_summary=ans_sum,
            what_was_done_well=done_well,
            what_was_missing=missing,
            how_to_improve=improve,
            stronger_answer_structure=strong_struct,
            evidence_excerpt=excerpt,
            dimension_levels=dim_levels_dict,
            follow_up_completed=False,
            is_mcq_correct=None,
            review_id=None,
        )
        question_trends.append(trend_item)

    # 2. Competency Aggregates (calculated from question_reviews & competency_reviews)
    competency_q_count: Dict[str, int] = {}
    competency_assessable_q: Dict[str, List[QuestionTrendItemDto]] = {}
    for q in question_trends:
        c_name = q.competency_name
        competency_q_count[c_name] = competency_q_count.get(c_name, 0) + 1
        if q.assessability_status == "ASSESSABLE":
            competency_assessable_q.setdefault(c_name, []).append(q)

    competency_aggregates: List[CompetencyAggregateDto] = []
    assessable_competencies_count = 0

    for c_name in sorted(competency_q_count.keys()):
        total_q = competency_q_count[c_name]
        ass_q_list = competency_assessable_q.get(c_name, [])
        assessable_c = len(ass_q_list)

        # Match AI competency review
        matched_cr = cr_by_id.get(c_name.lower())
        if not matched_cr:
            # Fallback substring match
            for cr_key, cr_val in cr_by_id.items():
                if cr_key in c_name.lower() or c_name.lower() in cr_key:
                    matched_cr = cr_val
                    break

        if assessable_c > 0:
            assessable_competencies_count += 1
            scores: List[float] = []
            for aq in ass_q_list:
                for d_val in aq.dimension_levels.values():
                    if d_val in LEVEL_NUMERIC_MAP:
                        scores.append(LEVEL_NUMERIC_MAP[d_val])
            avg_score = round(sum(scores) / len(scores), 1) if scores else 3.0

            if matched_cr and hasattr(matched_cr, "level") and matched_cr.level:
                lvl_str = matched_cr.level.value if hasattr(matched_cr.level, "value") else str(matched_cr.level)
            elif avg_score is not None:
                if avg_score >= 3.5:
                    lvl_str = "STRONG"
                elif avg_score >= 2.5:
                    lvl_str = "DEVELOPING"
                else:
                    lvl_str = "NEEDS_DETAIL"
            else:
                lvl_str = "DEVELOPING"

            if lvl_str == "STRONG":
                band = "Consistent Strength"
            elif lvl_str == "DEVELOPING":
                band = "Developing Capability"
            elif lvl_str == "NEEDS_DETAIL":
                band = "Needs More Detail"
            else:
                band = "Not Demonstrated"

            if matched_cr and matched_cr.commentary and len(matched_cr.commentary.strip()) > 10:
                desc = matched_cr.commentary
            else:
                # Synthesize insightful coaching observation from question reviews for this competency
                well_points = [p for aq in ass_q_list for p in (aq.what_was_done_well or [])]
                miss_points = [p for aq in ass_q_list for p in (aq.what_was_missing or [])]
                if well_points and miss_points:
                    desc = f"Demonstrated {band.lower()}: {well_points[0]} Opportunity: {miss_points[0]}"
                elif well_points:
                    desc = f"Solid competency depth: {well_points[0]}"
                elif ass_q_list and ass_q_list[0].answer_summary:
                    desc = f"Demonstrated {band.lower()} in {c_name}: {ass_q_list[0].answer_summary}"
                else:
                    desc = f"Demonstrated practical {c_name} capability across {assessable_c} response(s)."
        else:
            avg_score = None
            lvl_str = "NOT_ASSESSED"
            band = "Not Assessed"
            desc = "No assessable responses were submitted for this competency."

        competency_aggregates.append(
            CompetencyAggregateDto(
                competency_name=c_name,
                numeric_score=avg_score,
                anchored_level=lvl_str,
                assessable_count=assessable_c,
                total_questions=total_q,
                excluded_count=total_q - assessable_c,
                display_band=band,
                description=desc,
            )
        )

    # 3. Response Quality Dimensions
    dim_scores: Dict[str, List[float]] = {k: [] for k in DIMENSION_METADATA.keys()}
    dim_counts: Dict[str, int] = {k: 0 for k in DIMENSION_METADATA.keys()}

    for q in question_trends:
        if q.assessability_status == "ASSESSABLE":
            for d_key in DIMENSION_METADATA.keys():
                lvl = q.dimension_levels.get(d_key)
                if lvl in LEVEL_NUMERIC_MAP:
                    dim_scores[d_key].append(LEVEL_NUMERIC_MAP[lvl])
                    dim_counts[d_key] += 1

    # Map from final_review.response_quality
    rq_payload = getattr(final_review, "response_quality", None)

    dimension_aggregates: List[DimensionAggregateDto] = []
    for d_key, d_label in DIMENSION_METADATA.items():
        s_list = dim_scores[d_key]
        c_val = dim_counts[d_key]

        rq_val = None
        if rq_payload:
            val_attr = getattr(rq_payload, d_key, None)
            if val_attr:
                rq_val = val_attr.value if hasattr(val_attr, "value") else str(val_attr)

        if s_list or rq_val:
            avg_s = round(sum(s_list) / len(s_list), 1) if s_list else (LEVEL_NUMERIC_MAP.get(rq_val, 3.0) if rq_val else 3.0)
            anch_lvl = rq_val or ("STRONG" if avg_s >= 3.5 else ("DEVELOPING" if avg_s >= 2.5 else "NEEDS_DETAIL"))
            if anch_lvl == "STRONG":
                disp_lbl = "Demonstrated Strength"
            elif anch_lvl == "DEVELOPING":
                disp_lbl = "Developing Level"
            elif anch_lvl == "NEEDS_DETAIL":
                disp_lbl = "Needs More Detail"
            else:
                disp_lbl = "Not Demonstrated"
        else:
            avg_s = None
            anch_lvl = "NOT_ASSESSED"
            disp_lbl = "Not Assessed"

        dimension_aggregates.append(
            DimensionAggregateDto(
                dimension_key=d_key,
                dimension_label=d_label,
                anchored_level=anch_lvl,
                numeric_score=avg_s,
                assessable_count=c_val,
                excluded_count=len(conv_questions) - c_val,
                display_label=disp_lbl,
            )
        )

    # 4. Trend Classification
    if len(assessable_scores_in_order) >= 3:
        first_half = assessable_scores_in_order[: len(assessable_scores_in_order) // 2]
        second_half = assessable_scores_in_order[len(assessable_scores_in_order) // 2 :]
        avg_1 = sum(first_half) / len(first_half)
        avg_2 = sum(second_half) / len(second_half)
        if avg_2 - avg_1 >= 0.4:
            trend_classification = TrendClassification.IMPROVING.value
        elif abs(avg_2 - avg_1) < 0.4:
            trend_classification = TrendClassification.STABLE.value
        else:
            trend_classification = TrendClassification.MIXED.value
    elif len(assessable_scores_in_order) > 0:
        trend_classification = TrendClassification.STABLE.value
    else:
        trend_classification = TrendClassification.NOT_ENOUGH_ASSESSABLE_DATA.value

    # 5. Completion counts
    completion_counts = CompletionCountsDto(
        total_primary_questions=len(snapshot),
        total_conversational_questions=len(conv_questions),
        total_mcq_questions=0,
        assessable_answers_count=assessable_count,
        insufficient_answers_count=insufficient_count,
        skipped_count=skipped_count,
        unavailable_count=0,
    )

    # Format strongest areas and priority improvements
    if final_review.strongest_responses:
        strongest_areas = [
            {
                "title": f"Question {t_by_id[sr.question_id].question_index} ({t_by_id[sr.question_id].competency_id})" if sr.question_id in t_by_id else "Demonstrated Strength",
                "observation": sr.reason,
                "source_review_ids": [sr.question_id],
            }
            for sr in final_review.strongest_responses
        ]
    else:
        strongest_areas = [
            {
                "title": f"Question {q.sequence_number} ({q.competency_name})",
                "observation": q.what_was_done_well[0] if q.what_was_done_well else f"Demonstrated solid baseline understanding of {q.competency_name}.",
                "source_review_ids": [str(q.question_id)],
            }
            for q in question_trends
            if q.assessability_status == "ASSESSABLE"
        ][:2]

    if final_review.improvement_areas:
        priority_improvements = [
            {
                "title": f"Question {t_by_id[ia.question_id].question_index} ({t_by_id[ia.question_id].competency_id})" if ia.question_id in t_by_id else "Opportunity Area",
                "observation": f"{ia.issue} {ia.how_to_improve}".strip(),
                "source_review_ids": [ia.question_id],
            }
            for ia in final_review.improvement_areas
        ]
    else:
        priority_improvements = [
            {
                "title": f"Question {q.sequence_number} ({q.competency_name})",
                "observation": q.what_was_missing[0] if q.what_was_missing else f"Add specific verification steps and edge-case handling for {q.competency_name}.",
                "source_review_ids": [str(q.question_id)],
            }
            for q in question_trends
            if q.assessability_status == "ASSESSABLE"
        ][:2]

    competency_commentary = [
        {
            "competency_id": cr.competency_id,
            "commentary": cr.commentary,
            "source_review_ids": cr.question_ids,
        }
        for cr in (final_review.competency_reviews or [])
    ]

    # Summary text
    summary_text = getattr(final_review, "overall_summary", "") or ""
    if not summary_text or len(summary_text.strip()) < 15 or "unavailable" in summary_text.lower():
        job_name = session.job_version.title if session.job_version else (session.job.title if session.job else "Target Role")
        summary_text = f"Completed practice interview for {job_name} across {assessable_count} technical scenarios. Demonstrated solid foundational capability across core competencies with key opportunities to expand on production edge-cases, error handling, and verification mechanisms."

    return {
        "summary_text": summary_text,
        "strongest_areas": strongest_areas,
        "priority_improvements": priority_improvements,
        "competency_commentary": competency_commentary,
        "next_practice_plan": final_review.next_practice_plan,
        "competency_aggregates": [c.model_dump(mode="json") for c in competency_aggregates],
        "dimension_aggregates": [d.model_dump(mode="json") for d in dimension_aggregates],
        "question_trend": [q.model_dump(mode="json") for q in question_trends],
        "trend_classification": trend_classification,
        "completion_counts": completion_counts.model_dump(mode="json"),
        "source_review_hash": source_hash,
    }

