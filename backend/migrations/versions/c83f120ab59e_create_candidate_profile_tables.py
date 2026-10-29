"""create_candidate_profile_tables

Revision ID: c83f120ab59e
Revises: 6149aa0f05b6
Create Date: 2026-08-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c83f120ab59e'
down_revision: Union[str, None] = '6149aa0f05b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. candidate_identities table
    op.create_table(
        'candidate_identities',
        sa.Column('identity_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('artifact_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('full_name', sa.String(length=255), nullable=True),
        sa.Column('primary_email', sa.String(length=255), nullable=True),
        sa.Column('primary_phone', sa.String(length=50), nullable=True),
        sa.Column('identity_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['artifact_id'],
            ['resume_artifacts.artifact_id'],
            name='fk_candidate_identities_artifact_id_resume_artifacts',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('identity_id'),
        sa.UniqueConstraint('artifact_id', name='uq_candidate_identities_artifact_id'),
    )
    op.create_index('ix_candidate_identities_artifact_id', 'candidate_identities', ['artifact_id'], unique=False)

    # 2. candidate_profiles table
    op.create_table(
        'candidate_profiles',
        sa.Column('profile_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('profile_version', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('artifact_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('extraction_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('identity_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('schema_version', sa.String(length=20), server_default=sa.text("'1.0'"), nullable=False),
        sa.Column('parser_pipeline_version', sa.String(length=255), nullable=False),
        sa.Column('professional_profile', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('evidence_spans', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('review_required', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('warning_metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('profile_version >= 1', name='ck_candidate_profiles_profile_version_positive'),
        sa.CheckConstraint('length(trim(schema_version)) > 0', name='ck_candidate_profiles_schema_version_not_empty'),
        sa.CheckConstraint('length(trim(parser_pipeline_version)) > 0', name='ck_candidate_profiles_parser_pipeline_version_not_empty'),
        sa.ForeignKeyConstraint(
            ['artifact_id'],
            ['resume_artifacts.artifact_id'],
            name='fk_candidate_profiles_artifact_id_resume_artifacts',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['extraction_id'],
            ['resume_extractions.extraction_id'],
            name='fk_candidate_profiles_extraction_id_resume_extractions',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['identity_id'],
            ['candidate_identities.identity_id'],
            name='fk_candidate_profiles_identity_id_candidate_identities',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('profile_id'),
        sa.UniqueConstraint(
            'artifact_id',
            'parser_pipeline_version',
            'schema_version',
            name='uq_candidate_profiles_artifact_pipeline_schema',
        ),
    )
    op.create_index('ix_candidate_profiles_artifact_id', 'candidate_profiles', ['artifact_id'], unique=False)
    op.create_index('ix_candidate_profiles_extraction_id', 'candidate_profiles', ['extraction_id'], unique=False)
    op.create_index('ix_candidate_profiles_identity_id', 'candidate_profiles', ['identity_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_candidate_profiles_identity_id', table_name='candidate_profiles')
    op.drop_index('ix_candidate_profiles_extraction_id', table_name='candidate_profiles')
    op.drop_index('ix_candidate_profiles_artifact_id', table_name='candidate_profiles')
    op.drop_table('candidate_profiles')
    op.drop_index('ix_candidate_identities_artifact_id', table_name='candidate_identities')
    op.drop_table('candidate_identities')
