"""Update Phase 3D.5 AI Interview Question Sets table for requirement-grounded fallback

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-18 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Alter template_id to be nullable
    op.alter_column('ai_interview_question_sets', 'template_id',
               existing_type=postgresql.UUID(as_uuid=True),
               nullable=True)

    # 2. Alter source_template_version to be nullable
    op.alter_column('ai_interview_question_sets', 'source_template_version',
               existing_type=sa.Integer(),
               nullable=True)

    # 3. Add new Phase 3D.5 architecture columns
    op.add_column('ai_interview_question_sets', sa.Column('job_version_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('job_versions.job_version_id', ondelete='CASCADE'), nullable=True))
    op.add_column('ai_interview_question_sets', sa.Column('job_revision', sa.Integer(), nullable=True, server_default=sa.text('1')))
    op.add_column('ai_interview_question_sets', sa.Column('source_mode', sa.String(length=50), nullable=False, server_default=sa.text("'BLUEPRINT_GROUNDED'")))
    op.add_column('ai_interview_question_sets', sa.Column('source_requirement_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'")))
    op.add_column('ai_interview_question_sets', sa.Column('provider_profile_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('ai_interview_question_sets', sa.Column('model_identifier', sa.String(length=100), nullable=True))
    op.add_column('ai_interview_question_sets', sa.Column('prompt_template_version', sa.String(length=50), nullable=True))
    op.add_column('ai_interview_question_sets', sa.Column('predecessor_set_id', postgresql.UUID(as_uuid=True), nullable=True))

    # 4. Indexes
    op.create_index('ix_ai_question_sets_job_version_id', 'ai_interview_question_sets', ['job_version_id'], unique=False)
    op.create_index('ix_ai_question_sets_source_mode', 'ai_interview_question_sets', ['source_mode'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_ai_question_sets_source_mode', table_name='ai_interview_question_sets')
    op.drop_index('ix_ai_question_sets_job_version_id', table_name='ai_interview_question_sets')
    op.drop_column('ai_interview_question_sets', 'predecessor_set_id')
    op.drop_column('ai_interview_question_sets', 'prompt_template_version')
    op.drop_column('ai_interview_question_sets', 'model_identifier')
    op.drop_column('ai_interview_question_sets', 'provider_profile_id')
    op.drop_column('ai_interview_question_sets', 'source_requirement_ids')
    op.drop_column('ai_interview_question_sets', 'source_mode')
    op.drop_column('ai_interview_question_sets', 'job_revision')
    op.drop_column('ai_interview_question_sets', 'job_version_id')
    op.alter_column('ai_interview_question_sets', 'source_template_version',
               existing_type=sa.Integer(),
               nullable=False)
    op.alter_column('ai_interview_question_sets', 'template_id',
               existing_type=postgresql.UUID(as_uuid=True),
               nullable=False)
