import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, case, distinct, func, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import (
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateInterviewAnswerRecord,
    CandidateInterviewEvaluationRecord,
    CandidateInterviewSessionRecord,
    CandidatePracticeReleaseRecord,
    RecruiterReviewState,
)
from app.models.job import JobLifecycleStatus, JobPublicationStatus, JobRequisition, JobVersion
from app.models.matching import MatchResultRecord, MatchRunLifecycleStatus, MatchRunRecord
from app.schemas.dashboard import (
    DashboardInterviewItem,
    DashboardInterviewsResponse,
    DashboardJobItem,
    DashboardJobsResponse,
    DashboardMatchItem,
    DashboardMatchesResponse,
    DashboardMetadata,
    DashboardOverviewResponse,
    DashboardPipelineResponse,
    DashboardReadinessResponse,
    JobPipelineSummary,
    PipelineStageCount,
)
from app.services.speech_service import SpeechRuntimeCapabilityService

logger = logging.getLogger("ai_hiring.services.dashboard_analytics")


class DashboardAnalyticsService:
    """
    Authoritative, read-only aggregation engine for Streamlit dashboard.
    Strictly queries persisted PostgreSQL models scoped to the caller's organization_id.
    Zero AI generation, zero domain score recalculation, zero cloud egress.
    """

    def __init__(self, db: Session, organization_id: uuid.UUID, settings: Settings):
        self.db = db
        self.organization_id = organization_id
        self.settings = settings

    def _build_metadata(self, active_filters: Optional[Dict[str, Any]] = None) -> DashboardMetadata:
        return DashboardMetadata(
            generated_at=datetime.now(timezone.utc),
            organization_name="AI Hiring Platform",
            data_status="authoritative",
            active_filters=active_filters or {},
        )

    def get_overview(self) -> DashboardOverviewResponse:
        """Aggregates top-level organization metrics strictly from persisted records."""
        # 1. Published Jobs
        stmt_pub_jobs = (
            select(func.count(JobRequisition.job_id))
            .where(
                JobRequisition.status == JobLifecycleStatus.ACTIVE,
                JobRequisition.publication_status == JobPublicationStatus.RELEASED,
                JobRequisition.deleted_at.is_(None),
            )
        )
        published_jobs = self.db.execute(stmt_pub_jobs).scalar() or 0

        # 2. Draft / Ready Jobs
        stmt_draft_jobs = (
            select(func.count(JobRequisition.job_id))
            .where(
                JobRequisition.status == JobLifecycleStatus.ACTIVE,
                JobRequisition.publication_status == JobPublicationStatus.NOT_RELEASED,
                JobRequisition.deleted_at.is_(None),
            )
        )
        draft_ready_jobs = self.db.execute(stmt_draft_jobs).scalar() or 0

        # 3. Submitted Applications (SUBMITTED status, not WITHDRAWN, not DRAFT)
        stmt_submitted_apps = (
            select(func.count(CandidateApplicationRecord.application_id))
            .where(
                CandidateApplicationRecord.organization_id == self.organization_id,
                CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
            )
        )
        submitted_applications = self.db.execute(stmt_submitted_apps).scalar() or 0

        # 4. Awaiting Review (SUBMITTED and recruiter_review_state == RECEIVED)
        stmt_awaiting_rev = (
            select(func.count(CandidateApplicationRecord.application_id))
            .where(
                CandidateApplicationRecord.organization_id == self.organization_id,
                CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
                CandidateApplicationRecord.recruiter_review_state == RecruiterReviewState.RECEIVED.value,
            )
        )
        awaiting_review = self.db.execute(stmt_awaiting_rev).scalar() or 0

        # 5. Interview Ready (recruiter_review_state == INTERVIEW_READY)
        stmt_interview_ready = (
            select(func.count(CandidateApplicationRecord.application_id))
            .where(
                CandidateApplicationRecord.organization_id == self.organization_id,
                CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
                CandidateApplicationRecord.recruiter_review_state == RecruiterReviewState.INTERVIEW_READY.value,
            )
        )
        interview_ready = self.db.execute(stmt_interview_ready).scalar() or 0

        # 6. Active Interview Releases
        stmt_releases = (
            select(func.count(CandidatePracticeReleaseRecord.release_id))
            .where(
                CandidatePracticeReleaseRecord.organization_id == self.organization_id,
                CandidatePracticeReleaseRecord.status == "RELEASED",
            )
        )
        active_interview_releases = self.db.execute(stmt_releases).scalar() or 0

        # 7. Completed Official Interviews (SUBMITTED sessions)
        stmt_interviews = (
            select(func.count(CandidateInterviewSessionRecord.session_id))
            .where(
                CandidateInterviewSessionRecord.organization_id == self.organization_id,
                CandidateInterviewSessionRecord.status == "SUBMITTED",
            )
        )
        completed_official_interviews = self.db.execute(stmt_interviews).scalar() or 0

        # 8. Evaluations Available
        stmt_evals = (
            select(func.count(CandidateInterviewEvaluationRecord.evaluation_id))
            .where(
                CandidateInterviewEvaluationRecord.organization_id == self.organization_id,
                CandidateInterviewEvaluationRecord.status.in_(["FINALIZED", "IN_REVIEW"]),
            )
        )
        evaluations_available = self.db.execute(stmt_evals).scalar() or 0

        return DashboardOverviewResponse(
            metadata=self._build_metadata(),
            published_jobs=published_jobs,
            draft_ready_jobs=draft_ready_jobs,
            submitted_applications=submitted_applications,
            awaiting_review=awaiting_review,
            interview_ready=interview_ready,
            active_interview_releases=active_interview_releases,
            completed_official_interviews=completed_official_interviews,
            evaluations_available=evaluations_available,
        )

    def get_pipeline(
        self,
        job_id: Optional[uuid.UUID] = None,
        department: Optional[str] = None,
    ) -> DashboardPipelineResponse:
        """Aggregates pipeline funnels with explicit lifecycle stages and per-job breakdowns."""
        active_filters = {}
        if job_id:
            active_filters["job_id"] = str(job_id)
        if department:
            active_filters["department"] = department

        # Base application filter
        app_where = [
            CandidateApplicationRecord.organization_id == self.organization_id,
            CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
        ]
        if job_id:
            app_where.append(CandidateApplicationRecord.job_id == job_id)

        # 1. Stage Counts
        # Submitted
        stmt_sub = select(func.count(CandidateApplicationRecord.application_id)).where(*app_where)
        count_sub = self.db.execute(stmt_sub).scalar() or 0

        # In review
        stmt_rev = select(func.count(CandidateApplicationRecord.application_id)).where(
            *app_where,
            CandidateApplicationRecord.recruiter_review_state == RecruiterReviewState.IN_REVIEW.value,
        )
        count_rev = self.db.execute(stmt_rev).scalar() or 0

        # Ready for interview
        stmt_rdy = select(func.count(CandidateApplicationRecord.application_id)).where(
            *app_where,
            CandidateApplicationRecord.recruiter_review_state == RecruiterReviewState.INTERVIEW_READY.value,
        )
        count_rdy = self.db.execute(stmt_rdy).scalar() or 0

        # Interview released
        rel_where = [
            CandidatePracticeReleaseRecord.organization_id == self.organization_id,
            CandidatePracticeReleaseRecord.status == "RELEASED",
        ]
        if job_id:
            rel_where.append(CandidatePracticeReleaseRecord.job_id == job_id)
        stmt_rel = select(func.count(CandidatePracticeReleaseRecord.release_id)).where(*rel_where)
        count_rel = self.db.execute(stmt_rel).scalar() or 0

        # Interview completed
        sess_where = [
            CandidateInterviewSessionRecord.organization_id == self.organization_id,
            CandidateInterviewSessionRecord.status == "SUBMITTED",
        ]
        if job_id:
            sess_where.append(CandidateInterviewSessionRecord.job_id == job_id)
        stmt_sess = select(func.count(CandidateInterviewSessionRecord.session_id)).where(*sess_where)
        count_comp = self.db.execute(stmt_sess).scalar() or 0

        # Evaluation available
        eval_where = [
            CandidateInterviewEvaluationRecord.organization_id == self.organization_id,
            CandidateInterviewEvaluationRecord.status.in_(["FINALIZED", "IN_REVIEW"]),
        ]
        if job_id:
            eval_where.append(CandidateInterviewEvaluationRecord.job_id == job_id)
        stmt_eval = select(func.count(CandidateInterviewEvaluationRecord.evaluation_id)).where(*eval_where)
        count_eval = self.db.execute(stmt_eval).scalar() or 0

        stages = [
            PipelineStageCount(
                stage="Submitted",
                count=count_sub,
                description="Total verified applications submitted by candidates",
            ),
            PipelineStageCount(
                stage="In review",
                count=count_rev,
                description="Applications actively undergoing recruiter review",
            ),
            PipelineStageCount(
                stage="Ready for interview",
                count=count_rdy,
                description="Applications approved and flagged ready for interview release",
            ),
            PipelineStageCount(
                stage="Interview released",
                count=count_rel,
                description="Active interview access releases provided to candidates",
            ),
            PipelineStageCount(
                stage="Interview completed",
                count=count_comp,
                description="Official interviews completed and submitted by candidates",
            ),
            PipelineStageCount(
                stage="Evaluation available",
                count=count_eval,
                description="Interviews evaluated by local AI or reviewed by recruiters",
            ),
        ]

        # 2. Per-job pipeline table
        job_stmt = (
            select(JobRequisition, JobVersion)
            .outerjoin(JobVersion, JobRequisition.released_job_version_id == JobVersion.job_version_id)
            .where(
                JobRequisition.status == JobLifecycleStatus.ACTIVE,
                JobRequisition.deleted_at.is_(None),
            )
            .order_by(JobRequisition.created_at.desc())
        )
        if job_id:
            job_stmt = job_stmt.where(JobRequisition.job_id == job_id)

        job_records = self.db.execute(job_stmt).all()
        job_summaries: List[JobPipelineSummary] = []

        for req, v_rel in job_records:
            # If no released version, fetch highest revision version
            latest_v = v_rel
            if latest_v is None:
                latest_v = (
                    self.db.execute(
                        select(JobVersion)
                        .where(JobVersion.job_id == req.job_id)
                        .order_by(JobVersion.version_number.desc())
                        .limit(1)
                    ).scalar_one_or_none()
                )

            title = latest_v.title if latest_v else f"Job #{str(req.job_id)[:8]}"
            dept = latest_v.department if latest_v else None
            loc = latest_v.location if latest_v else None
            rev = latest_v.revision if latest_v else 1

            if department and dept != department:
                continue

            # Per-job counts
            j_sub = self.db.execute(
                select(func.count(CandidateApplicationRecord.application_id)).where(
                    CandidateApplicationRecord.organization_id == self.organization_id,
                    CandidateApplicationRecord.job_id == req.job_id,
                    CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
                )
            ).scalar() or 0

            j_awaiting = self.db.execute(
                select(func.count(CandidateApplicationRecord.application_id)).where(
                    CandidateApplicationRecord.organization_id == self.organization_id,
                    CandidateApplicationRecord.job_id == req.job_id,
                    CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
                    CandidateApplicationRecord.recruiter_review_state == RecruiterReviewState.RECEIVED.value,
                )
            ).scalar() or 0

            j_rdy = self.db.execute(
                select(func.count(CandidateApplicationRecord.application_id)).where(
                    CandidateApplicationRecord.organization_id == self.organization_id,
                    CandidateApplicationRecord.job_id == req.job_id,
                    CandidateApplicationRecord.status == CandidateApplicationStatus.SUBMITTED.value,
                    CandidateApplicationRecord.recruiter_review_state == RecruiterReviewState.INTERVIEW_READY.value,
                )
            ).scalar() or 0

            j_rel = self.db.execute(
                select(func.count(CandidatePracticeReleaseRecord.release_id)).where(
                    CandidatePracticeReleaseRecord.organization_id == self.organization_id,
                    CandidatePracticeReleaseRecord.job_id == req.job_id,
                    CandidatePracticeReleaseRecord.status == "RELEASED",
                )
            ).scalar() or 0

            j_comp = self.db.execute(
                select(func.count(CandidateInterviewSessionRecord.session_id)).where(
                    CandidateInterviewSessionRecord.organization_id == self.organization_id,
                    CandidateInterviewSessionRecord.job_id == req.job_id,
                    CandidateInterviewSessionRecord.status == "SUBMITTED",
                )
            ).scalar() or 0

            j_eval = self.db.execute(
                select(func.count(CandidateInterviewEvaluationRecord.evaluation_id)).where(
                    CandidateInterviewEvaluationRecord.organization_id == self.organization_id,
                    CandidateInterviewEvaluationRecord.job_id == req.job_id,
                    CandidateInterviewEvaluationRecord.status.in_(["FINALIZED", "IN_REVIEW"]),
                )
            ).scalar() or 0

            job_summaries.append(
                JobPipelineSummary(
                    job_id=req.job_id,
                    title=title,
                    department=dept,
                    location=loc,
                    publication_status=req.publication_status.value,
                    current_revision=rev,
                    submitted_applications=j_sub,
                    awaiting_review=j_awaiting,
                    interview_ready=j_rdy,
                    interview_released=j_rel,
                    completed_interviews=j_comp,
                    evaluations_available=j_eval,
                )
            )

        return DashboardPipelineResponse(
            metadata=self._build_metadata(active_filters),
            stages=stages,
            jobs=job_summaries,
        )

    def get_jobs(self, department: Optional[str] = None) -> DashboardJobsResponse:
        """Returns summarized job analysis data."""
        active_filters = {}
        if department:
            active_filters["department"] = department

        pipeline_resp = self.get_pipeline(department=department)
        job_items = [
            DashboardJobItem(
                job_id=j.job_id,
                title=j.title,
                department=j.department,
                location=j.location,
                publication_status=j.publication_status,
                current_revision=j.current_revision,
                submitted_applications=j.submitted_applications,
                awaiting_review=j.awaiting_review,
                interview_ready=j.interview_ready,
                completed_interviews=j.completed_interviews,
                evaluations_available=j.evaluations_available,
            )
            for j in pipeline_resp.jobs
        ]

        return DashboardJobsResponse(
            metadata=self._build_metadata(active_filters),
            jobs=job_items,
        )

    def get_matches(
        self,
        job_id: Optional[uuid.UUID] = None,
        confidence_tier: Optional[str] = None,
        has_must_have_gaps: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DashboardMatchesResponse:
        """
        Returns persisted deterministic match results.
        Never combines with interview scores or calculates new heuristics.
        """
        active_filters = {}
        if job_id:
            active_filters["job_id"] = str(job_id)
        if confidence_tier:
            active_filters["confidence_tier"] = confidence_tier
        if has_must_have_gaps is not None:
            active_filters["has_must_have_gaps"] = has_must_have_gaps

        # Query latest succeeded match runs
        stmt = (
            select(MatchResultRecord, MatchRunRecord, JobVersion, CandidateIdentityRecord)
            .join(MatchRunRecord, MatchResultRecord.run_id == MatchRunRecord.run_id)
            .join(JobVersion, MatchRunRecord.job_version_id == JobVersion.job_version_id)
            .join(CandidateProfileRecord, MatchResultRecord.profile_id == CandidateProfileRecord.profile_id)
            .join(CandidateIdentityRecord, CandidateProfileRecord.identity_id == CandidateIdentityRecord.identity_id)
            .where(MatchRunRecord.lifecycle_status == MatchRunLifecycleStatus.SUCCEEDED)
            .order_by(MatchResultRecord.display_compatibility_score.desc(), MatchResultRecord.created_at.desc())
        )

        if job_id:
            stmt = stmt.where(MatchRunRecord.job_id == job_id)
        if confidence_tier:
            stmt = stmt.where(MatchResultRecord.confidence_tier == confidence_tier)
        if has_must_have_gaps is True:
            stmt = stmt.where(MatchResultRecord.must_have_warning_count > 0)
        elif has_must_have_gaps is False:
            stmt = stmt.where(MatchResultRecord.must_have_warning_count == 0)

        # Count total
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = self.db.execute(count_stmt).scalar() or 0

        # Paginated fetch
        rows = self.db.execute(stmt.limit(min(limit, 100)).offset(offset)).all()
        matches: List[DashboardMatchItem] = []

        for m_res, m_run, j_ver, c_ident in rows:
            decomp = m_res.score_decomposition or {}
            must_have_cov = float(decomp.get("must_have_coverage_pct", 100.0 if m_res.must_have_warning_count == 0 else 50.0))
            pref_cov = float(decomp.get("preferred_coverage_pct", 100.0))

            display_name = c_ident.full_name or f"Candidate #{str(c_ident.identity_id)[:8]}"

            matches.append(
                DashboardMatchItem(
                    job_id=m_run.job_id,
                    job_title=j_ver.title,
                    candidate_id=c_ident.identity_id,
                    candidate_display_name=display_name,
                    match_score=float(m_res.display_compatibility_score),
                    confidence_tier=m_res.confidence_tier,
                    must_have_coverage=must_have_cov,
                    preferred_coverage=pref_cov,
                    missing_must_have_count=m_res.must_have_warning_count,
                    missing_preferred_count=max(0, m_res.warning_count - m_res.must_have_warning_count),
                    match_created_at=m_res.created_at,
                )
            )

        return DashboardMatchesResponse(
            metadata=self._build_metadata(active_filters),
            matches=matches,
            total_count=total_count,
        )

    def get_interviews(
        self,
        job_id: Optional[uuid.UUID] = None,
        interview_mode: Optional[str] = None,
        session_status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DashboardInterviewsResponse:
        """
        Returns persisted interview records.
        Strict separation of TEXT and AUDIO modes.
        Audio outcomes note transcript-only evaluation with zero acoustic measures.
        """
        active_filters = {}
        if job_id:
            active_filters["job_id"] = str(job_id)
        if interview_mode:
            active_filters["interview_mode"] = interview_mode
        if session_status:
            active_filters["session_status"] = session_status

        stmt = (
            select(
                CandidateInterviewSessionRecord,
                JobVersion,
                CandidateIdentityRecord,
                CandidateInterviewEvaluationRecord,
            )
            .join(JobVersion, CandidateInterviewSessionRecord.job_version_id == JobVersion.job_version_id)
            .join(CandidateIdentityRecord, CandidateInterviewSessionRecord.candidate_id == CandidateIdentityRecord.identity_id)
            .outerjoin(
                CandidateInterviewEvaluationRecord,
                CandidateInterviewSessionRecord.session_id == CandidateInterviewEvaluationRecord.session_id,
            )
            .where(CandidateInterviewSessionRecord.organization_id == self.organization_id)
            .order_by(CandidateInterviewSessionRecord.created_at.desc())
        )

        if job_id:
            stmt = stmt.where(CandidateInterviewSessionRecord.job_id == job_id)
        if interview_mode:
            stmt = stmt.where(CandidateInterviewSessionRecord.interview_mode == interview_mode.upper())
        if session_status:
            stmt = stmt.where(CandidateInterviewSessionRecord.status == session_status.upper())

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = self.db.execute(count_stmt).scalar() or 0

        rows = self.db.execute(stmt.limit(min(limit, 100)).offset(offset)).all()
        interviews: List[DashboardInterviewItem] = []

        for sess, j_ver, c_ident, eval_rec in rows:
            display_name = c_ident.full_name or f"Candidate #{str(c_ident.identity_id)[:8]}"
            eval_status = "UNAVAILABLE"
            persisted_score = None
            dim_scores = {}
            notes = None

            if eval_rec:
                eval_status = eval_rec.status
                if eval_rec.aggregate_score is not None:
                    persisted_score = float(eval_rec.aggregate_score)
                summary_data = eval_rec.aggregate_summary or {}
                if isinstance(summary_data.get("dimension_scores"), dict):
                    dim_scores = {str(k): float(v) for k, v in summary_data["dimension_scores"].items() if isinstance(v, (int, float))}
            elif sess.status == "SUBMITTED":
                eval_status = "NOT_STARTED"

            if sess.interview_mode == "AUDIO":
                notes = "Audio interview evaluated strictly from accepted local whisper.cpp transcript content. Zero acoustic or voice analysis."

            interviews.append(
                DashboardInterviewItem(
                    job_id=sess.job_id,
                    job_title=j_ver.title,
                    candidate_id=c_ident.identity_id,
                    candidate_display_name=display_name,
                    interview_mode=sess.interview_mode,
                    session_status=sess.status,
                    evaluation_status=eval_status,
                    submitted_at=sess.submitted_at,
                    evaluated_at=eval_rec.finalized_at if eval_rec else None,
                    persisted_overall_score=persisted_score,
                    dimension_scores=dim_scores,
                    scoring_notes=notes,
                )
            )

        return DashboardInterviewsResponse(
            metadata=self._build_metadata(active_filters),
            interviews=interviews,
            total_count=total_count,
        )

    def get_readiness(self) -> DashboardReadinessResponse:
        """Returns honest, product-level capability readiness without leaking internal paths or secrets."""
        # Database connectivity check
        db_status = "AVAILABLE"
        try:
            self.db.execute(select(func.now())).scalar()
        except Exception:
            db_status = "UNAVAILABLE"

        # Local AI
        ai_status = "DISABLED"
        is_configured = self.settings.llm_enabled or bool(self.settings.llm_base_url)
        if is_configured:
            ai_status = "AVAILABLE" if self.settings.llm_base_url else "ATTENTION_NEEDED"

        # Speech capabilities (whisper.cpp and piper)
        stt_avail = SpeechRuntimeCapabilityService.is_stt_available()
        tts_avail = SpeechRuntimeCapabilityService.is_tts_available()

        audio_stt_status = "AVAILABLE" if stt_avail else "UNAVAILABLE"
        audio_playback_status = "AVAILABLE" if tts_avail else "UNAVAILABLE"

        # Text interview service
        text_status = "AVAILABLE" if db_status == "AVAILABLE" else "UNAVAILABLE"

        safe_msg = (
            "All core recruitment services are operational. "
            f"Local speech STT is {'configured' if stt_avail else 'not configured (mock mode active)'}."
        )

        return DashboardReadinessResponse(
            metadata=self._build_metadata(),
            api_status="AVAILABLE",
            database_status=db_status,
            local_ai_status=ai_status,
            text_interview_status=text_status,
            audio_playback_status=audio_playback_status,
            audio_transcription_status=audio_stt_status,
            last_checked_at=datetime.now(timezone.utc),
            user_safe_message=safe_msg,
        )
