"""Phase 3G.2 Candidate Practice Answer Reviews and Segments

Revision ID: h1a2b3c4d5e6
Revises: g1a2b3c4d5e6
Create Date: 2026-09-24 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'h1a2b3c4d5e6'
down_revision: Union[str, None] = 'g1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

    # 1. Create candidate_practice_answer_segments table
    op.create_table(
        'candidate_practice_answer_segments',
        sa.Column('segment_id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('answer_id', sa.Uuid(), nullable=False),
        sa.Column('question_id', sa.Uuid(), nullable=False),
        sa.Column('sequence_order', sa.Integer(), nullable=False),
        sa.Column('start_char', sa.Integer(), nullable=False),
        sa.Column('end_char', sa.Integer(), nullable=False),
        sa.Column('segment_text', sa.Text(), nullable=False),
        sa.Column('segmentation_version', sa.String(length=32), server_default=sa.text("'1.0.0'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ['answer_id'], ['candidate_practice_messages.message_id'],
            name='fk_cand_practice_segments_answer_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['session_id'], ['candidate_practice_sessions.session_id'],
            name='fk_cand_practice_segments_session_id', ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('segment_id'),
        sa.UniqueConstraint('answer_id', 'sequence_order', name='uq_candidate_practice_segments_answer_seq')
    )
    op.create_index('ix_cand_practice_segments_answer_id', 'candidate_practice_answer_segments', ['answer_id'])
    op.create_index('ix_cand_practice_segments_session_id', 'candidate_practice_answer_segments', ['session_id'])
    op.create_index('ix_cand_practice_segments_question_id', 'candidate_practice_answer_segments', ['question_id'])

    # 2. Create candidate_practice_answer_reviews table
    op.create_table(
        'candidate_practice_answer_reviews',
        sa.Column('review_id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('question_id', sa.Uuid(), nullable=False),
        sa.Column('answer_id', sa.Uuid(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('schema_version', sa.String(length=50), server_default=sa.text("'practice-answer-review.v1'"), nullable=False),
        sa.Column('lifecycle_status', sa.String(length=50), server_default=sa.text("'REVIEW_READY'"), nullable=False),
        sa.Column('answer_status', sa.String(length=50), server_default=sa.text("'ASSESSABLE'"), nullable=False),
        sa.Column('acknowledgement', sa.Text(), nullable=False),
        sa.Column('strengths', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('improvement_areas', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('next_steps', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('stronger_structure', sa.Text(), nullable=True),
        sa.Column('dimension_levels', json_type, server_default=sa.text("'{}'"), nullable=False),
        sa.Column('follow_up_needed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('follow_up_question', sa.Text(), nullable=True),
        sa.Column('uncertainty_codes', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('provider_class', sa.String(length=50), nullable=True),
        sa.Column('model_identifier', sa.String(length=100), nullable=True),
        sa.Column('prompt_template_version', sa.String(length=50), nullable=True),
        sa.Column('guardrail_policy_version', sa.String(length=50), nullable=True),
        sa.Column('validation_status', sa.String(length=30), server_default=sa.text("'PASSED'"), nullable=False),
        sa.Column('retry_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['answer_id'], ['candidate_practice_messages.message_id'],
            name='fk_cand_practice_reviews_answer_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['session_id'], ['candidate_practice_sessions.session_id'],
            name='fk_cand_practice_reviews_session_id', ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('review_id'),
        sa.UniqueConstraint('answer_id', name='uq_candidate_practice_answer_reviews_answer_id'),
        sa.UniqueConstraint('session_id', 'idempotency_key', name='uq_candidate_practice_answer_reviews_idempotency')
    )
    op.create_index('ix_cand_practice_reviews_session_id', 'candidate_practice_answer_reviews', ['session_id'])
    op.create_index('ix_cand_practice_reviews_question_id', 'candidate_practice_answer_reviews', ['question_id'])
    op.create_index('ix_cand_practice_reviews_answer_id', 'candidate_practice_answer_reviews', ['answer_id'])
    op.create_index('ix_cand_practice_reviews_status', 'candidate_practice_answer_reviews', ['lifecycle_status'])


def downgrade() -> None:
    op.drop_table('candidate_practice_answer_reviews')
    op.drop_table('candidate_practice_answer_segments')
