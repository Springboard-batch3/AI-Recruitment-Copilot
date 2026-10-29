"""Create Phase 3D.3 human review and finalization tables

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-18 00:25:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create simulation_assessment_reviews table
    op.create_table(
        'simulation_assessment_reviews',
        sa.Column('review_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('assessment_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('simulation_question_assessments.assessment_id', ondelete='CASCADE'), nullable=False),
        sa.Column('attempt_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('simulation_attempts.attempt_id', ondelete='CASCADE'), nullable=False),
        sa.Column('question_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('reviewer_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('review_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('decision', sa.String(length=32), nullable=False),
        sa.Column('reviewed_observation', sa.Text(), nullable=True),
        sa.Column('reviewed_rubric_level', sa.Integer(), nullable=True),
        sa.Column('retained_citations', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('assessment_id', 'review_version', name='uq_sim_reviews_assessment_version'),
    )
    op.create_index('ix_sim_reviews_assessment_id', 'simulation_assessment_reviews', ['assessment_id'])
    op.create_index('ix_sim_reviews_attempt_id', 'simulation_assessment_reviews', ['attempt_id'])
    op.create_index('ix_sim_reviews_question_id', 'simulation_assessment_reviews', ['question_id'])
    op.create_index('ix_sim_reviews_reviewer_user_id', 'simulation_assessment_reviews', ['reviewer_user_id'])

    # 2. Create simulation_interview_assessments table
    op.create_table(
        'simulation_interview_assessments',
        sa.Column('interview_assessment_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('attempt_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('simulation_attempts.attempt_id', ondelete='CASCADE'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('finalized_by_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('lifecycle_status', sa.String(length=32), nullable=False, server_default='FINALIZED'),
        sa.Column('aggregate_score', sa.Float(), nullable=True),
        sa.Column('aggregate_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('policy_version', sa.String(length=32), nullable=False, server_default='1.0.0'),
        sa.Column('policy_hash', sa.String(length=64), nullable=False, server_default="''"),
        sa.Column('is_finalized', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('finalized_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('attempt_id', name='uq_sim_interview_assessments_attempt_id'),
    )
    op.create_index('ix_sim_interview_assessments_attempt_id', 'simulation_interview_assessments', ['attempt_id'])
    op.create_index('ix_sim_interview_assessments_org_id', 'simulation_interview_assessments', ['organization_id'])
    op.create_index('ix_sim_interview_assessments_finalized_by', 'simulation_interview_assessments', ['finalized_by_user_id'])

    # 3. Create simulation_review_audit_events table
    op.create_table(
        'simulation_review_audit_events',
        sa.Column('audit_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('attempt_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('simulation_attempts.attempt_id', ondelete='CASCADE'), nullable=False),
        sa.Column('question_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('actor_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('previous_state', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('new_state', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('safe_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_sim_review_audit_attempt_id', 'simulation_review_audit_events', ['attempt_id'])
    op.create_index('ix_sim_review_audit_actor_user_id', 'simulation_review_audit_events', ['actor_user_id'])
    op.create_index('ix_sim_review_audit_occurred_at', 'simulation_review_audit_events', ['occurred_at'])


def downgrade() -> None:
    op.drop_table('simulation_review_audit_events')
    op.drop_table('simulation_interview_assessments')
    op.drop_table('simulation_assessment_reviews')
