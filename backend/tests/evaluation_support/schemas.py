from enum import Enum
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictEvaluationSchema(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )


class CorpusSplit(str, Enum):
    DEVELOPMENT = "development"
    HELD_OUT = "held_out"


class DocumentType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"


class LayoutFamily(str, Enum):
    SINGLE_COLUMN = "single_column"
    TWO_COLUMN_SIDEBAR = "two_column_sidebar"
    MULTI_PAGE = "multi_page"
    TABLE_STRUCTURED = "table_structured"
    STANDARD_PARAGRAPHS = "standard_paragraphs"


class CoverageTag(str, Enum):
    SINGLE_COLUMN_PDF = "single_column_pdf"
    TWO_COLUMN_PDF = "two_column_pdf"
    MULTI_PAGE_PDF = "multi_page_pdf"
    DOCX_PARAGRAPHS = "docx_paragraphs"
    DOCX_TABLES = "docx_tables"
    REORDERED_SECTIONS = "reordered_sections"
    MISSING_OPTIONAL_SECTIONS = "missing_optional_sections"
    AMBIGUOUS_VALID_RECORDS = "ambiguous_valid_records"
    YEAR_ONLY_DATES = "year_only_dates"
    MONTH_YEAR_DATES = "month_year_dates"
    MULTIPLE_EMPLOYMENT = "multiple_employment"
    MULTIPLE_EDUCATION = "multiple_education"
    MULTIPLE_PROJECTS = "multiple_projects"
    MULTIPLE_CERTIFICATIONS = "multiple_certifications"
    MULTIPLE_LANGUAGES = "multiple_languages"


class ManifestEntry(StrictEvaluationSchema):
    fixture_id: str = Field(min_length=3, pattern=r"^[A-Z0-9_-]+$")
    relative_path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    document_type: DocumentType
    digital_text_required: bool = True
    layout_family: LayoutFamily
    page_count: Optional[int] = Field(default=None, ge=1)
    split: CorpusSplit
    ground_truth_relative_path: str = Field(min_length=1)
    synthetic_provenance: str = Field(min_length=5)
    coverage_tags: List[CoverageTag] = Field(min_length=1)
    fixture_version: str = Field(min_length=1)


class CorpusManifest(StrictEvaluationSchema):
    manifest_version: str = Field(min_length=1)
    description: str = Field(min_length=5)
    fixtures: List[ManifestEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest_integrity(self) -> "CorpusManifest":
        ids = set()
        paths = set()
        gt_paths = set()
        for f in self.fixtures:
            if f.fixture_id in ids:
                raise ValueError(f"Duplicate fixture_id in manifest: {f.fixture_id}")
            ids.add(f.fixture_id)

            if f.relative_path in paths:
                raise ValueError(f"Duplicate relative_path in manifest: {f.relative_path}")
            paths.add(f.relative_path)

            if f.ground_truth_relative_path in gt_paths:
                raise ValueError(f"Duplicate ground_truth_relative_path in manifest: {f.ground_truth_relative_path}")
            gt_paths.add(f.ground_truth_relative_path)

        return self


# =========================================================================
# Ground-Truth Contract Schemas
# =========================================================================


class EvidenceRequirement(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    INTENTIONALLY_ABSENT = "INTENTIONALLY_ABSENT"


class ExpectedFieldAssertion(StrictEvaluationSchema):
    value: Optional[str] = None
    normalized_value: Optional[str] = None
    requirement: EvidenceRequirement = EvidenceRequirement.REQUIRED
    evidence_keys: List[str] = Field(default_factory=list)


class ExpectedEmploymentRecord(StrictEvaluationSchema):
    record_id: Optional[str] = None
    job_title: ExpectedFieldAssertion
    organization: ExpectedFieldAssertion
    start_date: ExpectedFieldAssertion
    end_date: ExpectedFieldAssertion
    is_current: bool = False
    descriptions: List[ExpectedFieldAssertion] = Field(default_factory=list)


class ExpectedEducationRecord(StrictEvaluationSchema):
    record_id: Optional[str] = None
    institution: ExpectedFieldAssertion
    degree: ExpectedFieldAssertion
    field_of_study: Optional[ExpectedFieldAssertion] = None
    start_date: Optional[ExpectedFieldAssertion] = None
    end_date: Optional[ExpectedFieldAssertion] = None
    grade: Optional[ExpectedFieldAssertion] = None


class ExpectedProjectRecord(StrictEvaluationSchema):
    record_id: Optional[str] = None
    name: ExpectedFieldAssertion
    description: Optional[ExpectedFieldAssertion] = None
    technologies: List[ExpectedFieldAssertion] = Field(default_factory=list)


class ExpectedCertificationRecord(StrictEvaluationSchema):
    record_id: Optional[str] = None
    name: ExpectedFieldAssertion
    issuer: ExpectedFieldAssertion
    issued_date: Optional[ExpectedFieldAssertion] = None
    expiry_date: Optional[ExpectedFieldAssertion] = None
    credential_id: Optional[ExpectedFieldAssertion] = None


class ExpectedLanguageRecord(StrictEvaluationSchema):
    record_id: Optional[str] = None
    language: ExpectedFieldAssertion
    proficiency: Optional[ExpectedFieldAssertion] = None


class ExpectedEvidenceExpectation(StrictEvaluationSchema):
    evidence_key: str = Field(min_length=1)
    source_phrase: Optional[str] = None
    target_field_or_record: Optional[str] = None
    requirement: Optional[EvidenceRequirement] = None
    expected_page: Optional[int] = None
    text_snippet: Optional[str] = None
    description: Optional[str] = None


class GroundTruthIdentity(StrictEvaluationSchema):
    full_name: ExpectedFieldAssertion
    emails: List[ExpectedFieldAssertion] = Field(default_factory=list)
    phone_numbers: List[ExpectedFieldAssertion] = Field(default_factory=list)
    location: Optional[ExpectedFieldAssertion] = None
    links: List[ExpectedFieldAssertion] = Field(default_factory=list)
    portfolio: Optional[ExpectedFieldAssertion] = None


class GroundTruthProfessionalProfile(StrictEvaluationSchema):
    headline: Optional[ExpectedFieldAssertion] = None
    summary: Optional[ExpectedFieldAssertion] = None
    skills: List[ExpectedFieldAssertion] = Field(default_factory=list)
    employment: List[ExpectedEmploymentRecord] = Field(default_factory=list)
    education: List[ExpectedEducationRecord] = Field(default_factory=list)
    projects: List[ExpectedProjectRecord] = Field(default_factory=list)
    certifications: List[ExpectedCertificationRecord] = Field(default_factory=list)
    languages: List[ExpectedLanguageRecord] = Field(default_factory=list)


class GroundTruthDocument(StrictEvaluationSchema):
    fixture_id: str = Field(min_length=3)
    ground_truth_version: Optional[str] = None
    schema_version: Optional[str] = None
    split: CorpusSplit
    identity: GroundTruthIdentity
    professional_profile: GroundTruthProfessionalProfile
    evidence_expectations: List[ExpectedEvidenceExpectation] = Field(default_factory=list)
