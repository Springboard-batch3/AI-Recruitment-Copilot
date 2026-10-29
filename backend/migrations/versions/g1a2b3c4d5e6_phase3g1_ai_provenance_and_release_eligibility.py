"""Phase 3G.1 AI-only question provenance and release eligibility

Revision ID: g1a2b3c4d5e6
Revises: f5a6b7c8d9e0
Create Date: 2026-09-22 21:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'g1a2b3c4d5e6'
down_revision: Union[str, None] = 'f5a6b7c8d9e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add provenance columns to ai_interview_question_sets
    op.add_column('ai_interview_question_sets', sa.Column('provider_class', sa.String(length=50), nullable=True, server_default=sa.text("'LOCAL_OPENAI_COMPATIBLE'")))
    op.add_column('ai_interview_question_sets', sa.Column('guardrail_policy_version', sa.String(length=50), nullable=True, server_default=sa.text("'1.0.0'")))
    op.add_column('ai_interview_question_sets', sa.Column('schema_version', sa.String(length=50), nullable=True, server_default=sa.text("'practice-question.v1'")))

    # 2. Add indexes for release eligibility lookup
    op.create_index('ix_ai_question_sets_is_immutable', 'ai_interview_question_sets', ['is_immutable'], unique=False)

    # 3. Safe historic data backfill:
    # Historic rows with NULL or ambiguous origins remain honest legacy records
    # We do not delete or fabricate AI provenance on historic records.


def downgrade() -> None:
    op.drop_index('ix_ai_question_sets_is_immutable', table_name='ai_interview_question_sets')
    op.drop_column('ai_interview_question_sets', 'schema_version')
    op.drop_column('ai_interview_question_sets', 'guardrail_policy_version')
    op.drop_column('ai_interview_question_sets', 'provider_class')
