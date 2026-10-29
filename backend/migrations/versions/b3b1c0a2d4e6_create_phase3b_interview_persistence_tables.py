"""create_phase3b_interview_persistence_tables

Revision ID: b3b1c0a2d4e6
Revises: f35cae6d57b1
Create Date: 2026-09-16 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b3b1c0a2d4e6'
down_revision: Union[str, None] = 'f35cae6d57b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create job_interview_templates table
    op.create_table(
        'job_interview_templates',
        sa.Column('template_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_version_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('template_version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('parent_template_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('lifecycle_status', sa.String(length=50), nullable=False, server_default=sa.text("'DRAFT'")),
        sa.Column('guardrail_policy_version', sa.String(length=50), nullable=False),
        sa.Column('guardrail_policy_hash', sa.String(length=64), nullable=False),
        sa.Column('deterministic_builder_version', sa.String(length=50), nullable=False),
        sa.Column('question_catalogue_version', sa.String(length=50), nullable=False),
        sa.Column('rubric_catalogue_version', sa.String(length=50), nullable=False),
        sa.Column('input_snapshot_hash', sa.String(length=64), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('record_version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('template_version >= 1', name='ck_job_interview_templates_template_version_positive'),
        sa.CheckConstraint('revision >= 1', name='ck_job_interview_templates_revision_positive'),
        sa.CheckConstraint('record_version >= 1', name='ck_job_interview_templates_record_version_positive'),
        sa.CheckConstraint("lifecycle_status IN ('DRAFT', 'VALIDATED', 'APPROVED', 'IN_USE', 'SUPERSEDED')", name='ck_job_interview_templates_lifecycle_status'),
        sa.ForeignKeyConstraint(
            ['job_id'],
            ['job_requisitions.job_id'],
            name='fk_job_interview_templates_job_id_job_requisitions',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['job_version_id'],
            ['job_versions.job_version_id'],
            name='fk_job_interview_templates_job_version_id_job_versions',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['parent_template_id'],
            ['job_interview_templates.template_id'],
            name='fk_job_interview_templates_parent_template_id',
            ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['created_by_user_id'],
            ['users.user_id'],
            name='fk_job_interview_templates_created_by_user_id_users',
            ondelete='RESTRICT'
        ),
        sa.PrimaryKeyConstraint('template_id'),
        sa.UniqueConstraint('job_id', 'template_version', name='uq_job_interview_templates_job_id_template_version'),
    )
    op.create_index('ix_job_interview_templates_job_id', 'job_interview_templates', ['job_id'], unique=False)
    op.create_index('ix_job_interview_templates_job_version_id', 'job_interview_templates', ['job_version_id'], unique=False)
    op.create_index('ix_job_interview_templates_organization_id', 'job_interview_templates', ['organization_id'], unique=False)
    op.create_index('ix_job_interview_templates_created_by_user_id', 'job_interview_templates', ['created_by_user_id'], unique=False)
    op.create_index('ix_job_interview_templates_lifecycle_status', 'job_interview_templates', ['lifecycle_status'], unique=False)

    # 2. Create interview_questions table
    op.create_table(
        'interview_questions',
        sa.Column('question_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('template_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('question_version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('layer', sa.String(length=50), nullable=False),
        sa.Column('question_type', sa.String(length=50), nullable=False),
        sa.Column('authoring_origin', sa.String(length=50), nullable=False, server_default=sa.text("'DETERMINISTIC'")),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('competency_id', sa.String(length=100), nullable=True),
        sa.Column('requirement_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('deterministic_template_id', sa.String(length=100), nullable=True),
        sa.Column('deterministic_template_version', sa.String(length=50), nullable=True),
        sa.Column('question_text', sa.Text(), nullable=False),
        sa.Column('question_intent', sa.Text(), nullable=False),
        sa.Column('estimated_time_minutes', sa.Integer(), nullable=False, server_default=sa.text('5')),
        sa.Column('included_in_common_score', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('validation_state', sa.String(length=50), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column('permitted_probes', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('safe_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('display_order >= 0', name='ck_interview_questions_display_order_non_negative'),
        sa.CheckConstraint('question_version >= 1', name='ck_interview_questions_question_version_positive'),
        sa.CheckConstraint('estimated_time_minutes >= 1 AND estimated_time_minutes <= 60', name='ck_interview_questions_estimated_time_bounded'),
        sa.CheckConstraint('length(trim(question_text)) > 0', name='ck_interview_questions_question_text_not_empty'),
        sa.CheckConstraint('length(trim(question_intent)) > 0', name='ck_interview_questions_question_intent_not_empty'),
        sa.CheckConstraint("(layer = 'COMMON_CORE' AND included_in_common_score = true) OR (layer = 'SUPPLEMENTAL_EVIDENCE' AND included_in_common_score = false)", name='ck_interview_questions_layer_scoring_integrity'),
        sa.ForeignKeyConstraint(
            ['template_id'],
            ['job_interview_templates.template_id'],
            name='fk_interview_questions_template_id_job_interview_templates',
            ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('question_id'),
        sa.UniqueConstraint('template_id', 'display_order', name='uq_interview_questions_template_id_display_order'),
    )
    op.create_index('ix_interview_questions_template_id', 'interview_questions', ['template_id'], unique=False)
    op.create_index('ix_interview_questions_layer', 'interview_questions', ['layer'], unique=False)
    op.create_index('ix_interview_questions_question_type', 'interview_questions', ['question_type'], unique=False)
    op.create_index('ix_interview_questions_validation_state', 'interview_questions', ['validation_state'], unique=False)

    # 3. Create interview_rubrics table
    op.create_table(
        'interview_rubrics',
        sa.Column('rubric_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('question_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('rubric_version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('competency_definition', sa.Text(), nullable=False),
        sa.Column('question_intent', sa.Text(), nullable=False),
        sa.Column('observable_indicators', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('rating_anchors', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('sufficient_evidence_guidance', sa.Text(), nullable=False),
        sa.Column('insufficient_evidence_guidance', sa.Text(), nullable=False),
        sa.Column('incomplete_answer_guidance', sa.Text(), nullable=True),
        sa.Column('permitted_probes', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('prohibited_inferences', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('subject_matter_review_status', sa.String(length=50), nullable=False, server_default=sa.text("'NOT_REQUIRED'")),
        sa.Column('validation_state', sa.String(length=50), nullable=False, server_default=sa.text("'PASSED'")),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('rubric_version >= 1', name='ck_interview_rubrics_rubric_version_positive'),
        sa.CheckConstraint('length(trim(competency_definition)) > 0', name='ck_interview_rubrics_competency_def_not_empty'),
        sa.CheckConstraint('length(trim(question_intent)) > 0', name='ck_interview_rubrics_question_intent_not_empty'),
        sa.CheckConstraint('length(trim(sufficient_evidence_guidance)) > 0', name='ck_interview_rubrics_sufficient_evidence_not_empty'),
        sa.CheckConstraint('length(trim(insufficient_evidence_guidance)) > 0', name='ck_interview_rubrics_insufficient_evidence_not_empty'),
        sa.ForeignKeyConstraint(
            ['question_id'],
            ['interview_questions.question_id'],
            name='fk_interview_rubrics_question_id_interview_questions',
            ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('rubric_id'),
        sa.UniqueConstraint('question_id', name='uq_interview_rubrics_question_id'),
    )
    op.create_index('ix_interview_rubrics_question_id', 'interview_rubrics', ['question_id'], unique=False)

    # 4. Create candidate_interview_plans table
    op.create_table(
        'candidate_interview_plans',
        sa.Column('plan_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('template_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('template_version', sa.Integer(), nullable=False),
        sa.Column('candidate_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('profile_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('profile_version', sa.Integer(), nullable=False),
        sa.Column('match_result_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('match_result_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('plan_version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('parent_plan_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('lifecycle_status', sa.String(length=50), nullable=False, server_default=sa.text("'DRAFT'")),
        sa.Column('guardrail_policy_version', sa.String(length=50), nullable=False),
        sa.Column('scoring_separation_policy', sa.String(length=100), nullable=False, server_default=sa.text("'STRICT_COMMON_CORE_SEPARATION'")),
        sa.Column('input_snapshot_hash', sa.String(length=64), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('record_version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('plan_version >= 1', name='ck_cand_plans_plan_version_positive'),
        sa.CheckConstraint('template_version >= 1', name='ck_cand_plans_template_version_positive'),
        sa.CheckConstraint('profile_version >= 1', name='ck_cand_plans_profile_version_positive'),
        sa.CheckConstraint('record_version >= 1', name='ck_cand_plans_record_version_positive'),
        sa.CheckConstraint("lifecycle_status IN ('DRAFT', 'VALIDATED', 'APPROVED', 'IN_USE', 'SUPERSEDED')", name='ck_cand_plans_lifecycle_status'),
        sa.ForeignKeyConstraint(
            ['template_id'],
            ['job_interview_templates.template_id'],
            name='fk_cand_plans_template_id_job_interview_templates',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['profile_id'],
            ['candidate_profiles.profile_id'],
            name='fk_cand_plans_profile_id_candidate_profiles',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['match_result_id'],
            ['match_results.result_id'],
            name='fk_cand_plans_match_result_id_match_results',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['parent_plan_id'],
            ['candidate_interview_plans.plan_id'],
            name='fk_cand_plans_parent_plan_id',
            ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['created_by_user_id'],
            ['users.user_id'],
            name='fk_cand_plans_created_by_user_id_users',
            ondelete='RESTRICT'
        ),
        sa.PrimaryKeyConstraint('plan_id'),
        sa.UniqueConstraint('candidate_id', 'template_id', 'plan_version', name='uq_cand_plans_candidate_template_version'),
    )
    op.create_index('ix_candidate_interview_plans_template_id', 'candidate_interview_plans', ['template_id'], unique=False)
    op.create_index('ix_candidate_interview_plans_candidate_id', 'candidate_interview_plans', ['candidate_id'], unique=False)
    op.create_index('ix_candidate_interview_plans_profile_id', 'candidate_interview_plans', ['profile_id'], unique=False)
    op.create_index('ix_candidate_interview_plans_match_result_id', 'candidate_interview_plans', ['match_result_id'], unique=False)
    op.create_index('ix_candidate_interview_plans_organization_id', 'candidate_interview_plans', ['organization_id'], unique=False)
    op.create_index('ix_candidate_interview_plans_created_by_user_id', 'candidate_interview_plans', ['created_by_user_id'], unique=False)
    op.create_index('ix_candidate_interview_plans_lifecycle_status', 'candidate_interview_plans', ['lifecycle_status'], unique=False)

    # 5. Create supplemental_question_provenances table
    op.create_table(
        'supplemental_question_provenances',
        sa.Column('provenance_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('question_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('plan_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('source_requirement_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_match_result_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('source_gap_report_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('source_match_state', sa.String(length=50), nullable=False),
        sa.Column('allowlisted_evidence_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('excluded_from_common_score', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('excluded_from_common_score = true', name='ck_supp_prov_excluded_from_common_score'),
        sa.ForeignKeyConstraint(
            ['question_id'],
            ['interview_questions.question_id'],
            name='fk_supp_prov_question_id_interview_questions',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['plan_id'],
            ['candidate_interview_plans.plan_id'],
            name='fk_supp_prov_plan_id_cand_plans',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['source_requirement_id'],
            ['job_requirements.requirement_id'],
            name='fk_supp_prov_source_req_id_job_reqs',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['source_match_result_id'],
            ['match_results.result_id'],
            name='fk_supp_prov_source_match_result_id_match_results',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['source_gap_report_id'],
            ['gap_reports.report_id'],
            name='fk_supp_prov_source_gap_report_id_gap_reports',
            ondelete='RESTRICT'
        ),
        sa.PrimaryKeyConstraint('provenance_id'),
        sa.UniqueConstraint('question_id', name='uq_supp_prov_question_id'),
    )
    op.create_index('ix_supp_prov_question_id', 'supplemental_question_provenances', ['question_id'], unique=False)
    op.create_index('ix_supp_prov_plan_id', 'supplemental_question_provenances', ['plan_id'], unique=False)
    op.create_index('ix_supp_prov_source_req_id', 'supplemental_question_provenances', ['source_requirement_id'], unique=False)
    op.create_index('ix_supp_prov_source_match_result_id', 'supplemental_question_provenances', ['source_match_result_id'], unique=False)
    op.create_index('ix_supp_prov_source_gap_report_id', 'supplemental_question_provenances', ['source_gap_report_id'], unique=False)


def downgrade() -> None:
    op.drop_table('supplemental_question_provenances')
    op.drop_table('candidate_interview_plans')
    op.drop_table('interview_rubrics')
    op.drop_table('interview_questions')
    op.drop_table('job_interview_templates')
