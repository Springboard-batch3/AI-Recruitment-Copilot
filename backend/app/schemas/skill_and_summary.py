import enum
from typing import List, Optional
from pydantic import Field

from app.schemas.candidate import ConfidenceBand, EvidenceSpan, ExtractedField, ReviewStatus, StrictSchema


class SkillResolutionStatus(str, enum.Enum):
    RESOLVED_EXACT = "RESOLVED_EXACT"
    RESOLVED_ALIAS = "RESOLVED_ALIAS"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


class SkillCategory(str, enum.Enum):
    PROGRAMMING_LANGUAGE = "PROGRAMMING_LANGUAGE"
    FRAMEWORK = "FRAMEWORK"
    DATABASE = "DATABASE"
    TOOL_OR_PLATFORM = "TOOL_OR_PLATFORM"
    OTHER = "OTHER"


class SkillSummaryWarningCode(str, enum.Enum):
    UNRESOLVED_SKILL_CANDIDATE = "UNRESOLVED_SKILL_CANDIDATE"
    AMBIGUOUS_SKILL_ALIAS = "AMBIGUOUS_SKILL_ALIAS"
    SUMMARY_NOT_FOUND = "SUMMARY_NOT_FOUND"
    SUMMARY_STRUCTURALLY_AMBIGUOUS = "SUMMARY_STRUCTURALLY_AMBIGUOUS"
    HEADLINE_NOT_FOUND = "HEADLINE_NOT_FOUND"
    HEADLINE_AMBIGUOUS = "HEADLINE_AMBIGUOUS"


class SkillSummaryWarning(StrictSchema):
    code: SkillSummaryWarningCode
    field_name: Optional[str] = None
    candidate_count: Optional[int] = None
    evidence_id: Optional[str] = None


class ExtractedSkillItem(StrictSchema):
    canonical_value: str
    raw_values: List[str] = Field(default_factory=list)
    resolution_status: SkillResolutionStatus
    category: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_band: ConfidenceBand = ConfidenceBand.UNKNOWN
    evidence_ids: List[str] = Field(default_factory=list)
    extraction_method: Optional[str] = None
    review_required: bool = False
    sections: List[str] = Field(default_factory=list)


class ProfessionalSummaryResult(StrictSchema):
    summary_field: ExtractedField
    raw_text: Optional[str] = None
    normalized_text: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class ProfessionalHeadlineResult(StrictSchema):
    headline_field: ExtractedField
    raw_text: Optional[str] = None
    normalized_text: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class SkillAndSummaryExtractionResult(StrictSchema):
    parser_version: str
    catalog_version: str
    skills: List[ExtractedSkillItem] = Field(default_factory=list)
    summary: Optional[ProfessionalSummaryResult] = None
    headline: Optional[ProfessionalHeadlineResult] = None
    evidence_spans: List[EvidenceSpan] = Field(default_factory=list)
    warnings: List[SkillSummaryWarning] = Field(default_factory=list)
