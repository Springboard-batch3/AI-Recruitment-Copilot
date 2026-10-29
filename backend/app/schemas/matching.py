from datetime import datetime
from typing import Any, Dict, FrozenSet, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.job import (
    JobLifecycleStatus,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementOrigin,
    RequirementPriority,
)

MATCH_INPUT_SCHEMA_VERSION: str = "1.0.0"
MATCHER_ALGORITHM_VERSION: str = "1.0.0"

# Explicit prohibited keys that must never appear anywhere in serialized or projected feature views
PROHIBITED_IDENTITY_KEYS: FrozenSet[str] = frozenset({
    "candidate_name",
    "full_name",
    "first_name",
    "last_name",
    "primary_name",
    "middle_name",
    "surname",
    "given_name",
    "email",
    "emails",
    "primary_email",
    "phone",
    "phones",
    "phone_numbers",
    "primary_phone",
    "photo",
    "photo_url",
    "linkedin",
    "github",
    "portfolio",
    "project_url",
    "credential_url",
    "url",
    "urls",
    "address",
    "location",
    "complete_address",
    "street_address",
    "postal_code",
    "city",
    "state",
    "zip",
    "dob",
    "date_of_birth",
    "age",
    "gender",
    "sex",
    "race",
    "ethnicity",
    "religion",
    "caste",
    "marital_status",
    "family_status",
    "disability",
    "health",
    "identity_id",
    "candidate_identity_id",
    "original_filename",
    "filename",
    "raw_bytes",
    "raw_text",
    "source_text",
    "raw_resume_text",
    "job_description_text",
    "blocks",
    "storage_path",
    "operational_storage_path",
    "file_path",
})


def verify_no_prohibited_keys(data: Any, path: str = "") -> None:
    """Recursively checks that no prohibited identity or operational key is present in data."""
    if isinstance(data, dict):
        for k, v in data.items():
            k_lower = str(k).lower()
            if k_lower in PROHIBITED_IDENTITY_KEYS:
                location = f"{path}.{k}" if path else str(k)
                raise ValueError(
                    f"Prohibited identity or protected key '{k}' detected at '{location}'."
                )
            verify_no_prohibited_keys(v, f"{path}.{k}" if path else str(k))
    elif isinstance(data, (list, tuple, set)):
        for idx, item in enumerate(data):
            verify_no_prohibited_keys(item, f"{path}[{idx}]")


class FrozenSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )


class EvidenceReferenceFeature(FrozenSchema):
    evidence_id: str
    page_number: Optional[int] = None
    block_number: Optional[int] = None
    character_start: int = Field(ge=0)
    character_end: int = Field(gt=0)
    extraction_method: str

    @model_validator(mode="after")
    def validate_bounds(self) -> "EvidenceReferenceFeature":
        if self.character_end <= self.character_start:
            raise ValueError("character_end must be greater than character_start.")
        return self


class CandidateSkillFeature(FrozenSchema):
    name: str
    normalized_name: str
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence_band: str = "UNKNOWN"
    evidence_ids: List[str] = Field(default_factory=list)
    review_status: str = "UNREVIEWED"


class CandidateEmploymentRoleFeature(FrozenSchema):
    job_title: str
    organization: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    descriptions: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence_band: str = "UNKNOWN"


class CandidateEducationFeature(FrozenSchema):
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    institution: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    grade: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence_band: str = "UNKNOWN"


class CandidateProjectFeature(FrozenSchema):
    name: str
    description: Optional[str] = None
    technologies: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence_band: str = "UNKNOWN"


class CandidateCertificationFeature(FrozenSchema):
    name: str
    issuer: Optional[str] = None
    issued_date: Optional[str] = None
    expiry_date: Optional[str] = None
    credential_id: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence_band: str = "UNKNOWN"


class CandidateLanguageFeature(FrozenSchema):
    language: str
    normalized_language: Optional[str] = None
    proficiency: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence_band: str = "UNKNOWN"


class CandidateFeatureView(FrozenSchema):
    profile_id: UUID
    profile_version: int = Field(ge=1)
    schema_version: str
    parser_pipeline_version: str
    provenance: str
    headline: Optional[str] = None
    skills: List[CandidateSkillFeature] = Field(default_factory=list)
    employment: List[CandidateEmploymentRoleFeature] = Field(default_factory=list)
    education: List[CandidateEducationFeature] = Field(default_factory=list)
    projects: List[CandidateProjectFeature] = Field(default_factory=list)
    certifications: List[CandidateCertificationFeature] = Field(default_factory=list)
    languages: List[CandidateLanguageFeature] = Field(default_factory=list)
    evidence_references: List[EvidenceReferenceFeature] = Field(default_factory=list)
    review_required: bool = False
    warning_metadata: List[Dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_privacy_safety(self) -> "CandidateFeatureView":
        data = self.model_dump(mode="json")
        verify_no_prohibited_keys(data)
        return self


class RequirementMatchingItem(FrozenSchema):
    requirement_id: UUID
    display_order: int = Field(ge=0)
    category: RequirementCategory
    label: str = Field(min_length=1)
    normalized_label: str = Field(min_length=1)
    necessity: RequirementNecessity
    priority: RequirementPriority
    approved_weight: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    min_experience_months: Optional[int] = Field(default=None, gt=0)
    recency_years: Optional[int] = Field(default=None, ge=1, le=50)
    accepted_evidence_types: List[str] = Field(default_factory=list)
    accepted_equivalents: List[str] = Field(default_factory=list)
    rationale: Optional[str] = None
    origin: RequirementOrigin
    taxonomy_skill_concept_id: Optional[UUID] = None
    taxonomy_canonical_label: Optional[str] = None
    taxonomy_external_id: Optional[str] = None
    taxonomy_source: Optional[str] = None
    taxonomy_source_version: Optional[str] = None
    review_required: bool = False
    warning_metadata: List[Dict[str, Any]] = Field(default_factory=list)


class FrozenRequisitionMatchingView(FrozenSchema):
    requisition_id: UUID
    job_version_id: UUID
    version_number: int = Field(ge=1)
    revision: int = Field(ge=1)
    lifecycle_status: JobLifecycleStatus
    version_status: JobVersionStatus
    owner_user_id: UUID
    title: str = Field(min_length=1)
    occupation_concept_id: Optional[UUID] = None
    occupation_title: Optional[str] = None
    occupation_soc_code: Optional[str] = None
    taxonomy_version: Optional[str] = None
    requirements: List[RequirementMatchingItem] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_requisition_ready(self) -> "FrozenRequisitionMatchingView":
        if self.version_status != JobVersionStatus.READY_FOR_MATCHING:
            raise ValueError(
                f"Frozen requisition matching view requires READY_FOR_MATCHING status, got {self.version_status}."
            )
        if len(self.requirements) == 0:
            raise ValueError("Requirements collection must not be empty.")
        return self


class MatchInputSnapshot(FrozenSchema):
    match_input_schema_version: str = MATCH_INPUT_SCHEMA_VERSION
    matcher_algorithm_version: str = MATCHER_ALGORITHM_VERSION
    snapshot_created_at: datetime
    requisition_view: FrozenRequisitionMatchingView
    candidate_view: CandidateFeatureView
