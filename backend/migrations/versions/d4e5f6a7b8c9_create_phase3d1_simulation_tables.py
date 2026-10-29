"""create_phase3d1_simulation_tables

Revision ID: d4e5f6a7b8c9
Revises: c2f8c4604f59
Create Date: 2026-09-17 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c2f8c4604f59"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create simulation_attempts table
    op.create_table(
        "simulation_attempts",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recruiter_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("candidate_interview_plans.plan_id"), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_interview_templates.template_id"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_requisitions.job_id"), nullable=False),
        sa.Column("job_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_versions.job_version_id"), nullable=False),
        sa.Column("job_revision", sa.Integer(), nullable=False),
        sa.Column("candidate_profile_version", sa.Integer(), nullable=False),
        sa.Column("match_result_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("authoritative_content_hash", sa.String(length=64), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=32), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("guardrail_policy_version", sa.String(length=32), nullable=False),
        sa.Column("guardrail_policy_hash", sa.String(length=64), nullable=False),
        sa.Column("question_snapshots", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("total_questions_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("answered_questions_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("record_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "lifecycle_status IN ('NOT_STARTED', 'IN_PROGRESS', 'SUBMITTED')",
            name="ck_simulation_attempts_lifecycle_status",
        ),
    )
    op.create_index("ix_simulation_attempts_org_id", "simulation_attempts", ["organization_id"])
    op.create_index("ix_simulation_attempts_plan_id", "simulation_attempts", ["plan_id"])
    op.create_index("ix_simulation_attempts_template_id", "simulation_attempts", ["template_id"])
    op.create_index("ix_simulation_attempts_candidate_id", "simulation_attempts", ["candidate_id"])
    op.create_index("ix_simulation_attempts_job_id", "simulation_attempts", ["job_id"])
    op.create_index("ix_simulation_attempts_recruiter_id", "simulation_attempts", ["recruiter_user_id"])

    # 2. Create simulation_answers table
    op.create_table(
        "simulation_answers",
        sa.Column("answer_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulation_attempts.attempt_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("layer", sa.String(length=32), nullable=False, server_default="COMMON_CORE"),
        sa.Column("answer_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("answer_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_submitted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("safe_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "attempt_id",
            "question_id",
            name="uq_simulation_answers_attempt_id_question_id",
        ),
    )
    op.create_index("ix_simulation_answers_attempt_id", "simulation_answers", ["attempt_id"])
    op.create_index("ix_simulation_answers_question_id", "simulation_answers", ["question_id"])


def downgrade() -> None:
    op.drop_table("simulation_answers")
    op.drop_table("simulation_attempts")
