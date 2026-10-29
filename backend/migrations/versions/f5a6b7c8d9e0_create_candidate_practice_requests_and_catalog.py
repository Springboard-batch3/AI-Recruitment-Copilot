"""Create candidate practice requests and practice catalog tables

Revision ID: f5a6b7c8d9e0
Revises: f4a5b6c7d8e9
Create Date: 2026-09-22 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f5a6b7c8d9e0'
down_revision: Union[str, None] = 'f4a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

    # 1. Create candidate_practice_requests table
    op.create_table(
        'candidate_practice_requests',
        sa.Column('request_id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('candidate_id', sa.Uuid(), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('job_version_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.String(length=30), server_default=sa.text("'REQUESTED'"), nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('processed_by_recruiter_id', sa.Uuid(), nullable=True),
        sa.Column('resulting_release_id', sa.Uuid(), nullable=True),
        sa.Column('decline_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('REQUESTED', 'RELEASED', 'DECLINED', 'WITHDRAWN')",
            name='ck_candidate_practice_requests_status'
        ),
        sa.ForeignKeyConstraint(
            ['candidate_id'], ['candidate_identities.identity_id'],
            name='fk_candidate_practice_requests_candidate_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['job_id'], ['job_requisitions.job_id'],
            name='fk_candidate_practice_requests_job_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['job_version_id'], ['job_versions.job_version_id'],
            name='fk_candidate_practice_requests_job_version_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['processed_by_recruiter_id'], ['users.user_id'],
            name='fk_candidate_practice_requests_processed_by', ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['resulting_release_id'], ['candidate_practice_releases.release_id'],
            name='fk_candidate_practice_requests_resulting_release_id', ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('request_id')
    )
    op.create_index('ix_candidate_practice_requests_candidate_id', 'candidate_practice_requests', ['candidate_id'])
    op.create_index('ix_candidate_practice_requests_org_id', 'candidate_practice_requests', ['organization_id'])
    op.create_index('ix_candidate_practice_requests_job_id', 'candidate_practice_requests', ['job_id'])
    op.create_index('ix_candidate_practice_requests_job_version_id', 'candidate_practice_requests', ['job_version_id'])
    op.create_index('ix_candidate_practice_requests_status', 'candidate_practice_requests', ['status'])

    # 2. Create job_practice_catalogs table
    op.create_table(
        'job_practice_catalogs',
        sa.Column('catalog_id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('job_version_id', sa.Uuid(), nullable=False),
        sa.Column('safe_title', sa.String(length=255), nullable=False),
        sa.Column('safe_summary', sa.Text(), nullable=True),
        sa.Column('skill_labels', json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column('is_published', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('published_by_user_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ['job_id'], ['job_requisitions.job_id'],
            name='fk_job_practice_catalogs_job_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['job_version_id'], ['job_versions.job_version_id'],
            name='fk_job_practice_catalogs_job_version_id', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['published_by_user_id'], ['users.user_id'],
            name='fk_job_practice_catalogs_published_by', ondelete='RESTRICT'
        ),
        sa.PrimaryKeyConstraint('catalog_id'),
        sa.UniqueConstraint('job_version_id', name='uq_job_practice_catalogs_job_version_id')
    )
    op.create_index('ix_job_practice_catalogs_org_id', 'job_practice_catalogs', ['organization_id'])
    op.create_index('ix_job_practice_catalogs_job_id', 'job_practice_catalogs', ['job_id'])
    op.create_index('ix_job_practice_catalogs_is_published', 'job_practice_catalogs', ['is_published'])


def downgrade() -> None:
    op.drop_table('job_practice_catalogs')
    op.drop_table('candidate_practice_requests')
