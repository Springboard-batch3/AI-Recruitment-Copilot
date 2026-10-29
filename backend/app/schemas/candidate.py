from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class StrictSchema(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )



class ConfidenceBand(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class ReviewStatus(str, Enum):
    UNREVIEWED = "UNREVIEWED"
    CONFIRMED = "CONFIRMED"
    CORRECTED = "CORRECTED"


class EvidenceSpan(StrictSchema):
    evidence_id: str
    resume_artifact_id: str
    page_number: int | None = Field(default=None, ge=1)
    block_number: int | None = Field(default=None, ge=0)
    character_start: int = Field(ge=0)
    character_end: int
    original_text: str = Field(min_length=1)
    extraction_method: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_location_and_span(self) -> "EvidenceSpan":
        if self.page_number is None and self.block_number is None:
            raise ValueError("At least one of page_number or block_number must be present.")
        if self.character_end <= self.character_start:
            raise ValueError("character_end must be greater than character_start.")
        return self


class ExtractedField(StrictSchema):
    field_name: str
    value: str | None = None
    normalized_value: str | None = None
    category: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_band: ConfidenceBand = ConfidenceBand.UNKNOWN
    evidence_ids: list[str] = Field(default_factory=list)
    extraction_method: str | None = None
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED

    @model_validator(mode="after")
    def validate_field(self) -> "ExtractedField":
        if self.value is None:
            if self.confidence is not None:
                raise ValueError("When value is null, confidence must be null.")
            if len(self.evidence_ids) > 0:
                raise ValueError("When value is null, evidence_ids must be empty.")
            self.confidence_band = ConfidenceBand.UNKNOWN
        else:
            # Human-provided corrections without resume evidence
            is_human_provided = (
                self.extraction_method == "HUMAN_PROVIDED"
                or (self.review_status == ReviewStatus.CORRECTED and len(self.evidence_ids) == 0)
            )
            if is_human_provided:
                if self.confidence is None:
                    self.confidence_band = ConfidenceBand.UNKNOWN
                elif self.confidence >= 0.90:
                    self.confidence_band = ConfidenceBand.HIGH
                elif self.confidence >= 0.70:
                    self.confidence_band = ConfidenceBand.MEDIUM
                else:
                    self.confidence_band = ConfidenceBand.LOW
            else:
                if self.confidence is None:
                    raise ValueError("When value is present, confidence is required.")
                if len(self.evidence_ids) == 0:
                    raise ValueError("When value is present, at least one evidence ID is required.")

                if self.confidence >= 0.90:
                    self.confidence_band = ConfidenceBand.HIGH
                elif self.confidence >= 0.70:
                    self.confidence_band = ConfidenceBand.MEDIUM
                else:
                    self.confidence_band = ConfidenceBand.LOW

        return self


class ExperienceRecord(StrictSchema):
    job_title: ExtractedField
    organization: ExtractedField
    start_date: ExtractedField
    end_date: ExtractedField
    descriptions: list[ExtractedField] = Field(default_factory=list)


class EducationRecord(StrictSchema):
    degree: ExtractedField
    field_of_study: ExtractedField | None = None
    institution: ExtractedField
    start_date: ExtractedField
    end_date: ExtractedField
    grade: ExtractedField


class ProjectRecord(StrictSchema):
    name: ExtractedField
    description: ExtractedField
    technologies: list[ExtractedField] = Field(default_factory=list)
    project_url: ExtractedField


class CertificationRecord(StrictSchema):
    name: ExtractedField
    issuer: ExtractedField
    issued_date: ExtractedField
    expiry_date: ExtractedField | None = None
    credential_id: ExtractedField | None = None
    credential_url: ExtractedField


class CandidateIdentity(StrictSchema):
    resume_artifact_id: str
    full_name: ExtractedField
    emails: list[ExtractedField] = Field(default_factory=list)
    phone_numbers: list[ExtractedField] = Field(default_factory=list)
    linkedin: ExtractedField
    github: ExtractedField
    portfolio: ExtractedField
    location: ExtractedField


class CandidateProfessionalProfile(StrictSchema):
    headline: ExtractedField
    summary: ExtractedField
    skills: list[ExtractedField] = Field(default_factory=list)
    employment: list[ExperienceRecord] = Field(default_factory=list)
    education: list[EducationRecord] = Field(default_factory=list)
    projects: list[ProjectRecord] = Field(default_factory=list)
    certifications: list[CertificationRecord] = Field(default_factory=list)
    languages: list[ExtractedField] = Field(default_factory=list)
    awards: list[ExtractedField] = Field(default_factory=list)
    publications: list[ExtractedField] = Field(default_factory=list)


class CandidateProfileV1(StrictSchema):
    schema_version: Literal["1.0"] = "1.0"
    identity: CandidateIdentity
    professional_profile: CandidateProfessionalProfile
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
