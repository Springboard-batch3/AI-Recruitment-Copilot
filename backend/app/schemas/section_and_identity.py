import enum
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.candidate import CandidateIdentity, EvidenceSpan, StrictSchema


class SectionType(str, enum.Enum):
    HEADER = "HEADER"
    SUMMARY = "SUMMARY"
    SKILLS = "SKILLS"
    EXPERIENCE = "EXPERIENCE"
    EDUCATION = "EDUCATION"
    PROJECTS = "PROJECTS"
    CERTIFICATIONS = "CERTIFICATIONS"
    LANGUAGES = "LANGUAGES"
    OTHER = "OTHER"


class SectionHeadingMatch(StrictSchema):
    matched_heading_text: str
    normalized_heading_text: str
    section_type: SectionType
    heading_block_id: Optional[UUID] = None
    heading_sequence_index: int
    confidence: float = Field(ge=0.0, le=1.0)


class DetectedSection(StrictSchema):
    section_type: SectionType
    start_sequence_index: int
    end_sequence_index: int  # Inclusive
    block_ids: List[UUID] = Field(default_factory=list)
    heading: Optional[SectionHeadingMatch] = None


class SectionDetectionResult(StrictSchema):
    parser_version: str
    sections: List[DetectedSection] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class IdentityExtractionWarningCode(str, enum.Enum):
    NAME_NOT_FOUND = "NAME_NOT_FOUND"
    MULTIPLE_NAME_CANDIDATES = "MULTIPLE_NAME_CANDIDATES"
    EMAIL_NOT_FOUND = "EMAIL_NOT_FOUND"
    MULTIPLE_EMAIL_CANDIDATES = "MULTIPLE_EMAIL_CANDIDATES"
    PHONE_NOT_FOUND = "PHONE_NOT_FOUND"
    MULTIPLE_PHONE_CANDIDATES = "MULTIPLE_PHONE_CANDIDATES"
    INVALID_EVIDENCE_SPAN = "INVALID_EVIDENCE_SPAN"
    SECTION_HEADING_AMBIGUOUS = "SECTION_HEADING_AMBIGUOUS"


class IdentityExtractionWarning(StrictSchema):
    code: IdentityExtractionWarningCode
    field_name: Optional[str] = None
    candidate_count: Optional[int] = None


class IdentityExtractionResult(StrictSchema):
    parser_version: str
    identity: CandidateIdentity
    evidence_spans: List[EvidenceSpan] = Field(default_factory=list)
    warnings: List[IdentityExtractionWarning] = Field(default_factory=list)
