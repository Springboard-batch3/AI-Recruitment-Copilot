"""create_gap_reports_and_items_tables

Revision ID: a7b3c9d1e5f2
Revises: f4c2e6a8d1b3
Create Date: 2026-09-08 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a7b3c9d1e5f2'
down_revision: Union[str, None] = 'f4c2e6a8d1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create gap_reports table
    op.create_table(
        'gap_reports',
        sa.Column('report_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('recruiter_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('match_run_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('match_result_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_version_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('profile_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('profile_version', sa.Integer(), nullable=False),
        sa.Column('evaluation_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('matcher_algorithm_version', sa.String(length=50), nullable=False),
        sa.Column('matching_rule_version', sa.String(length=50), nullable=False),
        sa.Column('scoring_policy_version', sa.String(length=50), nullable=False),
        sa.Column('match_input_schema_version', sa.String(length=50), nullable=False),
        sa.Column('taxonomy_version', sa.String(length=50), nullable=True),
        sa.Column('gap_schema_version', sa.String(length=50), nullable=False),
        sa.Column('gap_policy_version', sa.String(length=50), nullable=False),
        sa.Column('wording_template_version', sa.String(length=50), nullable=False),
        sa.Column('source_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('report_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('idempotency_key', sa.String(length=255), nullable=False),
        sa.Column('total_requirements', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('covered_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('partially_covered_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('not_evidenced_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('unverified_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('conflicting_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('not_applicable_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('must_have_warning_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('revision >= 1', name='ck_gap_reports_revision_positive'),
        sa.CheckConstraint('profile_version >= 1', name='ck_gap_reports_profile_version_positive'),
        sa.CheckConstraint('total_requirements >= 0', name='ck_gap_reports_total_requirements_non_negative'),
        sa.ForeignKeyConstraint(
            ['recruiter_id'],
            ['users.user_id'],
            name='fk_gap_reports_recruiter_id_users',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['match_run_id'],
            ['match_runs.run_id'],
            name='fk_gap_reports_match_run_id_match_runs',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['match_result_id'],
            ['match_results.result_id'],
            name='fk_gap_reports_match_result_id_match_results',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['job_id'],
            ['job_requisitions.job_id'],
            name='fk_gap_reports_job_id_job_requisitions',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['job_version_id'],
            ['job_versions.job_version_id'],
            name='fk_gap_reports_job_version_id_job_versions',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['profile_id'],
            ['candidate_profiles.profile_id'],
            name='fk_gap_reports_profile_id_candidate_profiles',
            ondelete='RESTRICT'
        ),
        sa.PrimaryKeyConstraint('report_id'),
        sa.UniqueConstraint('recruiter_id', 'idempotency_key', name='uq_gap_reports_recruiter_id_idempotency_key'),
        sa.UniqueConstraint('report_fingerprint', name='uq_gap_reports_report_fingerprint'),
    )
    op.create_index('ix_gap_reports_recruiter_id', 'gap_reports', ['recruiter_id'], unique=False)
    op.create_index('ix_gap_reports_match_result_id', 'gap_reports', ['match_result_id'], unique=False)
    op.create_index('ix_gap_reports_match_run_id', 'gap_reports', ['match_run_id'], unique=False)
    op.create_index('ix_gap_reports_job_id', 'gap_reports', ['job_id'], unique=False)
    op.create_index('ix_gap_reports_job_version_id', 'gap_reports', ['job_version_id'], unique=False)
    op.create_index('ix_gap_reports_profile_id', 'gap_reports', ['profile_id'], unique=False)
    op.create_index('ix_gap_reports_created_at', 'gap_reports', ['created_at'], unique=False)

    # 2. Create gap_report_items table
    op.create_table(
        'gap_report_items',
        sa.Column('item_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('report_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('requirement_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('display_order', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('necessity', sa.String(length=50), nullable=False),
        sa.Column('priority', sa.String(length=50), nullable=False),
        sa.Column('source_match_state', sa.String(length=50), nullable=False),
        sa.Column('source_match_value', sa.Float(), nullable=False),
        sa.Column('gap_state', sa.String(length=50), nullable=False),
        sa.Column('required_value', sa.String(length=255), nullable=True),
        sa.Column('documented_value', sa.String(length=255), nullable=True),
        sa.Column('measurable_shortfall_value', sa.Float(), nullable=True),
        sa.Column('shortfall_unit', sa.String(length=50), nullable=True),
        sa.Column('shortfall_reason_code', sa.String(length=100), nullable=True),
        sa.Column('evidence_references', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('review_required', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('review_action_code', sa.String(length=50), nullable=False),
        sa.Column('must_have_warning_reference', sa.String(length=100), nullable=True),
        sa.Column('recruiter_wording', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('display_order >= 0', name='ck_gap_report_items_display_order_non_negative'),
        sa.CheckConstraint(
            'source_match_value >= 0.0 AND source_match_value <= 1.0',
            name='ck_gap_report_items_source_match_value_bounds'
        ),
        sa.CheckConstraint(
            'measurable_shortfall_value IS NULL OR measurable_shortfall_value >= 0.0',
            name='ck_gap_report_items_shortfall_value_non_negative'
        ),
        sa.CheckConstraint(
            '(measurable_shortfall_value IS NULL AND shortfall_unit IS NULL) OR (measurable_shortfall_value IS NOT NULL AND shortfall_unit IS NOT NULL)',
            name='ck_gap_report_items_shortfall_unit_required_with_value'
        ),
        sa.ForeignKeyConstraint(
            ['report_id'],
            ['gap_reports.report_id'],
            name='fk_gap_report_items_report_id_gap_reports',
            ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('item_id'),
        sa.UniqueConstraint('report_id', 'requirement_id', name='uq_gap_report_items_report_id_requirement_id'),
        sa.UniqueConstraint('report_id', 'display_order', name='uq_gap_report_items_report_id_display_order'),
    )
    op.create_index('ix_gap_report_items_report_id', 'gap_report_items', ['report_id'], unique=False)
    op.create_index('ix_gap_report_items_requirement_id', 'gap_report_items', ['requirement_id'], unique=False)
    op.create_index('ix_gap_report_items_gap_state', 'gap_report_items', ['gap_state'], unique=False)

    # 3. Create gap_audit_events table
    op.create_table(
        'gap_audit_events',
        sa.Column('audit_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('report_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('match_result_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('actor_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('safe_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('GAP_REPORT_CREATED', 'GAP_REPORT_REUSED', 'GAP_REPORT_CREATION_FAILED', 'GAP_REPORT_VIEWED', 'GAP_REPORT_JSON_ACCESSED')",
            name='ck_gap_audit_events_event_type'
        ),
        sa.ForeignKeyConstraint(
            ['report_id'],
            ['gap_reports.report_id'],
            name='fk_gap_audit_events_report_id_gap_reports',
            ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['match_result_id'],
            ['match_results.result_id'],
            name='fk_gap_audit_events_match_result_id_match_results',
            ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['actor_user_id'],
            ['users.user_id'],
            name='fk_gap_audit_events_actor_user_id_users',
            ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('audit_id'),
    )
    op.create_index('ix_gap_audit_events_report_id', 'gap_audit_events', ['report_id'], unique=False)
    op.create_index('ix_gap_audit_events_match_result_id', 'gap_audit_events', ['match_result_id'], unique=False)
    op.create_index('ix_gap_audit_events_actor_user_id', 'gap_audit_events', ['actor_user_id'], unique=False)
    op.create_index('ix_gap_audit_events_occurred_at', 'gap_audit_events', ['occurred_at'], unique=False)
    op.create_index('ix_gap_audit_events_event_type', 'gap_audit_events', ['event_type'], unique=False)


def downgrade() -> None:
    # 3. Drop gap_audit_events table
    op.drop_index('ix_gap_audit_events_event_type', table_name='gap_audit_events')
    op.drop_index('ix_gap_audit_events_occurred_at', table_name='gap_audit_events')
    op.drop_index('ix_gap_audit_events_actor_user_id', table_name='gap_audit_events')
    op.drop_index('ix_gap_audit_events_match_result_id', table_name='gap_audit_events')
    op.drop_index('ix_gap_audit_events_report_id', table_name='gap_audit_events')
    op.drop_table('gap_audit_events')

    # 2. Drop gap_report_items table
    op.drop_index('ix_gap_report_items_gap_state', table_name='gap_report_items')
    op.drop_index('ix_gap_report_items_requirement_id', table_name='gap_report_items')
    op.drop_index('ix_gap_report_items_report_id', table_name='gap_report_items')
    op.drop_table('gap_report_items')

    # 1. Drop gap_reports table
    op.drop_index('ix_gap_reports_created_at', table_name='gap_reports')
    op.drop_index('ix_gap_reports_profile_id', table_name='gap_reports')
    op.drop_index('ix_gap_reports_job_version_id', table_name='gap_reports')
    op.drop_index('ix_gap_reports_job_id', table_name='gap_reports')
    op.drop_index('ix_gap_reports_match_run_id', table_name='gap_reports')
    op.drop_index('ix_gap_reports_match_result_id', table_name='gap_reports')
    op.drop_index('ix_gap_reports_recruiter_id', table_name='gap_reports')
    op.drop_table('gap_reports')
