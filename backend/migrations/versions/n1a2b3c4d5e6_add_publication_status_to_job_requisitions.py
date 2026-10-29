"""Add publication status and release tracking to job_requisitions

Revision ID: n1a2b3c4d5e6
Revises: m1a2b3c4d5e6
Create Date: 2026-09-26 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'n1a2b3c4d5e6'
down_revision: Union[str, None] = 'm1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'job_requisitions',
        sa.Column(
            'publication_status',
            sa.String(length=30),
            server_default=sa.text("'NOT_RELEASED'"),
            nullable=False,
        ),
    )
    op.add_column(
        'job_requisitions',
        sa.Column(
            'released_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        'job_requisitions',
        sa.Column(
            'released_by_user_id',
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        'job_requisitions',
        sa.Column(
            'released_job_version_id',
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        'fk_job_requisitions_released_by_user_id_users',
        'job_requisitions',
        'users',
        ['released_by_user_id'],
        ['user_id'],
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_job_requisitions_released_job_version_id_versions',
        'job_requisitions',
        'job_versions',
        ['released_job_version_id'],
        ['job_version_id'],
        ondelete='SET NULL',
    )
    op.create_check_constraint(
        'ck_job_requisitions_publication_status',
        'job_requisitions',
        "publication_status IN ('NOT_RELEASED', 'RELEASED', 'CLOSED')",
    )
    op.create_index(
        'ix_job_requisitions_publication_status',
        'job_requisitions',
        ['publication_status'],
        unique=False,
    )
    op.create_index(
        'ix_job_requisitions_released_job_version_id',
        'job_requisitions',
        ['released_job_version_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_job_requisitions_released_job_version_id', table_name='job_requisitions')
    op.drop_index('ix_job_requisitions_publication_status', table_name='job_requisitions')
    op.drop_constraint('ck_job_requisitions_publication_status', table_name='job_requisitions', type_='check')
    op.drop_constraint('fk_job_requisitions_released_job_version_id_versions', table_name='job_requisitions', type_='foreignkey')
    op.drop_constraint('fk_job_requisitions_released_by_user_id_users', table_name='job_requisitions', type_='foreignkey')
    op.drop_column('job_requisitions', 'released_job_version_id')
    op.drop_column('job_requisitions', 'released_by_user_id')
    op.drop_column('job_requisitions', 'released_at')
    op.drop_column('job_requisitions', 'publication_status')
