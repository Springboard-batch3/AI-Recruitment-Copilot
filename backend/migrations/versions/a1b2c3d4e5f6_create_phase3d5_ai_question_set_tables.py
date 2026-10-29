"""Create Phase 3D.5 AI Interview Question Sets table

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-09-18 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ai_interview_question_sets',
        sa.Column('ai_set_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('template_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('job_interview_templates.template_id', ondelete='CASCADE'), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('job_requisitions.job_id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_template_version', sa.Integer(), nullable=False),
        sa.Column('source_content_hash', sa.String(length=64), nullable=False),
        sa.Column('set_version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('lifecycle_status', sa.String(length=50), nullable=False, server_default=sa.text("'DRAFT'")),
        sa.Column('content_hash', sa.String(length=64), nullable=True),
        sa.Column('is_immutable', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('items', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.user_id', ondelete='RESTRICT'), nullable=False),
        sa.Column('approved_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.user_id', ondelete='SET NULL'), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('template_id', 'set_version', name='uq_ai_question_sets_template_id_set_version'),
    )
    op.create_index('ix_ai_question_sets_template_id', 'ai_interview_question_sets', ['template_id'], unique=False)
    op.create_index('ix_ai_question_sets_job_id', 'ai_interview_question_sets', ['job_id'], unique=False)
    op.create_index('ix_ai_question_sets_organization_id', 'ai_interview_question_sets', ['organization_id'], unique=False)
    op.create_index('ix_ai_question_sets_lifecycle_status', 'ai_interview_question_sets', ['lifecycle_status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_ai_question_sets_lifecycle_status', table_name='ai_interview_question_sets')
    op.drop_index('ix_ai_question_sets_organization_id', table_name='ai_interview_question_sets')
    op.drop_index('ix_ai_question_sets_job_id', table_name='ai_interview_question_sets')
    op.drop_index('ix_ai_question_sets_template_id', table_name='ai_interview_question_sets')
    op.drop_table('ai_interview_question_sets')
