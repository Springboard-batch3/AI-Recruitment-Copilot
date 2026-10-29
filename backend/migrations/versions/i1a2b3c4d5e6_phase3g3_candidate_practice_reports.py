"""Phase 3G.3 Candidate Practice Coaching Reports

Revision ID: i1a2b3c4d5e6
Revises: h1a2b3c4d5e6
Create Date: 2026-09-24 20:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'i1a2b3c4d5e6'
down_revision: Union[str, None] = 'h1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

    op.create_table(
        'candidate_practice_reports',
        sa.Column('report_id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('candidate_id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('report_version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('schema_version', sa.String(length=50), server_default=sa.text("'practice-coaching-summary.v1'"), nullable=False),
        sa.Column('lifecycle_status', sa.String(length=50), server_default=sa.text("'SUMMARY_READY'"), nullable=False),
        sa.Column('source_review_hash', sa.String(length=64), nullable=False),
        sa.Column('aggregation_policy_version', sa.String(length=50), server_default=sa.text("'1.0.0'"), nullable=False),
        sa.Column('summary_text', sa.Text(), nullable=False),
        sa.Column('strongest_areas', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('priority_improvements', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('competency_commentary', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('response_patterns', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('next_practice_plan', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('competency_aggregates', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('dimension_aggregates', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('question_trend', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('trend_classification', sa.String(length=50), server_default=sa.text("'NOT_ENOUGH_ASSESSABLE_DATA'"), nullable=False),
        sa.Column('mcq_breakdown', json_type, server_default=sa.text("'{}'"), nullable=False),
        sa.Column('completion_counts', json_type, server_default=sa.text("'{}'"), nullable=False),
        sa.Column('disclaimer', sa.Text(), nullable=False),
        sa.Column('provider_class', sa.String(length=50), nullable=True),
        sa.Column('model_identifier', sa.String(length=100), nullable=True),
        sa.Column('prompt_template_version', sa.String(length=50), nullable=True),
        sa.Column('guardrail_policy_version', sa.String(length=50), nullable=True),
        sa.Column('validation_status', sa.String(length=30), server_default=sa.text("'PASSED'"), nullable=False),
        sa.Column('retry_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('is_current', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('superseded_by_report_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('finalized_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['session_id'], ['candidate_practice_sessions.session_id'],
            name='fk_cand_practice_reports_session_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['candidate_id'], ['candidate_identities.identity_id'],
            name='fk_cand_practice_reports_candidate_id', ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('report_id'),
        sa.UniqueConstraint('session_id', 'report_version', name='uq_candidate_practice_reports_session_version'),
        sa.UniqueConstraint('session_id', 'source_review_hash', name='uq_candidate_practice_reports_session_hash')
    )
    op.create_index('ix_cand_practice_reports_session_id', 'candidate_practice_reports', ['session_id'])
    op.create_index('ix_cand_practice_reports_candidate_id', 'candidate_practice_reports', ['candidate_id'])
    op.create_index('ix_cand_practice_reports_org_id', 'candidate_practice_reports', ['organization_id'])
    op.create_index('ix_cand_practice_reports_status', 'candidate_practice_reports', ['lifecycle_status'])


def downgrade() -> None:
    op.drop_table('candidate_practice_reports')
