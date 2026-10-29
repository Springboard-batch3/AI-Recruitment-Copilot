"""Create Phase 3D.2 assessment and segmentation tables

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-17 23:58:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Update check constraint on simulation_attempts.lifecycle_status
    op.drop_constraint('ck_simulation_attempts_lifecycle_status', 'simulation_attempts', type_='check')
    op.create_check_constraint(
        'ck_simulation_attempts_lifecycle_status',
        'simulation_attempts',
        "lifecycle_status IN ('NOT_STARTED', 'IN_PROGRESS', 'SUBMITTED', 'PROVISIONALLY_ASSESSED', 'HUMAN_REVIEWED', 'FINALIZED')"
    )

    # 2. Create simulation_answer_segments
    op.create_table(
        'simulation_answer_segments',
        sa.Column('segment_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('answer_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('simulation_answers.answer_id', ondelete='CASCADE'), nullable=False),
        sa.Column('attempt_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('simulation_attempts.attempt_id', ondelete='CASCADE'), nullable=False),
        sa.Column('question_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('sequence_order', sa.Integer(), nullable=False),
        sa.Column('start_char', sa.Integer(), nullable=False),
        sa.Column('end_char', sa.Integer(), nullable=False),
        sa.Column('segment_text', sa.Text(), nullable=False),
        sa.Column('segmentation_version', sa.String(length=32), nullable=False, server_default='1.0.0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('answer_id', 'sequence_order', name='uq_sim_segments_answer_seq'),
    )
    op.create_index('ix_sim_segments_answer_id', 'simulation_answer_segments', ['answer_id'])
    op.create_index('ix_sim_segments_attempt_id', 'simulation_answer_segments', ['attempt_id'])
    op.create_index('ix_sim_segments_question_id', 'simulation_answer_segments', ['question_id'])

    # 3. Create simulation_question_assessments
    op.create_table(
        'simulation_question_assessments',
        sa.Column('assessment_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('attempt_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('simulation_attempts.attempt_id', ondelete='CASCADE'), nullable=False),
        sa.Column('answer_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('simulation_answers.answer_id', ondelete='CASCADE'), nullable=False),
        sa.Column('question_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('rubric_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('rubric_version', sa.Integer(), nullable=True),
        sa.Column('assessment_status', sa.String(length=32), nullable=False, server_default='PROVISIONAL'),
        sa.Column('provisional_level', sa.Integer(), nullable=True),
        sa.Column('insufficient_evidence_reason', sa.String(length=100), nullable=True),
        sa.Column('criterion_observations', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('cited_segment_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('uncertainty_codes', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('validation_report', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('model_invocation_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('guardrail_policy_version', sa.String(length=32), nullable=False, server_default='1.0.0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('attempt_id', 'question_id', name='uq_sim_assessments_attempt_question'),
    )
    op.create_index('ix_sim_assessments_attempt_id', 'simulation_question_assessments', ['attempt_id'])
    op.create_index('ix_sim_assessments_answer_id', 'simulation_question_assessments', ['answer_id'])
    op.create_index('ix_sim_assessments_question_id', 'simulation_question_assessments', ['question_id'])
    op.create_index('ix_sim_assessments_status', 'simulation_question_assessments', ['assessment_status'])


def downgrade() -> None:
    op.drop_table('simulation_question_assessments')
    op.drop_table('simulation_answer_segments')
    op.drop_constraint('ck_simulation_attempts_lifecycle_status', 'simulation_attempts', type_='check')
    op.create_check_constraint(
        'ck_simulation_attempts_lifecycle_status',
        'simulation_attempts',
        "lifecycle_status IN ('NOT_STARTED', 'IN_PROGRESS', 'SUBMITTED')"
    )
