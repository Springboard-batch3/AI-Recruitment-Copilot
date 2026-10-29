import uuid
from typing import List, Optional
from pydantic import Field

from app.schemas.candidate import StrictSchema


class OccupationSearchItemResponse(StrictSchema):
    concept_id: uuid.UUID
    onet_soc_code: str
    canonical_title: str
    matched_alias: Optional[str] = None
    source: str
    source_version: str
    entity_type: str = "occupation"
    dataset_version: str = "31.0"


class SkillSearchItemResponse(StrictSchema):
    concept_id: uuid.UUID
    canonical_label: str
    matched_alias: Optional[str] = None
    category: Optional[str] = None
    source: str
    source_family: str = "software"
    source_version: Optional[str] = None
    external_id: Optional[str] = None
    entity_type: str = "skill"


class OccupationSkillSuggestionResponse(StrictSchema):
    concept_id: uuid.UUID
    canonical_label: str
    category: Optional[str] = None
    source: str
    source_family: str
    external_id: Optional[str] = None
    hot_technology: bool = False
    in_demand: bool = False
    importance_value: Optional[float] = None
    level_value: Optional[float] = None
    recommend_suppress: bool = False
    not_relevant: bool = False
