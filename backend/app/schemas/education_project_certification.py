import enum
from typing import List, Optional
from pydantic import Field

from app.schemas.candidate import (
    ConfidenceBand,
    EvidenceSpan,
    ExtractedField,
    ReviewStatus,
    StrictSchema,
)
from app.schemas.employment import ExtractedDateInterval, NormalizedDate


# =============================================================================
# Warning Codes & Models
# =============================================================================

class EducationWarningCode(str, enum.Enum):
    EDUCATION_DEGREE_MISSING = "EDUCATION_DEGREE_MISSING"
    EDUCATION_INSTITUTION_MISSING = "EDUCATION_INSTITUTION_MISSING"
    EDUCATION_RECORD_AMBIGUOUS = "EDUCATION_RECORD_AMBIGUOUS"
    EDUCATION_DATE_AMBIGUOUS = "EDUCATION_DATE_AMBIGUOUS"
    INVALID_EVIDENCE_SPAN = "INVALID_EVIDENCE_SPAN"


class EducationWarning(StrictSchema):
    code: EducationWarningCode
    record_index: Optional[int] = None
    field_name: Optional[str] = None
    candidate_count: Optional[int] = None
    evidence_id: Optional[str] = None


class ProjectWarningCode(str, enum.Enum):
    PROJECT_NAME_MISSING = "PROJECT_NAME_MISSING"
    PROJECT_BOUNDARY_AMBIGUOUS = "PROJECT_BOUNDARY_AMBIGUOUS"
    PROJECT_TECHNOLOGY_ASSOCIATION_AMBIGUOUS = "PROJECT_TECHNOLOGY_ASSOCIATION_AMBIGUOUS"
    INVALID_EVIDENCE_SPAN = "INVALID_EVIDENCE_SPAN"


class ProjectWarning(StrictSchema):
    code: ProjectWarningCode
    record_index: Optional[int] = None
    field_name: Optional[str] = None
    candidate_count: Optional[int] = None
    evidence_id: Optional[str] = None


class CertificationWarningCode(str, enum.Enum):
    CERTIFICATION_NAME_MISSING = "CERTIFICATION_NAME_MISSING"
    CERTIFICATION_ISSUER_AMBIGUOUS = "CERTIFICATION_ISSUER_AMBIGUOUS"
    CERTIFICATION_DATE_AMBIGUOUS = "CERTIFICATION_DATE_AMBIGUOUS"
    CERTIFICATION_CREDENTIAL_INCOMPLETE = "CERTIFICATION_CREDENTIAL_INCOMPLETE"
    INVALID_EVIDENCE_SPAN = "INVALID_EVIDENCE_SPAN"


class CertificationWarning(StrictSchema):
    code: CertificationWarningCode
    record_index: Optional[int] = None
    field_name: Optional[str] = None
    candidate_count: Optional[int] = None
    evidence_id: Optional[str] = None


class LanguageWarningCode(str, enum.Enum):
    LANGUAGE_PROFICIENCY_UNKNOWN = "LANGUAGE_PROFICIENCY_UNKNOWN"
    LANGUAGE_ENTRY_AMBIGUOUS = "LANGUAGE_ENTRY_AMBIGUOUS"
    NON_NATURAL_LANGUAGE_REJECTED = "NON_NATURAL_LANGUAGE_REJECTED"
    INVALID_EVIDENCE_SPAN = "INVALID_EVIDENCE_SPAN"


class LanguageWarning(StrictSchema):
    code: LanguageWarningCode
    record_index: Optional[int] = None
    field_name: Optional[str] = None
    candidate_count: Optional[int] = None
    evidence_id: Optional[str] = None


# =============================================================================
# Extraction Record Schemas
# =============================================================================

class DegreeLevel(str, enum.Enum):
    BACHELORS = "BACHELORS"
    MASTERS = "MASTERS"
    DOCTORATE = "DOCTORATE"
    DIPLOMA = "DIPLOMA"
    ASSOCIATE = "ASSOCIATE"
    CERTIFICATE = "CERTIFICATE"
    OTHER = "OTHER"


class ExtractedEducationRecord(StrictSchema):
    degree: ExtractedField
    field_of_study: Optional[ExtractedField] = None
    normalized_degree_level: Optional[DegreeLevel] = None
    institution: ExtractedField
    start_date: ExtractedField
    end_date: ExtractedField
    grade: ExtractedField
    date_interval: Optional[ExtractedDateInterval] = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: List[str] = Field(default_factory=list)
    warnings: List[EducationWarning] = Field(default_factory=list)


class ExtractedProjectRecord(StrictSchema):
    name: ExtractedField
    description: ExtractedField
    technologies: List[ExtractedField] = Field(default_factory=list)
    project_url: ExtractedField
    start_date: Optional[ExtractedField] = None
    end_date: Optional[ExtractedField] = None
    date_interval: Optional[ExtractedDateInterval] = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: List[str] = Field(default_factory=list)
    warnings: List[ProjectWarning] = Field(default_factory=list)


class ExtractedCertificationRecord(StrictSchema):
    name: ExtractedField
    issuer: ExtractedField
    issued_date: ExtractedField
    expiry_date: Optional[ExtractedField] = None
    credential_id: Optional[ExtractedField] = None
    credential_url: ExtractedField
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: List[str] = Field(default_factory=list)
    warnings: List[CertificationWarning] = Field(default_factory=list)


class ExtractedLanguageRecord(StrictSchema):
    language: ExtractedField
    proficiency: Optional[ExtractedField] = None
    normalized_proficiency: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: List[str] = Field(default_factory=list)
    warnings: List[LanguageWarning] = Field(default_factory=list)


# =============================================================================
# Extraction Result Schemas
# =============================================================================

class EducationExtractionResult(StrictSchema):
    parser_version: str
    records: List[ExtractedEducationRecord] = Field(default_factory=list)
    evidence_spans: List[EvidenceSpan] = Field(default_factory=list)
    warnings: List[EducationWarning] = Field(default_factory=list)


class ProjectExtractionResult(StrictSchema):
    parser_version: str
    records: List[ExtractedProjectRecord] = Field(default_factory=list)
    evidence_spans: List[EvidenceSpan] = Field(default_factory=list)
    warnings: List[ProjectWarning] = Field(default_factory=list)


class CertificationExtractionResult(StrictSchema):
    parser_version: str
    records: List[ExtractedCertificationRecord] = Field(default_factory=list)
    evidence_spans: List[EvidenceSpan] = Field(default_factory=list)
    warnings: List[CertificationWarning] = Field(default_factory=list)


class LanguageExtractionResult(StrictSchema):
    parser_version: str
    languages: List[ExtractedLanguageRecord] = Field(default_factory=list)
    evidence_spans: List[EvidenceSpan] = Field(default_factory=list)
    warnings: List[LanguageWarning] = Field(default_factory=list)


class Phase1D4ExtractionResult(StrictSchema):
    parser_version: str
    rules_version: str
    education: EducationExtractionResult
    projects: ProjectExtractionResult
    certifications: CertificationExtractionResult
    languages: LanguageExtractionResult
    evidence_spans: List[EvidenceSpan] = Field(default_factory=list)
