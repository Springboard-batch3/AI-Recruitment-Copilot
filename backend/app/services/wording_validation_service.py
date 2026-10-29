"""Semantic Validation Service for Phase 3C.3: Bounded Wording Assistance.

Strict application-level validation pipeline for untrusted model-generated wording proposals:
1. Transport/schema result
2. Operation and schema version
3. Count and length limits
4. Reference allowlist
5. Intent preservation
6. Competency preservation
7. One-primary-question rule
8. Question type and layer preservation
9. Unsupported-claim detection & neutral language
10. Protected and prohibited topic checks
11. Recommendation and score-language checks
12. URL, HTML, script, code and control-character checks
13. Duplicate and near-duplicate checks (against sibling questions)
14. Rubric-answer-cue checks (direct anchor leakage)
15. Estimated reading and speaking-time bounds

On ANY failure:
- Discards proposed wording;
- Retains deterministic Phase 3B wording;
- Returns typed validation codes without leaking raw prompts or secrets;
- Emits safe provenance metadata.
"""

import dataclasses
import re
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from app.models.interview import InterviewQuestionRecord, InterviewRubricRecord
from app.schemas.phase3_enums import (
    AuthoringOrigin,
    InterviewLayer,
    QuestionType,
    ValidationErrorCode,
)
from app.schemas.safety_policy import check_prohibited_topics
from app.schemas.wording_contract import (
    WORDING_RESPONSE_SCHEMA_VERSION,
    BoundedWordingRequest,
    BoundedWordingResponse,
    StructuredWordingVariant,
    WordingRationaleCode,
)

# =========================================================================
# Named Validation Thresholds & Constants
# =========================================================================

MIN_WORDING_CHARS: int = 10
MAX_WORDING_CHARS: int = 600
MIN_WORDING_WORDS: int = 3
MAX_WORDING_WORDS: int = 100
MAX_ALLOWED_QUESTION_MARKS: int = 1
MAX_DUPLICATE_SIMILARITY_THRESHOLD: float = 0.75
MIN_INTENT_TOKEN_OVERLAP_RATIO: float = 0.15
MIN_RUBRIC_LEAKAGE_NGRAM_SIZE: int = 5

# Common English stopwords ignored during semantic content token extraction
STOPWORDS: Set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
    "during", "each", "few", "for", "from", "further", "had", "hadn't", "has",
    "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
    "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
    "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it",
    "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my",
    "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other",
    "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "shan't",
    "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves", "then",
    "there", "there's", "these", "they", "they'd", "they'll", "they're", "they've",
    "this", "those", "through", "to", "too", "under", "until", "up", "very", "was",
    "wasn't", "we", "we'd", "we'll", "we're", "we've", "were", "weren't", "what",
    "what's", "when", "when's", "where", "where's", "which", "while", "who", "who's",
    "whom", "why", "why's", "with", "won't", "would", "wouldn't", "you", "you'd",
    "you'll", "you're", "you've", "your", "yours", "yourself", "yourselves",
    "tell", "describe", "explain", "share", "give", "example", "time", "instance",
    "situation", "experience", "discuss", "approach", "handle", "candidate", "interviewer"
}

# Unsafe content patterns (URLs, HTML, code, system instructions, prompt injection)
_URL_PATTERN = re.compile(r"(https?://\S+|www\.\S+|ftp://\S+)", re.IGNORECASE)
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_MARKDOWN_LINK_PATTERN = re.compile(r"\[.*?\]\(.*?\)")
_CODE_BLOCK_PATTERN = re.compile(r"```.*?```|`.*?`", re.DOTALL)
_TOOL_SYNTAX_PATTERN = re.compile(r"\b(tool_call|function_call|execute_command|rm\s+-rf|sudo\s+|bash:|javascript:|<script)\b", re.IGNORECASE)
_PROMPT_INJECTION_PATTERN = re.compile(r"\b(ignore\s+(all\s+)?previous\s+instructions|system\s*:|assistant\s*:|human\s*:)\b", re.IGNORECASE)
_CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Accusatory / Dishonesty / Unsupported claims patterns
_ACCUSATORY_PATTERNS = [
    re.compile(r"\b(why did you lie|falsified|failed to tell the truth|cheat(ed|ing)?|dishonest|guilty of|falsely claimed|fabricated|cover up your mistake|blame your team|admit that you failed|confess that)\b", re.IGNORECASE),
]

_UNSUPPORTED_FACT_PATTERNS = [
    re.compile(r"\b(since you lack|because you have no experience in|given your poor performance|given your failure at|why were you fired|why were you terminated|because you were let go)\b", re.IGNORECASE),
]

# Prohibited recommendation / scoring patterns
_RECOMMENDATION_PATTERNS = [
    re.compile(r"\b(recommend hire|should be hired|recommend rejection|should reject|do not hire|strong hire|poor candidate|rank candidate|give score|score of \d|rating of \d|match score is|better than other (candidates|applicants)|compared to the other candidates|advance to next round|pass this interview|final recommendation)\b", re.IGNORECASE),
]

# Additional protected / personality / emotion inference patterns
_ADDITIONAL_PROTECTED_PATTERNS = [
    re.compile(r"\b(personality test|lie detector|are you emotional|temperament|horoscope|zodiac sign|introvert or extrovert|are you an introvert|are you an extrovert|culture fit assessment)\b", re.IGNORECASE),
]


@dataclasses.dataclass(frozen=True)
class SemanticValidationOutcome:
    """Result of the semantic validation pipeline."""
    is_valid: bool
    validation_codes: List[str]
    rejection_reasons: List[str]
    validated_variants: List[StructuredWordingVariant]
    is_fallback: bool
    fallback_question_text: str
    authoring_origin: AuthoringOrigin


def _tokenize_content_words(text: str) -> Set[str]:
    """Deterministically extracts lowercased alphanumeric content tokens (excluding stopwords)."""
    if not text:
        return set()
    normalized = unicodedata.normalize("NFKC", text).lower()
    words = re.findall(r"\b[a-z0-9_-]{3,}\b", normalized)
    return {w for w in words if w not in STOPWORDS}


def _compute_jaccard_similarity(tokens1: Set[str], tokens2: Set[str]) -> float:
    """Computes Jaccard token set similarity."""
    if not tokens1 or not tokens2:
        return 0.0
    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)
    return len(intersection) / len(union) if union else 0.0


def _extract_ngrams(text: str, n: int) -> Set[Tuple[str, ...]]:
    """Extracts lowercase word n-grams from text."""
    if not text:
        return set()
    normalized = unicodedata.normalize("NFKC", text).lower()
    words = re.findall(r"\b[a-z0-9]+\b", normalized)
    if len(words) < n:
        return set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


class SemanticWordingValidator:
    """Rigorous semantic validation pipeline for untrusted LLM question proposals."""

    @classmethod
    def validate_wording_response(
        cls,
        source_question: InterviewQuestionRecord,
        request: BoundedWordingRequest,
        response: Optional[BoundedWordingResponse],
        sibling_questions: List[InterviewQuestionRecord],
        allowed_reference_ids: Optional[Set[str]] = None,
    ) -> SemanticValidationOutcome:
        """Executes the full 15-stage validation pipeline on provider wording output."""
        fallback_text = source_question.question_text.strip()
        ref_allowlist = allowed_reference_ids or set(request.allowed_reference_ids)

        # 1. Transport / Schema Result Check
        if response is None:
            return SemanticValidationOutcome(
                is_valid=False,
                validation_codes=[ValidationErrorCode.RESPONSE_VALIDATION_INCONCLUSIVE.value],
                rejection_reasons=["Provider returned no response body."],
                validated_variants=[],
                is_fallback=True,
                fallback_question_text=fallback_text,
                authoring_origin=AuthoringOrigin.DETERMINISTIC,
            )

        # 2. Operation and Schema Version
        if response.operation != "REWORD_QUESTION" or response.response_schema_version != WORDING_RESPONSE_SCHEMA_VERSION:
            return SemanticValidationOutcome(
                is_valid=False,
                validation_codes=[ValidationErrorCode.INVALID_SCHEMA.value],
                rejection_reasons=["Response operation or schema version mismatch."],
                validated_variants=[],
                is_fallback=True,
                fallback_question_text=fallback_text,
                authoring_origin=AuthoringOrigin.DETERMINISTIC,
            )

        # 3. Count Limits
        if not response.variants or len(response.variants) > request.max_variants:
            return SemanticValidationOutcome(
                is_valid=False,
                validation_codes=[ValidationErrorCode.RESPONSE_LENGTH_INVALID.value],
                rejection_reasons=[f"Variant count {len(response.variants)} exceeds requested bound {request.max_variants}."],
                validated_variants=[],
                is_fallback=True,
                fallback_question_text=fallback_text,
                authoring_origin=AuthoringOrigin.DETERMINISTIC,
            )

        validated_variants: List[StructuredWordingVariant] = []
        collected_codes: List[str] = []
        collected_reasons: List[str] = []

        # Validate each variant sequentially
        for idx, variant in enumerate(response.variants):
            v_text = variant.variant_text.strip() if variant.variant_text else ""

            # Check 3b. Length Limits (Chars & Words)
            if len(v_text) < MIN_WORDING_CHARS or len(v_text) > MAX_WORDING_CHARS:
                collected_codes.append(ValidationErrorCode.RESPONSE_LENGTH_INVALID.value)
                collected_reasons.append(f"Variant {idx+1} length ({len(v_text)} chars) outside bounds [{MIN_WORDING_CHARS}, {MAX_WORDING_CHARS}].")
                continue

            words = v_text.split()
            if len(words) < MIN_WORDING_WORDS or len(words) > MAX_WORDING_WORDS:
                collected_codes.append(ValidationErrorCode.RESPONSE_LENGTH_INVALID.value)
                collected_reasons.append(f"Variant {idx+1} word count ({len(words)} words) outside bounds [{MIN_WORDING_WORDS}, {MAX_WORDING_WORDS}].")
                continue

            # 4. Reference Allowlist
            invalid_refs = [ref for ref in variant.referenced_identifiers if ref not in ref_allowlist]
            if invalid_refs:
                collected_codes.append(ValidationErrorCode.RESPONSE_REFERENCE_INVALID.value)
                collected_reasons.append(f"Variant {idx+1} referenced unauthorized identifier.")
                continue

            # 5. Output Content Safety (URLs, HTML, scripts, tools, control chars)
            if _CONTROL_CHARS_PATTERN.search(v_text):
                collected_codes.append(ValidationErrorCode.RESPONSE_UNSAFE_CONTENT.value)
                collected_reasons.append(f"Variant {idx+1} contains disallowed control characters.")
                continue

            if _URL_PATTERN.search(v_text) or _EMAIL_PATTERN.search(v_text) or _MARKDOWN_LINK_PATTERN.search(v_text):
                collected_codes.append(ValidationErrorCode.RESPONSE_UNSAFE_CONTENT.value)
                collected_reasons.append(f"Variant {idx+1} contains URLs, email or links.")
                continue

            if _HTML_TAG_PATTERN.search(v_text) or _CODE_BLOCK_PATTERN.search(v_text):
                collected_codes.append(ValidationErrorCode.RESPONSE_UNSAFE_CONTENT.value)
                collected_reasons.append(f"Variant {idx+1} contains HTML tags or code blocks.")
                continue

            if _TOOL_SYNTAX_PATTERN.search(v_text) or _PROMPT_INJECTION_PATTERN.search(v_text):
                collected_codes.append(ValidationErrorCode.RESPONSE_UNSAFE_CONTENT.value)
                collected_reasons.append(f"Variant {idx+1} contains tool calls or prompt directives.")
                continue

            # 6. One-Primary-Question Rule
            q_mark_count = v_text.count("?")
            if q_mark_count > MAX_ALLOWED_QUESTION_MARKS:
                collected_codes.append(ValidationErrorCode.RESPONSE_MULTIPLE_QUESTIONS.value)
                collected_reasons.append(f"Variant {idx+1} contains {q_mark_count} question marks (maximum {MAX_ALLOWED_QUESTION_MARKS} allowed).")
                continue

            # 7. Unsupported Claims & Neutral Language
            if any(p.search(v_text) for p in _ACCUSATORY_PATTERNS) or any(p.search(v_text) for p in _UNSUPPORTED_FACT_PATTERNS):
                collected_codes.append(ValidationErrorCode.RESPONSE_UNSUPPORTED_CLAIM.value)
                collected_reasons.append(f"Variant {idx+1} contains accusatory or unsupported candidate claims.")
                continue

            # 8. Protected and Prohibited Topics
            topic_violation = check_prohibited_topics(v_text)
            if topic_violation is not None:
                collected_codes.append(ValidationErrorCode.RESPONSE_PROHIBITED_TOPIC.value)
                collected_reasons.append(f"Variant {idx+1} violates safety policy on prohibited topic '{topic_violation[0]}'.")
                continue

            if any(p.search(v_text) for p in _ADDITIONAL_PROTECTED_PATTERNS):
                collected_codes.append(ValidationErrorCode.RESPONSE_PROHIBITED_TOPIC.value)
                collected_reasons.append(f"Variant {idx+1} inquires into protected personality or emotional traits.")
                continue

            # 9. Recommendation and Scoring Language
            if any(p.search(v_text) for p in _RECOMMENDATION_PATTERNS):
                collected_codes.append(ValidationErrorCode.RESPONSE_RECOMMENDATION_BLOCKED.value)
                collected_reasons.append(f"Variant {idx+1} contains prohibited hiring recommendations or score language.")
                continue

            # 10. Rubric-Answer-Cue / Leakage Checks
            rubric_leaked = False
            if source_question.rubric:
                rubric_text_corpus = []
                rubric = source_question.rubric
                if rubric.rating_anchors and isinstance(rubric.rating_anchors, dict):
                    rubric_text_corpus.extend(str(v) for v in rubric.rating_anchors.values())
                if rubric.sufficient_evidence_guidance:
                    rubric_text_corpus.append(rubric.sufficient_evidence_guidance)
                if rubric.insufficient_evidence_guidance:
                    rubric_text_corpus.append(rubric.insufficient_evidence_guidance)

                variant_ngrams = _extract_ngrams(v_text, MIN_RUBRIC_LEAKAGE_NGRAM_SIZE)
                for r_text in rubric_text_corpus:
                    r_ngrams = _extract_ngrams(r_text, MIN_RUBRIC_LEAKAGE_NGRAM_SIZE)
                    if variant_ngrams.intersection(r_ngrams):
                        rubric_leaked = True
                        break

            if rubric_leaked:
                collected_codes.append(ValidationErrorCode.RESPONSE_RUBRIC_LEAKAGE.value)
                collected_reasons.append(f"Variant {idx+1} leaks scoring anchors or guidance from question rubric.")
                continue

            # 11. Intent & Competency Preservation (Semantic Token Overlap)
            variant_tokens = _tokenize_content_words(v_text)
            source_intent_tokens = _tokenize_content_words(source_question.question_intent)
            source_text_tokens = _tokenize_content_words(source_question.question_text)
            combined_source_tokens = source_intent_tokens.union(source_text_tokens)

            if combined_source_tokens and variant_tokens:
                overlap = len(variant_tokens.intersection(combined_source_tokens))
                coverage = overlap / len(combined_source_tokens)
                if coverage < MIN_INTENT_TOKEN_OVERLAP_RATIO and overlap == 0:
                    collected_codes.append(ValidationErrorCode.RESPONSE_INTENT_CHANGED.value)
                    collected_reasons.append(f"Variant {idx+1} failed intent preservation (token overlap {overlap}).")
                    continue

            # Check competency label if present
            if source_question.competency_id:
                competency_tokens = _tokenize_content_words(source_question.competency_id)
                # If competency has distinctive domain tokens (e.g., 'python', 'architecture'), check retention if intent was lean
                if competency_tokens and not variant_tokens.intersection(competency_tokens) and not variant_tokens.intersection(source_text_tokens):
                    collected_codes.append(ValidationErrorCode.RESPONSE_COMPETENCY_CHANGED.value)
                    collected_reasons.append(f"Variant {idx+1} does not preserve competency concepts.")
                    continue

            # 12. Duplicate / Near-Duplicate Checks (against other sibling questions in template/plan)
            is_duplicate_of_sibling = False
            for sibling in sibling_questions:
                if sibling.question_id == source_question.question_id:
                    continue  # Do not check against self!
                sibling_tokens = _tokenize_content_words(sibling.question_text)
                sim = _compute_jaccard_similarity(variant_tokens, sibling_tokens)
                if sim >= MAX_DUPLICATE_SIMILARITY_THRESHOLD:
                    is_duplicate_of_sibling = True
                    break

            if is_duplicate_of_sibling:
                collected_codes.append(ValidationErrorCode.RESPONSE_DUPLICATE.value)
                collected_reasons.append(f"Variant {idx+1} duplicates another question in the interview plan.")
                continue

            # If all checks passed for this variant, add to validated variants
            validated_variants.append(
                StructuredWordingVariant(
                    variant_text=v_text,
                    rationale_code=variant.rationale_code or WordingRationaleCode.CLARITY_IMPROVEMENT,
                    referenced_identifiers=[r for r in variant.referenced_identifiers if r in ref_allowlist],
                )
            )

        # Final Outcome Assembly
        if validated_variants:
            return SemanticValidationOutcome(
                is_valid=True,
                validation_codes=[],
                rejection_reasons=[],
                validated_variants=validated_variants,
                is_fallback=False,
                fallback_question_text=fallback_text,
                authoring_origin=AuthoringOrigin.AI_ASSISTED,
            )
        else:
            # All variants were rejected by validation
            distinct_codes = sorted(list(set(collected_codes))) if collected_codes else [ValidationErrorCode.RESPONSE_VALIDATION_INCONCLUSIVE.value]
            return SemanticValidationOutcome(
                is_valid=False,
                validation_codes=distinct_codes,
                rejection_reasons=collected_reasons or ["Proposed wording failed semantic validation checks."],
                validated_variants=[],
                is_fallback=True,
                fallback_question_text=fallback_text,
                authoring_origin=AuthoringOrigin.DETERMINISTIC,
            )
