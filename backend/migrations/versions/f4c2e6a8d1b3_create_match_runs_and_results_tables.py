"""create_match_runs_and_results_tables

Revision ID: f4c2e6a8d1b3
Revises: e2f5a7b9c1d4
Create Date: 2026-09-07 13:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f4c2e6a8d1b3'
down_revision: Union[str, None] = 'e2f5a7b9c1d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create match_runs table
    op.create_table(
        'match_runs',
        sa.Column('run_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('recruiter_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_version_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('evaluation_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('match_input_schema_version', sa.String(length=50), nullable=False),
        sa.Column('matcher_algorithm_version', sa.String(length=50), nullable=False),
        sa.Column('matching_rule_version', sa.String(length=50), nullable=False),
        sa.Column('scoring_policy_version', sa.String(length=50), nullable=False),
        sa.Column('candidate_input_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('input_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('idempotency_key', sa.String(length=255), nullable=False),
        sa.Column('lifecycle_status', sa.String(length=20), nullable=False),
        sa.Column('error_code', sa.String(length=100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('candidate_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "lifecycle_status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name='ck_match_runs_lifecycle_status'
        ),
        sa.CheckConstraint('candidate_count >= 0', name='ck_match_runs_candidate_count'),
        sa.ForeignKeyConstraint(
            ['recruiter_id'],
            ['users.user_id'],
            name='fk_match_runs_recruiter_id_users',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['job_id'],
            ['job_requisitions.job_id'],
            name='fk_match_runs_job_id_job_requisitions',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['job_version_id'],
            ['job_versions.job_version_id'],
            name='fk_match_runs_job_version_id_job_versions',
            ondelete='RESTRICT'
        ),
        sa.PrimaryKeyConstraint('run_id'),
        sa.UniqueConstraint('recruiter_id', 'idempotency_key', name='uq_match_runs_recruiter_id_idempotency_key'),
    )
    op.create_index('ix_match_runs_recruiter_id', 'match_runs', ['recruiter_id'], unique=False)
    op.create_index('ix_match_runs_job_id', 'match_runs', ['job_id'], unique=False)
    op.create_index('ix_match_runs_job_version_id', 'match_runs', ['job_version_id'], unique=False)
    op.create_index('ix_match_runs_lifecycle_status', 'match_runs', ['lifecycle_status'], unique=False)
    op.create_index('ix_match_runs_created_at', 'match_runs', ['created_at'], unique=False)

    # 2. Create match_results table
    op.create_table(
        'match_results',
        sa.Column('result_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('profile_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('profile_version', sa.Integer(), nullable=False),
        sa.Column('raw_compatibility_score', sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column('display_compatibility_score', sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column('confidence_tier', sa.String(length=20), nullable=False),
        sa.Column('warning_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('must_have_warning_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('is_tied', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('tie_group_id', sa.String(length=50), nullable=True),
        sa.Column('score_decomposition', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            'raw_compatibility_score >= 0.0 AND raw_compatibility_score <= 100.0',
            name='ck_match_results_raw_score'
        ),
        sa.CheckConstraint(
            'display_compatibility_score >= 0.0 AND display_compatibility_score <= 100.0',
            name='ck_match_results_display_score'
        ),
        sa.CheckConstraint('rank >= 1', name='ck_match_results_rank'),
        sa.CheckConstraint('warning_count >= 0', name='ck_match_results_warning_count'),
        sa.CheckConstraint('must_have_warning_count >= 0', name='ck_match_results_must_have_warning_count'),
        sa.ForeignKeyConstraint(
            ['run_id'],
            ['match_runs.run_id'],
            name='fk_match_results_run_id_match_runs',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['profile_id'],
            ['candidate_profiles.profile_id'],
            name='fk_match_results_profile_id_candidate_profiles',
            ondelete='RESTRICT'
        ),
        sa.PrimaryKeyConstraint('result_id'),
        sa.UniqueConstraint('run_id', 'profile_id', 'profile_version', name='uq_match_results_run_id_profile_id_version'),
    )
    op.create_index('ix_match_results_run_id', 'match_results', ['run_id'], unique=False)
    op.create_index('ix_match_results_profile_id', 'match_results', ['profile_id'], unique=False)
    op.create_index('ix_match_results_run_id_rank', 'match_results', ['run_id', 'rank'], unique=False)

    # 3. Create match_audit_events table
    op.create_table(
        'match_audit_events',
        sa.Column('audit_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('actor_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_version_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('revision', sa.Integer(), nullable=True),
        sa.Column('candidate_count', sa.Integer(), nullable=True),
        sa.Column('matcher_algorithm_version', sa.String(length=50), nullable=True),
        sa.Column('scoring_policy_version', sa.String(length=50), nullable=True),
        sa.Column('input_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('safe_metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('MATCH_RUN_CREATED', 'MATCH_RUN_SUCCEEDED', 'MATCH_RUN_FAILED', 'MATCH_RUN_REUSED', 'MATCH_RESULTS_VIEWED')",
            name='ck_match_audit_events_event_type'
        ),
        sa.ForeignKeyConstraint(
            ['run_id'],
            ['match_runs.run_id'],
            name='fk_match_audit_events_run_id_match_runs',
            ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['actor_user_id'],
            ['users.user_id'],
            name='fk_match_audit_events_actor_user_id_users',
            ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('audit_id'),
    )
    op.create_index('ix_match_audit_events_run_id', 'match_audit_events', ['run_id'], unique=False)
    op.create_index('ix_match_audit_events_actor_user_id', 'match_audit_events', ['actor_user_id'], unique=False)
    op.create_index('ix_match_audit_events_occurred_at', 'match_audit_events', ['occurred_at'], unique=False)
    op.create_index('ix_match_audit_events_event_type', 'match_audit_events', ['event_type'], unique=False)


def downgrade() -> None:
    # 3. Drop match_audit_events table
    op.drop_index('ix_match_audit_events_event_type', table_name='match_audit_events')
    op.drop_index('ix_match_audit_events_occurred_at', table_name='match_audit_events')
    op.drop_index('ix_match_audit_events_actor_user_id', table_name='match_audit_events')
    op.drop_index('ix_match_audit_events_run_id', table_name='match_audit_events')
    op.drop_table('match_audit_events')

    # 2. Drop match_results table
    op.drop_index('ix_match_results_run_id_rank', table_name='match_results')
    op.drop_index('ix_match_results_profile_id', table_name='match_results')
    op.drop_index('ix_match_results_run_id', table_name='match_results')
    op.drop_table('match_results')

    # 1. Drop match_runs table
    op.drop_index('ix_match_runs_created_at', table_name='match_runs')
    op.drop_index('ix_match_runs_lifecycle_status', table_name='match_runs')
    op.drop_index('ix_match_runs_job_version_id', table_name='match_runs')
    op.drop_index('ix_match_runs_job_id', table_name='match_runs')
    op.drop_index('ix_match_runs_recruiter_id', table_name='match_runs')
    op.drop_table('match_runs')
