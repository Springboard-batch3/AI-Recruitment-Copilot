"""create_profile_correction_tables

Revision ID: d92e10a8b71f
Revises: c83f120ab59e
Create Date: 2026-08-30 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd92e10a8b71f'
down_revision: Union[str, None] = 'c83f120ab59e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update candidate_identities: drop uq_candidate_identities_artifact_id constraint
    op.drop_constraint('uq_candidate_identities_artifact_id', 'candidate_identities', type_='unique')

    # 2. Update candidate_profiles:
    # Drop previous unique constraint on artifact_id, parser_pipeline_version, schema_version
    op.drop_constraint('uq_candidate_profiles_artifact_pipeline_schema', 'candidate_profiles', type_='unique')
    
    # Add new unique constraint on (artifact_id, profile_version)
    op.create_unique_constraint(
        'uq_candidate_profiles_artifact_id_profile_version',
        'candidate_profiles',
        ['artifact_id', 'profile_version']
    )

    # Add lineage and provenance columns
    op.add_column(
        'candidate_profiles',
        sa.Column('parent_profile_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        'candidate_profiles',
        sa.Column('parent_profile_version', sa.Integer(), nullable=True)
    )
    op.add_column(
        'candidate_profiles',
        sa.Column('origin_profile_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        'candidate_profiles',
        sa.Column('provenance', sa.String(length=50), server_default=sa.text("'PARSER_EXTRACTED'"), nullable=False)
    )
    op.add_column(
        'candidate_profiles',
        sa.Column('correction_batch_id', postgresql.UUID(as_uuid=True), nullable=True)
    )

    op.create_foreign_key(
        'fk_candidate_profiles_parent_profile_id',
        'candidate_profiles',
        'candidate_profiles',
        ['parent_profile_id'],
        ['profile_id'],
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_candidate_profiles_origin_profile_id',
        'candidate_profiles',
        'candidate_profiles',
        ['origin_profile_id'],
        ['profile_id'],
        ondelete='SET NULL'
    )

    op.create_index('ix_candidate_profiles_parent_profile_id', 'candidate_profiles', ['parent_profile_id'], unique=False)
    op.create_index('ix_candidate_profiles_origin_profile_id', 'candidate_profiles', ['origin_profile_id'], unique=False)

    # 3. Create profile_corrections table
    op.create_table(
        'profile_corrections',
        sa.Column('correction_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('artifact_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('base_profile_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('base_profile_version', sa.Integer(), nullable=False),
        sa.Column('new_profile_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('new_profile_version', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=255), nullable=False),
        sa.Column('reason_category', sa.String(length=50), nullable=False),
        sa.Column('reviewer_note', sa.Text(), nullable=True),
        sa.Column('operations_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('actor_context', sa.String(length=100), server_default=sa.text("'LOCAL_DEVELOPMENT_REVIEWER'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['artifact_id'],
            ['resume_artifacts.artifact_id'],
            name='fk_profile_corrections_artifact_id_resume_artifacts',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['base_profile_id'],
            ['candidate_profiles.profile_id'],
            name='fk_profile_corrections_base_profile_id_candidate_profiles',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['new_profile_id'],
            ['candidate_profiles.profile_id'],
            name='fk_profile_corrections_new_profile_id_candidate_profiles',
            ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('correction_id'),
        sa.UniqueConstraint('artifact_id', 'idempotency_key', name='uq_profile_corrections_artifact_id_idempotency_key'),
    )
    op.create_index('ix_profile_corrections_artifact_id', 'profile_corrections', ['artifact_id'], unique=False)
    op.create_index('ix_profile_corrections_base_profile_id', 'profile_corrections', ['base_profile_id'], unique=False)
    op.create_index('ix_profile_corrections_new_profile_id', 'profile_corrections', ['new_profile_id'], unique=False)


def downgrade() -> None:
    # 1. Drop profile_corrections table
    op.drop_index('ix_profile_corrections_new_profile_id', table_name='profile_corrections')
    op.drop_index('ix_profile_corrections_base_profile_id', table_name='profile_corrections')
    op.drop_index('ix_profile_corrections_artifact_id', table_name='profile_corrections')
    op.drop_table('profile_corrections')

    # 2. Revert candidate_profiles
    op.drop_index('ix_candidate_profiles_origin_profile_id', table_name='candidate_profiles')
    op.drop_index('ix_candidate_profiles_parent_profile_id', table_name='candidate_profiles')
    op.drop_constraint('fk_candidate_profiles_origin_profile_id', 'candidate_profiles', type_='foreignkey')
    op.drop_constraint('fk_candidate_profiles_parent_profile_id', 'candidate_profiles', type_='foreignkey')
    op.drop_column('candidate_profiles', 'correction_batch_id')
    op.drop_column('candidate_profiles', 'provenance')
    op.drop_column('candidate_profiles', 'origin_profile_id')
    op.drop_column('candidate_profiles', 'parent_profile_version')
    op.drop_column('candidate_profiles', 'parent_profile_id')
    op.drop_constraint('uq_candidate_profiles_artifact_id_profile_version', 'candidate_profiles', type_='unique')
    op.create_unique_constraint(
        'uq_candidate_profiles_artifact_pipeline_schema',
        'candidate_profiles',
        ['artifact_id', 'parser_pipeline_version', 'schema_version']
    )

    # 3. Revert candidate_identities
    op.create_unique_constraint('uq_candidate_identities_artifact_id', 'candidate_identities', ['artifact_id'])
