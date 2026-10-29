"""Domain service for Phase 3G.1 AI Question Provenance and Release Eligibility.

Enforces strict local-AI provenance traceability, 20 server-side validation checks,
and authoritative release eligibility evaluation across interview sets, releases, and candidate retrieval.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError

from app.models.interview import AiInterviewQuestionSetRecord
from app.models.job import JobRequisition, JobVersion
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    QuestionDifficulty,
    QuestionFormat,
    QuestionType,
    ReleaseIneligibilityReasonCode,
    ValidationErrorCode,
    ValidationState,
)
from app.schemas.safety_policy import check_prohibited_topics

logger = logging.getLogger("ai_hiring.ai_provenance_service")

# Policy and Schema Constants
CANONICAL_GUARDRAIL_POLICY_VERSION = "1.0.0"
CONVERSATIONAL_SCHEMA_VERSION = "practice-question.v1"
MCQ_SCHEMA_VERSION = "practice-mcq.v1"


# --- Strict Pydantic Models (extra forbidden) ---

class StrictMcqOption(BaseModel):
    id: str = Field(min_length=1, max_length=10)
    text: str = Field(min_length=1, max_length=500)

    model_config = {"extra": "forbid"}


class StrictConversationalQuestionPayload(BaseModel):
    schema_version: str = Field(default=CONVERSATIONAL_SCHEMA_VERSION)
    question: str = Field(min_length=15, max_length=800)
    competency: str = Field(min_length=2, max_length=200)
    question_type: str = Field(default="TECHNICAL")
    intent_summary: str = Field(min_length=5, max_length=400)
    safe_follow_up_goal: Optional[str] = Field(default=None, max_length=400)
    source_requirement_ids: List[str] = Field(default_factory=list)
    estimated_answer_minutes: int = Field(default=3, ge=1, le=10)

    model_config = {"extra": "forbid"}


class StrictMcqPayload(BaseModel):
    schema_version: str = Field(default=MCQ_SCHEMA_VERSION)
    stem: str = Field(min_length=15, max_length=800)
    options: List[StrictMcqOption] = Field(min_length=2, max_length=5)
    correct_option_id: str = Field(min_length=1, max_length=10)
    explanation: str = Field(min_length=10, max_length=800)
    competency: str = Field(min_length=2, max_length=200)
    source_requirement_ids: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


@dataclass
class ItemValidationOutcome:
    is_valid: bool
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


@dataclass
class ReleaseEligibilityResult:
    is_eligible: bool
    reason_code: Optional[ReleaseIneligibilityReasonCode] = None
    safe_message: Optional[str] = None
    blocking_items: List[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_eligible": self.is_eligible,
            "reason_code": self.reason_code.value if self.reason_code else None,
            "safe_message": self.safe_message,
            "blocking_items": self.blocking_items or [],
        }


import difflib
import unicodedata

def normalize_stem(text: str) -> str:
    """Performs canonical normalization for question stem comparison:
    1. Unicode normalization (NFKC)
    2. Lowercase
    3. Remove non-semantic punctuation
    4. Collapse repeated whitespace and trim
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_strong_stem_duplicate(stem_a: str, stem_b: str, threshold: float = 0.80) -> bool:
    """Deterministically detects exact and strong near-duplicates between question stems."""
    norm_a = normalize_stem(stem_a)
    norm_b = normalize_stem(stem_b)
    if not norm_a or not norm_b:
        return False
    # Exact normalized match
    if norm_a == norm_b:
        return True
    # If substantial overlap, check SequenceMatcher similarity ratio
    ratio = difflib.SequenceMatcher(None, norm_a, norm_b).ratio()
    if ratio >= threshold:
        return True
    # Word token Jaccard similarity for questions of substantial length
    words_a = set(norm_a.split())
    words_b = set(norm_b.split())
    if len(words_a) >= 4 and len(words_b) >= 4:
        intersection = words_a.intersection(words_b)
        union = words_a.union(words_b)
        if union and (len(intersection) / len(union)) >= 0.75:
            return True
    return False


def validate_conversational_question_text(
    question_text: str,
    competency_name: str,
    job_version: Optional[JobVersion] = None,
    source_requirement_ids: Optional[List[str]] = None,
    existing_question_texts: Optional[List[str]] = None,
    interview_mode: Optional[str] = None,
    cross_mode_existing_texts: Optional[List[str]] = None,
) -> ItemValidationOutcome:
    """Performs 20 strict server-side validation checks on a candidate-visible conversational question."""
    raw = (question_text or "").strip()
    if not raw:
        return ItemValidationOutcome(False, "WORDING_EMPTY", "Question text cannot be empty.")

    # 1. Length bounds
    if len(raw) < 15:
        return ItemValidationOutcome(False, "WORDING_TOO_SHORT", "Question text is too short (minimum 15 characters).")
    if len(raw) > 800:
        return ItemValidationOutcome(False, "WORDING_TOO_LONG", "Question text is too long (maximum 800 characters).")

    # 2. Control characters, URLs, HTML, Markdown headers / code blocks, Tool execution tokens
    if re.search(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", raw):
        return ItemValidationOutcome(False, "UNSAFE_CONTROL_CHARACTERS", "Question contains prohibited control characters.")
    if re.search(r"<[^>]+>", raw):
        return ItemValidationOutcome(False, "PROHIBITED_HTML", "Question contains prohibited HTML tags.")
    if re.search(r"https?://|www\.", raw, re.IGNORECASE):
        return ItemValidationOutcome(False, "PROHIBITED_URL", "Question contains prohibited URL links.")
    if re.search(r"```|^#+\s", raw, re.MULTILINE):
        return ItemValidationOutcome(False, "PROHIBITED_MARKDOWN", "Question contains prohibited Markdown headers or code blocks.")
    if re.search(r"<tool_call>|<function_call>|eval\(|exec\(", raw, re.IGNORECASE):
        return ItemValidationOutcome(False, "PROHIBITED_TOOL_TOKENS", "Question contains prohibited tool execution directives.")

    # Audio-specific restrictions: No visual/code/table/diagram instructions
    if (interview_mode or "").upper() == "AUDIO":
        audio_visual_terms = [
            r"\bwrite a (function|class|method|script|query|code)\b",
            r"\blook at the (code|table|diagram|chart|graph|image|snippet)\b",
            r"\bread the (following|above) (table|text|code|snippet)\b",
            r"\bcalculate (on paper|the exact formula)\b",
            r"\bfill in the (blank|table)\b",
            r"\bsee below\b",
        ]
        for pat in audio_visual_terms:
            if re.search(pat, raw, re.IGNORECASE):
                return ItemValidationOutcome(False, "AUDIO_VISUAL_QUESTION_NOT_ALLOWED", "Audio interview questions cannot require written code, tables, or diagrams.")

    # 3. Prohibited & protected topics
    violation = check_prohibited_topics(raw)
    if violation is not None:
        category, _ = violation
        return ItemValidationOutcome(False, f"PROHIBITED_TOPIC_{category}", f"Question contains prohibited topic: {category}")

    # 4. Invented candidate claims (e.g., "In your role as VP at Google", "Given your 10 years...")
    invented_claim_patterns = [
        r"\bin your (previous |former |past )?(role|job|time|experience) at [A-Z][a-zA-Z0-9]+",
        r"\bas you (managed|built|designed|led|founded) at [A-Z][a-zA-Z0-9]+",
        r"\bgiven your \d+ years of experience as\b",
        r"\bsince you worked at [A-Z][a-zA-Z0-9]+",
    ]
    for pat in invented_claim_patterns:
        if re.search(pat, raw, re.IGNORECASE):
            return ItemValidationOutcome(False, "INVENTED_CANDIDATE_CLAIM", "Model invented specific past candidate employers or history.")

    # 5. Hiring recommendation, score, rank, evaluation tokens in question text
    lower = raw.lower()
    for token in ["hiring recommendation", "candidate score:", "pass/fail", "selection decision", "candidate ranking"]:
        if token in lower:
            return ItemValidationOutcome(False, "PROHIBITED_EVALUATION_LANGUAGE", "Question text contains prohibited scoring or selection language.")

    # 6. Single primary ask rule (reject multiple distinct question marks)
    q_mark_count = raw.count("?")
    if q_mark_count > 1:
        # Check if it's genuinely multiple separate questions
        sub_questions = [s.strip() for s in raw.split("?") if s.strip()]
        if len(sub_questions) > 1:
            return ItemValidationOutcome(False, "MULTIPLE_PRIMARY_ASKS", "Question must contain only one primary ask (multiple question marks detected).")

    # 7. Check for duplicate questions in the same set
    if existing_question_texts:
        for ex in existing_question_texts:
            if ex and is_strong_stem_duplicate(raw, ex, threshold=0.80):
                return ItemValidationOutcome(False, "DUPLICATE_QUESTION", "Near-duplicate question detected in the same question set.")

    # 7b. Check for cross-mode duplicates against the other interview mode set for the same revision
    if cross_mode_existing_texts:
        for ex in cross_mode_existing_texts:
            if ex and is_strong_stem_duplicate(raw, ex, threshold=0.80):
                return ItemValidationOutcome(False, "CROSS_MODE_DUPLICATE_QUESTION", "Exact or near-duplicate question exists in another interview mode for this job revision (CROSS_MODE_DUPLICATE_QUESTION).")

    # 8. Source requirement ID validation against job version
    if job_version and source_requirement_ids:
        valid_req_ids = {str(r.requirement_id) for r in (job_version.requirements or [])}
        for rid in source_requirement_ids:
            if str(rid) not in valid_req_ids:
                return ItemValidationOutcome(False, "FORGED_REQUIREMENT_ID", f"Source requirement ID '{rid}' does not belong to target job revision.")

    return ItemValidationOutcome(True)


def validate_mcq_structure_and_safety(
    stem: str,
    options: List[Dict[str, str]],
    correct_option_id: str,
    explanation: str,
    competency_name: str,
    job_version: Optional[JobVersion] = None,
    source_requirement_ids: Optional[List[str]] = None,
    existing_stems: Optional[List[str]] = None,
) -> ItemValidationOutcome:
    """Performs strict server-side validation checks on a candidate-visible multiple choice question."""
    # 1. Stem validation
    stem_clean = (stem or "").strip()
    if not stem_clean or len(stem_clean) < 15:
        return ItemValidationOutcome(False, "MCQ_STEM_TOO_SHORT", "MCQ stem is too short (minimum 15 characters).")
    if len(stem_clean) > 800:
        return ItemValidationOutcome(False, "MCQ_STEM_TOO_LONG", "MCQ stem exceeds maximum 800 characters.")

    # Control chars, HTML, URLs in stem
    if re.search(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", stem_clean) or re.search(r"<[^>]+>", stem_clean) or re.search(r"https?://|www\.", stem_clean, re.IGNORECASE):
        return ItemValidationOutcome(False, "MCQ_STEM_UNSAFE", "MCQ stem contains prohibited HTML, URLs, or control characters.")

    # Prohibited topics in stem
    violation = check_prohibited_topics(stem_clean)
    if violation is not None:
        category, _ = violation
        return ItemValidationOutcome(False, f"PROHIBITED_TOPIC_{category}", f"MCQ contains prohibited topic: {category}")

    # 2. Options validation (2 to 5 options)
    if not options or len(options) < 2 or len(options) > 5:
        return ItemValidationOutcome(False, "MCQ_INVALID_OPTION_COUNT", f"MCQ must have between 2 and 5 options (found {len(options) if options else 0}).")

    seen_ids: Set[str] = set()
    seen_texts: Set[str] = set()
    for opt in options:
        opt_id = (opt.get("option_id") or opt.get("id") or "").strip().upper()
        opt_text = (opt.get("text") or "").strip()
        if not opt_id:
            return ItemValidationOutcome(False, "MCQ_MISSING_OPTION_ID", "Option is missing required identifier.")
        if opt_id in seen_ids:
            return ItemValidationOutcome(False, "MCQ_DUPLICATE_OPTION_ID", f"Duplicate option ID '{opt_id}' in MCQ options.")
        seen_ids.add(opt_id)

        if not opt_text:
            return ItemValidationOutcome(False, "MCQ_EMPTY_OPTION_TEXT", f"Option '{opt_id}' text cannot be empty.")
        if len(opt_text) > 500:
            return ItemValidationOutcome(False, "MCQ_OPTION_TEXT_TOO_LONG", f"Option '{opt_id}' text exceeds 500 characters.")

        norm_text = opt_text.lower()
        if norm_text in seen_texts:
            return ItemValidationOutcome(False, "MCQ_DUPLICATE_OPTION_TEXT", f"Duplicate option text found in MCQ option '{opt_id}'.")
        seen_texts.add(norm_text)

    # 3. Correct option verification
    correct_clean = (correct_option_id or "").strip().upper()
    if not correct_clean or correct_clean not in seen_ids:
        return ItemValidationOutcome(False, "MCQ_INVALID_CORRECT_OPTION", f"Correct option '{correct_clean}' is not in valid option IDs {sorted(seen_ids)}.")

    # 4. Explanation validation
    exp_clean = (explanation or "").strip()
    if not exp_clean or len(exp_clean) < 10:
        return ItemValidationOutcome(False, "MCQ_EXPLANATION_TOO_SHORT", "MCQ explanation is required (minimum 10 characters).")
    if len(exp_clean) > 800:
        return ItemValidationOutcome(False, "MCQ_EXPLANATION_TOO_LONG", "MCQ explanation exceeds 800 characters.")

    # 5. Answer cue in stem leakage check (e.g., "The correct answer is Option B because...")
    for opt in options:
        opt_text = opt.get("text", "").strip()
        if opt_text and len(opt_text) > 20 and opt_text.lower() in stem_clean.lower():
            return ItemValidationOutcome(False, "MCQ_ANSWER_CUE_IN_STEM", "MCQ stem leaks the complete text of an option.")

    # 6. Check duplicate stem
    if existing_stems:
        norm_stem = re.sub(r"[^\w\s]", "", stem_clean.lower())
        for ex in existing_stems:
            norm_ex = re.sub(r"[^\w\s]", "", (ex or "").lower().strip())
            if norm_stem and norm_ex and norm_stem == norm_ex:
                return ItemValidationOutcome(False, "DUPLICATE_MCQ_STEM", "Duplicate MCQ stem detected in the same question set.")

    # 7. Source requirement ID validation against job version
    if job_version and source_requirement_ids:
        valid_req_ids = {str(r.requirement_id) for r in (job_version.requirements or [])}
        for rid in source_requirement_ids:
            if str(rid) not in valid_req_ids:
                return ItemValidationOutcome(False, "FORGED_REQUIREMENT_ID", f"Source requirement ID '{rid}' does not belong to target job revision.")

    return ItemValidationOutcome(True)


# --- Canonical Provenance Builder ---

def build_canonical_question_provenance(
    origin: AuthoringOrigin,
    provider_profile_id: Optional[UUID],
    provider_class: str,
    model_identifier: Optional[str],
    invocation_id: Optional[str],
    job_version_id: Optional[UUID],
    job_revision: int,
    source_requirement_ids: List[str],
    set_version: int,
    prompt_template_version: str = "practice-question.v1",
    guardrail_policy_version: str = CANONICAL_GUARDRAIL_POLICY_VERSION,
    schema_version: str = CONVERSATIONAL_SCHEMA_VERSION,
    validation_status: str = "PASSED",
    validation_result: Optional[str] = None,
    is_edited: bool = False,
    reviewer_user_id: Optional[UUID] = None,
    reviewed_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Constructs complete, standardized provenance metadata for a practice question item."""
    return {
        "authoring_origin": origin.value if hasattr(origin, "value") else str(origin),
        "provider_profile_id": str(provider_profile_id) if provider_profile_id else None,
        "provider_class": provider_class,
        "model_identifier": model_identifier or "qwen/qwen3-4b-2507",
        "invocation_id": invocation_id or str(uuid4()),
        "prompt_template_version": prompt_template_version,
        "guardrail_policy_version": guardrail_policy_version,
        "schema_version": schema_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "validation_status": validation_status,
        "validation_result": validation_result or "PASSED",
        "job_version_id": str(job_version_id) if job_version_id else None,
        "job_revision": job_revision,
        "source_requirement_ids": [str(r) for r in source_requirement_ids],
        "set_version": set_version,
        "is_edited": is_edited,
        "reviewer_user_id": str(reviewer_user_id) if reviewer_user_id else None,
        "reviewed_at": reviewed_at.isoformat() if reviewed_at else None,
    }


# --- Authoritative Release Eligibility Service ---

def evaluate_release_eligibility(
    ai_set: AiInterviewQuestionSetRecord,
    target_job_version: JobVersion,
) -> ReleaseEligibilityResult:
    """Authoritative release eligibility evaluation.
    
    A practice set is eligible for a new Phase 3G release ONLY IF:
    1. Set lifecycle_status == 'APPROVED'
    2. Exact job revision matches target ready job revision
    3. Has at least 1 question item
    4. Every conversational question has origin in [LOCAL_LLM_GENERATED, LOCAL_LLM_GENERATED_RECRUITER_EDITED]
    5. Every MCQ question has origin in [LOCAL_LLM_GENERATED, LOCAL_LLM_GENERATED_RECRUITER_EDITED]
    6. Complete local-AI model/provider provenance exists
    7. Every question validation_status == 'PASSED'
    8. No legacy deterministic or legacy manual questions exist
    9. No stale, draft, invalid, or generation-failed questions exist
    """
    if not ai_set:
        return ReleaseEligibilityResult(
            is_eligible=False,
            reason_code=ReleaseIneligibilityReasonCode.NOT_APPROVED,
            safe_message="Question set record does not exist.",
        )

    # 1. Approval check
    if ai_set.lifecycle_status != "APPROVED":
        return ReleaseEligibilityResult(
            is_eligible=False,
            reason_code=ReleaseIneligibilityReasonCode.NOT_APPROVED,
            safe_message=f"Question set is in {ai_set.lifecycle_status} state and has not been approved.",
        )

    # 2. Revision check
    raw_rev = getattr(target_job_version, "revision", None)
    if isinstance(raw_rev, int):
        target_rev = raw_rev
    else:
        raw_vnum = getattr(target_job_version, "version_number", None)
        target_rev = raw_vnum if isinstance(raw_vnum, int) else 1

    # Check for revision mismatch: either job_version_id matches or job_revision matches target_rev
    revision_matches = False
    if ai_set.job_version_id and target_job_version.job_version_id and ai_set.job_version_id == target_job_version.job_version_id:
        revision_matches = True
    elif ai_set.job_revision and ai_set.job_revision == target_rev:
        revision_matches = True

    if not revision_matches:
        return ReleaseEligibilityResult(
            is_eligible=False,
            reason_code=ReleaseIneligibilityReasonCode.STALE_JOB_REVISION,
            safe_message=f"Question set was approved for revision #{ai_set.job_revision or 'unknown'}, but current job revision is #{target_rev}.",
            blocking_items=[],
        )

    items = ai_set.items or []
    if not items or len(items) == 0:
        return ReleaseEligibilityResult(
            is_eligible=False,
            reason_code=ReleaseIneligibilityReasonCode.EMPTY_SET,
            safe_message="Question set contains zero questions.",
        )

    blocking_items: List[Dict[str, Any]] = []

    for idx, it in enumerate(items):
        slot_num = idx + 1
        origin_val = it.get("authoring_origin") or it.get("origin") or "DETERMINISTIC"
        norm_origin = AuthoringOrigin.normalize_to_canonical(origin_val)

        # Origin eligibility check
        if not AuthoringOrigin.is_eligible_for_release(norm_origin):
            if norm_origin == AuthoringOrigin.LEGACY_DETERMINISTIC:
                blocking_items.append({
                    "slot_number": slot_num,
                    "slot_id": it.get("slot_id"),
                    "reason_code": ReleaseIneligibilityReasonCode.LEGACY_DETERMINISTIC_PRESENT.value,
                    "message": f"Question #{slot_num} is a legacy deterministic question and cannot be released in Phase 3G.",
                })
            else:
                blocking_items.append({
                    "slot_number": slot_num,
                    "slot_id": it.get("slot_id"),
                    "reason_code": ReleaseIneligibilityReasonCode.LEGACY_MANUAL_PRESENT.value,
                    "message": f"Question #{slot_num} is a manually authored question without AI generation provenance.",
                })
            continue

        # Validation status check
        val_status = it.get("validation_status", "PENDING")
        if val_status != "PASSED":
            blocking_items.append({
                "slot_number": slot_num,
                "slot_id": it.get("slot_id"),
                "reason_code": ReleaseIneligibilityReasonCode.VALIDATION_FAILED.value,
                "message": f"Question #{slot_num} has validation state '{val_status}' ({it.get('safe_failure_code')}).",
            })
            continue

        # Provenance completeness check
        has_prov = bool(it.get("model_identifier") or it.get("provider_provenance") or ai_set.model_identifier)
        if not has_prov:
            blocking_items.append({
                "slot_number": slot_num,
                "slot_id": it.get("slot_id"),
                "reason_code": ReleaseIneligibilityReasonCode.MISSING_PROVENANCE.value,
                "message": f"Question #{slot_num} lacks required local model provenance.",
            })

    if blocking_items:
        primary_reason = ReleaseIneligibilityReasonCode(blocking_items[0]["reason_code"])
        return ReleaseEligibilityResult(
            is_eligible=False,
            reason_code=primary_reason,
            safe_message=f"Question set is ineligible for release: {blocking_items[0]['message']} Please regenerate as an AI set.",
            blocking_items=blocking_items,
        )

    return ReleaseEligibilityResult(
        is_eligible=True,
        safe_message="Question set is eligible for Phase 3G release.",
    )
