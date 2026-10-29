import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field, field_validator, model_validator
from app.schemas.candidate import StrictSchema


class CreateCandidateInvitationRequest(StrictSchema):
    candidate_id: Optional[uuid.UUID] = None
    organization_id: Optional[uuid.UUID] = None
    application_id: Optional[uuid.UUID] = None
    purpose: str = Field(default="CANDIDATE_ACCESS", max_length=50)
    valid_hours: int = Field(default=24, ge=1, le=168)


class CandidateAccessInvitationInfo(StrictSchema):
    invitation_id: uuid.UUID
    status: str  # "ACTIVE", "EXPIRED", "CONSUMED", "REVOKED"
    expires_at: datetime
    created_at: datetime


class CandidateAccessStatusResponse(StrictSchema):
    candidate_id: uuid.UUID
    account_status: str  # "NOT_INVITED", "INVITED", "ACTIVE", "SUSPENDED"
    has_active_account: bool
    email: Optional[str] = None
    display_name: Optional[str] = None
    invitation: Optional[CandidateAccessInvitationInfo] = None


class CreateCandidateInvitationResponse(StrictSchema):
    invitation_id: uuid.UUID
    candidate_id: uuid.UUID
    organization_id: uuid.UUID
    raw_token: str
    activation_url: str
    expires_at: datetime
    created_at: datetime


class CandidateInvitationSummary(StrictSchema):
    invitation_id: uuid.UUID
    candidate_id: uuid.UUID
    organization_id: uuid.UUID
    purpose: str
    status: str  # "ACTIVE", "CONSUMED", "EXPIRED", "REVOKED"
    expires_at: datetime
    consumed_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_by_user_id: uuid.UUID
    created_at: datetime


class CandidateActivationRequest(StrictSchema):
    token: str = Field(min_length=16, max_length=256)
    password: str = Field(min_length=8, max_length=128)
    password_confirmation: str = Field(min_length=8, max_length=128)

    @field_validator("token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Activation token cannot be empty.")
        return cleaned

    @model_validator(mode="after")
    def validate_password_confirmation(self) -> "CandidateActivationRequest":
        if self.password != self.password_confirmation:
            raise ValueError("Password and confirmation password do not match.")
        return self


class CandidateRegisterRequest(StrictSchema):
    full_name: str = Field(min_length=1, max_length=255)
    email: str = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Full name cannot be empty.")
        return cleaned

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        from app.services.security import canonicalize_email
        return canonicalize_email(v)


class CandidateActivationResponse(StrictSchema):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    candidate_id: Optional[uuid.UUID] = None
    organization_id: Optional[uuid.UUID] = None
    binding_status: Optional[str] = None
    csrf_token: str


class CandidateLoginRequest(StrictSchema):
    email: str = Field(max_length=255)
    password: str = Field(max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        from app.services.security import canonicalize_email
        return canonicalize_email(v)


class CandidateLoginResponse(StrictSchema):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    candidate_id: Optional[uuid.UUID] = None
    organization_id: Optional[uuid.UUID] = None
    binding_status: Optional[str] = None
    csrf_token: str


class CandidateMeResponse(StrictSchema):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    candidate_id: Optional[uuid.UUID] = None
    organization_id: Optional[uuid.UUID] = None
    binding_status: Optional[str] = None
    idle_expires_at: datetime
    absolute_expires_at: datetime
    csrf_token: str

