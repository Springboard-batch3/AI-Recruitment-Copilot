"""Add soft delete columns to job_requisitions

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-09-21 17:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e1f2a3b4c5d6'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('job_requisitions', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('job_requisitions', sa.Column('deleted_by_user_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_job_requisitions_deleted_by_user_id_users',
        'job_requisitions',
        'users',
        ['deleted_by_user_id'],
        ['user_id'],
        ondelete='SET NULL'
    )
    op.create_index('ix_job_requisitions_deleted_at', 'job_requisitions', ['deleted_at'])


def downgrade() -> None:
    op.drop_index('ix_job_requisitions_deleted_at', table_name='job_requisitions')
    op.drop_constraint('fk_job_requisitions_deleted_by_user_id_users', 'job_requisitions', type_='foreignkey')
    op.drop_column('job_requisitions', 'deleted_by_user_id')
    op.drop_column('job_requisitions', 'deleted_at')
