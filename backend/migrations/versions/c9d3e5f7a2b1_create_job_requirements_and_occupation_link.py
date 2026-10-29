"""create_job_requirements_and_occupation_link

Revision ID: c9d3e5f7a2b1
Revises: b7c2d8e4f1a0
Create Date: 2026-09-03 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c9d3e5f7a2b1'
down_revision: Union[str, None] = 'b7c2d8e4f1a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add occupation_concept_id to job_versions
    op.add_column(
        'job_versions',
        sa.Column('occupation_concept_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_job_versions_occupation_concept_id_occupation_concepts',
        'job_versions',
        'occupation_concepts',
        ['occupation_concept_id'],
        ['concept_id'],
        ondelete='SET NULL',
    )
    op.create_index('ix_job_versions_occupation_concept_id', 'job_versions', ['occupation_concept_id'], unique=False)

    # 2. Create job_requirements table
    op.create_table(
        'job_requirements',
        sa.Column('requirement_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_version_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('display_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=False),
        sa.Column('normalized_label', sa.String(length=255), nullable=False),
        sa.Column('taxonomy_skill_concept_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('necessity', sa.String(length=50), nullable=False),
        sa.Column('priority', sa.String(length=50), nullable=False),
        sa.Column('custom_weight_override', sa.Float(), nullable=True),
        sa.Column('min_experience_months', sa.Integer(), nullable=True),
        sa.Column('recency_years', sa.Integer(), nullable=True),
        sa.Column('accepted_evidence_types', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False),
        sa.Column('accepted_equivalents', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('origin', sa.String(length=50), server_default=sa.text("'RECRUITER_ENTERED'"), nullable=False),
        sa.Column('review_required', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('warning_metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('display_order >= 0', name='ck_job_requirements_display_order_non_negative'),
        sa.CheckConstraint('length(trim(label)) > 0', name='ck_job_requirements_label_not_empty'),
        sa.CheckConstraint('length(trim(normalized_label)) > 0', name='ck_job_requirements_normalized_label_not_empty'),
        sa.CheckConstraint(
            "category IN ('SKILL', 'EXPERIENCE', 'RESPONSIBILITY', 'EDUCATION', 'CERTIFICATION', 'LANGUAGE', 'OTHER')",
            name='ck_job_requirements_category',
        ),
        sa.CheckConstraint("necessity IN ('MUST_HAVE', 'PREFERRED')", name='ck_job_requirements_necessity'),
        sa.CheckConstraint("priority IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')", name='ck_job_requirements_priority'),
        sa.CheckConstraint('min_experience_months IS NULL OR min_experience_months > 0', name='ck_job_requirements_min_experience_positive'),
        sa.CheckConstraint('recency_years IS NULL OR (recency_years >= 1 AND recency_years <= 50)', name='ck_job_requirements_recency_years_bounded'),
        sa.CheckConstraint('custom_weight_override IS NULL OR (custom_weight_override >= 0.0 AND custom_weight_override <= 10.0)', name='ck_job_requirements_custom_weight_bounded'),
        sa.CheckConstraint('rationale IS NULL OR length(rationale) <= 2000', name='ck_job_requirements_rationale_length'),
        sa.ForeignKeyConstraint(
            ['job_version_id'],
            ['job_versions.job_version_id'],
            name='fk_job_requirements_job_version_id_job_versions',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['taxonomy_skill_concept_id'],
            ['skill_concepts.concept_id'],
            name='fk_job_requirements_taxonomy_skill_concept_id_skill_concepts',
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('requirement_id'),
        sa.UniqueConstraint('job_version_id', 'category', 'normalized_label', name='uq_job_requirements_version_category_normalized_label'),
    )
    op.create_index('ix_job_requirements_job_version_id', 'job_requirements', ['job_version_id'], unique=False)
    op.create_index('ix_job_requirements_display_order', 'job_requirements', ['job_version_id', 'display_order'], unique=False)
    op.create_index('ix_job_requirements_taxonomy_skill_concept_id', 'job_requirements', ['taxonomy_skill_concept_id'], unique=False)


def downgrade() -> None:
    # 1. Drop job_requirements table
    op.drop_index('ix_job_requirements_taxonomy_skill_concept_id', table_name='job_requirements')
    op.drop_index('ix_job_requirements_display_order', table_name='job_requirements')
    op.drop_index('ix_job_requirements_job_version_id', table_name='job_requirements')
    op.drop_table('job_requirements')

    # 2. Drop occupation_concept_id column and index from job_versions
    op.drop_index('ix_job_versions_occupation_concept_id', table_name='job_versions')
    op.drop_constraint('fk_job_versions_occupation_concept_id_occupation_concepts', 'job_versions', type_='foreignkey')
    op.drop_column('job_versions', 'occupation_concept_id')
