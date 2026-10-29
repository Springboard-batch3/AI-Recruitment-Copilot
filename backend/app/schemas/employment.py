import enum
from typing import List, Optional
from pydantic import Field

from app.schemas.candidate import EvidenceSpan, ExtractedField, StrictSchema


class DatePrecision(str, enum.Enum):
    MONTH = "MONTH"
    YEAR = "YEAR"
    UNKNOWN = "UNKNOWN"


class EmploymentWarningCode(str, enum.Enum):
    EMPLOYMENT_ROLE_MISSING = "EMPLOYMENT_ROLE_MISSING"
    EMPLOYMENT_ORGANIZATION_MISSING = "EMPLOYMENT_ORGANIZATION_MISSING"
    EMPLOYMENT_HEADER_AMBIGUOUS = "EMPLOYMENT_HEADER_AMBIGUOUS"
    EMPLOYMENT_RECORD_BOUNDARY_AMBIGUOUS = "EMPLOYMENT_RECORD_BOUNDARY_AMBIGUOUS"
    EMPLOYMENT_DATE_MISSING = "EMPLOYMENT_DATE_MISSING"
    EMPLOYMENT_DATE_AMBIGUOUS = "EMPLOYMENT_DATE_AMBIGUOUS"
    EMPLOYMENT_DATE_REVERSED = "EMPLOYMENT_DATE_REVERSED"
    EMPLOYMENT_DATE_CONFLICT = "EMPLOYMENT_DATE_CONFLICT"
    EMPLOYMENT_DATE_PRECISION_INSUFFICIENT = "EMPLOYMENT_DATE_PRECISION_INSUFFICIENT"
    CURRENT_ROLE_REQUIRES_AS_OF_DATE = "CURRENT_ROLE_REQUIRES_AS_OF_DATE"


class EmploymentWarning(StrictSchema):
    code: EmploymentWarningCode
    record_index: Optional[int] = None
    field_name: Optional[str] = None
    candidate_count: Optional[int] = None
    evidence_id: Optional[str] = None


class NormalizedDate(StrictSchema):
    raw_text: str
    year: Optional[int] = None
    month: Optional[int] = None  # 1 to 12
    precision: DatePrecision = DatePrecision.UNKNOWN
    is_current: bool = False


class ExtractedDateInterval(StrictSchema):
    raw_text: str
    start_date: Optional[NormalizedDate] = None
    end_date: Optional[NormalizedDate] = None
    is_current: bool = False
    duration_months: Optional[int] = None
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractedEmploymentRecord(StrictSchema):
    job_title: ExtractedField
    organization: ExtractedField
    start_date: ExtractedField
    end_date: ExtractedField
    descriptions: List[ExtractedField] = Field(default_factory=list)
    date_interval: Optional[ExtractedDateInterval] = None
    duration_months: Optional[int] = None
    is_current: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: List[str] = Field(default_factory=list)
    warnings: List[EmploymentWarning] = Field(default_factory=list)


class EmploymentExtractionResult(StrictSchema):
    parser_version: str
    date_rules_version: str
    as_of_date: Optional[str] = None
    records: List[ExtractedEmploymentRecord] = Field(default_factory=list)
    total_experience_months: Optional[int] = None
    evidence_spans: List[EvidenceSpan] = Field(default_factory=list)
    warnings: List[EmploymentWarning] = Field(default_factory=list)
