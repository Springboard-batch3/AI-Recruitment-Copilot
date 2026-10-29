"""Phase 3G Alter Candidate Practice Sessions Column Lengths

Revision ID: k1a2b3c4d5e6
Revises: j1a2b3c4d5e6
Create Date: 2026-09-24 21:57:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'k1a2b3c4d5e6'
down_revision: Union[str, None] = 'j1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'candidate_practice_sessions',
        'lifecycle_status',
        existing_type=sa.String(length=30),
        type_=sa.String(length=50),
        existing_nullable=False,
    )
    op.alter_column(
        'candidate_practice_sessions',
        'current_section',
        existing_type=sa.String(length=30),
        type_=sa.String(length=50),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'candidate_practice_sessions',
        'current_section',
        existing_type=sa.String(length=50),
        type_=sa.String(length=30),
        existing_nullable=False,
    )
    op.alter_column(
        'candidate_practice_sessions',
        'lifecycle_status',
        existing_type=sa.String(length=50),
        type_=sa.String(length=30),
        existing_nullable=False,
    )
