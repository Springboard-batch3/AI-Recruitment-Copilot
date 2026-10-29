"""upgrade_taxonomy_onet_31_fields

Revision ID: x1b2c3d4e5f6
Revises: w1a2b3c4d5e6
Create Date: 2026-10-03 16:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'x1b2c3d4e5f6'
down_revision: Union[str, None] = 'w1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add source_family to skill_concepts
    op.add_column(
        'skill_concepts',
        sa.Column('source_family', sa.String(length=50), server_default=sa.text("'software'"), nullable=False)
    )
    op.create_index('ix_skill_concepts_source_family', 'skill_concepts', ['source_family'], unique=False)

    # 2. Add source_family, importance_value, level_value, recommend_suppress, not_relevant to occupation_skill_links
    op.add_column(
        'occupation_skill_links',
        sa.Column('source_family', sa.String(length=50), server_default=sa.text("'software'"), nullable=False)
    )
    op.add_column(
        'occupation_skill_links',
        sa.Column('importance_value', sa.Float(), nullable=True)
    )
    op.add_column(
        'occupation_skill_links',
        sa.Column('level_value', sa.Float(), nullable=True)
    )
    op.add_column(
        'occupation_skill_links',
        sa.Column('recommend_suppress', sa.Boolean(), server_default=sa.text('false'), nullable=False)
    )
    op.add_column(
        'occupation_skill_links',
        sa.Column('not_relevant', sa.Boolean(), server_default=sa.text('false'), nullable=False)
    )


def downgrade() -> None:
    op.drop_column('occupation_skill_links', 'not_relevant')
    op.drop_column('occupation_skill_links', 'recommend_suppress')
    op.drop_column('occupation_skill_links', 'level_value')
    op.drop_column('occupation_skill_links', 'importance_value')
    op.drop_column('occupation_skill_links', 'source_family')

    op.drop_index('ix_skill_concepts_source_family', table_name='skill_concepts')
    op.drop_column('skill_concepts', 'source_family')
