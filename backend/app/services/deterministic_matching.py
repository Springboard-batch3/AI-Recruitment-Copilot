import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import (
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.models.taxonomy import SkillAlias, SkillConcept, TaxonomySnapshot, TaxonomySnapshotState
from app.schemas.matching import (
    CandidateCertificationFeature,
    CandidateEducationFeature,
    CandidateEmploymentRoleFeature,
    CandidateFeatureView,
    CandidateLanguageFeature,
    CandidateProjectFeature,
    CandidateSkillFeature,
    MatchInputSnapshot,
    RequirementMatchingItem,
)
from app.schemas.requirement_match import (
    MatchComparisonMethod,
    MatchResultState,
    RequirementMatch,
    RequirementMatchResultSet,
)
from app.services.date_extraction import (
    calculate_total_experience_months,
    parse_single_date,
)
from app.services.taxonomy_normalization import normalize_search_query

logger = logging.getLogger("ai_hiring.services.deterministic_matching")

# Matching policy constants
MATCHER_RULES_VERSION = "1.0.0"
MIN_CREDITABLE_EVIDENCE_CONFIDENCE = 0.70
HIGH_CONFIDENCE_THRESHOLD = 0.90


# Domain Error Hierarchy
class DeterministicMatchingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class UnsupportedRequirementCategoryError(DeterministicMatchingError):
    def __init__(self, message: str = "Unsupported requirement category for matching."):
        super().__init__(code="UNSUPPORTED_REQUIREMENT_CATEGORY", message=message)


class InvalidRequirementConfigurationError(DeterministicMatchingError):
    def __init__(self, message: str = "Invalid requirement matching configuration."):
        super().__init__(code="INVALID_REQUIREMENT_CONFIGURATION", message=message)


class ForeignEvidenceReferenceError(DeterministicMatchingError):
    def __init__(self, message: str = "Evidence reference does not belong to candidate profile lineage."):
        super().__init__(code="FOREIGN_EVIDENCE_REFERENCE", message=message)


class MissingEvaluationDateError(DeterministicMatchingError):
    def __init__(self, message: str = "Explicit evaluation date is required for recency and experience matching."):
        super().__init__(code="MISSING_EVALUATION_DATE", message=message)


def _safe_exact_token_match(target_text: str, candidate_phrase: str) -> bool:
    """Checks if candidate_phrase appears as an exact isolated token/phrase inside target_text,

    preventing substring false positives (e.g. 'Java' vs 'JavaScript', 'C' vs 'C++',
    'React' vs 'React Native', 'SQL' vs 'NoSQL', 'R' vs words with r).
    """
    if not target_text or not candidate_phrase:
        return False

    norm_target = normalize_search_query(target_text)
    norm_candidate = normalize_search_query(candidate_phrase)

    if not norm_target or not norm_candidate:
        return False

    # Exact equality is always safe
    if norm_target == norm_candidate:
        return True

    # Boundary-safe regex search
    pattern = rf"(?<![\w#+.-]){re.escape(norm_candidate)}(?![\w#+.-])"
    return bool(re.search(pattern, norm_target))


def _match_skill_requirement(
    req: RequirementMatchingItem,
    candidate_view: CandidateFeatureView,
    db: Optional[Session] = None,
) -> RequirementMatch:
    """Matches a SKILL requirement against candidate skills and projects."""
    norm_req_label = normalize_search_query(req.normalized_label or req.label)
    approved_aliases = [normalize_search_query(a) for a in req.accepted_equivalents if a and a.strip()]

    matched_method: Optional[MatchComparisonMethod] = None
    observed_value: Optional[str] = None
    credited_evidence_ids: List[str] = []
    min_confidence: Optional[float] = None
    has_low_conf = False

    # 1. Compare against candidate skills
    for s in candidate_view.skills:
        norm_skill = normalize_search_query(s.normalized_name or s.name)
        is_match = False
        method: Optional[MatchComparisonMethod] = None

        # a. Exact canonical taxonomy ID match
        if (
            req.taxonomy_skill_concept_id is not None
            and req.taxonomy_canonical_label is not None
            and normalize_search_query(req.taxonomy_canonical_label) == norm_skill
        ):
            is_match = True
            method = MatchComparisonMethod.CANONICAL_TAXONOMY_ID

        # b. Exact normalized label match
        elif norm_skill == norm_req_label:
            is_match = True
            method = MatchComparisonMethod.EXACT_NORMALIZED_LABEL

        # c. Recruiter-approved alias match
        elif norm_skill in approved_aliases:
            is_match = True
            method = MatchComparisonMethod.RECRUITER_APPROVED_ALIAS

        # d. Local taxonomy snapshot alias match (if DB session is supplied)
        elif req.taxonomy_skill_concept_id is not None and db is not None:
            stmt = select(SkillAlias).where(
                SkillAlias.skill_concept_id == req.taxonomy_skill_concept_id,
                SkillAlias.normalized_alias == norm_skill,
            )
            tax_alias = db.execute(stmt).scalar_one_or_none()
            if tax_alias is not None:
                is_match = True
                method = MatchComparisonMethod.TAXONOMY_ALIAS

        if is_match:
            matched_method = method
            observed_value = f"{s.name} found in Skills"
            credited_evidence_ids.extend(s.evidence_ids)
            if s.confidence is not None:
                min_confidence = min(min_confidence or 1.0, s.confidence)
                if s.confidence < MIN_CREDITABLE_EVIDENCE_CONFIDENCE:
                    has_low_conf = True
            break

    # 2. If not matched in skills, check projects technologies if permitted
    if matched_method is None:
        for p in candidate_view.projects:
            for t in p.technologies:
                norm_t = normalize_search_query(t)
                if norm_t == norm_req_label or norm_t in approved_aliases:
                    matched_method = MatchComparisonMethod.DOCUMENTED_RESPONSIBILITY
                    observed_value = f"{t} found in Project: {p.name}"
                    credited_evidence_ids.extend(p.evidence_ids)
                    if p.confidence is not None:
                        min_confidence = min(min_confidence or 1.0, p.confidence)
                        if p.confidence < MIN_CREDITABLE_EVIDENCE_CONFIDENCE:
                            has_low_conf = True
                    break
            if matched_method is not None:
                break

    # Construct result
    unique_ev_ids = sorted(list(set(credited_evidence_ids)))
    reason_codes: List[str] = []
    review_recommended = False

    if matched_method is not None and len(unique_ev_ids) > 0:
        if has_low_conf:
            result_state = MatchResultState.PARTIALLY_MATCHED
            match_value = 0.5
            review_recommended = True
            reason_codes.append("LOW_EXTRACTION_CONFIDENCE")
        else:
            result_state = MatchResultState.MATCHED
            match_value = 1.0
    else:
        result_state = MatchResultState.NOT_EVIDENCED
        match_value = 0.0
        matched_method = MatchComparisonMethod.EXACT_NORMALIZED_LABEL
        unique_ev_ids = []
        observed_value = None

    # Preserve must-have unfulfilled warning code
    if req.necessity == RequirementNecessity.MUST_HAVE and result_state != MatchResultState.MATCHED:
        reason_codes.append("MUST_HAVE_UNFULFILLED")

    return RequirementMatch(
        requirement_id=req.requirement_id,
        requirement_name=req.label,
        display_order=req.display_order,
        category=req.category,
        result_state=result_state,
        match_value=match_value,
        comparison_method=matched_method,
        expected_value=req.label,
        observed_value=observed_value,
        evidence_references=unique_ev_ids,
        reason_codes=reason_codes,
        evidence_confidence=min_confidence,
        review_recommended=review_recommended,
        matching_rule_version=MATCHER_RULES_VERSION,
        necessity=req.necessity,
        priority=req.priority,
        approved_weight=req.approved_weight,
    )


def _match_experience_requirement(
    req: RequirementMatchingItem,
    candidate_view: CandidateFeatureView,
    evaluation_date: datetime,
) -> RequirementMatch:
    """Matches an EXPERIENCE requirement against candidate employment timeline and recency."""
    norm_req_label = normalize_search_query(req.normalized_label or req.label)
    approved_aliases = [normalize_search_query(a) for a in req.accepted_equivalents if a and a.strip()]

    eval_year = evaluation_date.year
    eval_month = evaluation_date.month

    qualifying_intervals: List[Tuple[int, int, int, int]] = []
    credited_ev_ids: Set[str] = set()
    has_conflicting_dates = False
    has_imprecise_dates = False
    latest_qualifying_end_month: Optional[int] = None
    is_currently_employed_in_role = False
    observed_titles: List[str] = []

    for role in candidate_view.employment:
        # Check relevance: title or description matches requirement
        norm_title = normalize_search_query(role.job_title)
        is_relevant = _safe_exact_token_match(norm_title, norm_req_label)
        if not is_relevant:
            for alias in approved_aliases:
                if _safe_exact_token_match(norm_title, alias):
                    is_relevant = True
                    break
        if not is_relevant:
            for desc in role.descriptions:
                if _safe_exact_token_match(desc, norm_req_label):
                    is_relevant = True
                    break
                for alias in approved_aliases:
                    if _safe_exact_token_match(desc, alias):
                        is_relevant = True
                        break
                if is_relevant:
                    break

        if not is_relevant:
            continue

        observed_titles.append(role.job_title)
        credited_ev_ids.update(role.evidence_ids)

        # Parse start and end dates
        start_date_obj = parse_single_date(role.start_date) if role.start_date else None
        end_date_obj = parse_single_date(role.end_date) if role.end_date else None

        if not start_date_obj or not end_date_obj:
            has_imprecise_dates = True
            continue

        # Extract numeric start year/month
        if start_date_obj.year is None or start_date_obj.month is None:
            has_imprecise_dates = True
            continue
        s_year = start_date_obj.year
        s_month = start_date_obj.month

        # Extract numeric end year/month
        if end_date_obj.is_current:
            e_year = eval_year
            e_month = eval_month
            is_currently_employed_in_role = True
        elif end_date_obj.year is not None and end_date_obj.month is not None:
            e_year = end_date_obj.year
            e_month = end_date_obj.month
        else:
            has_imprecise_dates = True
            continue

        # Conflict check: start > end
        if (s_year > e_year) or (s_year == e_year and s_month > e_month):
            has_conflicting_dates = True
            continue

        qualifying_intervals.append((s_year, s_month, e_year, e_month))

        # Track latest end for recency
        end_month_idx = e_year * 12 + e_month
        if latest_qualifying_end_month is None or end_month_idx > latest_qualifying_end_month:
            latest_qualifying_end_month = end_month_idx

    # Conflict check
    if has_conflicting_dates:
        reason_codes = ["INVERTED_DATE_INTERVAL"]
        if req.necessity == RequirementNecessity.MUST_HAVE:
            reason_codes.append("MUST_HAVE_UNFULFILLED")
        return RequirementMatch(
            requirement_id=req.requirement_id,
            requirement_name=req.label,
            display_order=req.display_order,
            category=req.category,
            result_state=MatchResultState.CONFLICTING_EVIDENCE,
            match_value=0.0,
            comparison_method=MatchComparisonMethod.EXPERIENCE_INTERVAL_CALCULATION,
            expected_value=f"{req.min_experience_months or 0} months of {req.label}" if req.min_experience_months else req.label,
            observed_value=", ".join(observed_titles) if observed_titles else None,
            evidence_references=sorted(list(credited_ev_ids)),
            reason_codes=reason_codes,
            review_recommended=True,
            matching_rule_version=MATCHER_RULES_VERSION,
            necessity=req.necessity,
            priority=req.priority,
            approved_weight=req.approved_weight,
        )

    # Calculate non-overlapping duration
    total_months = calculate_total_experience_months(qualifying_intervals)
    req_months = req.min_experience_months or 0
    reason_codes: List[str] = []
    review_recommended = False

    if req_months <= 0:
        # Non-duration experience requirement (e.g. role title presence)
        if len(observed_titles) > 0 and len(credited_ev_ids) > 0:
            result_state = MatchResultState.MATCHED
            match_value = 1.0
        else:
            result_state = MatchResultState.NOT_EVIDENCED
            match_value = 0.0
            credited_ev_ids = set()
    else:
        if total_months >= req_months and len(credited_ev_ids) > 0:
            result_state = MatchResultState.MATCHED
            match_value = 1.0
        elif total_months > 0 and len(credited_ev_ids) > 0:
            result_state = MatchResultState.PARTIALLY_MATCHED
            match_value = round(min(total_months / req_months, 1.0), 4)
            reason_codes.append(f"EXPERIENCE_BELOW_THRESHOLD_{total_months}_OF_{req_months}_MONTHS")
        elif has_imprecise_dates and len(observed_titles) > 0:
            result_state = MatchResultState.INSUFFICIENT_EVIDENCE
            match_value = 0.0
            reason_codes.append("IMPRECISE_DATE_PRECISION")
            review_recommended = True
        else:
            result_state = MatchResultState.NOT_EVIDENCED
            match_value = 0.0
            credited_ev_ids = set()

    # Recency evaluation relative to explicit evaluation date
    if req.recency_years and latest_qualifying_end_month is not None and not is_currently_employed_in_role:
        eval_month_idx = eval_year * 12 + eval_month
        months_elapsed = eval_month_idx - latest_qualifying_end_month
        if months_elapsed > req.recency_years * 12:
            reason_codes.append("RECENCY_THRESHOLD_EXCEEDED")
            review_recommended = True
            if result_state == MatchResultState.MATCHED:
                result_state = MatchResultState.PARTIALLY_MATCHED
                match_value = 0.5

    if req.necessity == RequirementNecessity.MUST_HAVE and result_state != MatchResultState.MATCHED:
        reason_codes.append("MUST_HAVE_UNFULFILLED")

    exp_expected = f"{req_months} months of {req.label}" if req_months > 0 else req.label
    if total_months > 0:
        titles_str = f" ({', '.join(observed_titles)})" if observed_titles else ""
        exp_observed = f"{total_months} qualifying months{titles_str}"
    elif observed_titles and result_state != MatchResultState.NOT_EVIDENCED:
        exp_observed = ", ".join(observed_titles)
    else:
        exp_observed = None

    return RequirementMatch(
        requirement_id=req.requirement_id,
        requirement_name=req.label,
        display_order=req.display_order,
        category=req.category,
        result_state=result_state,
        match_value=match_value,
        comparison_method=MatchComparisonMethod.EXPERIENCE_INTERVAL_CALCULATION,
        expected_value=exp_expected,
        observed_value=exp_observed,
        evidence_references=sorted(list(credited_ev_ids)),
        reason_codes=reason_codes,
        review_recommended=review_recommended,
        matching_rule_version=MATCHER_RULES_VERSION,
        necessity=req.necessity,
        priority=req.priority,
        approved_weight=req.approved_weight,
    )


def _match_education_requirement(
    req: RequirementMatchingItem,
    candidate_view: CandidateFeatureView,
) -> RequirementMatch:
    """Matches an EDUCATION requirement against candidate education records."""
    norm_req_label = normalize_search_query(req.normalized_label or req.label)
    approved_aliases = [normalize_search_query(a) for a in req.accepted_equivalents if a and a.strip()]

    matched_edu: Optional[CandidateEducationFeature] = None
    for edu in candidate_view.education:
        norm_deg = normalize_search_query(edu.degree or "")
        norm_fos = normalize_search_query(edu.field_of_study or "")
        combined = f"{norm_deg} {norm_fos}".strip()

        if _safe_exact_token_match(norm_deg, norm_req_label) or _safe_exact_token_match(combined, norm_req_label):
            matched_edu = edu
            break
        for alias in approved_aliases:
            if _safe_exact_token_match(norm_deg, alias) or _safe_exact_token_match(combined, alias):
                matched_edu = edu
                break
        if matched_edu is not None:
            break

    reason_codes: List[str] = []
    if matched_edu is not None and len(matched_edu.evidence_ids) > 0:
        result_state = MatchResultState.MATCHED
        match_value = 1.0
        ev_refs = sorted(list(set(matched_edu.evidence_ids)))
        observed_val = f"{matched_edu.degree or ''} in {matched_edu.field_of_study or ''}".strip()
    else:
        result_state = MatchResultState.NOT_EVIDENCED
        match_value = 0.0
        ev_refs = []
        observed_val = None

    if req.necessity == RequirementNecessity.MUST_HAVE and result_state != MatchResultState.MATCHED:
        reason_codes.append("MUST_HAVE_UNFULFILLED")

    edu_observed = f"{matched_edu.degree or ''} in {matched_edu.field_of_study or ''}".strip() if matched_edu else None
    if edu_observed and not edu_observed.startswith("in "):
        doc_edu = edu_observed
    elif matched_edu and (matched_edu.degree or matched_edu.field_of_study):
        doc_edu = matched_edu.degree or matched_edu.field_of_study
    else:
        doc_edu = None

    return RequirementMatch(
        requirement_id=req.requirement_id,
        requirement_name=req.label,
        display_order=req.display_order,
        category=req.category,
        result_state=result_state,
        match_value=match_value,
        comparison_method=MatchComparisonMethod.EDUCATION_DEGREE_AND_FIELD,
        expected_value=req.label,
        observed_value=doc_edu if result_state != MatchResultState.NOT_EVIDENCED else None,
        evidence_references=ev_refs,
        reason_codes=reason_codes,
        matching_rule_version=MATCHER_RULES_VERSION,
        necessity=req.necessity,
        priority=req.priority,
        approved_weight=req.approved_weight,
    )


def _match_certification_requirement(
    req: RequirementMatchingItem,
    candidate_view: CandidateFeatureView,
    evaluation_date: datetime,
) -> RequirementMatch:
    """Matches a CERTIFICATION requirement against candidate certifications."""
    norm_req_label = normalize_search_query(req.normalized_label or req.label)
    approved_aliases = [normalize_search_query(a) for a in req.accepted_equivalents if a and a.strip()]

    matched_cert: Optional[CandidateCertificationFeature] = None
    for cert in candidate_view.certifications:
        norm_cert = normalize_search_query(cert.name)
        if _safe_exact_token_match(norm_cert, norm_req_label):
            matched_cert = cert
            break
        for alias in approved_aliases:
            if _safe_exact_token_match(norm_cert, alias):
                matched_cert = cert
                break
        if matched_cert is not None:
            break

    reason_codes: List[str] = []
    review_recommended = False

    if matched_cert is not None and len(matched_cert.evidence_ids) > 0:
        ev_refs = sorted(list(set(matched_cert.evidence_ids)))
        observed_val = matched_cert.name

        # Expiry check relative to explicit evaluation date
        if matched_cert.expiry_date:
            exp_date = parse_single_date(matched_cert.expiry_date)
            if exp_date and exp_date.year is not None:
                exp_month = exp_date.month or 12
                if (exp_date.year < evaluation_date.year) or (
                    exp_date.year == evaluation_date.year and exp_month < evaluation_date.month
                ):
                    reason_codes.append("CERTIFICATION_EXPIRED")
                    review_recommended = True
                    result_state = MatchResultState.PARTIALLY_MATCHED
                    match_value = 0.5
                else:
                    result_state = MatchResultState.MATCHED
                    match_value = 1.0
            else:
                result_state = MatchResultState.MATCHED
                match_value = 1.0
        else:
            result_state = MatchResultState.MATCHED
            match_value = 1.0
    else:
        result_state = MatchResultState.NOT_EVIDENCED
        match_value = 0.0
        ev_refs = []
        observed_val = None

    if req.necessity == RequirementNecessity.MUST_HAVE and result_state != MatchResultState.MATCHED:
        reason_codes.append("MUST_HAVE_UNFULFILLED")

    return RequirementMatch(
        requirement_id=req.requirement_id,
        requirement_name=req.label,
        display_order=req.display_order,
        category=req.category,
        result_state=result_state,
        match_value=match_value,
        comparison_method=MatchComparisonMethod.CERTIFICATION_VERIFICATION,
        expected_value=req.label,
        observed_value=observed_val,
        evidence_references=ev_refs,
        reason_codes=reason_codes,
        review_recommended=review_recommended,
        matching_rule_version=MATCHER_RULES_VERSION,
        necessity=req.necessity,
        priority=req.priority,
        approved_weight=req.approved_weight,
    )


def _match_language_requirement(
    req: RequirementMatchingItem,
    candidate_view: CandidateFeatureView,
) -> RequirementMatch:
    """Matches a LANGUAGE requirement against candidate language features."""
    norm_req_label = normalize_search_query(req.normalized_label or req.label)
    approved_aliases = [normalize_search_query(a) for a in req.accepted_equivalents if a and a.strip()]

    matched_lang: Optional[CandidateLanguageFeature] = None
    for l in candidate_view.languages:
        norm_l = normalize_search_query(l.normalized_language or l.language)
        if _safe_exact_token_match(norm_l, norm_req_label):
            matched_lang = l
            break
        for alias in approved_aliases:
            if _safe_exact_token_match(norm_l, alias):
                matched_lang = l
                break
        if matched_lang is not None:
            break

    reason_codes: List[str] = []
    if matched_lang is not None and len(matched_lang.evidence_ids) > 0:
        result_state = MatchResultState.MATCHED
        match_value = 1.0
        ev_refs = sorted(list(set(matched_lang.evidence_ids)))
        observed_val = f"{matched_lang.language} ({matched_lang.proficiency or 'Documented'})"
    else:
        result_state = MatchResultState.NOT_EVIDENCED
        match_value = 0.0
        ev_refs = []
        observed_val = None

    if req.necessity == RequirementNecessity.MUST_HAVE and result_state != MatchResultState.MATCHED:
        reason_codes.append("MUST_HAVE_UNFULFILLED")

    return RequirementMatch(
        requirement_id=req.requirement_id,
        requirement_name=req.label,
        display_order=req.display_order,
        category=req.category,
        result_state=result_state,
        match_value=match_value,
        comparison_method=MatchComparisonMethod.LANGUAGE_PROFICIENCY,
        expected_value=req.label,
        observed_value=observed_val,
        evidence_references=ev_refs,
        reason_codes=reason_codes,
        matching_rule_version=MATCHER_RULES_VERSION,
        necessity=req.necessity,
        priority=req.priority,
        approved_weight=req.approved_weight,
    )


def _match_responsibility_or_other_requirement(
    req: RequirementMatchingItem,
    candidate_view: CandidateFeatureView,
) -> RequirementMatch:
    """Matches a RESPONSIBILITY or OTHER requirement against candidate employment/project descriptions."""
    norm_req_label = normalize_search_query(req.normalized_label or req.label)
    approved_aliases = [normalize_search_query(a) for a in req.accepted_equivalents if a and a.strip()]

    credited_ev_ids: List[str] = []
    observed_text: Optional[str] = None

    # Search employment descriptions
    for role in candidate_view.employment:
        for desc in role.descriptions:
            if _safe_exact_token_match(desc, norm_req_label):
                credited_ev_ids.extend(role.evidence_ids)
                observed_text = f"Employment ({role.job_title}): {desc[:80]}..." if len(desc) > 80 else f"Employment ({role.job_title}): {desc}"
                break
            for alias in approved_aliases:
                if _safe_exact_token_match(desc, alias):
                    credited_ev_ids.extend(role.evidence_ids)
                    observed_text = f"Employment ({role.job_title}): {desc[:80]}..." if len(desc) > 80 else f"Employment ({role.job_title}): {desc}"
                    break
            if credited_ev_ids:
                break
        if credited_ev_ids:
            break

    # Search project descriptions
    if not credited_ev_ids:
        for p in candidate_view.projects:
            if p.description and _safe_exact_token_match(p.description, norm_req_label):
                credited_ev_ids.extend(p.evidence_ids)
                observed_text = f"Project ({p.name}): {p.description[:80]}..." if len(p.description) > 80 else f"Project ({p.name}): {p.description}"
                break
            for alias in approved_aliases:
                if p.description and _safe_exact_token_match(p.description, alias):
                    credited_ev_ids.extend(p.evidence_ids)
                    observed_text = f"Project ({p.name}): {p.description[:80]}..." if len(p.description) > 80 else f"Project ({p.name}): {p.description}"
                    break
            if credited_ev_ids:
                break

    reason_codes: List[str] = []
    if credited_ev_ids:
        result_state = MatchResultState.MATCHED
        match_value = 1.0
        ev_refs = sorted(list(set(credited_ev_ids)))
    else:
        result_state = MatchResultState.NOT_EVIDENCED
        match_value = 0.0
        ev_refs = []
        observed_text = None

    if req.necessity == RequirementNecessity.MUST_HAVE and result_state != MatchResultState.MATCHED:
        reason_codes.append("MUST_HAVE_UNFULFILLED")

    return RequirementMatch(
        requirement_id=req.requirement_id,
        requirement_name=req.label,
        display_order=req.display_order,
        category=req.category,
        result_state=result_state,
        match_value=match_value,
        comparison_method=MatchComparisonMethod.DOCUMENTED_RESPONSIBILITY,
        expected_value=req.label,
        observed_value=observed_text,
        evidence_references=ev_refs,
        reason_codes=reason_codes,
        matching_rule_version=MATCHER_RULES_VERSION,
        necessity=req.necessity,
        priority=req.priority,
        approved_weight=req.approved_weight,
    )


def match_requisition_requirements(
    snapshot: MatchInputSnapshot,
    evaluation_date: Optional[datetime] = None,
    db: Optional[Session] = None,
) -> RequirementMatchResultSet:
    """Deterministic requirement comparison orchestrator.

    Iterates through all active requirements in strict persisted display_order,
    executes category-specific deterministic matchers against candidate features,
    verifies evidence reference lineage, and produces an immutable RequirementMatchResultSet.
    """
    # 1. Deterministic evaluation date resolution
    eval_date = evaluation_date or snapshot.snapshot_created_at
    if eval_date.tzinfo is None:
        eval_date = eval_date.replace(tzinfo=timezone.utc)

    # 2. Extract valid candidate evidence IDs for lineage verification
    valid_candidate_evidence_ids: Set[str] = {
        ref.evidence_id for ref in snapshot.candidate_view.evidence_references
    }

    # 3. Process requirements in exact persisted display_order
    sorted_reqs = sorted(
        snapshot.requisition_view.requirements,
        key=lambda r: (r.display_order, str(r.requirement_id)),
    )

    matches: List[RequirementMatch] = []
    for req in sorted_reqs:
        cat = req.category
        if cat == RequirementCategory.SKILL:
            m = _match_skill_requirement(req, snapshot.candidate_view, db=db)
        elif cat == RequirementCategory.EXPERIENCE:
            m = _match_experience_requirement(req, snapshot.candidate_view, evaluation_date=eval_date)
        elif cat == RequirementCategory.EDUCATION:
            m = _match_education_requirement(req, snapshot.candidate_view)
        elif cat == RequirementCategory.CERTIFICATION:
            m = _match_certification_requirement(req, snapshot.candidate_view, evaluation_date=eval_date)
        elif cat == RequirementCategory.LANGUAGE:
            m = _match_language_requirement(req, snapshot.candidate_view)
        elif cat in {RequirementCategory.RESPONSIBILITY, RequirementCategory.OTHER}:
            m = _match_responsibility_or_other_requirement(req, snapshot.candidate_view)
        else:
            raise UnsupportedRequirementCategoryError(f"Unsupported category: {cat}")

        # 4. Verify candidate evidence references belong strictly to candidate profile lineage
        for ev_id in m.evidence_references:
            if ev_id not in valid_candidate_evidence_ids:
                raise ForeignEvidenceReferenceError(
                    f"Evidence ID '{ev_id}' referenced in match for requirement '{req.label}' does not belong to candidate profile."
                )

        matches.append(m)

    return RequirementMatchResultSet(
        match_input_schema_version=snapshot.match_input_schema_version,
        matcher_algorithm_version=MATCHER_RULES_VERSION,
        evaluation_date=eval_date,
        requisition_id=snapshot.requisition_view.requisition_id,
        job_version_id=snapshot.requisition_view.job_version_id,
        revision=snapshot.requisition_view.revision,
        profile_id=snapshot.candidate_view.profile_id,
        profile_version=snapshot.candidate_view.profile_version,
        matches=matches,
    )
