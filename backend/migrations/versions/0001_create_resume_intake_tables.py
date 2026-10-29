"""create resume intake tables

Revision ID: 0001_create_resume_intake_tables
Revises: 
Create Date: 2026-08-26 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_create_resume_intake_tables"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create resume_artifacts table
    op.create_table(
        "resume_artifacts",
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("declared_mime_type", sa.String(length=100), nullable=True),
        sa.Column(
            "detected_format",
            sa.Enum("pdf", "docx", name="ck_resume_artifacts_detected_format", native_enum=False),
            nullable=True,
        ),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "validation_status",
            sa.Enum("PENDING", "VALIDATED", "REJECTED", name="ck_resume_artifacts_validation_status", native_enum=False),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("quarantine_key", sa.String(length=512), nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_resume_artifacts_size_bytes_non_negative"),
        sa.CheckConstraint("sha256_hash IS NULL OR length(sha256_hash) = 64", name="ck_resume_artifacts_sha256_hash_length"),
        sa.PrimaryKeyConstraint("artifact_id", name="pk_resume_artifacts"),
        sa.UniqueConstraint("quarantine_key", name="uq_resume_artifacts_quarantine_key"),
    )
    op.create_index(
        "ix_resume_artifacts_sha256_hash",
        "resume_artifacts",
        ["sha256_hash"],
        unique=False,
    )

    # 2. Create processing_jobs table
    op.create_table(
        "processing_jobs",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", name="ck_processing_jobs_status", native_enum=False),
            server_default=sa.text("'QUEUED'"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("safe_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_processing_jobs_attempt_count_non_negative"),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["resume_artifacts.artifact_id"],
            name="fk_processing_jobs_artifact_id_resume_artifacts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", name="pk_processing_jobs"),
    )
    op.create_index(
        "ix_processing_jobs_artifact_id",
        "processing_jobs",
        ["artifact_id"],
        unique=False,
    )


def downgrade() -> None:
    # 1. Drop processing_jobs table (with indexes and constraints)
    op.drop_index("ix_processing_jobs_artifact_id", table_name="processing_jobs")
    op.drop_table("processing_jobs")

    # 2. Drop resume_artifacts table (with indexes and constraints)
    op.drop_index("ix_resume_artifacts_sha256_hash", table_name="resume_artifacts")
    op.drop_table("resume_artifacts")
