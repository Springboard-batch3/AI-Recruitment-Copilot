"""Create candidate_applications table for Phase 4B.4

Revision ID: l1a2b3c4d5e6
Revises: k1a2b3c4d5e6
Create Date: 2026-09-26 17:37:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'l1a2b3c4d5e6'
down_revision: Union[str, None] = 'k1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'candidate_applications',
        sa.Column('application_id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('binding_id', sa.Uuid(), nullable=False),
        sa.Column('candidate_id', sa.Uuid(), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('job_version_id', sa.Uuid(), nullable=False),
        sa.Column('job_revision', sa.Integer(), nullable=False),
        sa.Column('profile_id', sa.Uuid(), nullable=False),
        sa.Column('profile_version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=30), server_default=sa.text("'SUBMITTED'"), nullable=False),
        sa.Column('submission_source', sa.String(length=50), server_default=sa.text("'CANDIDATE_PORTAL'"), nullable=False),
        sa.Column('idempotency_key', sa.String(length=255), nullable=True),
        sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('SUBMITTED', 'WITHDRAWN')", name='ck_candidate_applications_status'),
        sa.CheckConstraint('job_revision >= 1', name='ck_candidate_applications_job_revision_positive'),
        sa.CheckConstraint('profile_version >= 1', name='ck_candidate_applications_profile_version_positive'),
        sa.CheckConstraint(
            "(status = 'WITHDRAWN' AND withdrawn_at IS NOT NULL) OR (status = 'SUBMITTED' AND withdrawn_at IS NULL)",
            name='ck_candidate_applications_withdrawn_timestamp_consistency',
        ),
        sa.ForeignKeyConstraint(
            ['binding_id'],
            ['candidate_account_bindings.binding_id'],
            name='fk_candidate_applications_binding_id_bindings',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['candidate_id'],
            ['candidate_identities.identity_id'],
            name='fk_candidate_applications_candidate_id_identities',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['job_id'],
            ['job_requisitions.job_id'],
            name='fk_candidate_applications_job_id_requisitions',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['job_version_id'],
            ['job_versions.job_version_id'],
            name='fk_candidate_applications_job_version_id_versions',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['profile_id'],
            ['candidate_profiles.profile_id'],
            name='fk_candidate_applications_profile_id_profiles',
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('application_id'),
    )

    op.create_index('ix_candidate_applications_binding_id', 'candidate_applications', ['binding_id'], unique=False)
    op.create_index('ix_candidate_applications_candidate_id', 'candidate_applications', ['candidate_id'], unique=False)
    op.create_index('ix_candidate_applications_org_id', 'candidate_applications', ['organization_id'], unique=False)
    op.create_index('ix_candidate_applications_job_id', 'candidate_applications', ['job_id'], unique=False)
    op.create_index('ix_candidate_applications_status', 'candidate_applications', ['status'], unique=False)
    op.create_index(
        'ix_candidate_applications_idempotency_key',
        'candidate_applications',
        ['binding_id', 'idempotency_key'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('ix_candidate_applications_idempotency_key', table_name='candidate_applications')
    op.drop_index('ix_candidate_applications_status', table_name='candidate_applications')
    op.drop_index('ix_candidate_applications_job_id', table_name='candidate_applications')
    op.drop_index('ix_candidate_applications_org_id', table_name='candidate_applications')
    op.drop_index('ix_candidate_applications_candidate_id', table_name='candidate_applications')
    op.drop_index('ix_candidate_applications_binding_id', table_name='candidate_applications')
    op.drop_table('candidate_applications')
