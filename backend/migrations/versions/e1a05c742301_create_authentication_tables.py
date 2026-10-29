"""create_authentication_tables

Revision ID: e1a05c742301
Revises: d92e10a8b71f
Create Date: 2026-09-01 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e1a05c742301'
down_revision: Union[str, None] = 'd92e10a8b71f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create users table
    op.create_table(
        'users',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('must_change_password', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('failed_login_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('failed_login_count >= 0', name='ck_users_failed_login_count_non_negative'),
        sa.CheckConstraint('length(trim(email)) > 0', name='ck_users_email_not_empty'),
        sa.CheckConstraint('length(trim(display_name)) > 0', name='ck_users_display_name_not_empty'),
        sa.CheckConstraint("role IN ('ADMIN', 'RECRUITER', 'REVIEWER')", name='ck_users_role'),
        sa.PrimaryKeyConstraint('user_id'),
        sa.UniqueConstraint('email', name='uq_users_email'),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    # 2. Create user_sessions table
    op.create_table(
        'user_sessions',
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('csrf_token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('idle_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('absolute_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoke_reason', sa.String(length=50), nullable=True),
        sa.CheckConstraint('idle_expires_at <= absolute_expires_at', name='ck_user_sessions_idle_lte_absolute_expiry'),
        sa.CheckConstraint('length(token_hash) = 64', name='ck_user_sessions_token_hash_length'),
        sa.CheckConstraint('length(csrf_token_hash) = 64', name='ck_user_sessions_csrf_token_hash_length'),
        sa.CheckConstraint(
            "revoke_reason IS NULL OR revoke_reason IN ('LOGOUT', 'EXPIRED_IDLE', 'EXPIRED_ABSOLUTE', 'PASSWORD_CHANGED', 'ADMIN_REVOKED', 'SUSPICIOUS_ACTIVITY')",
            name='ck_user_sessions_revoke_reason'
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.user_id'],
            name='fk_user_sessions_user_id_users',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('session_id'),
        sa.UniqueConstraint('token_hash', name='uq_user_sessions_token_hash'),
    )
    op.create_index('ix_user_sessions_user_id', 'user_sessions', ['user_id'], unique=False)
    op.create_index('ix_user_sessions_token_hash', 'user_sessions', ['token_hash'], unique=False)

    # 3. Create security_audit_events table
    op.create_table(
        'security_audit_events',
        sa.Column('event_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('actor_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('resource_type', sa.String(length=100), nullable=True),
        sa.Column('resource_id', sa.String(length=255), nullable=True),
        sa.Column('result', sa.String(length=50), nullable=False),
        sa.Column('reason_code', sa.String(length=50), nullable=False),
        sa.Column('safe_metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('LOGIN_ATTEMPT', 'LOGOUT', 'SESSION_REVOKED', 'PASSWORD_CHANGED', 'USER_BOOTSTRAPPED', 'ACCOUNT_LOCKED', 'ACCOUNT_UNLOCKED')",
            name='ck_security_audit_events_event_type'
        ),
        sa.CheckConstraint(
            "result IN ('SUCCESS', 'FAILURE', 'DENIED')",
            name='ck_security_audit_events_result'
        ),
        sa.CheckConstraint(
            "reason_code IN ('CREDENTIALS_INVALID', 'ACCOUNT_LOCKED', 'SESSION_EXPIRED', 'UNAUTHORIZED', 'SYSTEM_BOOTSTRAP', 'SUCCESSFUL_AUTHENTICATION', 'PASSWORD_CHANGE_REQUIRED')",
            name='ck_security_audit_events_reason_code'
        ),
        sa.ForeignKeyConstraint(
            ['actor_user_id'],
            ['users.user_id'],
            name='fk_security_audit_events_actor_user_id_users',
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('event_id'),
    )
    op.create_index('ix_security_audit_events_occurred_at', 'security_audit_events', ['occurred_at'], unique=False)
    op.create_index('ix_security_audit_events_actor_user_id', 'security_audit_events', ['actor_user_id'], unique=False)
    op.create_index('ix_security_audit_events_event_type', 'security_audit_events', ['event_type'], unique=False)


def downgrade() -> None:
    # 1. Drop security_audit_events table
    op.drop_index('ix_security_audit_events_event_type', table_name='security_audit_events')
    op.drop_index('ix_security_audit_events_actor_user_id', table_name='security_audit_events')
    op.drop_index('ix_security_audit_events_occurred_at', table_name='security_audit_events')
    op.drop_table('security_audit_events')

    # 2. Drop user_sessions table
    op.drop_index('ix_user_sessions_token_hash', table_name='user_sessions')
    op.drop_index('ix_user_sessions_user_id', table_name='user_sessions')
    op.drop_table('user_sessions')

    # 3. Drop users table
    op.drop_index('ix_users_email', table_name='users')
    op.drop_table('users')
