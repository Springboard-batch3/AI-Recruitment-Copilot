from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.schemas.candidate import (
    CertificationRecord,
    EducationRecord,
    ExperienceRecord,
    ProjectRecord,
    StrictSchema,
)
from app.schemas.profile_assembly import CandidateProfileResponse


class ProfileProvenance(str, Enum):
    PARSER_EXTRACTED = "PARSER_EXTRACTED"
    HUMAN_CORRECTED = "HUMAN_CORRECTED"
    LOCAL_ATS_DEMO = "Local ATS Demo"
    ATS_IMPORTED = "ATS_IMPORTED"


class CorrectionReasonCategory(str, Enum):
    INCORRECT_EXTRACTION = "INCORRECT_EXTRACTION"
    MISSING_INFORMATION = "MISSING_INFORMATION"
    WRONG_RECORD_ASSOCIATION = "WRONG_RECORD_ASSOCIATION"
    DUPLICATE_RECORD = "DUPLICATE_RECORD"
    INCORRECT_NORMALIZATION = "INCORRECT_NORMALIZATION"
    INCORRECT_DATE = "INCORRECT_DATE"
    OTHER = "OTHER"


class CorrectionOperationType(str, Enum):
    REPLACE_SCALAR = "REPLACE_SCALAR"
    ADD_SET_ITEM = "ADD_SET_ITEM"
    REMOVE_SET_ITEM = "REMOVE_SET_ITEM"
    UPDATE_RECORD = "UPDATE_RECORD"
    ADD_RECORD = "ADD_RECORD"
    REMOVE_RECORD = "REMOVE_RECORD"


class EvidenceProvenanceType(str, Enum):
    HUMAN_CORRECTED_WITH_EVIDENCE = "HUMAN_CORRECTED_WITH_EVIDENCE"
    HUMAN_PROVIDED = "HUMAN_PROVIDED"


class CorrectionOperation(StrictSchema):
    operation_type: CorrectionOperationType
    target_section: str = Field(min_length=1, max_length=50)  # e.g., "identity.full_name", "skills", "employment"
    target_index: Optional[int] = Field(default=None, ge=0)
    value: Optional[str] = Field(default=None, max_length=5000)
    record_payload: Optional[Dict[str, Any]] = None
    selected_evidence_ids: List[str] = Field(default_factory=list)
    provenance_type: EvidenceProvenanceType = EvidenceProvenanceType.HUMAN_PROVIDED

    @model_validator(mode="after")
    def validate_operation(self) -> "CorrectionOperation":
        op = self.operation_type
        if op == CorrectionOperationType.REPLACE_SCALAR:
            # value can be string or empty (for clearing), record_payload must be None
            if self.record_payload is not None:
                raise ValueError("record_payload must be None for REPLACE_SCALAR operation.")
        elif op in (CorrectionOperationType.ADD_SET_ITEM, CorrectionOperationType.REMOVE_SET_ITEM):
            if not self.value or not self.value.strip():
                raise ValueError("value is required and non-empty for set item operations.")
        elif op == CorrectionOperationType.UPDATE_RECORD:
            if self.target_index is None:
                raise ValueError("target_index is required for UPDATE_RECORD operation.")
            if self.record_payload is None:
                raise ValueError("record_payload is required for UPDATE_RECORD operation.")
        elif op == CorrectionOperationType.ADD_RECORD:
            if self.record_payload is None:
                raise ValueError("record_payload is required for ADD_RECORD operation.")
        elif op == CorrectionOperationType.REMOVE_RECORD:
            if self.target_index is None:
                raise ValueError("target_index is required for REMOVE_RECORD operation.")

        if self.provenance_type == EvidenceProvenanceType.HUMAN_CORRECTED_WITH_EVIDENCE:
            if len(self.selected_evidence_ids) == 0:
                raise ValueError("selected_evidence_ids cannot be empty when provenance_type is HUMAN_CORRECTED_WITH_EVIDENCE.")
        elif self.provenance_type == EvidenceProvenanceType.HUMAN_PROVIDED:
            if len(self.selected_evidence_ids) > 0:
                raise ValueError("selected_evidence_ids must be empty when provenance_type is HUMAN_PROVIDED.")

        return self


class ProfileCorrectionRequest(StrictSchema):
    base_profile_id: UUID
    base_profile_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=255)
    reason_category: CorrectionReasonCategory
    reviewer_note: Optional[str] = Field(default=None, max_length=500)
    operations: List[CorrectionOperation] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_reason(self) -> "ProfileCorrectionRequest":
        if self.reason_category == CorrectionReasonCategory.OTHER:
            if not self.reviewer_note or not self.reviewer_note.strip():
                raise ValueError("reviewer_note is required when reason_category is OTHER.")
        return self


class ProfileCorrectionResponse(StrictSchema):
    correction_id: str
    previous_profile_id: str
    previous_profile_version: int
    new_profile_id: str
    new_profile_version: int
    operation_count: int
    provenance: ProfileProvenance
    created_at: str
    profile: CandidateProfileResponse


class ProfileSummaryResponse(StrictSchema):
    profile_id: str
    profile_version: int
    schema_version: str
    parser_pipeline_version: str
    provenance: ProfileProvenance
    review_required: bool
    created_at: str
    parent_profile_id: Optional[str] = None
    parent_profile_version: Optional[int] = None
    correction_summary: Optional[Dict[str, Any]] = None
