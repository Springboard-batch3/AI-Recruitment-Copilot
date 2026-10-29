"""Add LOCAL_FOLDER_DEMO to ATS connections adapter_type check constraint

Revision ID: d1e2f3a4b5c6
Revises: c1d2e3f4a5b6
Create Date: 2026-09-21 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1e2f3a4b5c6'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('ck_ats_connections_adapter_type', 'ats_connections', type_='check')
    op.create_check_constraint(
        'ck_ats_connections_adapter_type',
        'ats_connections',
        "adapter_type IN ('MOCK', 'CSV', 'JSON', 'LOCAL_FOLDER_DEMO')"
    )


def downgrade() -> None:
    op.drop_constraint('ck_ats_connections_adapter_type', 'ats_connections', type_='check')
    op.create_check_constraint(
        'ck_ats_connections_adapter_type',
        'ats_connections',
        "adapter_type IN ('MOCK', 'CSV', 'JSON')"
    )
