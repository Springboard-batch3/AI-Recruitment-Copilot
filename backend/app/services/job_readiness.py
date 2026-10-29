import logging
import uuid
from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import (
    JobRequirement,
    JobVersion,
    JobVersionStatus,
    RequirementCategory,
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

logger = logging.getLogger("ai_hiring.services.job_readiness")


class ReadinessSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class JobReadinessFinding:
    code: str
    severity: ReadinessSeverity
    message: str
    field_name: Optional[str] = None
    requirement_id: Optional[uuid.UUID] = None
    blocking: bool = False
    metadata: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class JobReadinessResult:
    job_id: uuid.UUID
    job_version_id: uuid.UUID
    version_number: int
    revision: int
    version_status: JobVersionStatus
    is_ready: bool
    errors: List[JobReadinessFinding] = dc_field(default_factory=list)
    warnings: List[JobReadinessFinding] = dc_field(default_factory=list)
    info: List[JobReadinessFinding] = dc_field(default_factory=list)


def evaluate_job_version_readiness(
    version: JobVersion,
    db: Session,
    expected_revision: Optional[int] = None,
) -> JobReadinessResult:
    """Authoritative server-side readiness validation for a job requisition revision.

    Evaluates blocking constraints (ERROR), advisory warnings (WARNING), and
    informational notices (INFO). Does not mutate the version state.
    """
    errors: List[JobReadinessFinding] = []
    warnings: List[JobReadinessFinding] = []
    info: List[JobReadinessFinding] = []

    # 1. Check expected revision if provided
    if expected_revision is not None and version.revision != expected_revision:
        errors.append(
            JobReadinessFinding(
                code="STALE_REVISION",
                severity=ReadinessSeverity.ERROR,
                message=f"Draft revision mismatch. Server has revision {version.revision}, expected {expected_revision}.",
                field_name="revision",
                blocking=True,
            )
        )

    # 2. Check title
    if not version.title or not version.title.strip():
        errors.append(
            JobReadinessFinding(
                code="EMPTY_TITLE",
                severity=ReadinessSeverity.ERROR,
                message="Job title is required and cannot be blank.",
                field_name="title",
                blocking=True,
            )
        )

    # 3. Check core scalar bounds
    if version.openings_count is not None and version.openings_count <= 0:
        errors.append(
            JobReadinessFinding(
                code="INVALID_OPENINGS_COUNT",
                severity=ReadinessSeverity.ERROR,
                message="Number of openings must be greater than zero.",
                field_name="openings_count",
                blocking=True,
            )
        )

    if version.summary and len(version.summary) > 10000:
        errors.append(
            JobReadinessFinding(
                code="SUMMARY_TOO_LONG",
                severity=ReadinessSeverity.ERROR,
                message="Role summary exceeds maximum length of 10,000 characters.",
                field_name="summary",
                blocking=True,
            )
        )

    if version.responsibilities and len(version.responsibilities) > 50000:
        errors.append(
            JobReadinessFinding(
                code="RESPONSIBILITIES_TOO_LONG",
                severity=ReadinessSeverity.ERROR,
                message="Responsibilities text exceeds maximum length of 50,000 characters.",
                field_name="responsibilities",
                blocking=True,
            )
        )

    # 4. Check Occupation reference (Optional & Advisory)
    if not version.occupation_concept_id:
        info.append(
            JobReadinessFinding(
                code="NO_OCCUPATION_REFERENCE",
                severity=ReadinessSeverity.INFO,
                message="No O*NET occupation reference selected. Standard occupation reference is optional and advisory.",
                field_name="occupation_concept_id",
                blocking=False,
            )
        )
    else:
        # Validate that referenced occupation exists in active snapshot
        stmt = (
            select(OccupationConcept)
            .join(TaxonomySnapshot, OccupationConcept.snapshot_id == TaxonomySnapshot.snapshot_id)
            .where(
                TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE,
                OccupationConcept.concept_id == version.occupation_concept_id,
            )
        )
        occ = db.execute(stmt).scalar_one_or_none()
        if occ is None:
            # Fallback to check if concept exists in superseded snapshot
            stmt_prev = select(OccupationConcept).where(
                OccupationConcept.concept_id == version.occupation_concept_id
            )
            occ = db.execute(stmt_prev).scalar_one_or_none()

        if occ is None:
            errors.append(
                JobReadinessFinding(
                    code="INVALID_OCCUPATION_REFERENCE",
                    severity=ReadinessSeverity.ERROR,
                    message=f"Referenced occupation '{version.occupation_concept_id}' was not found in active O*NET taxonomy snapshot.",
                    field_name="occupation_concept_id",
                    blocking=True,
                )
            )
        else:
            info.append(
                JobReadinessFinding(
                    code="ONET_OCCUPATION_LINKED",
                    severity=ReadinessSeverity.INFO,
                    message=f"Linked to standard O*NET occupation: '{occ.canonical_title}' ({occ.onet_soc_code}).",
                    field_name="occupation_concept_id",
                    blocking=False,
                    metadata={"concept_id": str(occ.concept_id), "title": occ.canonical_title, "soc": occ.onet_soc_code},
                )
            )

    # 5. Check Structured Requirements
    reqs = version.requirements or []
    if len(reqs) == 0:
        errors.append(
            JobReadinessFinding(
                code="NO_REQUIREMENTS",
                severity=ReadinessSeverity.ERROR,
                message="Requisition has no structured requirements. Add at least one requirement before marking ready.",
                field_name="requirements",
                blocking=True,
            )
        )
    else:
        must_have_count = 0
        preferred_count = 0
        seen_category_labels = set()

        for idx, r in enumerate(reqs):
            r_id = r.requirement_id
            label = r.label or ""
            norm_label = r.normalized_label or label.strip().lower()

            # Empty label
            if not label.strip():
                errors.append(
                    JobReadinessFinding(
                        code="EMPTY_REQUIREMENT_LABEL",
                        severity=ReadinessSeverity.ERROR,
                        message=f"Requirement #{idx + 1} has an empty label.",
                        requirement_id=r_id,
                        blocking=True,
                    )
                )

            # Category validity
            if not isinstance(r.category, RequirementCategory) and r.category not in [c.value for c in RequirementCategory]:
                errors.append(
                    JobReadinessFinding(
                        code="INVALID_REQUIREMENT_CATEGORY",
                        severity=ReadinessSeverity.ERROR,
                        message=f"Requirement '{label}' has invalid category '{r.category}'.",
                        requirement_id=r_id,
                        blocking=True,
                    )
                )

            # Necessity validity
            if r.necessity == RequirementNecessity.MUST_HAVE:
                must_have_count += 1
            elif r.necessity == RequirementNecessity.PREFERRED:
                preferred_count += 1
            else:
                errors.append(
                    JobReadinessFinding(
                        code="UNRESOLVED_NECESSITY",
                        severity=ReadinessSeverity.ERROR,
                        message=f"Requirement '{label}' has undecided necessity status. Choose Required or Preferred.",
                        requirement_id=r_id,
                        blocking=True,
                    )
                )

            # Priority validity
            if not isinstance(r.priority, RequirementPriority) and r.priority not in [p.value for p in RequirementPriority]:
                errors.append(
                    JobReadinessFinding(
                        code="INVALID_PRIORITY",
                        severity=ReadinessSeverity.ERROR,
                        message=f"Requirement '{label}' has invalid priority '{r.priority}'.",
                        requirement_id=r_id,
                        blocking=True,
                    )
                )

            # Duplicate check in same category
            cat_key = (r.category, norm_label)
            if cat_key in seen_category_labels:
                errors.append(
                    JobReadinessFinding(
                        code="DUPLICATE_REQUIREMENT",
                        severity=ReadinessSeverity.ERROR,
                        message=f"Duplicate requirement '{label}' in category '{r.category.value if hasattr(r.category, 'value') else r.category}'.",
                        requirement_id=r_id,
                        blocking=True,
                    )
                )
            else:
                seen_category_labels.add(cat_key)

            # Experience bounds
            if r.min_experience_months is not None:
                if r.min_experience_months <= 0:
                    errors.append(
                        JobReadinessFinding(
                            code="INVALID_EXPERIENCE_BOUNDS",
                            severity=ReadinessSeverity.ERROR,
                            message=f"Requirement '{label}' minimum experience must be greater than 0 months.",
                            requirement_id=r_id,
                            blocking=True,
                        )
                    )
                elif r.min_experience_months > 120:
                    warnings.append(
                        JobReadinessFinding(
                            code="HIGH_EXPERIENCE_THRESHOLD",
                            severity=ReadinessSeverity.WARNING,
                            message=f"Requirement '{label}' specifies {r.min_experience_months // 12} years ({r.min_experience_months} months) of experience, which is unusually high.",
                            requirement_id=r_id,
                            blocking=False,
                        )
                    )

            # Recency bounds
            if r.recency_years is not None and (r.recency_years < 1 or r.recency_years > 50):
                errors.append(
                    JobReadinessFinding(
                        code="INVALID_RECENCY_BOUNDS",
                        severity=ReadinessSeverity.ERROR,
                        message=f"Requirement '{label}' recency must be between 1 and 50 years.",
                        requirement_id=r_id,
                        blocking=True,
                    )
                )

            # Custom weight override bounds
            if r.custom_weight_override is not None and (r.custom_weight_override < 0.0 or r.custom_weight_override > 10.0):
                errors.append(
                    JobReadinessFinding(
                        code="INVALID_CUSTOM_WEIGHT",
                        severity=ReadinessSeverity.ERROR,
                        message=f"Requirement '{label}' custom weight override must be between 0.0 and 10.0.",
                        requirement_id=r_id,
                        blocking=True,
                    )
                )

            # Must-have / Critical Rationale requirement
            is_must_have = r.necessity == RequirementNecessity.MUST_HAVE
            is_critical = r.priority == RequirementPriority.CRITICAL
            if (is_must_have or is_critical) and (not r.rationale or not r.rationale.strip()):
                errors.append(
                    JobReadinessFinding(
                        code="MISSING_MUST_HAVE_RATIONALE",
                        severity=ReadinessSeverity.ERROR,
                        message=f"Required criterion '{label}' requires a human-written job-related business rationale.",
                        requirement_id=r_id,
                        field_name="rationale",
                        blocking=True,
                    )
                )

            # Taxonomy Skill Integrity check
            if r.taxonomy_skill_concept_id is not None:
                stmt_skill = (
                    select(SkillConcept)
                    .join(TaxonomySnapshot, SkillConcept.snapshot_id == TaxonomySnapshot.snapshot_id)
                    .where(
                        TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE,
                        SkillConcept.concept_id == r.taxonomy_skill_concept_id,
                    )
                )
                skill_obj = db.execute(stmt_skill).scalar_one_or_none()
                if skill_obj is None:
                    # Fallback to check if concept exists in superseded snapshot
                    stmt_prev_sk = select(SkillConcept).where(
                        SkillConcept.concept_id == r.taxonomy_skill_concept_id
                    )
                    skill_obj = db.execute(stmt_prev_sk).scalar_one_or_none()

                if skill_obj is None:
                    errors.append(
                        JobReadinessFinding(
                            code="INVALID_TAXONOMY_SKILL",
                            severity=ReadinessSeverity.ERROR,
                            message=f"Requirement '{label}' references a skill concept '{r.taxonomy_skill_concept_id}' not found in active O*NET snapshot.",
                            requirement_id=r_id,
                            blocking=True,
                        )
                    )
                else:
                    info.append(
                        JobReadinessFinding(
                            code="ONET_SKILL_LINKED",
                            severity=ReadinessSeverity.INFO,
                            message=f"Requirement '{label}' validated against O*NET canonical skill '{skill_obj.canonical_label}'.",
                            requirement_id=r_id,
                            blocking=False,
                            metadata={"concept_id": str(skill_obj.concept_id), "canonical_label": skill_obj.canonical_label},
                        )
                    )
            elif r.origin == RequirementOrigin.TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED:
                # Provenance claims O*NET confirmed but concept ID is missing
                errors.append(
                    JobReadinessFinding(
                        code="FALSE_TAXONOMY_PROVENANCE",
                        severity=ReadinessSeverity.ERROR,
                        message=f"Requirement '{label}' is marked as taxonomy-backed but has no canonical concept ID.",
                        requirement_id=r_id,
                        blocking=True,
                    )
                )
            else:
                # Custom requirement (Valid and supported)
                info.append(
                    JobReadinessFinding(
                        code="CUSTOM_REQUIREMENT",
                        severity=ReadinessSeverity.INFO,
                        message=f"Requirement '{label}' is a recruiter-defined custom requirement.",
                        requirement_id=r_id,
                        blocking=False,
                    )
                )

        # Cross-requirement warnings (non-blocking)
        if len(reqs) > 1 and must_have_count == len(reqs):
            warnings.append(
                JobReadinessFinding(
                    code="ALL_MUST_HAVE_CRITERIA",
                    severity=ReadinessSeverity.WARNING,
                    message="All requirements are marked as must-have. Consider marking some complementary skills as preferred.",
                    field_name="requirements",
                    blocking=False,
                )
            )

        if len(reqs) > 1 and preferred_count == 0 and must_have_count > 0:
            info.append(
                JobReadinessFinding(
                    code="NO_PREFERRED_CRITERIA",
                    severity=ReadinessSeverity.INFO,
                    message="No preferred (nice-to-have) requirements specified for this requisition.",
                    field_name="requirements",
                    blocking=False,
                )
            )

    is_ready = len(errors) == 0

    return JobReadinessResult(
        job_id=version.job_id,
        job_version_id=version.job_version_id,
        version_number=version.version_number,
        revision=version.revision,
        version_status=version.version_status,
        is_ready=is_ready,
        errors=errors,
        warnings=warnings,
        info=info,
    )
