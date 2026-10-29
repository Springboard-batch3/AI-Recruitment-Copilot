"""update_security_audit_events_candidate_access_enums

Revision ID: f4a5b6c7d8e9
Revises: f3a4b5c6d7e8
Create Date: 2026-09-22 17:48:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_EVENT_TYPES = (
    "'LOGIN_ATTEMPT', 'LOGOUT', 'SESSION_REVOKED', 'PASSWORD_CHANGED', "
    "'USER_BOOTSTRAPPED', 'ACCOUNT_LOCKED', 'ACCOUNT_UNLOCKED', "
    "'PROVIDER_PROFILE_CREATED', 'PROVIDER_PROFILE_UPDATED', "
    "'PROVIDER_PROFILE_ACTIVATED', 'PROVIDER_PROFILE_DISABLED', "
    "'PROVIDER_CONFIG_REJECTED', 'PROVIDER_COMPATIBILITY_TESTED', 'ACCESS_DENIED', "
    "'CANDIDATE_INVITATION_CREATED', 'CANDIDATE_INVITATION_REVOKED', "
    "'CANDIDATE_ACTIVATED', 'CANDIDATE_BINDING_UPDATED'"
)

OLD_EVENT_TYPES = (
    "'LOGIN_ATTEMPT', 'LOGOUT', 'SESSION_REVOKED', 'PASSWORD_CHANGED', "
    "'USER_BOOTSTRAPPED', 'ACCOUNT_LOCKED', 'ACCOUNT_UNLOCKED', "
    "'PROVIDER_PROFILE_CREATED', 'PROVIDER_PROFILE_UPDATED', "
    "'PROVIDER_PROFILE_ACTIVATED', 'PROVIDER_PROFILE_DISABLED', "
    "'PROVIDER_CONFIG_REJECTED', 'PROVIDER_COMPATIBILITY_TESTED', 'ACCESS_DENIED'"
)

NEW_REASON_CODES = (
    "'CREDENTIALS_INVALID', 'ACCOUNT_LOCKED', 'SESSION_EXPIRED', 'UNAUTHORIZED', "
    "'SYSTEM_BOOTSTRAP', 'SUCCESSFUL_AUTHENTICATION', 'PASSWORD_CHANGE_REQUIRED', "
    "'PROVIDER_CONFIGURED', 'PROVIDER_UPDATED', 'PROVIDER_ACTIVATED', "
    "'PROVIDER_DISABLED', 'PROVIDER_INVALID_CONFIG', 'PROVIDER_INCOMPATIBLE', "
    "'PROVIDER_COMPATIBILITY_SUCCESS', 'PROVIDER_COMPATIBILITY_FAILED', "
    "'ACCESS_DENIED', 'STALE_VERSION', 'MASS_ASSIGNMENT_ATTEMPT', "
    "'INVITATION_EXPIRED', 'INVITATION_REVOKED', 'INVITATION_CONSUMED', "
    "'INVITATION_INVALID', 'BINDING_SUSPENDED', 'INVITATION_CREATED', "
    "'INVITATION_REVOKED_BY_RECRUITER', 'CANDIDATE_ACCOUNT_ACTIVATED'"
)

OLD_REASON_CODES = (
    "'CREDENTIALS_INVALID', 'ACCOUNT_LOCKED', 'SESSION_EXPIRED', 'UNAUTHORIZED', "
    "'SYSTEM_BOOTSTRAP', 'SUCCESSFUL_AUTHENTICATION', 'PASSWORD_CHANGE_REQUIRED', "
    "'PROVIDER_CONFIGURED', 'PROVIDER_UPDATED', 'PROVIDER_ACTIVATED', "
    "'PROVIDER_DISABLED', 'PROVIDER_INVALID_CONFIG', 'PROVIDER_INCOMPATIBLE', "
    "'PROVIDER_COMPATIBILITY_SUCCESS', 'PROVIDER_COMPATIBILITY_FAILED', "
    "'ACCESS_DENIED', 'STALE_VERSION', 'MASS_ASSIGNMENT_ATTEMPT'"
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.engine.name == "postgresql":
        # Drop existing check constraints on security_audit_events
        op.drop_constraint('ck_security_audit_events_event_type', 'security_audit_events', type_='check')
        op.drop_constraint('ck_security_audit_events_reason_code', 'security_audit_events', type_='check')

        # Re-create check constraints with full set of candidate access audit enum values
        op.create_check_constraint(
            'ck_security_audit_events_event_type',
            'security_audit_events',
            f"event_type IN ({NEW_EVENT_TYPES})"
        )
        op.create_check_constraint(
            'ck_security_audit_events_reason_code',
            'security_audit_events',
            f"reason_code IN ({NEW_REASON_CODES})"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.engine.name == "postgresql":
        op.drop_constraint('ck_security_audit_events_event_type', 'security_audit_events', type_='check')
        op.drop_constraint('ck_security_audit_events_reason_code', 'security_audit_events', type_='check')

        op.create_check_constraint(
            'ck_security_audit_events_event_type',
            'security_audit_events',
            f"event_type IN ({OLD_EVENT_TYPES})"
        )
        op.create_check_constraint(
            'ck_security_audit_events_reason_code',
            'security_audit_events',
            f"reason_code IN ({OLD_REASON_CODES})"
        )
