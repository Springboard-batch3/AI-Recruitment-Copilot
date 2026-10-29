"""Add match_result_id to candidate_practice_releases for Phase 4C.1

Revision ID: r1a2b3c4d5e6
Revises: q1a2b3c4d5e6
Create Date: 2026-09-27 16:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'r1a2b3c4d5e6'
down_revision: Union[str, None] = 'q1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'candidate_practice_releases',
        sa.Column('match_result_id', sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        'fk_candidate_practice_releases_match_result_id',
        'candidate_practice_releases',
        'match_results',
        ['match_result_id'],
        ['result_id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_candidate_practice_releases_match_result_id',
        'candidate_practice_releases',
        ['match_result_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_candidate_practice_releases_match_result_id', table_name='candidate_practice_releases')
    op.drop_constraint('fk_candidate_practice_releases_match_result_id', 'candidate_practice_releases', type_='foreignkey')
    op.drop_column('candidate_practice_releases', 'match_result_id')
