"""add is_archived to provider_profiles

Revision ID: c2f8c4604f59
Revises: c4a1d8e2f5b7
Create Date: 2026-09-17 17:50:28.967348

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2f8c4604f59'
down_revision: Union[str, None] = 'c4a1d8e2f5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('provider_profiles', sa.Column('is_archived', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    op.drop_column('provider_profiles', 'is_archived')
