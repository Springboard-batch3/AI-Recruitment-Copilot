"""Create candidate practice session, message, and MCQ answer tables

Revision ID: f3a4b5c6d7e8
Revises: f2a3b4c5d6e7
Create Date: 2026-09-21 23:45:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f3a4b5c6d7e8'
down_revision = 'f2a3b4c5d6e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

    # 1. Create candidate_practice_sessions table
    op.create_table(
        'candidate_practice_sessions',
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('binding_id', sa.Uuid(), nullable=False),
        sa.Column('candidate_id', sa.Uuid(), nullable=False),
        sa.Column('release_id', sa.Uuid(), nullable=False),
        sa.Column('application_id', sa.Uuid(), nullable=True),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('job_version_id', sa.Uuid(), nullable=False),
        sa.Column('job_revision', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('ai_set_id', sa.Uuid(), nullable=True),
        sa.Column('set_version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('snapshot_hash', sa.String(length=64), nullable=False),
        sa.Column('questions_snapshot', json_type, nullable=False),
        sa.Column('lifecycle_status', sa.String(length=30), server_default=sa.text("'IN_PROGRESS'"), nullable=False),
        sa.Column('current_section', sa.String(length=30), server_default=sa.text("'CONVERSATION'"), nullable=False),
        sa.Column('current_conv_index', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('current_conv_turn_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('current_mcq_index', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('total_conv_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('total_mcq_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('total_response_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('model_call_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('concurrency_version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('abandoned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expired_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('retention_class', sa.String(length=50), server_default=sa.text("'SIMULATION_CONTENT'"), nullable=False),
        sa.Column('fallback_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('fallback_reason', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "lifecycle_status IN ('IN_PROGRESS', 'COMPLETED', 'ABANDONED', 'EXPIRED', 'REVOKED')",
            name='ck_candidate_practice_sessions_lifecycle'
        ),
        sa.CheckConstraint(
            "current_section IN ('CONVERSATION', 'MCQ', 'COMPLETE')",
            name='ck_candidate_practice_sessions_section'
        ),
        sa.CheckConstraint(
            'length(snapshot_hash) = 64',
            name='ck_candidate_practice_sessions_snapshot_hash_length'
        ),
        sa.ForeignKeyConstraint(
            ['binding_id'], ['candidate_account_bindings.binding_id'],
            name='fk_candidate_practice_sessions_binding_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['candidate_id'], ['candidate_identities.identity_id'],
            name='fk_candidate_practice_sessions_candidate_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['release_id'], ['candidate_practice_releases.release_id'],
            name='fk_candidate_practice_sessions_release_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['job_id'], ['job_requisitions.job_id'],
            name='fk_candidate_practice_sessions_job_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['job_version_id'], ['job_versions.job_version_id'],
            name='fk_candidate_practice_sessions_job_version_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['ai_set_id'], ['ai_interview_question_sets.ai_set_id'],
            name='fk_candidate_practice_sessions_ai_set_id', ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('session_id')
    )
    op.create_index('ix_candidate_practice_sessions_candidate_id', 'candidate_practice_sessions', ['candidate_id'])
    op.create_index('ix_candidate_practice_sessions_org_id', 'candidate_practice_sessions', ['organization_id'])
    op.create_index('ix_candidate_practice_sessions_release_id', 'candidate_practice_sessions', ['release_id'])
    op.create_index('ix_candidate_practice_sessions_job_id', 'candidate_practice_sessions', ['job_id'])
    op.create_index('ix_candidate_practice_sessions_status', 'candidate_practice_sessions', ['lifecycle_status'])
    op.create_index('ix_candidate_practice_sessions_started_at', 'candidate_practice_sessions', ['started_at'])

    # 2. Create candidate_practice_messages table
    op.create_table(
        'candidate_practice_messages',
        sa.Column('message_id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('sequence_number', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('message_kind', sa.String(length=30), nullable=False),
        sa.Column('source_question_id', sa.Uuid(), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('model_invocation_ref', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "role IN ('INTERVIEWER', 'CANDIDATE')",
            name='ck_candidate_practice_messages_role'
        ),
        sa.CheckConstraint(
            "message_kind IN ('GREETING', 'PRIMARY_QUESTION', 'ANSWER', 'ACKNOWLEDGEMENT', 'FOLLOW_UP')",
            name='ck_candidate_practice_messages_kind'
        ),
        sa.ForeignKeyConstraint(
            ['session_id'], ['candidate_practice_sessions.session_id'],
            name='fk_candidate_practice_messages_session_id', ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('message_id'),
        sa.UniqueConstraint('session_id', 'sequence_number', name='uq_candidate_practice_messages_session_seq')
    )
    op.create_index('ix_candidate_practice_messages_session_id', 'candidate_practice_messages', ['session_id'])
    op.create_index('ix_candidate_practice_messages_seq', 'candidate_practice_messages', ['session_id', 'sequence_number'])

    # 3. Create candidate_practice_mcq_answers table
    op.create_table(
        'candidate_practice_mcq_answers',
        sa.Column('answer_id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('question_id', sa.Uuid(), nullable=False),
        sa.Column('selected_option_id', sa.String(length=10), nullable=False),
        sa.Column('is_correct', sa.Boolean(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ['session_id'], ['candidate_practice_sessions.session_id'],
            name='fk_candidate_practice_mcq_answers_session_id', ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('answer_id'),
        sa.UniqueConstraint('session_id', 'question_id', name='uq_candidate_practice_mcq_answers_session_question')
    )
    op.create_index('ix_candidate_practice_mcq_answers_session_id', 'candidate_practice_mcq_answers', ['session_id'])
    op.create_index('ix_candidate_practice_mcq_answers_question_id', 'candidate_practice_mcq_answers', ['question_id'])


def downgrade() -> None:
    op.drop_table('candidate_practice_mcq_answers')
    op.drop_table('candidate_practice_messages')
    op.drop_table('candidate_practice_sessions')
