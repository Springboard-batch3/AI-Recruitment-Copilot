"""Create MCQ columns on interview_questions and candidate_practice_releases table

Revision ID: f2a3b4c5d6e7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-21 23:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f2a3b4c5d6e7'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add MCQ columns to interview_questions
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
    
    op.add_column(
        'interview_questions',
        sa.Column(
            'question_format',
            sa.String(30),
            nullable=False,
            server_default=sa.text("'CONVERSATION'"),
        )
    )
    op.add_column(
        'interview_questions',
        sa.Column(
            'options',
            json_type,
            nullable=True,
        )
    )
    op.add_column(
        'interview_questions',
        sa.Column(
            'correct_option_id',
            sa.String(100),
            nullable=True,
        )
    )
    op.add_column(
        'interview_questions',
        sa.Column(
            'answer_explanation',
            sa.Text(),
            nullable=True,
        )
    )
    op.add_column(
        'interview_questions',
        sa.Column(
            'difficulty',
            sa.String(30),
            nullable=True,
        )
    )

    op.create_index(
        'ix_interview_questions_question_format',
        'interview_questions',
        ['question_format'],
    )

    bind = op.get_bind()
    if bind.engine.name == "postgresql":
        op.create_check_constraint(
            'ck_interview_questions_question_format_enum',
            'interview_questions',
            "question_format IN ('CONVERSATION', 'MCQ_SINGLE_ANSWER')"
        )
        op.create_check_constraint(
            'ck_interview_questions_difficulty_enum',
            'interview_questions',
            "difficulty IS NULL OR difficulty IN ('FOUNDATIONAL', 'INTERMEDIATE', 'ADVANCED')"
        )

    # 2. Create candidate_practice_releases table
    op.create_table(
        'candidate_practice_releases',
        sa.Column('release_id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('candidate_id', sa.Uuid(), nullable=False),
        sa.Column('application_id', sa.Uuid(), nullable=True),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('job_version_id', sa.Uuid(), nullable=False),
        sa.Column('job_revision', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('template_id', sa.Uuid(), nullable=True),
        sa.Column('ai_set_id', sa.Uuid(), nullable=True),
        sa.Column('set_version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('status', sa.String(length=20), nullable=False, server_default=sa.text("'RELEASED'")),
        sa.Column('released_by_user_id', sa.Uuid(), nullable=False),
        sa.Column('released_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('revoked_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['candidate_id'],
            ['candidate_identities.identity_id'],
            name='fk_candidate_practice_releases_candidate_id',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['job_id'],
            ['job_requisitions.job_id'],
            name='fk_candidate_practice_releases_job_id',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['job_version_id'],
            ['job_versions.job_version_id'],
            name='fk_candidate_practice_releases_job_version_id',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['template_id'],
            ['job_interview_templates.template_id'],
            name='fk_candidate_practice_releases_template_id',
            ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['ai_set_id'],
            ['ai_interview_question_sets.ai_set_id'],
            name='fk_candidate_practice_releases_ai_set_id',
            ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['released_by_user_id'],
            ['users.user_id'],
            name='fk_candidate_practice_releases_released_by',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['revoked_by_user_id'],
            ['users.user_id'],
            name='fk_candidate_practice_releases_revoked_by',
            ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('release_id'),
        sa.CheckConstraint(
            "status IN ('RELEASED', 'REVOKED')",
            name='ck_candidate_practice_releases_status'
        ),
        sa.CheckConstraint(
            "job_revision >= 1",
            name='ck_candidate_practice_releases_job_revision_positive'
        ),
        sa.CheckConstraint(
            "set_version >= 1",
            name='ck_candidate_practice_releases_set_version_positive'
        )
    )

    op.create_index('ix_candidate_practice_releases_candidate_id', 'candidate_practice_releases', ['candidate_id'])
    op.create_index('ix_candidate_practice_releases_org_id', 'candidate_practice_releases', ['organization_id'])
    op.create_index('ix_candidate_practice_releases_job_id', 'candidate_practice_releases', ['job_id'])
    op.create_index('ix_candidate_practice_releases_status', 'candidate_practice_releases', ['status'])
    op.create_index('ix_candidate_practice_releases_ai_set_id', 'candidate_practice_releases', ['ai_set_id'])
    op.create_index('ix_candidate_practice_releases_template_id', 'candidate_practice_releases', ['template_id'])


def downgrade() -> None:
    op.drop_table('candidate_practice_releases')

    bind = op.get_bind()
    if bind.engine.name == "postgresql":
        op.drop_constraint('ck_interview_questions_difficulty_enum', 'interview_questions', type_='check')
        op.drop_constraint('ck_interview_questions_question_format_enum', 'interview_questions', type_='check')

    op.drop_index('ix_interview_questions_question_format', table_name='interview_questions')
    op.drop_column('interview_questions', 'difficulty')
    op.drop_column('interview_questions', 'answer_explanation')
    op.drop_column('interview_questions', 'correct_option_id')
    op.drop_column('interview_questions', 'options')
    op.drop_column('interview_questions', 'question_format')
