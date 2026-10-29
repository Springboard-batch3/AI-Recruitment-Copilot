"""Phase 3G Live Sequential AI Interview Lifecycles

Revision ID: j1a2b3c4d5e6
Revises: i1a2b3c4d5e6
Create Date: 2026-09-24 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'j1a2b3c4d5e6'
down_revision: Union[str, None] = 'i1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop existing check constraint and add updated lifecycle statuses
    op.drop_constraint('ck_candidate_practice_sessions_lifecycle', 'candidate_practice_sessions', type_='check')
    op.create_check_constraint(
        'ck_candidate_practice_sessions_lifecycle',
        'candidate_practice_sessions',
        "lifecycle_status IN ('IN_PROGRESS', 'QUESTION_GENERATING', 'AWAITING_ANSWER', 'ANSWER_SAVING', 'QUESTION_GENERATION_FAILED_RETRYABLE', 'FINAL_ANSWER_SAVING', 'FINAL_REVIEW_GENERATING', 'FINAL_REVIEW_FAILED_RETRYABLE', 'COMPLETED', 'ABANDONED', 'EXPIRED', 'REVOKED')"
    )


def downgrade() -> None:
    op.drop_constraint('ck_candidate_practice_sessions_lifecycle', 'candidate_practice_sessions', type_='check')
    op.create_check_constraint(
        'ck_candidate_practice_sessions_lifecycle',
        'candidate_practice_sessions',
        "lifecycle_status IN ('IN_PROGRESS', 'COMPLETED', 'ABANDONED', 'EXPIRED', 'REVOKED')"
    )
