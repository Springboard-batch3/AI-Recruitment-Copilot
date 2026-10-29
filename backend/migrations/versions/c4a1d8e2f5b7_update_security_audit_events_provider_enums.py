"""update_security_audit_events_provider_enums

Revision ID: c4a1d8e2f5b7
Revises: b3b1c0a2d4e6
Create Date: 2026-09-16 21:27:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4a1d8e2f5b7'
down_revision: Union[str, None] = 'b3b1c0a2d4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_EVENT_TYPES = (
    "'LOGIN_ATTEMPT', 'LOGOUT', 'SESSION_REVOKED', 'PASSWORD_CHANGED', "
    "'USER_BOOTSTRAPPED', 'ACCOUNT_LOCKED', 'ACCOUNT_UNLOCKED', "
    "'PROVIDER_PROFILE_CREATED', 'PROVIDER_PROFILE_UPDATED', "
    "'PROVIDER_PROFILE_ACTIVATED', 'PROVIDER_PROFILE_DISABLED', "
    "'PROVIDER_CONFIG_REJECTED', 'PROVIDER_COMPATIBILITY_TESTED', 'ACCESS_DENIED'"
)

OLD_EVENT_TYPES = (
    "'LOGIN_ATTEMPT', 'LOGOUT', 'SESSION_REVOKED', 'PASSWORD_CHANGED', "
    "'USER_BOOTSTRAPPED', 'ACCOUNT_LOCKED', 'ACCOUNT_UNLOCKED'"
)

NEW_REASON_CODES = (
    "'CREDENTIALS_INVALID', 'ACCOUNT_LOCKED', 'SESSION_EXPIRED', 'UNAUTHORIZED', "
    "'SYSTEM_BOOTSTRAP', 'SUCCESSFUL_AUTHENTICATION', 'PASSWORD_CHANGE_REQUIRED', "
    "'PROVIDER_CONFIGURED', 'PROVIDER_UPDATED', 'PROVIDER_ACTIVATED', "
    "'PROVIDER_DISABLED', 'PROVIDER_INVALID_CONFIG', 'PROVIDER_INCOMPATIBLE', "
    "'PROVIDER_COMPATIBILITY_SUCCESS', 'PROVIDER_COMPATIBILITY_FAILED', "
    "'ACCESS_DENIED', 'STALE_VERSION', 'MASS_ASSIGNMENT_ATTEMPT'"
)

OLD_REASON_CODES = (
    "'CREDENTIALS_INVALID', 'ACCOUNT_LOCKED', 'SESSION_EXPIRED', 'UNAUTHORIZED', "
    "'SYSTEM_BOOTSTRAP', 'SUCCESSFUL_AUTHENTICATION', 'PASSWORD_CHANGE_REQUIRED'"
)


def upgrade() -> None:
    # Drop existing check constraints on security_audit_events
    op.drop_constraint('ck_security_audit_events_event_type', 'security_audit_events', type_='check')
    op.drop_constraint('ck_security_audit_events_reason_code', 'security_audit_events', type_='check')

    # Re-create check constraints with full set of provider audit enum values
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
