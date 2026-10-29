import uuid
from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase

import app.models
from app.models import (
    ArtifactFormat,
    ArtifactValidationStatus,
    Base,
    ProcessingJob,
    ProcessingJobStatus,
    ResumeArtifact,
)


def test_models_exported_and_tables_registered():
    """Verify Base is a DeclarativeBase and tables are registered upon import."""
    assert issubclass(Base, DeclarativeBase)
    table_names = set(Base.metadata.tables.keys())
    assert table_names == {
        "resume_artifacts",
        "processing_jobs",
        "resume_extractions",
        "extraction_blocks",
        "candidate_identities",
        "candidate_profiles",
        "profile_corrections",
        "users",
        "user_sessions",
        "security_audit_events",
        "job_requisitions",
        "job_versions",
        "job_requirements",
        "taxonomy_snapshots",
        "occupation_concepts",
        "occupation_aliases",
        "skill_concepts",
        "skill_aliases",
        "occupation_skill_links",
        "job_description_imports",
        "match_runs",
        "match_results",
        "match_audit_events",
        "gap_reports",
        "gap_report_items",
        "gap_audit_events",
        "job_interview_templates",
        "interview_questions",
        "interview_rubrics",
        "candidate_interview_plans",
        "supplemental_question_provenances",
        "provider_profiles",
        "simulation_attempts",
        "simulation_answers",
        "simulation_interview_assessments",
        "simulation_question_assessments",
        "simulation_answer_segments",
        "simulation_assessment_reviews",
        "simulation_review_audit_events",
        "ai_interview_question_sets",
        "ats_connections",
        "ats_external_jobs",
        "ats_external_candidates",
        "ats_external_attachments",
        "ats_object_mappings",
        "ats_import_runs",
        "ats_import_item_results",
        "ats_sync_checkpoints",
        "ats_export_receipts",
        "ats_reconciliation_records",
        "candidate_invitations",
        "candidate_account_bindings",
        "candidate_practice_releases",
        "candidate_practice_sessions",
        "candidate_practice_messages",
        "candidate_practice_mcq_answers",
        "candidate_practice_requests",
        "job_practice_catalogs",
        "candidate_practice_answer_reviews",
        "candidate_practice_answer_segments",
        "candidate_practice_reports",
        "candidate_applications",
        "candidate_interview_sessions",
        "candidate_interview_answers",
        "candidate_interview_evaluations",
        "candidate_question_evaluations",
        "dashboard_tickets",
    }





def test_resume_artifacts_table_structure_and_constraints():
    """Verify table name, columns, data types, PK, and constraints for resume_artifacts."""
    table = Base.metadata.tables["resume_artifacts"]

    # Table columns existence
    col_names = set(table.columns.keys())
    expected_cols = {
        "artifact_id",
        "original_filename",
        "declared_mime_type",
        "detected_format",
        "size_bytes",
        "sha256_hash",
        "validation_status",
        "quarantine_key",
        "retention_until",
        "created_at",
        "updated_at",
    }
    assert col_names == expected_cols

    # Primary key
    assert table.c.artifact_id.primary_key is True
    assert isinstance(table.c.artifact_id.type, UUID)

    # Not nullable columns
    assert table.c.original_filename.nullable is False
    assert isinstance(table.c.original_filename.type, String)
    assert table.c.original_filename.type.length == 255

    assert table.c.validation_status.nullable is False
    assert table.c.created_at.nullable is False
    assert table.c.updated_at.nullable is False

    # Nullable columns
    assert table.c.declared_mime_type.nullable is True
    assert table.c.detected_format.nullable is True
    assert table.c.size_bytes.nullable is True
    assert isinstance(table.c.size_bytes.type, BigInteger)
    assert table.c.sha256_hash.nullable is True
    assert table.c.quarantine_key.nullable is True
    assert table.c.retention_until.nullable is True

    # Unique quarantine_key
    assert table.c.quarantine_key.unique is True

    # Timezone awareness
    assert isinstance(table.c.created_at.type, DateTime)
    assert table.c.created_at.type.timezone is True
    assert isinstance(table.c.updated_at.type, DateTime)
    assert table.c.updated_at.type.timezone is True
    assert isinstance(table.c.retention_until.type, DateTime)
    assert table.c.retention_until.type.timezone is True

    # Constraints
    constraint_names = {c.name for c in table.constraints if c.name is not None}
    assert "ck_resume_artifacts_size_bytes_non_negative" in constraint_names
    assert "ck_resume_artifacts_sha256_hash_length" in constraint_names

    # Indexes
    index_names = {idx.name for idx in table.indexes}
    assert "ix_resume_artifacts_sha256_hash" in index_names


def test_processing_jobs_table_structure_and_constraints():
    """Verify table name, columns, PK, FK, attempt_count check constraint, and indexes."""
    table = Base.metadata.tables["processing_jobs"]

    # Table columns existence
    col_names = set(table.columns.keys())
    expected_cols = {
        "job_id",
        "artifact_id",
        "status",
        "attempt_count",
        "safe_error_code",
        "created_at",
        "started_at",
        "completed_at",
        "updated_at",
    }
    assert col_names == expected_cols

    # Primary key
    assert table.c.job_id.primary_key is True
    assert isinstance(table.c.job_id.type, UUID)

    # Foreign key
    assert table.c.artifact_id.nullable is False
    fk_list = list(table.c.artifact_id.foreign_keys)
    assert len(fk_list) == 1
    fk = fk_list[0]
    assert fk.target_fullname == "resume_artifacts.artifact_id"
    assert fk.name == "fk_processing_jobs_artifact_id_resume_artifacts"
    assert fk.ondelete == "CASCADE"

    # Status and attempt_count
    assert table.c.status.nullable is False
    assert table.c.attempt_count.nullable is False
    assert isinstance(table.c.attempt_count.type, Integer)

    # Error code & lifecycle timestamps
    assert table.c.safe_error_code.nullable is True
    assert table.c.created_at.nullable is False
    assert table.c.started_at.nullable is True
    assert table.c.completed_at.nullable is True
    assert table.c.updated_at.nullable is False

    # Timezone-aware datetimes
    for col_name in ["created_at", "started_at", "completed_at", "updated_at"]:
        assert isinstance(table.c[col_name].type, DateTime)
        assert table.c[col_name].type.timezone is True

    # Constraints
    constraint_names = {c.name for c in table.constraints if c.name is not None}
    assert "ck_processing_jobs_attempt_count_non_negative" in constraint_names

    # Indexes
    index_names = {idx.name for idx in table.indexes}
    assert "ix_processing_jobs_artifact_id" in index_names


def test_enums_values():
    """Verify expected string-backed enum members and values."""
    assert [e.value for e in ArtifactFormat] == ["pdf", "docx"]
    assert [e.value for e in ArtifactValidationStatus] == ["PENDING", "VALIDATED", "REJECTED"]
    assert [e.value for e in ProcessingJobStatus] == ["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]


def test_no_forbidden_fields_in_models():
    """Ensure models do not contain raw resume-text, binary payload, or raw exception stack traces."""
    resume_artifact_cols = {c.name for c in Base.metadata.tables["resume_artifacts"].columns}
    processing_job_cols = {c.name for c in Base.metadata.tables["processing_jobs"].columns}

    forbidden_resume_terms = {"text", "raw_text", "extracted_text", "content", "file_data", "bytes"}
    forbidden_job_terms = {"stack_trace", "traceback", "raw_exception", "exception_details", "error_message"}

    assert forbidden_resume_terms.isdisjoint(resume_artifact_cols)
    assert forbidden_job_terms.isdisjoint(processing_job_cols)


def test_model_defaults_in_metadata_and_instantiation():
    """Verify ORM defaults, callable generators, server defaults, and instance creation."""
    artifact_table = Base.metadata.tables["resume_artifacts"]
    assert artifact_table.c.artifact_id.default.arg.__name__ == "uuid4"
    assert artifact_table.c.validation_status.default.arg == ArtifactValidationStatus.PENDING
    assert str(artifact_table.c.validation_status.server_default.arg) == "'PENDING'"

    job_table = Base.metadata.tables["processing_jobs"]
    assert job_table.c.job_id.default.arg.__name__ == "uuid4"
    assert job_table.c.status.default.arg == ProcessingJobStatus.QUEUED
    assert str(job_table.c.status.server_default.arg) == "'QUEUED'"
    assert job_table.c.attempt_count.default.arg == 0
    assert str(job_table.c.attempt_count.server_default.arg) == "0"

    art_id = uuid.uuid4()
    artifact = ResumeArtifact(
        artifact_id=art_id,
        original_filename="sample_resume.pdf",
        validation_status=ArtifactValidationStatus.PENDING,
    )
    assert artifact.artifact_id == art_id
    assert artifact.original_filename == "sample_resume.pdf"
    assert artifact.validation_status == ArtifactValidationStatus.PENDING

    job = ProcessingJob(
        artifact_id=art_id,
        status=ProcessingJobStatus.QUEUED,
        attempt_count=0,
    )
    assert job.artifact_id == art_id
    assert job.status == ProcessingJobStatus.QUEUED
    assert job.attempt_count == 0
