"""Create official candidate interview sessions and answers tables for Phase 4C.2

Revision ID: s1a2b3c4d5e6
Revises: r1a2b3c4d5e6
Create Date: 2026-09-27 17:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 's1a2b3c4d5e6'
down_revision: Union[str, None] = 'r1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

    op.create_table(
        'candidate_interview_sessions',
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('release_id', sa.Uuid(), nullable=False),
        sa.Column('candidate_id', sa.Uuid(), nullable=False),
        sa.Column('application_id', sa.Uuid(), nullable=True),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('job_version_id', sa.Uuid(), nullable=False),
        sa.Column('job_revision', sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column('ai_set_id', sa.Uuid(), nullable=True),
        sa.Column('set_version', sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column('snapshot_hash', sa.String(length=64), nullable=False),
        sa.Column('questions_snapshot', json_type, nullable=False),
        sa.Column('status', sa.String(length=30), server_default=sa.text("'IN_PROGRESS'"), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_saved_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('IN_PROGRESS', 'SUBMITTED')", name='ck_candidate_interview_sessions_status'),
        sa.CheckConstraint('job_revision >= 1', name='ck_candidate_interview_sessions_job_rev_positive'),
        sa.CheckConstraint('set_version >= 1', name='ck_candidate_interview_sessions_set_ver_positive'),
        sa.CheckConstraint('length(snapshot_hash) = 64', name='ck_candidate_interview_sessions_hash_length'),
        sa.ForeignKeyConstraint(
            ['release_id'],
            ['candidate_practice_releases.release_id'],
            name='fk_candidate_interview_sessions_release_id',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['candidate_id'],
            ['candidate_identities.identity_id'],
            name='fk_candidate_interview_sessions_candidate_id',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['job_id'],
            ['job_requisitions.job_id'],
            name='fk_candidate_interview_sessions_job_id',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['job_version_id'],
            ['job_versions.job_version_id'],
            name='fk_candidate_interview_sessions_job_version_id',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['ai_set_id'],
            ['ai_interview_question_sets.ai_set_id'],
            name='fk_candidate_interview_sessions_ai_set_id',
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('session_id'),
    )

    op.create_index('ix_candidate_interview_sessions_release_id', 'candidate_interview_sessions', ['release_id'], unique=False)
    op.create_index('ix_candidate_interview_sessions_candidate_id', 'candidate_interview_sessions', ['candidate_id'], unique=False)
    op.create_index('ix_candidate_interview_sessions_org_id', 'candidate_interview_sessions', ['organization_id'], unique=False)
    op.create_index('ix_candidate_interview_sessions_status', 'candidate_interview_sessions', ['status'], unique=False)

    op.create_table(
        'candidate_interview_answers',
        sa.Column('answer_id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('slot_id', sa.Uuid(), nullable=False),
        sa.Column('question_format', sa.String(length=30), nullable=False),
        sa.Column('text_answer', sa.Text(), nullable=True),
        sa.Column('selected_option_id', sa.String(length=20), nullable=True),
        sa.Column('answer_order', sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column('is_skipped', sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column('saved_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("question_format IN ('CONVERSATION', 'MCQ_SINGLE_ANSWER')", name='ck_candidate_interview_answers_format'),
        sa.UniqueConstraint('session_id', 'slot_id', name='uq_candidate_interview_answers_session_slot'),
        sa.ForeignKeyConstraint(
            ['session_id'],
            ['candidate_interview_sessions.session_id'],
            name='fk_candidate_interview_answers_session_id',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('answer_id'),
    )

    op.create_index('ix_candidate_interview_answers_session_id', 'candidate_interview_answers', ['session_id'], unique=False)
    op.create_index('ix_candidate_interview_answers_slot_id', 'candidate_interview_answers', ['slot_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_candidate_interview_answers_slot_id', table_name='candidate_interview_answers')
    op.drop_index('ix_candidate_interview_answers_session_id', table_name='candidate_interview_answers')
    op.drop_table('candidate_interview_answers')
    op.drop_index('ix_candidate_interview_sessions_status', table_name='candidate_interview_sessions')
    op.drop_index('ix_candidate_interview_sessions_org_id', table_name='candidate_interview_sessions')
    op.drop_index('ix_candidate_interview_sessions_candidate_id', table_name='candidate_interview_sessions')
    op.drop_index('ix_candidate_interview_sessions_release_id', table_name='candidate_interview_sessions')
    op.drop_table('candidate_interview_sessions')
