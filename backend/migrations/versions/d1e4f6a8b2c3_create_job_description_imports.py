"""create_job_description_imports

Revision ID: d1e4f6a8b2c3
Revises: c9d3e5f7a2b1
Create Date: 2026-09-04 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd1e4f6a8b2c3'
down_revision: Union[str, None] = 'c9d3e5f7a2b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'job_description_imports',
        sa.Column('session_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('job_version_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('expected_revision', sa.Integer(), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_type', sa.String(length=50), nullable=False, server_default='PASTED_TEXT'),
        sa.Column('source_filename', sa.String(length=255), nullable=True),
        sa.Column('source_text', sa.Text(), nullable=False),
        sa.Column('parser_version', sa.String(length=50), nullable=False, server_default='1.0.0'),
        sa.Column('taxonomy_version', sa.String(length=50), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='PENDING_REVIEW'),
        sa.Column('suggestions', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(
            ['created_by_user_id'],
            ['users.user_id'],
            name='fk_job_description_imports_created_by_user_id_users',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['job_id'],
            ['job_requisitions.job_id'],
            name='fk_job_description_imports_job_id_job_requisitions',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['job_version_id'],
            ['job_versions.job_version_id'],
            name='fk_job_description_imports_job_version_id_job_versions',
            ondelete='CASCADE',
        ),
        sa.CheckConstraint(
            "source_type IN ('PASTED_TEXT', 'PDF', 'DOCX')",
            name='ck_job_description_imports_source_type',
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_REVIEW', 'APPLIED', 'DISCARDED')",
            name='ck_job_description_imports_status',
        ),
        sa.CheckConstraint(
            "length(trim(source_text)) > 0",
            name='ck_job_description_imports_source_text_not_empty',
        ),
    )
    op.create_index('ix_job_description_imports_created_by_user_id', 'job_description_imports', ['created_by_user_id'], unique=False)
    op.create_index('ix_job_description_imports_job_id', 'job_description_imports', ['job_id'], unique=False)
    op.create_index('ix_job_description_imports_job_version_id', 'job_description_imports', ['job_version_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_job_description_imports_job_version_id', table_name='job_description_imports')
    op.drop_index('ix_job_description_imports_job_id', table_name='job_description_imports')
    op.drop_index('ix_job_description_imports_created_by_user_id', table_name='job_description_imports')
    op.drop_table('job_description_imports')
