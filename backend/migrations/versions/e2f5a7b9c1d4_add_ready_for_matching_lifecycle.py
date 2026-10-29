"""add_ready_for_matching_lifecycle

Revision ID: e2f5a7b9c1d4
Revises: d1e4f6a8b2c3
Create Date: 2026-09-04 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e2f5a7b9c1d4'
down_revision: Union[str, None] = 'd1e4f6a8b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add ready_by_user_id and ready_at columns to job_versions
    op.add_column(
        'job_versions',
        sa.Column('ready_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        'job_versions',
        sa.Column('ready_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_job_versions_ready_by_user_id_users',
        'job_versions',
        'users',
        ['ready_by_user_id'],
        ['user_id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_job_versions_ready_by_user_id',
        'job_versions',
        ['ready_by_user_id'],
        unique=False,
    )

    # 2. Update version_status CheckConstraint to allow 'READY_FOR_MATCHING'
    op.drop_constraint('ck_job_versions_version_status', 'job_versions', type_='check')
    op.create_check_constraint(
        'ck_job_versions_version_status',
        'job_versions',
        "version_status IN ('DRAFT', 'READY_FOR_MATCHING', 'APPROVED')",
    )


def downgrade() -> None:
    # 1. Revert CheckConstraint
    op.drop_constraint('ck_job_versions_version_status', 'job_versions', type_='check')
    op.create_check_constraint(
        'ck_job_versions_version_status',
        'job_versions',
        "version_status IN ('DRAFT', 'APPROVED')",
    )

    # 2. Drop columns
    op.drop_index('ix_job_versions_ready_by_user_id', table_name='job_versions')
    op.drop_constraint('fk_job_versions_ready_by_user_id_users', 'job_versions', type_='foreignkey')
    op.drop_column('job_versions', 'ready_at')
    op.drop_column('job_versions', 'ready_by_user_id')
