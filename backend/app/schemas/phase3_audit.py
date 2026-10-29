from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.matching import FrozenSchema, verify_no_prohibited_keys
from app.schemas.phase3_enums import AuditRetentionClass

AUDIT_CONTRACT_SCHEMA_VERSION: str = "1.0.0"


class Phase3AuditEvent(FrozenSchema):
    """Immutable audit event contract for Phase 3 operations."""
    event_id: UUID
    actor_user_id: Optional[UUID] = None
    organization_id: Optional[UUID] = None
    event_type: str = Field(min_length=3, max_length=100)
    resource_type: str = Field(min_length=3, max_length=100)
    resource_id: str = Field(min_length=1, max_length=255)
    resource_version: Optional[int] = Field(default=None, ge=1)
    schema_version: str = Field(default=AUDIT_CONTRACT_SCHEMA_VERSION)
    policy_id: Optional[str] = None
    policy_version: Optional[str] = None
    policy_hash: Optional[str] = None
    retention_class: AuditRetentionClass = AuditRetentionClass.OPERATIONAL_TRACE
    result_code: str = Field(default="SUCCESS", max_length=50)
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_audit_invariants(self) -> "Phase3AuditEvent":
        # Check no prohibited keys in safe_metadata or full audit payload
        verify_no_prohibited_keys(self.model_dump())
        # Forbid dangerous keys in safe_metadata
        for forbidden in ["api_key", "authorization", "bearer", "password", "token", "prompt", "raw_response", "candidate_answer"]:
            if forbidden in self.safe_metadata:
                raise ValueError(f"Prohibited sensitive key '{forbidden}' found in audit safe_metadata.")
        return self
