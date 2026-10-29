import enum
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from app.models.job import (
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
)
from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys


class MatchResultState(str, enum.Enum):
    MATCHED = "MATCHED"
    PARTIALLY_MATCHED = "PARTIALLY_MATCHED"
    NOT_EVIDENCED = "NOT_EVIDENCED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class MatchComparisonMethod(str, enum.Enum):
    CANONICAL_TAXONOMY_ID = "CANONICAL_TAXONOMY_ID"
    EXACT_NORMALIZED_LABEL = "EXACT_NORMALIZED_LABEL"
    RECRUITER_APPROVED_ALIAS = "RECRUITER_APPROVED_ALIAS"
    TAXONOMY_ALIAS = "TAXONOMY_ALIAS"
    EXPERIENCE_INTERVAL_CALCULATION = "EXPERIENCE_INTERVAL_CALCULATION"
    EDUCATION_DEGREE_AND_FIELD = "EDUCATION_DEGREE_AND_FIELD"
    CERTIFICATION_VERIFICATION = "CERTIFICATION_VERIFICATION"
    LANGUAGE_PROFICIENCY = "LANGUAGE_PROFICIENCY"
    DOCUMENTED_RESPONSIBILITY = "DOCUMENTED_RESPONSIBILITY"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RequirementMatch(FrozenSchema):
    requirement_id: UUID
    requirement_name: str = Field(default="Requirement", min_length=1)
    display_order: int = Field(ge=0)
    category: RequirementCategory
    result_state: MatchResultState
    match_value: float = Field(ge=0.0, le=1.0)
    comparison_method: MatchComparisonMethod
    expected_value: Optional[str] = None
    observed_value: Optional[str] = None
    evidence_references: List[str] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)
    evidence_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_recommended: bool = False
    matching_rule_version: str
    necessity: RequirementNecessity
    priority: RequirementPriority
    approved_weight: Optional[float] = Field(default=None, ge=0.0, le=10.0)

    @model_validator(mode="after")
    def validate_match_integrity(self) -> "RequirementMatch":
        # 1. NOT_EVIDENCED must not carry credited evidence or non-zero match value
        if self.result_state == MatchResultState.NOT_EVIDENCED:
            if self.match_value > 0.0:
                raise ValueError("NOT_EVIDENCED state must have match_value == 0.0.")
            if len(self.evidence_references) > 0:
                raise ValueError("NOT_EVIDENCED state must not contain credited evidence references.")

        # 2. NOT_APPLICABLE must not contribute credit
        if self.result_state == MatchResultState.NOT_APPLICABLE:
            if self.match_value > 0.0:
                raise ValueError("NOT_APPLICABLE state must have match_value == 0.0.")

        # 3. Credited results must contain qualifying evidence
        if self.match_value > 0.0 or self.result_state in {
            MatchResultState.MATCHED,
            MatchResultState.PARTIALLY_MATCHED,
        }:
            if len(self.evidence_references) == 0:
                raise ValueError(
                    f"Result state '{self.result_state}' with match_value {self.match_value} must contain qualifying evidence references."
                )

        # 4. Evidence references must be deterministically sorted
        if self.evidence_references != sorted(self.evidence_references):
            raise ValueError("evidence_references must be deterministically sorted.")

        # 5. Verify no prohibited identity or protected keys exist in serialized view
        verify_no_prohibited_keys(self.model_dump(mode="json"))

        return self


class RequirementMatchResultSet(FrozenSchema):
    match_input_schema_version: str
    matcher_algorithm_version: str
    evaluation_date: datetime
    requisition_id: UUID
    job_version_id: UUID
    revision: int = Field(ge=1)
    profile_id: UUID
    profile_version: int = Field(ge=1)
    matches: List[RequirementMatch]

    @model_validator(mode="after")
    def validate_result_set(self) -> "RequirementMatchResultSet":
        # Ensure order matches display_order strictly
        orders = [m.display_order for m in self.matches]
        if orders != sorted(orders):
            raise ValueError("Requirement matches must follow strict display_order ascending.")
        return self
