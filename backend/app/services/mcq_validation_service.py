"""Authoritative backend validation service for Multiple Choice Questions (MCQs)."""
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from app.schemas.safety_policy import check_prohibited_topics

# Negative phrasing detection patterns
PROHIBITED_NEGATIVE_PATTERNS = [
    r"\bwhich of the following is not\b",
    r"\ball of the following are .* except\b",
    r"\bwhich is not\b",
    r"\bwhich one is not\b",
    r"\bwhich is false\b",
    r"\bwhich statement is incorrect\b",
]

# "All of the above" / "None of the above" patterns
PROHIBITED_META_OPTION_PATTERNS = [
    r"^all of the above\.?$",
    r"^none of the above\.?$",
    r"^both [a-d] and [a-d]\.?$",
    r"^all the above\.?$",
    r"^none of these\.?$",
]

# Resume/Candidate-derived leakage patterns
PROHIBITED_CANDIDATE_LEAK_PATTERNS = [
    r"\baccording to (your|the) resume\b",
    r"\bin your previous role at\b",
    r"\byour experience with\b",
    r"\bas stated in your application\b",
    r"\bcandidate\b",
]

# Personality / culture-fit keywords
PROHIBITED_CULTURE_FIT_PATTERNS = [
    r"\bculture fit\b",
    r"\bpersonality type\b",
    r"\bmyers-briggs\b",
    r"\bmbti\b",
    r"\benneagram\b",
    r"\bhoroscope\b",
    r"\bzodiac\b",
]


def normalize_text_for_comparison(text: str) -> str:
    """Normalizes text by stripping punctuation, extra whitespace, and case folding."""
    if not text:
        return ""
    clean = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(clean.split())


class McqValidationResult:
    def __init__(self, is_valid: bool, error_code: Optional[str] = None, error_message: Optional[str] = None):
        self.is_valid = is_valid
        self.error_code = error_code
        self.error_message = error_message

    def __bool__(self) -> bool:
        return self.is_valid


def validate_mcq_item(
    question_text: str,
    options: List[Dict[str, Any]],
    correct_option_id: str,
    answer_explanation: str,
    competency_name: Optional[str] = None,
    requirement_id: Optional[Any] = None,
    existing_question_texts: Optional[List[str]] = None,
) -> McqValidationResult:
    """Strictly validates an individual MCQ item against all policy, safety, and correctness rules."""
    
    # 1. Question Text Checks
    if not question_text or not question_text.strip():
        return McqValidationResult(False, "EMPTY_QUESTION_TEXT", "Question text cannot be empty.")
    
    q_trimmed = question_text.strip()
    if len(q_trimmed) < 10:
        return McqValidationResult(False, "QUESTION_TOO_SHORT", "Question text must be at least 10 characters.")
    if len(q_trimmed) > 1200:
        return McqValidationResult(False, "QUESTION_TOO_LONG", "Question text exceeds maximum limit of 1200 characters.")
    
    # HTML / Script injection check
    if re.search(r"<[^>]+>", q_trimmed) or "javascript:" in q_trimmed.lower() or "<script" in q_trimmed.lower():
        return McqValidationResult(False, "PROHIBITED_HTML_CONTENT", "Question text cannot contain HTML or scripts.")
    
    # Control characters
    if any(ord(c) < 32 and c not in ('\n', '\r', '\t') for c in q_trimmed):
        return McqValidationResult(False, "PROHIBITED_CONTROL_CHARACTERS", "Question text contains invalid control characters.")

    # 2. Options Bounding & Integrity Checks
    if not options or not isinstance(options, list):
        return McqValidationResult(False, "MISSING_OPTIONS", "MCQ must have options list.")
    
    if len(options) < 2:
        return McqValidationResult(False, "TOO_FEW_OPTIONS", "MCQ must have at least 2 options.")
    if len(options) > 5:
        return McqValidationResult(False, "TOO_MANY_OPTIONS", "MCQ cannot have more than 5 options.")
    
    option_ids: Set[str] = set()
    normalized_options: Set[str] = set()
    
    for idx, opt in enumerate(options):
        if not isinstance(opt, dict):
            return McqValidationResult(False, "INVALID_OPTION_FORMAT", f"Option #{idx+1} format is invalid.")
        
        opt_id = str(opt.get("option_id") or "").strip()
        opt_text = str(opt.get("text") or "").strip()
        
        if not opt_id:
            return McqValidationResult(False, "EMPTY_OPTION_ID", f"Option #{idx+1} ID cannot be empty.")
        if opt_id in option_ids:
            return McqValidationResult(False, "DUPLICATE_OPTION_ID", f"Option ID '{opt_id}' is duplicated.")
        option_ids.add(opt_id)
        
        if not opt_text:
            return McqValidationResult(False, "EMPTY_OPTION_TEXT", f"Option '{opt_id}' text cannot be empty.")
        if len(opt_text) > 500:
            return McqValidationResult(False, "OPTION_TOO_LONG", f"Option '{opt_id}' text exceeds 500 characters.")
            
        # Check HTML in option
        if re.search(r"<[^>]+>", opt_text):
            return McqValidationResult(False, "PROHIBITED_HTML_IN_OPTION", f"Option '{opt_id}' cannot contain HTML.")
            
        # Check "All of the above" / "None of the above"
        for meta_pat in PROHIBITED_META_OPTION_PATTERNS:
            if re.match(meta_pat, opt_text, re.IGNORECASE):
                return McqValidationResult(
                    False,
                    "PROHIBITED_META_OPTION",
                    f"Option '{opt_id}' contains prohibited meta-answer ('all/none of the above')."
                )
                
        norm_opt = normalize_text_for_comparison(opt_text)
        if norm_opt in normalized_options:
            return McqValidationResult(
                False,
                "DUPLICATE_OPTION_CONTENT",
                f"Option '{opt_id}' has duplicate text content with another option."
            )
        normalized_options.add(norm_opt)

    # 3. Correct Option Verification
    if not correct_option_id or not str(correct_option_id).strip():
        return McqValidationResult(False, "MISSING_CORRECT_OPTION", "A single correct option must be specified.")
    
    clean_correct_id = str(correct_option_id).strip()
    if clean_correct_id not in option_ids:
        return McqValidationResult(
            False,
            "INVALID_CORRECT_OPTION_REFERENCE",
            f"Correct option '{clean_correct_id}' does not match any valid option in the options list."
        )

    # 4. Answer Explanation Verification
    if not answer_explanation or not str(answer_explanation).strip():
        return McqValidationResult(False, "EMPTY_EXPLANATION", "Answer explanation is required.")
    
    expl_trimmed = str(answer_explanation).strip()
    if len(expl_trimmed) > 1000:
        return McqValidationResult(False, "EXPLANATION_TOO_LONG", "Answer explanation exceeds 1000 characters.")
    if re.search(r"<[^>]+>", expl_trimmed):
        return McqValidationResult(False, "PROHIBITED_HTML_IN_EXPLANATION", "Answer explanation cannot contain HTML.")

    # 5. Safety, Negative Phrasing, Leakage, Culture Fit Checks
    # Culture fit / personality checks
    for cf_pat in PROHIBITED_CULTURE_FIT_PATTERNS:
        if re.search(cf_pat, q_trimmed, re.IGNORECASE):
            return McqValidationResult(
                False,
                "PROHIBITED_CULTURE_FIT",
                "Questions assessing personality, horoscope, or non-job culture fit are strictly prohibited."
            )

    # Negative phrasing check ("which is NOT")
    for neg_pat in PROHIBITED_NEGATIVE_PATTERNS:
        if re.search(neg_pat, q_trimmed, re.IGNORECASE):
            return McqValidationResult(
                False,
                "PROHIBITED_NEGATIVE_PHRASING",
                "Unnecessary negative phrasing ('which is NOT / except') is prohibited to ensure question clarity."
            )

    # Candidate-resume leak check
    for cand_pat in PROHIBITED_CANDIDATE_LEAK_PATTERNS:
        if re.search(cand_pat, q_trimmed, re.IGNORECASE):
            return McqValidationResult(
                False,
                "PROHIBITED_CANDIDATE_LEAK",
                "MCQs must test job requirements generally and cannot reference specific candidate resume details."
            )

    # Protected attributes / personal topics
    violation = check_prohibited_topics(q_trimmed)
    if violation is not None:
        cat, _ = violation
        return McqValidationResult(False, f"PROHIBITED_TOPIC_{cat}", f"Question contains prohibited topic ({cat}).")
    
    expl_violation = check_prohibited_topics(expl_trimmed)
    if expl_violation is not None:
        cat, _ = expl_violation
        return McqValidationResult(False, f"PROHIBITED_TOPIC_{cat}", f"Explanation contains prohibited topic ({cat}).")

    # 6. Requirement / Competency Grounding
    if not competency_name and not requirement_id:
        return McqValidationResult(
            False,
            "MISSING_COMPETENCY_GROUNDING",
            "MCQ must be grounded in an approved job requirement or competency."
        )

    # 7. Duplicate Question Checks within same set
    if existing_question_texts:
        norm_q = normalize_text_for_comparison(q_trimmed)
        for existing in existing_question_texts:
            if existing and normalize_text_for_comparison(existing) == norm_q:
                return McqValidationResult(
                    False,
                    "DUPLICATE_QUESTION_TEXT",
                    "A question with identical or near-identical text already exists in this question set."
                )

    return McqValidationResult(True)
