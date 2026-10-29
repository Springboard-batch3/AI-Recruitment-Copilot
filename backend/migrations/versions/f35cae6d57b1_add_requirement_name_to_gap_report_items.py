"""add_requirement_name_to_gap_report_items

Revision ID: f35cae6d57b1
Revises: a7b3c9d1e5f2
Create Date: 2026-09-10 15:41:10.618501

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f35cae6d57b1'
down_revision: Union[str, None] = 'a7b3c9d1e5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'gap_report_items',
        sa.Column(
            'requirement_name',
            sa.String(length=255),
            nullable=False,
            server_default=sa.text("'Requirement'"),
        ),
    )


def downgrade() -> None:
    op.drop_column('gap_report_items', 'requirement_name')
