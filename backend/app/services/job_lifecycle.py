import enum
from datetime import datetime, timezone
from typing import Optional

from app.models.resume import ProcessingJob, ProcessingJobStatus


class SafeProcessingErrorCode(str, enum.Enum):
    DIRECT_TEXT_UNAVAILABLE = "DIRECT_TEXT_UNAVAILABLE"
    DOCUMENT_READ_FAILED = "DOCUMENT_READ_FAILED"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    ARTIFACT_INTEGRITY_FAILED = "ARTIFACT_INTEGRITY_FAILED"
    PROCESSING_TIMEOUT = "PROCESSING_TIMEOUT"
    INTERNAL_PROCESSING_ERROR = "INTERNAL_PROCESSING_ERROR"


class InvalidJobTransition(Exception):
    """Raised when an illegal lifecycle transition is attempted on a ProcessingJob."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def start_job(job: ProcessingJob) -> ProcessingJob:
    """Transition a job from QUEUED to RUNNING.

    Increments attempt_count, sets timezone-aware started_at, clears completed_at
    and safe_error_code, and sets status to RUNNING.
    Does not commit the session.
    """
    if job.status != ProcessingJobStatus.QUEUED:
        raise InvalidJobTransition(
            f"Cannot start job {job.job_id}: current status is {job.status}, expected QUEUED."
        )

    job.attempt_count = (job.attempt_count or 0) + 1
    job.started_at = datetime.now(timezone.utc)
    job.completed_at = None
    job.safe_error_code = None
    job.status = ProcessingJobStatus.RUNNING
    return job


def succeed_job(job: ProcessingJob) -> ProcessingJob:
    """Transition a job from RUNNING to SUCCEEDED.

    Sets completed_at, ensures safe_error_code is null, and sets status to SUCCEEDED.
    Does not commit the session.
    """
    if job.status != ProcessingJobStatus.RUNNING:
        raise InvalidJobTransition(
            f"Cannot succeed job {job.job_id}: current status is {job.status}, expected RUNNING."
        )

    job.status = ProcessingJobStatus.SUCCEEDED
    job.completed_at = datetime.now(timezone.utc)
    job.safe_error_code = None
    return job


def fail_job(job: ProcessingJob, error_code: SafeProcessingErrorCode) -> ProcessingJob:
    """Transition a job from RUNNING to FAILED.

    Requires a controlled safe error code from SafeProcessingErrorCode enum.
    Sets completed_at, stores only the safe error code string, and sets status to FAILED.
    Does not accept raw exception text.
    Does not commit the session.
    """
    if job.status != ProcessingJobStatus.RUNNING:
        raise InvalidJobTransition(
            f"Cannot fail job {job.job_id}: current status is {job.status}, expected RUNNING."
        )

    if not isinstance(error_code, SafeProcessingErrorCode):
        # Even if passed as a string, it must match an exact enum member
        try:
            error_code = SafeProcessingErrorCode(error_code)
        except ValueError:
            raise InvalidJobTransition(
                f"Invalid error_code '{error_code}'. Must be a member of SafeProcessingErrorCode."
            )

    job.status = ProcessingJobStatus.FAILED
    job.completed_at = datetime.now(timezone.utc)
    job.safe_error_code = error_code.value
    return job


def retry_job(job: ProcessingJob) -> ProcessingJob:
    """Transition a job from FAILED to QUEUED.

    Sets status to QUEUED, clears started_at, completed_at, and safe_error_code.
    Does not increment attempt_count until the next transition to RUNNING.
    Does not commit the session.
    """
    if job.status != ProcessingJobStatus.FAILED:
        raise InvalidJobTransition(
            f"Cannot retry job {job.job_id}: current status is {job.status}, expected FAILED."
        )

    job.status = ProcessingJobStatus.QUEUED
    job.started_at = None
    job.completed_at = None
    job.safe_error_code = None
    return job
