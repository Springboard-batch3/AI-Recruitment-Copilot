"""create_taxonomy_tables

Revision ID: b7c2d8e4f1a0
Revises: a5b1e62c4d99
Create Date: 2026-09-03 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b7c2d8e4f1a0'
down_revision: Union[str, None] = 'a5b1e62c4d99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create taxonomy_snapshots table
    op.create_table(
        'taxonomy_snapshots',
        sa.Column('snapshot_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source', sa.String(length=50), server_default=sa.text("'ONET'"), nullable=False),
        sa.Column('source_version', sa.String(length=50), nullable=False),
        sa.Column('combined_sha256_hash', sa.String(length=64), nullable=False),
        sa.Column('state', sa.String(length=50), server_default=sa.text("'IMPORTING'"), nullable=False),
        sa.Column('record_counts', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('source_filenames', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('imported_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("state IN ('IMPORTING', 'ACTIVE', 'SUPERSEDED', 'FAILED')", name='ck_taxonomy_snapshots_state'),
        sa.PrimaryKeyConstraint('snapshot_id'),
    )
    op.create_index('ix_taxonomy_snapshots_state', 'taxonomy_snapshots', ['state'], unique=False)
    op.create_index('ix_taxonomy_snapshots_source_version', 'taxonomy_snapshots', ['source', 'source_version'], unique=False)

    # 2. Create occupation_concepts table
    op.create_table(
        'occupation_concepts',
        sa.Column('concept_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('snapshot_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('onet_soc_code', sa.String(length=20), nullable=False),
        sa.Column('canonical_title', sa.String(length=255), nullable=False),
        sa.Column('normalized_title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('source_provenance', sa.String(length=50), server_default=sa.text("'ONET_31_0'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('length(trim(canonical_title)) > 0', name='ck_occupation_concepts_canonical_title_not_empty'),
        sa.ForeignKeyConstraint(
            ['snapshot_id'],
            ['taxonomy_snapshots.snapshot_id'],
            name='fk_occupation_concepts_snapshot_id_taxonomy_snapshots',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('concept_id'),
        sa.UniqueConstraint('snapshot_id', 'onet_soc_code', name='uq_occupation_concepts_snapshot_id_onet_soc_code'),
    )
    op.create_index('ix_occupation_concepts_snapshot_id', 'occupation_concepts', ['snapshot_id'], unique=False)
    op.create_index('ix_occupation_concepts_onet_soc_code', 'occupation_concepts', ['onet_soc_code'], unique=False)
    op.create_index('ix_occupation_concepts_normalized_title', 'occupation_concepts', ['normalized_title'], unique=False)

    # 3. Create occupation_aliases table
    op.create_table(
        'occupation_aliases',
        sa.Column('alias_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('occupation_concept_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_title_alias', sa.String(length=255), nullable=False),
        sa.Column('normalized_alias', sa.String(length=255), nullable=False),
        sa.Column('source_provenance', sa.String(length=50), server_default=sa.text("'ONET_31_0_JOB_TITLES'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('length(trim(job_title_alias)) > 0', name='ck_occupation_aliases_job_title_alias_not_empty'),
        sa.ForeignKeyConstraint(
            ['occupation_concept_id'],
            ['occupation_concepts.concept_id'],
            name='fk_occupation_aliases_occupation_concept_id_occupation_concepts',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('alias_id'),
        sa.UniqueConstraint('occupation_concept_id', 'normalized_alias', name='uq_occupation_aliases_concept_id_normalized_alias'),
    )
    op.create_index('ix_occupation_aliases_concept_id', 'occupation_aliases', ['occupation_concept_id'], unique=False)
    op.create_index('ix_occupation_aliases_normalized_alias', 'occupation_aliases', ['normalized_alias'], unique=False)

    # 4. Create skill_concepts table
    op.create_table(
        'skill_concepts',
        sa.Column('concept_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('snapshot_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('canonical_label', sa.String(length=255), nullable=False),
        sa.Column('normalized_label', sa.String(length=255), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=True),
        sa.Column('source', sa.String(length=50), server_default=sa.text("'ONET_31_0'"), nullable=False),
        sa.Column('external_id', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('length(trim(canonical_label)) > 0', name='ck_skill_concepts_canonical_label_not_empty'),
        sa.ForeignKeyConstraint(
            ['snapshot_id'],
            ['taxonomy_snapshots.snapshot_id'],
            name='fk_skill_concepts_snapshot_id_taxonomy_snapshots',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('concept_id'),
        sa.UniqueConstraint('snapshot_id', 'normalized_label', name='uq_skill_concepts_snapshot_id_normalized_label'),
    )
    op.create_index('ix_skill_concepts_snapshot_id', 'skill_concepts', ['snapshot_id'], unique=False)
    op.create_index('ix_skill_concepts_normalized_label', 'skill_concepts', ['normalized_label'], unique=False)
    op.create_index('ix_skill_concepts_source', 'skill_concepts', ['source'], unique=False)

    # 5. Create skill_aliases table
    op.create_table(
        'skill_aliases',
        sa.Column('alias_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('skill_concept_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('alias_text', sa.String(length=255), nullable=False),
        sa.Column('normalized_alias', sa.String(length=255), nullable=False),
        sa.Column('alias_source', sa.String(length=50), server_default=sa.text("'INTERNAL_CONTROLLED'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('length(trim(alias_text)) > 0', name='ck_skill_aliases_alias_text_not_empty'),
        sa.ForeignKeyConstraint(
            ['skill_concept_id'],
            ['skill_concepts.concept_id'],
            name='fk_skill_aliases_skill_concept_id_skill_concepts',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('alias_id'),
        sa.UniqueConstraint('skill_concept_id', 'normalized_alias', name='uq_skill_aliases_concept_id_normalized_alias'),
    )
    op.create_index('ix_skill_aliases_concept_id', 'skill_aliases', ['skill_concept_id'], unique=False)
    op.create_index('ix_skill_aliases_normalized_alias', 'skill_aliases', ['normalized_alias'], unique=False)

    # 6. Create occupation_skill_links table
    op.create_table(
        'occupation_skill_links',
        sa.Column('link_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('occupation_concept_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('skill_concept_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('hot_technology', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('in_demand', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('element_id', sa.String(length=50), nullable=True),
        sa.Column('element_name', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['occupation_concept_id'],
            ['occupation_concepts.concept_id'],
            name='fk_occupation_skill_links_occupation_concept_id',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['skill_concept_id'],
            ['skill_concepts.concept_id'],
            name='fk_occupation_skill_links_skill_concept_id',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('link_id'),
        sa.UniqueConstraint('occupation_concept_id', 'skill_concept_id', name='uq_occupation_skill_links_occupation_skill'),
    )
    op.create_index('ix_occupation_skill_links_occupation_id', 'occupation_skill_links', ['occupation_concept_id'], unique=False)
    op.create_index('ix_occupation_skill_links_skill_id', 'occupation_skill_links', ['skill_concept_id'], unique=False)


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_index('ix_occupation_skill_links_skill_id', table_name='occupation_skill_links')
    op.drop_index('ix_occupation_skill_links_occupation_id', table_name='occupation_skill_links')
    op.drop_table('occupation_skill_links')

    op.drop_index('ix_skill_aliases_normalized_alias', table_name='skill_aliases')
    op.drop_index('ix_skill_aliases_concept_id', table_name='skill_aliases')
    op.drop_table('skill_aliases')

    op.drop_index('ix_skill_concepts_source', table_name='skill_concepts')
    op.drop_index('ix_skill_concepts_normalized_label', table_name='skill_concepts')
    op.drop_index('ix_skill_concepts_snapshot_id', table_name='skill_concepts')
    op.drop_table('skill_concepts')

    op.drop_index('ix_occupation_aliases_normalized_alias', table_name='occupation_aliases')
    op.drop_index('ix_occupation_aliases_concept_id', table_name='occupation_aliases')
    op.drop_table('occupation_aliases')

    op.drop_index('ix_occupation_concepts_normalized_title', table_name='occupation_concepts')
    op.drop_index('ix_occupation_concepts_onet_soc_code', table_name='occupation_concepts')
    op.drop_index('ix_occupation_concepts_snapshot_id', table_name='occupation_concepts')
    op.drop_table('occupation_concepts')

    op.drop_index('ix_taxonomy_snapshots_source_version', table_name='taxonomy_snapshots')
    op.drop_index('ix_taxonomy_snapshots_state', table_name='taxonomy_snapshots')
    op.drop_table('taxonomy_snapshots')
