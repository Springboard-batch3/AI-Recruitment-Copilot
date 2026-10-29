from decimal import Decimal
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.models.extraction import BlockType, ExtractionMethod


class ExtractionBlockData(BaseModel):
    block_id: Optional[UUID] = None
    sequence_index: int = Field(ge=0)
    page_number: Optional[int] = Field(default=None, ge=1)
    structural_location: Optional[str] = Field(default=None, max_length=255)
    block_type: BlockType
    raw_text: str
    normalized_text: str
    extraction_method: ExtractionMethod
    confidence: Optional[Decimal] = Field(default=None, ge=0.0, le=1.0)
    review_required: bool = False

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    @field_validator("structural_location")
    @classmethod
    def validate_structural_location_non_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("structural_location cannot be empty or whitespace only when provided.")
        return v

    @model_validator(mode="after")
    def validate_block_rules(self) -> "ExtractionBlockData":
        # Rule 5: page_number or structural_location must be present
        if self.page_number is None and (self.structural_location is None or not self.structural_location.strip()):
            raise ValueError("At least one usable source locator (page_number or structural_location) must be present.")

        # Rule 7: raw_text and normalized_text are not both blank
        raw_blank = not self.raw_text or not self.raw_text.strip()
        norm_blank = not self.normalized_text or not self.normalized_text.strip()
        if raw_blank and norm_blank:
            raise ValueError("raw_text and normalized_text cannot both be blank.")

        return self


class ExtractionResult(BaseModel):
    extractor_version: str = Field(min_length=1, max_length=100)
    review_required: bool = False
    blocks: List[ExtractionBlockData]

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    @field_validator("extractor_version")
    @classmethod
    def validate_extractor_version_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("extractor_version cannot be blank or whitespace only.")
        return v

    @model_validator(mode="after")
    def validate_result_rules(self) -> "ExtractionResult":
        # Rule 2: blocks are not empty
        if not self.blocks:
            raise ValueError("ExtractionResult blocks list cannot be empty.")

        # Rule 3 & 4: sequence indexes are unique, ordered, and contiguous from zero
        sequence_indexes = [b.sequence_index for b in self.blocks]
        expected_indexes = list(range(len(self.blocks)))

        if sequence_indexes != expected_indexes:
            raise ValueError(
                "Sequence indexes must be unique, ordered, and contiguous starting from 0 (e.g. 0, 1, 2, ...)."
            )

        return self
