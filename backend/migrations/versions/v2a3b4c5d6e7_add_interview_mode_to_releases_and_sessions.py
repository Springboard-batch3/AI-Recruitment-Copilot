"""Add interview_mode to releases and sessions for Phase 4D.2

Revision ID: v2a3b4c5d6e7
Revises: v1a2b3c4d5e6
Create Date: 2026-09-28 20:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'v2a3b4c5d6e7'
down_revision: Union[str, None] = 'v1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add column interview_mode to candidate_practice_releases with server_default 'TEXT'
    op.add_column(
        'candidate_practice_releases',
        sa.Column(
            'interview_mode',
            sa.String(length=32),
            server_default=sa.text("'TEXT'"),
            nullable=False,
        ),
    )
    
    # Backfill existing releases
    op.execute("UPDATE candidate_practice_releases SET interview_mode = 'TEXT' WHERE interview_mode IS NULL")
    
    op.create_index(
        'ix_candidate_practice_releases_interview_mode',
        'candidate_practice_releases',
        ['interview_mode'],
        unique=False,
    )

    # 2. Add column interview_mode to candidate_interview_sessions with server_default 'TEXT'
    op.add_column(
        'candidate_interview_sessions',
        sa.Column(
            'interview_mode',
            sa.String(length=32),
            server_default=sa.text("'TEXT'"),
            nullable=False,
        ),
    )
    
    # Backfill existing sessions
    op.execute("UPDATE candidate_interview_sessions SET interview_mode = 'TEXT' WHERE interview_mode IS NULL")
    
    op.create_index(
        'ix_candidate_interview_sessions_interview_mode',
        'candidate_interview_sessions',
        ['interview_mode'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_candidate_interview_sessions_interview_mode', table_name='candidate_interview_sessions')
    op.drop_column('candidate_interview_sessions', 'interview_mode')
    op.drop_index('ix_candidate_practice_releases_interview_mode', table_name='candidate_practice_releases')
    op.drop_column('candidate_practice_releases', 'interview_mode')
