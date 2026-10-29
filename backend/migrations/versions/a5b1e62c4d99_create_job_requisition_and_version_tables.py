"""create_job_requisition_and_version_tables

Revision ID: a5b1e62c4d99
Revises: e1a05c742301
Create Date: 2026-09-02 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a5b1e62c4d99'
down_revision: Union[str, None] = 'e1a05c742301'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create job_requisitions table
    op.create_table(
        'job_requisitions',
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(length=50), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column('idempotency_key', sa.String(length=255), nullable=True),
        sa.Column('idempotency_payload_hash', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name='ck_job_requisitions_status'),
        sa.ForeignKeyConstraint(
            ['created_by_user_id'],
            ['users.user_id'],
            name='fk_job_requisitions_created_by_user_id_users',
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('job_id'),
    )
    op.create_index('ix_job_requisitions_created_by_user_id', 'job_requisitions', ['created_by_user_id'], unique=False)
    op.create_index('ix_job_requisitions_status', 'job_requisitions', ['status'], unique=False)
    op.create_index('ix_job_requisitions_created_by_updated_at', 'job_requisitions', ['created_by_user_id', 'updated_at'], unique=False)
    op.create_index('ix_job_requisitions_idempotency_key', 'job_requisitions', ['idempotency_key'], unique=False)

    # 2. Create job_versions table
    op.create_table(
        'job_versions',
        sa.Column('job_version_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('version_number', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('version_status', sa.String(length=50), server_default=sa.text("'DRAFT'"), nullable=False),
        sa.Column('revision', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('department', sa.String(length=100), nullable=True),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('employment_type', sa.String(length=50), nullable=True),
        sa.Column('workplace_type', sa.String(length=50), nullable=True),
        sa.Column('openings_count', sa.Integer(), nullable=True),
        sa.Column('internal_reference', sa.String(length=100), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('responsibilities', sa.Text(), nullable=True),
        sa.Column('job_description_text', sa.Text(), nullable=True),
        sa.Column('taxonomy_version', sa.String(length=50), nullable=True),
        sa.Column('parser_version', sa.String(length=50), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('approved_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('version_number >= 1', name='ck_job_versions_version_number_positive'),
        sa.CheckConstraint('revision >= 1', name='ck_job_versions_revision_positive'),
        sa.CheckConstraint('openings_count IS NULL OR openings_count > 0', name='ck_job_versions_openings_count_positive'),
        sa.CheckConstraint('length(trim(title)) > 0', name='ck_job_versions_title_not_empty'),
        sa.CheckConstraint("version_status IN ('DRAFT', 'APPROVED')", name='ck_job_versions_version_status'),
        sa.CheckConstraint(
            "employment_type IS NULL OR employment_type IN ('FULL_TIME', 'PART_TIME', 'CONTRACT', 'INTERNSHIP', 'TEMPORARY')",
            name='ck_job_versions_employment_type'
        ),
        sa.CheckConstraint(
            "workplace_type IS NULL OR workplace_type IN ('ON_SITE', 'HYBRID', 'REMOTE')",
            name='ck_job_versions_workplace_type'
        ),
        sa.CheckConstraint('summary IS NULL OR length(summary) <= 10000', name='ck_job_versions_summary_length'),
        sa.CheckConstraint('responsibilities IS NULL OR length(responsibilities) <= 50000', name='ck_job_versions_responsibilities_length'),
        sa.CheckConstraint('job_description_text IS NULL OR length(job_description_text) <= 100000', name='ck_job_versions_job_description_text_length'),
        sa.ForeignKeyConstraint(
            ['job_id'],
            ['job_requisitions.job_id'],
            name='fk_job_versions_job_id_job_requisitions',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['created_by_user_id'],
            ['users.user_id'],
            name='fk_job_versions_created_by_user_id_users',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['approved_by_user_id'],
            ['users.user_id'],
            name='fk_job_versions_approved_by_user_id_users',
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('job_version_id'),
        sa.UniqueConstraint('job_id', 'version_number', name='uq_job_versions_job_id_version_number'),
    )
    op.create_index('ix_job_versions_job_id', 'job_versions', ['job_id'], unique=False)
    op.create_index('ix_job_versions_created_by_user_id', 'job_versions', ['created_by_user_id'], unique=False)
    op.create_index('ix_job_versions_version_status', 'job_versions', ['version_status'], unique=False)


def downgrade() -> None:
    # 1. Drop job_versions table
    op.drop_index('ix_job_versions_version_status', table_name='job_versions')
    op.drop_index('ix_job_versions_created_by_user_id', table_name='job_versions')
    op.drop_index('ix_job_versions_job_id', table_name='job_versions')
    op.drop_table('job_versions')

    # 2. Drop job_requisitions table
    op.drop_index('ix_job_requisitions_idempotency_key', table_name='job_requisitions')
    op.drop_index('ix_job_requisitions_created_by_updated_at', table_name='job_requisitions')
    op.drop_index('ix_job_requisitions_status', table_name='job_requisitions')
    op.drop_index('ix_job_requisitions_created_by_user_id', table_name='job_requisitions')
    op.drop_table('job_requisitions')
