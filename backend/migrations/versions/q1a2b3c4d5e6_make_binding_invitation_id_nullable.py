"""Make candidate_account_bindings invitation_id nullable for candidate self-registration

Revision ID: q1a2b3c4d5e6
Revises: p1a2b3c4d5e6
Create Date: 2026-09-26 22:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'q1a2b3c4d5e6'
down_revision: Union[str, None] = 'p1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_EVENT_TYPES = (
    "'LOGIN_ATTEMPT', 'LOGOUT', 'SESSION_REVOKED', 'PASSWORD_CHANGED', "
    "'USER_BOOTSTRAPPED', 'ACCOUNT_LOCKED', 'ACCOUNT_UNLOCKED', "
    "'PROVIDER_PROFILE_CREATED', 'PROVIDER_PROFILE_UPDATED', "
    "'PROVIDER_PROFILE_ACTIVATED', 'PROVIDER_PROFILE_DISABLED', "
    "'PROVIDER_CONFIG_REJECTED', 'PROVIDER_COMPATIBILITY_TESTED', 'ACCESS_DENIED', "
    "'CANDIDATE_INVITATION_CREATED', 'CANDIDATE_INVITATION_REVOKED', "
    "'CANDIDATE_ACTIVATED', 'CANDIDATE_BINDING_UPDATED', "
    "'CANDIDATE_APPLICATION_SUBMITTED', 'CANDIDATE_APPLICATION_WITHDRAWN', "
    "'CANDIDATE_REVIEW_STATE_UPDATED', 'CANDIDATE_INTERVIEW_RELEASED', "
    "'CANDIDATE_REGISTERED'"
)

OLD_EVENT_TYPES = (
    "'LOGIN_ATTEMPT', 'LOGOUT', 'SESSION_REVOKED', 'PASSWORD_CHANGED', "
    "'USER_BOOTSTRAPPED', 'ACCOUNT_LOCKED', 'ACCOUNT_UNLOCKED', "
    "'PROVIDER_PROFILE_CREATED', 'PROVIDER_PROFILE_UPDATED', "
    "'PROVIDER_PROFILE_ACTIVATED', 'PROVIDER_PROFILE_DISABLED', "
    "'PROVIDER_CONFIG_REJECTED', 'PROVIDER_COMPATIBILITY_TESTED', 'ACCESS_DENIED', "
    "'CANDIDATE_INVITATION_CREATED', 'CANDIDATE_INVITATION_REVOKED', "
    "'CANDIDATE_ACTIVATED', 'CANDIDATE_BINDING_UPDATED', "
    "'CANDIDATE_APPLICATION_SUBMITTED', 'CANDIDATE_APPLICATION_WITHDRAWN', "
    "'CANDIDATE_REVIEW_STATE_UPDATED', 'CANDIDATE_INTERVIEW_RELEASED'"
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
    "'INVITATION_REVOKED_BY_RECRUITER', 'CANDIDATE_ACCOUNT_ACTIVATED', "
    "'APPLICATION_SUBMITTED', 'APPLICATION_WITHDRAWN', 'DUPLICATE_APPLICATION', "
    "'APPLICATION_NOT_ELIGIBLE', 'RESOURCE_NOT_FOUND', 'REVIEW_STATE_CHANGED', "
    "'INTERVIEW_RELEASED', 'CANDIDATE_ACCOUNT_REGISTERED'"
)

OLD_REASON_CODES = (
    "'CREDENTIALS_INVALID', 'ACCOUNT_LOCKED', 'SESSION_EXPIRED', 'UNAUTHORIZED', "
    "'SYSTEM_BOOTSTRAP', 'SUCCESSFUL_AUTHENTICATION', 'PASSWORD_CHANGE_REQUIRED', "
    "'PROVIDER_CONFIGURED', 'PROVIDER_UPDATED', 'PROVIDER_ACTIVATED', "
    "'PROVIDER_DISABLED', 'PROVIDER_INVALID_CONFIG', 'PROVIDER_INCOMPATIBLE', "
    "'PROVIDER_COMPATIBILITY_SUCCESS', 'PROVIDER_COMPATIBILITY_FAILED', "
    "'ACCESS_DENIED', 'STALE_VERSION', 'MASS_ASSIGNMENT_ATTEMPT', "
    "'INVITATION_EXPIRED', 'INVITATION_REVOKED', 'INVITATION_CONSUMED', "
    "'INVITATION_INVALID', 'BINDING_SUSPENDED', 'INVITATION_CREATED', "
    "'INVITATION_REVOKED_BY_RECRUITER', 'CANDIDATE_ACCOUNT_ACTIVATED', "
    "'APPLICATION_SUBMITTED', 'APPLICATION_WITHDRAWN', 'DUPLICATE_APPLICATION', "
    "'APPLICATION_NOT_ELIGIBLE', 'RESOURCE_NOT_FOUND', 'REVIEW_STATE_CHANGED', "
    "'INTERVIEW_RELEASED'"
)


def upgrade() -> None:
    # 1. Make invitation_id nullable on candidate_account_bindings
    op.alter_column(
        'candidate_account_bindings',
        'invitation_id',
        existing_type=sa.Uuid(),
        nullable=True,
    )
    # 2. Add user_id to candidate_applications
    op.add_column(
        'candidate_applications',
        sa.Column('user_id', sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        'fk_candidate_applications_user_id_users',
        'candidate_applications',
        'users',
        ['user_id'],
        ['user_id'],
        ondelete='CASCADE',
    )
    op.create_index(
        'ix_candidate_applications_user_id',
        'candidate_applications',
        ['user_id'],
        unique=False,
    )

    # 3. Populate user_id from candidate_account_bindings for existing applications
    op.execute(
        """
        UPDATE candidate_applications ca
        SET user_id = cab.user_id
        FROM candidate_account_bindings cab
        WHERE ca.binding_id = cab.binding_id
        """
    )

    # 4. Make binding_id and candidate_id nullable on candidate_applications
    op.alter_column(
        'candidate_applications',
        'binding_id',
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.alter_column(
        'candidate_applications',
        'candidate_id',
        existing_type=sa.Uuid(),
        nullable=True,
    )

    # 5. Update security_audit_events check constraints
    bind = op.get_bind()
    if bind.engine.name == "postgresql":
        op.drop_constraint('ck_security_audit_events_event_type', 'security_audit_events', type_='check')
        op.drop_constraint('ck_security_audit_events_reason_code', 'security_audit_events', type_='check')

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

    op.alter_column(
        'candidate_applications',
        'candidate_id',
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.alter_column(
        'candidate_applications',
        'binding_id',
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_index('ix_candidate_applications_user_id', table_name='candidate_applications')
    op.drop_constraint('fk_candidate_applications_user_id_users', 'candidate_applications', type_='foreignkey')
    op.drop_column('candidate_applications', 'user_id')
    op.alter_column(
        'candidate_account_bindings',
        'invitation_id',
        existing_type=sa.Uuid(),
        nullable=False,
    )


