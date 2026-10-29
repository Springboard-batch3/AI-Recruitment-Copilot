"""Create official candidate interview evaluation and question review tables for Phase 4C.4

Revision ID: u1a2b3c4d5e6
Revises: t1a2b3c4d5e6
Create Date: 2026-09-27 23:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'u1a2b3c4d5e6'
down_revision: Union[str, None] = 't1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

    # 1. Candidate Interview Evaluations summary table
    op.create_table(
        'candidate_interview_evaluations',
        sa.Column('evaluation_id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('job_version_id', sa.Uuid(), nullable=False),
        sa.Column('candidate_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.String(length=32), server_default=sa.text("'NOT_STARTED'"), nullable=False),
        sa.Column('questions_reviewed', sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column('core_questions_included', sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column('supplemental_questions_excluded', sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column('scoring_policy_version', sa.String(length=32), server_default=sa.text("'1.0.0'"), nullable=False),
        sa.Column('aggregate_score', sa.Float(), nullable=True),
        sa.Column('aggregate_summary', json_type, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('finalized_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('finalized_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('candidate_feedback_status', sa.String(length=32), server_default=sa.text("'UNPUBLISHED'"), nullable=False),
        sa.Column('candidate_feedback_published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('candidate_feedback_payload', json_type, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('evaluation_id', name='pk_candidate_interview_evaluations'),
        sa.UniqueConstraint('session_id', name='uq_candidate_interview_evaluations_session_id'),
        sa.CheckConstraint("status IN ('NOT_STARTED', 'IN_REVIEW', 'FINALIZED')", name='ck_candidate_interview_evaluations_status'),
        sa.CheckConstraint("candidate_feedback_status IN ('UNPUBLISHED', 'PUBLISHED')", name='ck_candidate_interview_evaluations_fb_status'),
        sa.ForeignKeyConstraint(
            ['session_id'],
            ['candidate_interview_sessions.session_id'],
            name='fk_candidate_interview_evaluations_session_id',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['job_id'],
            ['job_requisitions.job_id'],
            name='fk_candidate_interview_evaluations_job_id',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['job_version_id'],
            ['job_versions.job_version_id'],
            name='fk_candidate_interview_evaluations_job_version_id',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['candidate_id'],
            ['candidate_identities.identity_id'],
            name='fk_candidate_interview_evaluations_candidate_id',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['finalized_by_user_id'],
            ['users.user_id'],
            name='fk_candidate_interview_evaluations_finalized_by',
            ondelete='SET NULL',
        ),
    )
    op.create_index('ix_candidate_interview_evaluations_org_id', 'candidate_interview_evaluations', ['organization_id'])
    op.create_index('ix_candidate_interview_evaluations_job_id', 'candidate_interview_evaluations', ['job_id'])
    op.create_index('ix_candidate_interview_evaluations_candidate_id', 'candidate_interview_evaluations', ['candidate_id'])
    op.create_index('ix_candidate_interview_evaluations_status', 'candidate_interview_evaluations', ['status'])

    # 2. Candidate Question Evaluations table
    op.create_table(
        'candidate_question_evaluations',
        sa.Column('question_evaluation_id', sa.Uuid(), nullable=False),
        sa.Column('evaluation_id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('answer_id', sa.Uuid(), nullable=True),
        sa.Column('slot_id', sa.Uuid(), nullable=False),
        sa.Column('source_analysis_id', sa.Uuid(), nullable=True),
        sa.Column('review_state', sa.String(length=32), server_default=sa.text("'UNREVIEWED'"), nullable=False),
        sa.Column('reviewed_rating', sa.Integer(), nullable=True),
        sa.Column('reviewed_observations', json_type, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('override_reason', sa.Text(), nullable=True),
        sa.Column('recruiter_note', sa.Text(), nullable=True),
        sa.Column('reviewer_user_id', sa.Uuid(), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('question_evaluation_id', name='pk_candidate_question_evaluations'),
        sa.UniqueConstraint('session_id', 'slot_id', name='uq_candidate_question_evaluations_session_slot'),
        sa.CheckConstraint(
            "review_state IN ('ACCEPTED', 'EDITED', 'OVERRIDDEN', 'INSUFFICIENT', 'UNREVIEWED')",
            name='ck_candidate_question_evaluations_review_state'
        ),
        sa.CheckConstraint(
            "reviewed_rating IS NULL OR (reviewed_rating >= 1 AND reviewed_rating <= 5)",
            name='ck_candidate_question_evaluations_rating_range'
        ),
        sa.ForeignKeyConstraint(
            ['evaluation_id'],
            ['candidate_interview_evaluations.evaluation_id'],
            name='fk_candidate_question_evaluations_eval_id',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['session_id'],
            ['candidate_interview_sessions.session_id'],
            name='fk_candidate_question_evaluations_session_id',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['answer_id'],
            ['candidate_interview_answers.answer_id'],
            name='fk_candidate_question_evaluations_answer_id',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['reviewer_user_id'],
            ['users.user_id'],
            name='fk_candidate_question_evaluations_reviewer',
            ondelete='CASCADE',
        ),
    )
    op.create_index('ix_candidate_question_evaluations_eval_id', 'candidate_question_evaluations', ['evaluation_id'])
    op.create_index('ix_candidate_question_evaluations_session_id', 'candidate_question_evaluations', ['session_id'])
    op.create_index('ix_candidate_question_evaluations_slot_id', 'candidate_question_evaluations', ['slot_id'])


def downgrade() -> None:
    op.drop_table('candidate_question_evaluations')
    op.drop_table('candidate_interview_evaluations')
