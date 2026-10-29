"""Add interview_mode to ai_interview_question_sets for Phase 4D.1

Revision ID: v1a2b3c4d5e6
Revises: u1a2b3c4d5e6
Create Date: 2026-09-28 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'v1a2b3c4d5e6'
down_revision: Union[str, None] = 'u1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add column interview_mode with server_default 'TEXT'
    op.add_column(
        'ai_interview_question_sets',
        sa.Column(
            'interview_mode',
            sa.String(length=32),
            server_default=sa.text("'TEXT'"),
            nullable=False,
        ),
    )
    
    # 2. Backfill any existing rows to 'TEXT' safely
    op.execute("UPDATE ai_interview_question_sets SET interview_mode = 'TEXT' WHERE interview_mode IS NULL")
    
    # 3. Create index on interview_mode
    op.create_index(
        'ix_ai_question_sets_interview_mode',
        'ai_interview_question_sets',
        ['interview_mode'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_ai_question_sets_interview_mode', table_name='ai_interview_question_sets')
    op.drop_column('ai_interview_question_sets', 'interview_mode')
