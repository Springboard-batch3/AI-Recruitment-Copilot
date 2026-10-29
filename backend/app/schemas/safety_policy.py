import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, FrozenSet, List, Optional
from pydantic import BaseModel, ConfigDict, Field

SAFETY_POLICY_ID: str = "safety_policy_v1"
SAFETY_POLICY_VERSION: str = "1.0.0"
SAFETY_POLICY_SCHEMA_VERSION: str = "1.0.0"
SAFETY_POLICY_EFFECTIVE_DATE: str = "2026-09-15T00:00:00Z"

# 1. Allowed model purposes
ALLOWED_MODEL_PURPOSES: FrozenSet[str] = frozenset({
    "REWORD_QUESTION",
    "CREATE_BOUNDED_VARIANTS",
    "STRUCTURE_PROVISIONAL_ASSESSMENT",
    "SUMMARIZE_SELECTED_EVIDENCE",
})

# 2. Prohibited model purposes
PROHIBITED_MODEL_PURPOSES: FrozenSet[str] = frozenset({
    "HIRING_DECISION",
    "HIRING_RECOMMENDATION",
    "CANDIDATE_RANKING",
    "SCORE_MODIFICATION",
    "REQUIREMENT_SELECTION",
    "UNSUPPORTED_FACT_GENERATION",
    "PROTECTED_CHARACTERISTIC_INFERENCE",
    "HEALTH_OR_DISABILITY_INFERENCE",
    "FAMILY_STATUS_INFERENCE",
    "PERSONALITY_OR_EMOTION_JUDGMENT",
    "HONESTY_OR_CULTURE_FIT_JUDGMENT",
    "ACCENT_JUDGMENT",
    "FUTURE_PERFORMANCE_PREDICTION",
    "IDENTITY_OR_PRESTIGE_PROXIES",
    "TOOL_OR_SYSTEM_ACTION",
    "SELF_APPROVAL",
    "EXTERNAL_CONTACT_OR_ACTION",
})

# 3. Prohibited interview topic categories and keywords
PROHIBITED_TOPIC_CATEGORIES: Dict[str, List[str]] = {
    "AGE_AND_DOB": ["age", "date of birth", "dob", "birth year", "how old are you", "graduation year"],
    "RELIGION_AND_BELIEF": ["religion", "religious", "faith", "worship", "church", "mosque", "synagogue", "temple", "belief", "spiritual"],
    "ETHNICITY_AND_RACE": ["race", "ethnicity", "caste", "tribe", "skin color", "racial", "ethnic background"],
    "IRRELEVANT_NATIONALITY": ["nationality", "citizenship origin", "place of birth", "native country", "native language"],
    "DISABILITY_AND_MEDICAL": ["disability", "medical condition", "health status", "illness", "chronic illness", "mental health", "physical limitation", "medical history"],
    "PREGNANCY_AND_FAMILY_PLANS": ["pregnancy", "pregnant", "children plans", "family planning", "maternity", "paternity", "childcare"],
    "MARITAL_AND_FAMILY_STATUS": ["marital status", "married", "single", "divorced", "spouse", "partner", "dependents", "family status"],
    "SEXUALITY_AND_GENDER": ["sexual orientation", "sexuality", "gender identity", "gender expression"],
    "POLITICAL_BELIEF": ["political party", "political belief", "politics", "voting", "political affiliation"],
    "SOCIOECONOMIC_STATUS": ["socioeconomic", "financial status", "salary history", "credit score", "wealth", "home ownership", "household income", "family background"],
    "UNRELATED_PERSONAL_LIFE": ["hobbies", "personal lifestyle", "dating", "roommates", "social life", "drinking habits", "personal habits"],
}

# 4. Canonical Failure Posture Matrix
FAILURE_POSTURE_MATRIX: Dict[str, str] = {
    "PROVIDER_UNAVAILABLE": "FALLBACK_DETERMINISTIC_WORDING",
    "INVALID_SCHEMA": "REJECT_WITH_BOUNDED_REPAIR",
    "UNSUPPORTED_REFERENCE": "REJECT",
    "SAFETY_POLICY_MATCH": "BLOCK",
    "AUTHORIZATION_UNCERTAINTY": "DENY",
    "STALE_SOURCE_VERSION": "REJECT_AND_REQUIRE_NEW_VERSION",
    "UNCERTAIN_ATS_WRITE": "RECONCILIATION_REQUIRED",
}


def _compute_canonical_policy_hash() -> str:
    """Computes a deterministic SHA-256 digest of the static policy rules."""
    policy_dict = {
        "policy_id": SAFETY_POLICY_ID,
        "policy_version": SAFETY_POLICY_VERSION,
        "schema_version": SAFETY_POLICY_SCHEMA_VERSION,
        "effective_date": SAFETY_POLICY_EFFECTIVE_DATE,
        "allowed_purposes": sorted(list(ALLOWED_MODEL_PURPOSES)),
        "prohibited_purposes": sorted(list(PROHIBITED_MODEL_PURPOSES)),
        "prohibited_topic_categories": {k: sorted(v) for k, v in sorted(PROHIBITED_TOPIC_CATEGORIES.items())},
        "failure_posture": sorted(list(FAILURE_POSTURE_MATRIX.items())),
    }
    serialized = json.dumps(policy_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()


SAFETY_POLICY_HASH: str = _compute_canonical_policy_hash()


class SafetyPolicyDefinition(BaseModel):
    """Immutable runtime representation of the system safety policy."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str = SAFETY_POLICY_ID
    policy_version: str = SAFETY_POLICY_VERSION
    schema_version: str = SAFETY_POLICY_SCHEMA_VERSION
    policy_hash: str = SAFETY_POLICY_HASH
    effective_date: str = SAFETY_POLICY_EFFECTIVE_DATE
    allowed_purposes: List[str] = Field(default_factory=lambda: sorted(list(ALLOWED_MODEL_PURPOSES)))
    prohibited_purposes: List[str] = Field(default_factory=lambda: sorted(list(PROHIBITED_MODEL_PURPOSES)))
    prohibited_topics: Dict[str, List[str]] = Field(default_factory=lambda: {k: sorted(v) for k, v in sorted(PROHIBITED_TOPIC_CATEGORIES.items())})
    failure_posture: Dict[str, str] = Field(default_factory=lambda: dict(FAILURE_POSTURE_MATRIX))


def get_current_safety_policy() -> SafetyPolicyDefinition:
    """Returns the immutable active safety policy definition."""
    return SafetyPolicyDefinition()


import re


def check_prohibited_topics(text: str) -> Optional[tuple[str, str]]:
    """Checks text against all prohibited topic categories.
    
    Returns (category, matched_phrase) if a violation is found, or None if clean.
    """
    if not text:
        return None
    text_lower = text.lower()
    for category, terms in PROHIBITED_TOPIC_CATEGORIES.items():
        for term in terms:
            term_clean = term.strip().lower()
            if not term_clean:
                continue
            if " " in term_clean:
                if term_clean in text_lower:
                    return (category, term)
            else:
                pattern = r'\b' + re.escape(term_clean) + r'\b'
                if re.search(pattern, text_lower):
                    return (category, term)
    return None

