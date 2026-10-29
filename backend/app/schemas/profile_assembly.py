import enum
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.schemas.candidate import (
    CandidateIdentity,
    CandidateProfessionalProfile,
    EvidenceSpan,
    ReviewStatus,
    StrictSchema,
)


class ProfileAssemblyWarningCode(str, enum.Enum):
    PROFILE_EVIDENCE_INVALID = "PROFILE_EVIDENCE_INVALID"
    PROFILE_EVIDENCE_NOT_FOUND = "PROFILE_EVIDENCE_NOT_FOUND"
    PROFILE_EVIDENCE_CROSS_ARTIFACT = "PROFILE_EVIDENCE_CROSS_ARTIFACT"
    PROFILE_EVIDENCE_CROSS_EXTRACTION = "PROFILE_EVIDENCE_CROSS_EXTRACTION"
    PROHIBITED_IDENTITY_IN_PROFESSIONAL_PROFILE = "PROHIBITED_IDENTITY_IN_PROFESSIONAL_PROFILE"
    PROFILE_ASSEMBLY_FAILED = "PROFILE_ASSEMBLY_FAILED"
    PROFILE_REVIEW_REQUIRED = "PROFILE_REVIEW_REQUIRED"
    PARSER_AMBIGUITY_DETECTED = "PARSER_AMBIGUITY_DETECTED"
    UNRESOLVED_PROFESSIONAL_ITEMS = "UNRESOLVED_PROFESSIONAL_ITEMS"


class ProfileAssemblyWarning(StrictSchema):
    code: str
    category: str
    message: Optional[str] = None
    field_name: Optional[str] = None
    count: Optional[int] = None
    evidence_id: Optional[str] = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ProfileAssemblyResult(StrictSchema):
    schema_version: str = "1.0"
    parser_pipeline_version: str
    identity: CandidateIdentity
    professional_profile: CandidateProfessionalProfile
    evidence_spans: List[EvidenceSpan] = Field(default_factory=list)
    review_required: bool = False
    warnings: List[ProfileAssemblyWarning] = Field(default_factory=list)


class CandidateProfileResponse(StrictSchema):
    candidate_id: Optional[str] = None
    artifact_id: str
    profile_id: str
    profile_version: int
    schema_version: str
    parser_pipeline_version: str
    provenance: str = "PARSER_EXTRACTED"
    parent_profile_id: Optional[str] = None
    parent_profile_version: Optional[int] = None
    created_at: Optional[str] = None
    review_required: bool
    warnings: List[ProfileAssemblyWarning] = Field(default_factory=list)
    identity: CandidateIdentity
    professional_profile: CandidateProfessionalProfile
    evidence: List[EvidenceSpan] = Field(default_factory=list)

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

