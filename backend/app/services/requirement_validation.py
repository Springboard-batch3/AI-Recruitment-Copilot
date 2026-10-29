import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import (
    JobRequirement,
    JobVersion,
    RequirementCategory,
    RequirementEvidenceType,
    RequirementNecessity,
    RequirementOrigin,
    RequirementPriority,
)
from app.models.taxonomy import (
    OccupationConcept,
    SkillConcept,
    TaxonomySnapshot,
    TaxonomySnapshotState,
)
from app.services.taxonomy_normalization import normalize_search_query

# Bounded, deterministic patterns for non-conclusive review warnings
# Flags potentially non-job-related or sensitive criteria (age, gender, marital status, nationality, health assumptions)
SENSITIVE_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(young|youthful|digital native|recent grad(?:uate)?|energetic)\b", "POTENTIAL_AGE_BIAS"),
    (r"\b(mature|elderly|senior citizen)\b", "POTENTIAL_AGE_BIAS"),
    (r"\b(female|male|woman|man|girl|boy|guy|gentleman|lady)\b", "POTENTIAL_GENDER_BIAS"),
    (r"\b(married|single|unmarried|spouse|family status|parent|mother|father)\b", "POTENTIAL_FAMILY_STATUS_BIAS"),
    (r"\b(native english speaker|native speaker|citizen only|citizenship required)\b", "POTENTIAL_NATIONAL_ORIGIN_BIAS"),
    (r"\b(able-bodied|clean bill of health|no disabilities|healthy)\b", "POTENTIAL_HEALTH_DISABILITY_BIAS"),
    (r"\b(christian|muslim|jewish|hindu|religious)\b", "POTENTIAL_RELIGION_BIAS"),
]


class RequirementValidationError(Exception):
    def __init__(self, message: str, code: str = "INVALID_REQUIREMENT", field: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.field = field


def evaluate_requirement_warnings(
    label: str,
    necessity: RequirementNecessity,
    priority: RequirementPriority,
    rationale: Optional[str],
) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    Evaluates review warnings for a requirement.
    Does NOT block draft saving; returns review_required boolean and list of structured warning metadata.
    """
    warnings: List[Dict[str, Any]] = []
    clean_rationale = (rationale or "").strip()

    # 1. Missing rationale warnings for MUST_HAVE or CRITICAL requirements
    if necessity == RequirementNecessity.MUST_HAVE and not clean_rationale:
        warnings.append({
            "code": "MISSING_MUST_HAVE_RATIONALE",
            "message": "Must-have requirements should include a job-related business rationale for auditability.",
            "severity": "REVIEW_SUGGESTED",
        })

    if priority == RequirementPriority.CRITICAL and not clean_rationale:
        if not any(w["code"] == "MISSING_MUST_HAVE_RATIONALE" for w in warnings):
            warnings.append({
                "code": "MISSING_CRITICAL_RATIONALE",
                "message": "Critical priority requirements should include a job-related rationale explaining why they are essential.",
                "severity": "REVIEW_SUGGESTED",
            })

    # 2. Bounded, deterministic scan for potentially sensitive phrasing
    combined_text = f"{label} {clean_rationale}".lower()
    detected_sensitivities: Set[str] = set()

    for pattern, rule_code in SENSITIVE_PATTERNS:
        match = re.search(pattern, combined_text, flags=re.IGNORECASE)
        if match and rule_code not in detected_sensitivities:
            detected_sensitivities.add(rule_code)
            matched_word = match.group(0)
            warnings.append({
                "code": "POTENTIALLY_SENSITIVE_CRITERIA",
                "rule": rule_code,
                "matched_term": matched_word,
                "message": f"Wording contains '{matched_word}', which may imply non-job-related criteria ({rule_code}). Human review recommended.",
                "severity": "HUMAN_REVIEW_RECOMMENDED",
            })

    review_required = len(warnings) > 0
    return review_required, warnings


def validate_and_prepare_requirements(
    requirements_data: List[Dict[str, Any]],
    job_version_id: uuid.UUID,
    db: Session,
) -> List[JobRequirement]:
    """
    Validates a list of requirement dictionaries atomically.
    Ensures:
    1. Non-empty label after normalization.
    2. Valid necessity, priority, category.
    3. Positive min_experience_months, bounded recency (1-50), bounded weight (0-10).
    4. Deduplicated accepted equivalents, distinct from canonical label.
    5. Valid accepted evidence types.
    6. Unique (category, normalized_label) per job version draft.
    7. Taxonomy verification: If taxonomy_skill_concept_id provided, derives canonical label from active DB snapshot.
    8. Generates review warnings for missing rationales or sensitive wording.
    """
    # 1. Fetch active taxonomy snapshot if any concept IDs are provided
    skill_concept_ids = [
        r.get("taxonomy_skill_concept_id") or r.get("taxonomySkillConceptId")
        for r in requirements_data
        if r.get("taxonomy_skill_concept_id") or r.get("taxonomySkillConceptId")
    ]

    active_skills_map: Dict[uuid.UUID, SkillConcept] = {}
    if skill_concept_ids:
        # Filter valid UUIDs
        parsed_ids = []
        for cid in skill_concept_ids:
            if isinstance(cid, str):
                try:
                    parsed_ids.append(uuid.UUID(cid))
                except ValueError:
                    raise RequirementValidationError(f"Invalid taxonomy concept UUID: {cid}", code="INVALID_TAXONOMY_CONCEPT")
            elif isinstance(cid, uuid.UUID):
                parsed_ids.append(cid)

        if parsed_ids:
            stmt = (
                select(SkillConcept)
                .join(TaxonomySnapshot, SkillConcept.snapshot_id == TaxonomySnapshot.snapshot_id)
                .where(
                    TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE,
                    SkillConcept.concept_id.in_(parsed_ids),
                )
            )
            skills = db.execute(stmt).scalars().all()
            active_skills_map = {s.concept_id: s for s in skills}

    validated_models: List[JobRequirement] = []
    seen_keys: Set[Tuple[str, str]] = set()

    for idx, item in enumerate(requirements_data):
        raw_label = item.get("label", "")
        if not raw_label or not raw_label.strip():
            raise RequirementValidationError("Requirement label cannot be empty or whitespace.", code="EMPTY_REQUIREMENT_LABEL", field="label")

        norm_label = normalize_search_query(raw_label.strip())
        if not norm_label:
            raise RequirementValidationError("Requirement label is empty after normalization.", code="EMPTY_REQUIREMENT_LABEL", field="label")

        # Category
        raw_cat = item.get("category", "SKILL")
        try:
            category = RequirementCategory(raw_cat)
        except ValueError:
            raise RequirementValidationError(f"Invalid requirement category '{raw_cat}'.", code="INVALID_CATEGORY", field="category")

        # Duplicate check within this version draft
        key = (category.value, norm_label)
        if key in seen_keys:
            raise RequirementValidationError(
                f"Duplicate requirement '{raw_label.strip()}' in category '{category.value}'.",
                code="DUPLICATE_REQUIREMENT",
                field="label",
            )
        seen_keys.add(key)

        # Necessity
        raw_nec = item.get("necessity", "MUST_HAVE")
        try:
            necessity = RequirementNecessity(raw_nec)
        except ValueError:
            raise RequirementValidationError(f"Invalid necessity value '{raw_nec}'.", code="INVALID_NECESSITY", field="necessity")

        # Priority
        raw_prio = item.get("priority", "MEDIUM")
        try:
            priority = RequirementPriority(raw_prio)
        except ValueError:
            raise RequirementValidationError(f"Invalid priority value '{raw_prio}'.", code="INVALID_PRIORITY", field="priority")

        # Min experience
        min_exp = item.get("min_experience_months") or item.get("minExperienceMonths")
        if min_exp is not None:
            if not isinstance(min_exp, int) or min_exp <= 0:
                raise RequirementValidationError("Minimum experience must be a positive integer in months.", code="INVALID_MIN_EXPERIENCE", field="min_experience_months")

        # Recency
        recency = item.get("recency_years") or item.get("recencyYears")
        if recency is not None:
            if not isinstance(recency, int) or recency < 1 or recency > 50:
                raise RequirementValidationError("Recency in years must be between 1 and 50.", code="INVALID_RECENCY", field="recency_years")

        # Custom weight
        weight = item.get("custom_weight_override") or item.get("customWeightOverride")
        if weight is not None:
            try:
                weight_val = float(weight)
                if weight_val < 0.0 or weight_val > 10.0:
                    raise ValueError()
            except (ValueError, TypeError):
                raise RequirementValidationError("Custom weight override must be between 0.0 and 10.0.", code="INVALID_CUSTOM_WEIGHT", field="custom_weight_override")
        else:
            weight_val = None

        # Evidence types
        raw_evidence = item.get("accepted_evidence_types") or item.get("acceptedEvidenceTypes") or []
        accepted_evidence_list: List[str] = []
        for ev in raw_evidence:
            try:
                ev_enum = RequirementEvidenceType(ev)
                if ev_enum.value not in accepted_evidence_list:
                    accepted_evidence_list.append(ev_enum.value)
            except ValueError:
                raise RequirementValidationError(f"Invalid evidence type '{ev}'.", code="INVALID_EVIDENCE_TYPE", field="accepted_evidence_types")

        # Accepted equivalents
        raw_equivs = item.get("accepted_equivalents") or item.get("acceptedEquivalents") or []
        accepted_equivs_list: List[str] = []
        for eq in raw_equivs:
            if not isinstance(eq, str):
                continue
            clean_eq = eq.strip()
            if not clean_eq:
                continue
            norm_eq = normalize_search_query(clean_eq)
            if norm_eq == norm_label:
                # Do not duplicate canonical requirement label
                continue
            if len(clean_eq) > 255:
                raise RequirementValidationError(f"Accepted equivalent '{clean_eq[:30]}...' exceeds 255 characters.", code="EQUIVALENT_TOO_LONG")
            if clean_eq not in accepted_equivs_list:
                accepted_equivs_list.append(clean_eq)

        # Rationale
        rationale = item.get("rationale")
        if rationale is not None:
            clean_rat = rationale.strip()
            if len(clean_rat) > 2000:
                raise RequirementValidationError("Rationale exceeds maximum length of 2000 characters.", code="RATIONALE_TOO_LONG", field="rationale")
            final_rationale = clean_rat if clean_rat else None
        else:
            final_rationale = None

        # Taxonomy resolution & Origin
        concept_id_raw = item.get("taxonomy_skill_concept_id") or item.get("taxonomySkillConceptId")
        final_concept_id: Optional[uuid.UUID] = None
        final_label = raw_label.strip()
        final_origin = RequirementOrigin.RECRUITER_ENTERED

        if concept_id_raw:
            cid = uuid.UUID(str(concept_id_raw)) if not isinstance(concept_id_raw, uuid.UUID) else concept_id_raw
            if cid not in active_skills_map:
                raise RequirementValidationError(
                    f"Taxonomy concept '{cid}' does not exist in the active O*NET taxonomy snapshot.",
                    code="TAXONOMY_CONCEPT_NOT_ACTIVE",
                    field="taxonomy_skill_concept_id",
                )
            # Authoritative server-side canonical label derived from record
            server_concept = active_skills_map[cid]
            final_concept_id = cid
            final_label = server_concept.canonical_label
            norm_label = server_concept.normalized_label
            final_origin = RequirementOrigin.TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED

        # Evaluate warnings
        review_required, warnings = evaluate_requirement_warnings(
            label=final_label,
            necessity=necessity,
            priority=priority,
            rationale=final_rationale,
        )

        display_order = item.get("display_order") or item.get("displayOrder") or idx

        req_model = JobRequirement(
            requirement_id=uuid.uuid4(),
            job_version_id=job_version_id,
            display_order=display_order,
            category=category,
            label=final_label,
            normalized_label=norm_label,
            taxonomy_skill_concept_id=final_concept_id,
            necessity=necessity,
            priority=priority,
            custom_weight_override=weight_val,
            min_experience_months=min_exp,
            recency_years=recency,
            accepted_evidence_types=accepted_evidence_list,
            accepted_equivalents=accepted_equivs_list,
            rationale=final_rationale,
            origin=final_origin,
            review_required=review_required,
            warning_metadata=warnings,
        )
        validated_models.append(req_model)

    return validated_models
