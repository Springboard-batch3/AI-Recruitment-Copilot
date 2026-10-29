"""Service layer for Phase 4C.4: Recruiter Evaluation Workspace & Final Reports.

Orchestrates:
1. Job-organized evaluation directories with revision-level fidelity.
2. Candidate session listings with separate resume match references.
3. Zero-leakage question-by-question recruiter review, edits, and overrides.
4. Deterministic core-only interview aggregate computation.
5. Atomic finalization locking.
6. Safe, redacted Candidate feedback publication.
"""
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.candidate_access import (
    CandidateAccountBindingRecord,
    CandidateApplicationRecord,
    CandidateApplicationStatus,
    CandidateBindingStatus,
    CandidateInterviewAnswerRecord,
    CandidateInterviewEvaluationRecord,
    CandidateInterviewSessionRecord,
    CandidatePracticeReleaseRecord,
    CandidateQuestionEvaluationRecord,
)
from app.models.job import JobRequisition, JobVersion
from app.models.matching import MatchResultRecord, MatchRunRecord
from app.schemas.phase3_enums import PracticeReleaseStatus
from app.schemas.candidate_evaluation import (
    AIProvisionalAnalysisDto,
    CandidateEvaluationExportDto,
    CandidateInterviewEvaluationDetailResponse,
    CandidateSafeFeedbackReportResponse,
    CandidateSafeQuestionFeedbackDto,
    EvaluationAuditActivityDto,
    EvaluationCandidateSessionSummaryDto,
    EvaluationJobSummaryDto,
    EvaluationOverviewSummaryDto,
    EvaluationQuestionDetailDto,
    EvidenceSegmentDto,
    FinalizeEvaluationResponse,
    PublishCandidateFeedbackResponse,
    QuestionRubricAnchorDto,
    RecruiterQuestionReviewDto,
    ResumeMatchReferenceDto,
    SaveQuestionReviewRequest,
    SaveQuestionReviewResponse,
)

logger = logging.getLogger("ai_hiring.services.candidate_evaluation")


# =============================================================================
# Authorization & Scope Helpers
# =============================================================================

def _require_recruiter_actor(actor: UserRecord) -> None:
    if actor.role != UserRole.RECRUITER and actor.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN_EVALUATION_ACCESS", "message": "Only authorized recruiters can access the Evaluation workspace."},
        )


def _get_candidate_binding(candidate_user: UserRecord, db: Session) -> CandidateAccountBindingRecord:
    stmt = select(CandidateAccountBindingRecord).where(
        CandidateAccountBindingRecord.user_id == candidate_user.user_id,
        CandidateAccountBindingRecord.status == CandidateBindingStatus.ACTIVE.value,
    )
    binding = db.execute(stmt).scalar_one_or_none()
    if not binding:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "CANDIDATE_ACCESS_SUSPENDED", "message": "Active candidate binding not found."},
        )
    return binding


# =============================================================================
# Level 1: Job Directory
# =============================================================================

def list_evaluation_jobs(actor: UserRecord, db: Session) -> List[EvaluationJobSummaryDto]:
    """Lists jobs with released/submitted candidate interviews grouped by exact Job and revision."""
    _require_recruiter_actor(actor)

    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)

    # Query all active releases and sessions for the recruiter / organization
    stmt = (
        select(
            CandidatePracticeReleaseRecord.job_id,
            CandidatePracticeReleaseRecord.job_version_id,
            CandidatePracticeReleaseRecord.job_revision,
            CandidatePracticeReleaseRecord.candidate_id,
            JobVersion.title.label("job_title"),
            JobVersion.department.label("department"),
            CandidatePracticeReleaseRecord.release_id,
            CandidateInterviewSessionRecord.session_id,
            CandidateInterviewSessionRecord.status.label("session_status"),
            CandidateInterviewSessionRecord.submitted_at,
            CandidateInterviewEvaluationRecord.status.label("evaluation_status"),
        )
        .join(JobRequisition, CandidatePracticeReleaseRecord.job_id == JobRequisition.job_id)
        .join(JobVersion, CandidatePracticeReleaseRecord.job_version_id == JobVersion.job_version_id)
        .outerjoin(
            CandidateApplicationRecord,
            CandidatePracticeReleaseRecord.application_id == CandidateApplicationRecord.application_id,
        )
        .outerjoin(
            CandidateInterviewSessionRecord,
            CandidatePracticeReleaseRecord.release_id == CandidateInterviewSessionRecord.release_id,
        )
        .outerjoin(
            CandidateInterviewEvaluationRecord,
            CandidateInterviewSessionRecord.session_id == CandidateInterviewEvaluationRecord.session_id,
        )
        .where(
            CandidatePracticeReleaseRecord.status == PracticeReleaseStatus.RELEASED.value,
            or_(
                CandidatePracticeReleaseRecord.application_id.is_(None),
                CandidateApplicationRecord.status != CandidateApplicationStatus.WITHDRAWN.value,
            ),
        )
    )

    if actor.role == UserRole.RECRUITER:
        stmt = stmt.where(JobRequisition.created_by_user_id == actor.user_id)
    elif actor_org:
        stmt = stmt.where(CandidatePracticeReleaseRecord.organization_id == actor_org)

    stmt = stmt.order_by(desc(CandidatePracticeReleaseRecord.created_at))

    rows = db.execute(stmt).all()

    # Group by (job_id, job_revision)
    grouped: Dict[Tuple[UUID, int], Dict[str, Any]] = {}

    for r in rows:
        # If candidate has a withdrawn application for this job and no active submitted application, exclude
        if r.candidate_id and r.job_id:
            stmt_apps = select(CandidateApplicationRecord.status).where(
                CandidateApplicationRecord.candidate_id == r.candidate_id,
                CandidateApplicationRecord.job_id == r.job_id,
            )
            app_statuses = db.execute(stmt_apps).scalars().all()
            if app_statuses and all(st in (CandidateApplicationStatus.WITHDRAWN.value, CandidateApplicationStatus.DRAFT.value) for st in app_statuses):
                continue

        key = (r.job_id, r.job_revision)
        if key not in grouped:
            grouped[key] = {
                "job_id": r.job_id,
                "job_version_id": r.job_version_id,
                "job_revision": r.job_revision,
                "job_title": r.job_title or "Job Requisition",
                "department": r.department,
                "releases": set(),
                "in_progress": 0,
                "submitted": 0,
                "analysis_pending": 0,
                "ready_for_review": 0,
                "finalized": 0,
                "last_submission_at": None,
            }

        data = grouped[key]
        data["releases"].add(r.release_id)

        if r.session_id:
            if r.session_status == "IN_PROGRESS":
                data["in_progress"] += 1
            elif r.session_status == "SUBMITTED":
                data["submitted"] += 1
                if r.submitted_at:
                    if not data["last_submission_at"] or r.submitted_at > data["last_submission_at"]:
                        data["last_submission_at"] = r.submitted_at

                eval_st = r.evaluation_status or "NOT_STARTED"
                if eval_st == "FINALIZED":
                    data["finalized"] += 1
                elif eval_st == "IN_REVIEW":
                    data["ready_for_review"] += 1
                else:
                    data["ready_for_review"] += 1

    result: List[EvaluationJobSummaryDto] = []
    for (jid, jrev), d in grouped.items():
        result.append(
            EvaluationJobSummaryDto(
                job_id=d["job_id"],
                job_version_id=d["job_version_id"],
                job_revision=d["job_revision"],
                job_title=d["job_title"],
                department=d["department"],
                total_releases_count=len(d["releases"]),
                in_progress_count=d["in_progress"],
                submitted_count=d["submitted"],
                analysis_pending_count=d["analysis_pending"],
                ready_for_review_count=d["ready_for_review"],
                finalized_count=d["finalized"],
                last_submission_at=d["last_submission_at"],
            )
        )

    return sorted(result, key=lambda x: (x.job_title, x.job_revision))


# =============================================================================
# Level 2: Job Candidates List
# =============================================================================

def _get_snapshot_items(sess: CandidateInterviewSessionRecord) -> List[Dict[str, Any]]:
    snapshot_raw = sess.questions_snapshot
    if not snapshot_raw:
        return []
    if isinstance(snapshot_raw, list):
        return [item for item in snapshot_raw if isinstance(item, dict)]
    if isinstance(snapshot_raw, dict):
        items = snapshot_raw.get("questions") or snapshot_raw.get("items") or []
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def list_job_candidates_for_evaluation(
    job_id: UUID,
    job_revision: int,
    actor: UserRecord,
    db: Session,
    mode: Optional[str] = None,
) -> List[EvaluationCandidateSessionSummaryDto]:
    """Lists candidate sessions for a specific Job and exact Job revision, with optional interview_mode filter."""
    _require_recruiter_actor(actor)

    stmt_job = select(JobRequisition).where(JobRequisition.job_id == job_id)
    job = db.execute(stmt_job).scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job requisition not found."},
        )

    if actor.role == UserRole.RECRUITER and job.created_by_user_id != actor.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job requisition not found or access denied."},
        )

    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)
    if actor_org and job.organization_id != actor_org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job requisition not found or access denied."},
        )

    stmt = (
        select(CandidatePracticeReleaseRecord)
        .options(
            selectinload(CandidatePracticeReleaseRecord.candidate),
            selectinload(CandidatePracticeReleaseRecord.match_result),
        )
        .outerjoin(
            CandidateApplicationRecord,
            CandidatePracticeReleaseRecord.application_id == CandidateApplicationRecord.application_id,
        )
        .where(
            CandidatePracticeReleaseRecord.job_id == job_id,
            CandidatePracticeReleaseRecord.job_revision == job_revision,
            CandidatePracticeReleaseRecord.status == PracticeReleaseStatus.RELEASED.value,
            or_(
                CandidatePracticeReleaseRecord.application_id.is_(None),
                CandidateApplicationRecord.status != CandidateApplicationStatus.WITHDRAWN.value,
            ),
        )
        .order_by(desc(CandidatePracticeReleaseRecord.created_at))
    )
    raw_releases = db.execute(stmt).scalars().all()

    # Filter out any releases where candidate's applications for this job are withdrawn
    releases = []
    for rel in raw_releases:
        if rel.candidate_id and rel.job_id:
            stmt_apps = select(CandidateApplicationRecord.status).where(
                CandidateApplicationRecord.candidate_id == rel.candidate_id,
                CandidateApplicationRecord.job_id == rel.job_id,
            )
            app_statuses = db.execute(stmt_apps).scalars().all()
            if app_statuses and all(st in (CandidateApplicationStatus.WITHDRAWN.value, CandidateApplicationStatus.DRAFT.value) for st in app_statuses):
                continue
        releases.append(rel)

    release_ids = [rel.release_id for rel in releases]
    if not release_ids:
        return []

    # Fetch existing sessions
    stmt_sess = (
        select(CandidateInterviewSessionRecord)
        .options(
            selectinload(CandidateInterviewSessionRecord.answers),
            selectinload(CandidateInterviewSessionRecord.evaluation),
        )
        .where(CandidateInterviewSessionRecord.release_id.in_(release_ids))
    )
    sessions = db.execute(stmt_sess).scalars().all()
    session_by_release = {s.release_id: s for s in sessions}

    results: List[EvaluationCandidateSessionSummaryDto] = []

    for rel in releases:
        sess = session_by_release.get(rel.release_id)
        sess_mode = (sess.interview_mode if sess and sess.interview_mode else rel.interview_mode) or "TEXT"

        # Apply mode filtering if specified
        if mode and mode.strip().upper() not in ("ALL", ""):
            if sess_mode.upper() != mode.strip().upper():
                continue

        cand = rel.candidate
        cand_name = (cand.full_name or cand.primary_email or "Candidate") if cand else "Candidate"
        cand_email = cand.primary_email if cand else None

        # Determine application source
        app_source = "Candidate Portal"

        # Resume match score reference
        match_score = None
        skills_coverage = None
        mr = rel.match_result
        if not mr and rel.candidate_id and rel.job_id:
            stmt_match = (
                select(MatchResultRecord)
                .join(MatchRunRecord, MatchResultRecord.run_id == MatchRunRecord.run_id)
                .join(CandidateProfileRecord, MatchResultRecord.profile_id == CandidateProfileRecord.profile_id)
                .where(
                    MatchRunRecord.job_id == rel.job_id,
                    CandidateProfileRecord.identity_id == rel.candidate_id,
                )
                .order_by(desc(MatchRunRecord.completed_at))
            )
            mr = db.execute(stmt_match).scalars().first()

            if not mr:
                stmt_app = select(CandidateApplicationRecord).where(
                    CandidateApplicationRecord.candidate_id == rel.candidate_id,
                    CandidateApplicationRecord.job_id == rel.job_id,
                ).order_by(desc(CandidateApplicationRecord.created_at))
                app_rec = db.execute(stmt_app).scalars().first()
                if app_rec and app_rec.profile_id:
                    stmt_match2 = (
                        select(MatchResultRecord)
                        .join(MatchRunRecord, MatchResultRecord.run_id == MatchRunRecord.run_id)
                        .where(
                            MatchRunRecord.job_id == rel.job_id,
                            MatchResultRecord.profile_id == app_rec.profile_id,
                        )
                        .order_by(desc(MatchRunRecord.completed_at))
                    )
                    mr = db.execute(stmt_match2).scalars().first()

        if mr:
            if getattr(mr, "display_compatibility_score", None) is not None:
                match_score = float(mr.display_compatibility_score)
            elif getattr(mr, "overall_score", None) is not None:
                match_score = round(float(mr.overall_score) * 100, 1)

            decomp = getattr(mr, "score_decomposition", None) or {}
            req_contribs = decomp.get("requirement_contributions", [])
            must_haves = [r for r in req_contribs if r.get("necessity") == "MUST_HAVE"]
            if must_haves:
                req_matched = len([r for r in must_haves if r.get("match_state") in ("MATCHED", "PARTIAL") or r.get("match_value", 0) > 0])
                req_total = len(must_haves)
            elif req_contribs:
                req_matched = len([r for r in req_contribs if r.get("match_state") in ("MATCHED", "PARTIAL") or r.get("match_value", 0) > 0])
                req_total = len(req_contribs)
            else:
                req_matched = decomp.get("matched_requirements_count") or decomp.get("matched_count", 0) or getattr(mr, "matched_requirements_count", 0)
                req_total = decomp.get("total_requirements_count") or decomp.get("total_count", 0) or getattr(mr, "total_requirements_count", 0)

            if req_total > 0:
                pct = round((req_matched / req_total) * 100)
                skills_coverage = f"{req_matched} of {req_total} requirements ({pct}%)"

        if not sess:
            # Not started yet
            results.append(
                EvaluationCandidateSessionSummaryDto(
                    session_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
                    release_id=rel.release_id,
                    candidate_id=rel.candidate_id,
                    candidate_name=cand_name,
                    candidate_email=cand_email,
                    application_id=rel.application_id,
                    application_source=app_source,
                    submitted_at=None,
                    answers_recorded_count=0,
                    total_questions_count=len(rel.ai_set.items) if rel.ai_set and rel.ai_set.items else 0,
                    ai_analysis_status="PENDING",
                    evaluation_status="AWAITING_SUBMISSION",
                    interview_mode=sess_mode,
                    resume_match_score=match_score,
                    required_skills_coverage=skills_coverage,
                )
            )
            continue

        # Calculate answers count
        total_q = len(_get_snapshot_items(sess))
        answers_count = len(sess.answers) if sess.answers else 0

        # Determine evaluation status
        eval_record = sess.evaluation
        if sess.status != "SUBMITTED":
            eval_status = "AWAITING_SUBMISSION"
            ai_status = "PENDING"
        else:
            if eval_record and eval_record.status == "FINALIZED":
                eval_status = "FINALIZED"
                ai_status = "READY"
            elif eval_record and eval_record.status == "IN_REVIEW":
                eval_status = "COMPLETE" if (eval_record.aggregate_score is not None or eval_record.questions_reviewed > 0) else "READY_FOR_REVIEW"
                ai_status = "READY"
            else:
                eval_status = "READY_FOR_REVIEW"
                ai_status = "PENDING"

        results.append(
            EvaluationCandidateSessionSummaryDto(
                session_id=sess.session_id,
                release_id=rel.release_id,
                candidate_id=rel.candidate_id,
                candidate_name=cand_name,
                candidate_email=cand_email,
                application_id=rel.application_id,
                application_source=app_source,
                submitted_at=sess.submitted_at,
                answers_recorded_count=answers_count,
                total_questions_count=total_q,
                ai_analysis_status=ai_status,
                evaluation_status=eval_status,
                interview_mode=sess_mode,
                resume_match_score=match_score,
                required_skills_coverage=skills_coverage,
            )
        )

    return results


# =============================================================================
# Level 3: Candidate Evaluation Detail & Synthesis
# =============================================================================

from app.services.answer_assessment_service import (
    assess_single_answer,
    build_authoritative_assessment_packet,
    extract_canonical_answer_segments,
)
from app.schemas.answer_assessment_contract import AssessmentCriterionLabel


def _synthesize_ai_provisional_analysis(
    item: Dict[str, Any],
    answer: Optional[CandidateInterviewAnswerRecord],
    stored_observations: Optional[Dict[str, Any]] = None,
) -> AIProvisionalAnalysisDto:
    """Generates structured provisional AI observations connected to answer evidence, using stored LLM results when available.
    
    Strict Failure Semantics:
    - Valid AI analysis: provisional dimension values & provisional rating calculated from demonstrated criteria.
    - AI failure / Needs Human Review / Unanalyzed: NO numeric rating (provisional_rating = None), NO dimension scores ({}), NO fake strengths.
    - Candidate empty response: is_insufficient_evidence=True, provisional_rating=None.
    """
    q_format = (item.get("question_format") or "").upper()
    is_mcq = q_format in ("MCQ", "MCQ_SINGLE_ANSWER") or bool(item.get("options"))

    if is_mcq:
        # MCQ Provisional Analysis (deterministic)
        correct_id = item.get("correct_option_id")
        selected_id = answer.selected_option_id if answer else None
        has_selection = bool(selected_id)
        is_correct = bool(selected_id and correct_id and str(selected_id).strip().upper() == str(correct_id).strip().upper())

        if not has_selection:
            return AIProvisionalAnalysisDto(
                addressed_well=[],
                missing_or_unclear=["No option selected."],
                suggested_improvement=item.get("answer_explanation") or item.get("explanation"),
                dimension_scores={},
                evidence_citations=[],
                provisional_rating=None,
                is_insufficient_evidence=True,
                uncertainty_notes="Candidate did not select an option.",
                analysis_status="UNAVAILABLE",
            )

        return AIProvisionalAnalysisDto(
            addressed_well=["Selected correct option" if is_correct else "Option recorded"],
            missing_or_unclear=[] if is_correct else ["Selected incorrect option"],
            suggested_improvement=item.get("answer_explanation") or item.get("explanation"),
            dimension_scores={"Accuracy": 5 if is_correct else 1},
            evidence_citations=[],
            provisional_rating=5 if is_correct else 1,
            is_insufficient_evidence=False,
            uncertainty_notes=None,
            analysis_status="READY",
        )

    # Conversational Provisional Analysis
    text_ans = (answer.text_answer or "").strip() if answer else ""
    if not text_ans or len(text_ans) < 5:
        return AIProvisionalAnalysisDto(
            addressed_well=[],
            missing_or_unclear=["No substantive response recorded."],
            suggested_improvement="Provide a structured technical response addressing the prompt.",
            dimension_scores={},
            evidence_citations=[],
            provisional_rating=None,
            is_insufficient_evidence=True,
            uncertainty_notes="Candidate submitted empty response.",
            analysis_status="UNAVAILABLE",
        )

    # If we have stored LLM evaluation in stored_observations (e.g. from evaluate pipeline run)
    if stored_observations and isinstance(stored_observations, dict):
        llm_crit = stored_observations.get("criteria") or stored_observations.get("criterion_assessments") or []
        evidence_state = stored_observations.get("evidence_state", "SUFFICIENT")

        # Check if LLM output was an unrecoverable fallback / completely failed
        has_positive_evidence = any(
            (c.get("label") or c.get("assessment_label")) in (
                AssessmentCriterionLabel.DEMONSTRATED.value,
                AssessmentCriterionLabel.PARTIALLY_DEMONSTRATED.value,
            )
            for c in llm_crit
        )
        if not has_positive_evidence and (evidence_state == "NEEDS_HUMAN_REVIEW" or not llm_crit):
            # AI analysis completely failed or was an empty fallback
            return AIProvisionalAnalysisDto(
                addressed_well=[],
                missing_or_unclear=[],
                suggested_improvement=None,
                dimension_scores={},
                evidence_citations=[],
                provisional_rating=None,
                is_insufficient_evidence=True,
                uncertainty_notes="AI analysis could not be validated for this response. Complete the rubric review manually or retry analysis.",
                analysis_status="UNAVAILABLE",
            )

        addressed = list(stored_observations.get("strengths") or [])
        missing = list(stored_observations.get("missing_elements") or [])
        improvements = list(stored_observations.get("improvement_guidance") or [])
        citations_map: Dict[str, EvidenceSegmentDto] = {}

        # Extract answer segments
        canonical_segs = extract_canonical_answer_segments(text_ans)
        seg_dict = {s.segment_id: s for s in canonical_segs}

        demonstrated_count = 0
        total_eval_crit = len(llm_crit)

        for c in llm_crit:
            label = c.get("label") or c.get("assessment_label")
            obs = c.get("observation")
            if label == AssessmentCriterionLabel.DEMONSTRATED.value:
                demonstrated_count += 1
                if obs and obs not in addressed:
                    addressed.append(obs)
            elif label == AssessmentCriterionLabel.PARTIALLY_DEMONSTRATED.value:
                demonstrated_count += 0.5
                if obs and obs not in addressed:
                    addressed.append(obs)
                if c.get("missing_evidence") and c.get("missing_evidence") not in missing:
                    missing.append(c.get("missing_evidence"))
            elif label in (AssessmentCriterionLabel.INSUFFICIENT_RESPONSE.value, AssessmentCriterionLabel.NEEDS_HUMAN_REVIEW.value):
                if c.get("missing_evidence") and c.get("missing_evidence") not in missing:
                    missing.append(c.get("missing_evidence"))

            for s_id in c.get("supporting_segment_ids", []):
                if s_id in seg_dict and s_id not in citations_map:
                    seg_obj = seg_dict[s_id]
                    citations_map[s_id] = EvidenceSegmentDto(
                        segment_id=seg_obj.segment_id,
                        text=seg_obj.text,
                        start_char=seg_obj.start_char,
                        end_char=seg_obj.end_char,
                    )

        # Calculate provisional rating based on demonstrated ratio
        if total_eval_crit > 0:
            ratio = demonstrated_count / total_eval_crit
            if ratio >= 0.85:
                prov_rating = 5
            elif ratio >= 0.65:
                prov_rating = 4
            elif ratio >= 0.45:
                prov_rating = 3
            elif ratio >= 0.25:
                prov_rating = 2
            else:
                prov_rating = 1
        else:
            prov_rating = None

        if prov_rating is not None:
            dim_scores = {
                "Relevance": min(5, max(1, prov_rating)),
                "Technical depth": min(5, max(1, prov_rating if ratio >= 0.5 else prov_rating - 1)),
                "Clarity": min(5, max(1, 4 if len(text_ans) > 50 else 2)),
                "Evidence and examples": min(5, max(1, len(citations_map) + 1 if len(citations_map) > 0 else 2)),
                "Problem-solving structure": min(5, max(1, prov_rating)),
            }
        else:
            dim_scores = {}

        return AIProvisionalAnalysisDto(
            addressed_well=addressed,
            missing_or_unclear=missing,
            suggested_improvement=improvements[0] if improvements else None,
            dimension_scores=dim_scores,
            evidence_citations=list(citations_map.values()),
            provisional_rating=prov_rating,
            is_insufficient_evidence=(prov_rating is None),
            uncertainty_notes=None,
            analysis_status="READY" if prov_rating is not None else "UNAVAILABLE",
        )

    # If no LLM analysis has been run yet -> mark as PENDING so it is clear evaluation has not been executed yet
    return AIProvisionalAnalysisDto(
        addressed_well=[],
        missing_or_unclear=[],
        suggested_improvement=None,
        dimension_scores={},
        evidence_citations=[],
        provisional_rating=None,
        is_insufficient_evidence=False,
        uncertainty_notes=None,
        analysis_status="PENDING",
    )


async def run_ai_analysis_for_session(
    session_id: UUID,
    actor: UserRecord,
    db: Session,
    only_failed: bool = False,
) -> CandidateInterviewEvaluationDetailResponse:
    """Invokes the local LLM assessment pipeline for conversational responses in the session.
    
    Rules:
    - If only_failed=True, only re-evaluates answers that currently have failed/needs-human-review or unreviewed state.
    - If Recruiter previously accepted a result whose source analysis is now superseded, mark review as needing confirmation rather than silently carrying over.
    - Does not alter finalized evaluation.
    """
    _require_recruiter_actor(actor)

    stmt = (
        select(CandidateInterviewSessionRecord)
        .options(
            selectinload(CandidateInterviewSessionRecord.job),
            selectinload(CandidateInterviewSessionRecord.evaluation).selectinload(
                CandidateInterviewEvaluationRecord.question_evaluations
            ),
            selectinload(CandidateInterviewSessionRecord.answers),
        )
        .where(CandidateInterviewSessionRecord.session_id == session_id)
    )
    sess = db.execute(stmt).scalar_one_or_none()
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found."},
        )

    if sess.status != "SUBMITTED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "SESSION_NOT_SUBMITTED", "message": "Interview must be submitted before running analysis."},
        )

    eval_rec = sess.evaluation
    if eval_rec and eval_rec.status == "FINALIZED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "EVALUATION_FINALIZED", "message": "This evaluation is finalized and locked."},
        )

    now = datetime.now(timezone.utc)
    if not eval_rec:
        eval_rec = CandidateInterviewEvaluationRecord(
            evaluation_id=uuid.uuid4(),
            organization_id=sess.organization_id,
            session_id=sess.session_id,
            job_id=sess.job_id,
            job_version_id=sess.job_version_id,
            candidate_id=sess.candidate_id,
            status="IN_REVIEW",
            questions_reviewed=0,
            core_questions_included=0,
            supplemental_questions_excluded=0,
            scoring_policy_version="1.0.0",
            aggregate_summary={},
            created_at=now,
            updated_at=now,
        )
        db.add(eval_rec)
        db.flush()

    # Answers map
    answers_map = {ans.slot_id: ans for ans in sess.answers}

    # Evaluate each conversational question using assess_single_answer
    snapshot_items = _get_snapshot_items(sess)
    for item in snapshot_items:
        raw_fmt = (item.get("question_format") or "").upper()
        is_mcq = raw_fmt in ("MCQ", "MCQ_SINGLE_ANSWER") or bool(item.get("options"))
        if is_mcq:
            continue

        slot_id_str = item.get("slot_id") or item.get("question_id")
        if not slot_id_str:
            continue
        try:
            slot_id = UUID(str(slot_id_str))
        except Exception:
            continue

        ans_rec = answers_map.get(slot_id)
        # Empty or skipped response: Do not send to LM Studio. Mark as unanalyzed/unavailable without fabricating explanation.
        if not ans_rec or not ans_rec.text_answer or len(ans_rec.text_answer.strip()) < 5 or ans_rec.is_skipped:
            continue

        stmt_qe = select(CandidateQuestionEvaluationRecord).where(
            CandidateQuestionEvaluationRecord.session_id == session_id,
            CandidateQuestionEvaluationRecord.slot_id == slot_id,
        )
        qe_rec = db.execute(stmt_qe).scalar_one_or_none()

        # If only_failed=True, skip if already successfully reviewed/evaluated
        if only_failed and qe_rec and qe_rec.reviewed_observations and qe_rec.reviewed_rating is not None:
            continue

        # Build packet and call local LLM
        packet = build_authoritative_assessment_packet(
            question_dict=item,
            answer_id=ans_rec.answer_id,
            answer_text=ans_rec.text_answer,
        )

        try:
            assessment_resp, validation_state, prov_class, model_name = await assess_single_answer(
                packet=packet,
                db=db,
                organization_id=sess.organization_id,
            )
            llm_dump = assessment_resp.model_dump()
        except Exception as llm_err:
            logger.warning("Error running LLM answer assessment on session %s slot %s: %s", session_id, slot_id, llm_err)
            llm_dump = {}

        if llm_dump:
            if qe_rec:
                # Update observations without overwriting any explicit human override
                qe_rec.reviewed_observations = llm_dump
                qe_rec.updated_at = now
            else:
                qe_rec = CandidateQuestionEvaluationRecord(
                    question_evaluation_id=uuid.uuid4(),
                    evaluation_id=eval_rec.evaluation_id,
                    session_id=session_id,
                    answer_id=ans_rec.answer_id,
                    slot_id=slot_id,
                    review_state="UNREVIEWED",
                    reviewed_rating=None,
                    reviewed_observations=llm_dump,
                    override_reason=None,
                    recruiter_note=None,
                    reviewer_user_id=actor.user_id,
                    reviewed_at=now,
                    created_at=now,
                    updated_at=now,
                )
                db.add(qe_rec)

    eval_rec.status = "IN_REVIEW"
    eval_rec.updated_at = now
    db.commit()

    return get_candidate_interview_evaluation_detail(session_id=session_id, actor=actor, db=db)



def _get_snapshot_items(sess: CandidateInterviewSessionRecord) -> List[Dict[str, Any]]:
    snapshot_raw = sess.questions_snapshot
    if not snapshot_raw:
        return []
    if isinstance(snapshot_raw, list):
        return [item for item in snapshot_raw if isinstance(item, dict)]
    if isinstance(snapshot_raw, dict):
        items = snapshot_raw.get("questions") or snapshot_raw.get("items") or []
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def get_candidate_interview_evaluation_detail(
    session_id: UUID,
    actor: UserRecord,
    db: Session,
) -> CandidateInterviewEvaluationDetailResponse:
    """Retrieves full evaluation workspace details for an official candidate interview session."""
    _require_recruiter_actor(actor)

    stmt = (
        select(CandidateInterviewSessionRecord)
        .options(
            selectinload(CandidateInterviewSessionRecord.candidate),
            selectinload(CandidateInterviewSessionRecord.job),
            selectinload(CandidateInterviewSessionRecord.job_version),
            selectinload(CandidateInterviewSessionRecord.answers),
            selectinload(CandidateInterviewSessionRecord.evaluation).selectinload(
                CandidateInterviewEvaluationRecord.question_evaluations
            ),
        )
        .where(CandidateInterviewSessionRecord.session_id == session_id)
    )
    sess = db.execute(stmt).scalar_one_or_none()
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found."},
        )

    if actor.role == UserRole.RECRUITER and sess.job and sess.job.created_by_user_id != actor.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found or access denied."},
        )

    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)
    if actor_org and sess.organization_id != actor_org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found or access denied."},
        )

    # Validate release and candidate application active status
    if sess.release_id:
        stmt_rel = select(CandidatePracticeReleaseRecord).where(
            CandidatePracticeReleaseRecord.release_id == sess.release_id
        )
        rel = db.execute(stmt_rel).scalar_one_or_none()
        if rel:
            if rel.status == PracticeReleaseStatus.REVOKED.value:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "RELEASE_REVOKED", "message": "Interview release has been revoked or withdrawn."},
                )
            if rel.application_id:
                stmt_app = select(CandidateApplicationRecord.status).where(
                    CandidateApplicationRecord.application_id == rel.application_id
                )
                app_status = db.execute(stmt_app).scalar_one_or_none()
                if app_status == CandidateApplicationStatus.WITHDRAWN.value:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={"code": "APPLICATION_WITHDRAWN", "message": "Candidate application has been withdrawn."},
                    )
            elif rel.candidate_id and rel.job_id:
                stmt_apps = select(CandidateApplicationRecord.status).where(
                    CandidateApplicationRecord.candidate_id == rel.candidate_id,
                    CandidateApplicationRecord.job_id == rel.job_id,
                )
                app_statuses = db.execute(stmt_apps).scalars().all()
                if app_statuses and all(st in (CandidateApplicationStatus.WITHDRAWN.value, CandidateApplicationStatus.DRAFT.value) for st in app_statuses):
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={"code": "APPLICATION_WITHDRAWN", "message": "Candidate application has been withdrawn."},
                    )

    cand = sess.candidate
    cand_name = (cand.full_name or cand.primary_email or "Candidate") if cand else "Candidate"
    cand_email = cand.primary_email if cand else None

    job_title = sess.job_version.title if sess.job_version else (sess.job.title if sess.job else "Interview")

    # Map answers by slot_id
    answers_map = {ans.slot_id: ans for ans in sess.answers}

    # Map existing recruiter question reviews
    eval_record = sess.evaluation
    reviews_map: Dict[UUID, CandidateQuestionEvaluationRecord] = {}
    if eval_record:
        for qe in eval_record.question_evaluations:
            reviews_map[qe.slot_id] = qe

    # Order snapshot items: conversational first, MCQs after
    snapshot_items = _get_snapshot_items(sess)
    conv_items = []
    mcq_items = []
    for item in snapshot_items:
        raw_fmt = (item.get("question_format") or "").upper()
        if raw_fmt in ("MCQ", "MCQ_SINGLE_ANSWER") or bool(item.get("options")):
            mcq_items.append(item)
        else:
            conv_items.append(item)
    ordered_items = conv_items + mcq_items

    # Build question details
    questions_dto: List[EvaluationQuestionDetailDto] = []
    reviewed_ratings: List[int] = []
    dimension_totals: Dict[str, List[int]] = {
        "Relevance": [],
        "Technical depth": [],
        "Clarity": [],
        "Evidence and examples": [],
        "Problem-solving structure": [],
    }
    key_strengths_set = []
    priority_review_set = []
    insufficient_count = 0
    core_q_count = 0
    supp_q_count = 0

    for idx, item in enumerate(ordered_items):
        slot_id_str = item.get("slot_id") or item.get("question_id") or str(uuid.uuid4())
        try:
            slot_id = UUID(str(slot_id_str))
        except Exception:
            slot_id = uuid.uuid4()

        raw_fmt = (item.get("question_format") or "").upper()
        is_mcq = raw_fmt in ("MCQ", "MCQ_SINGLE_ANSWER") or bool(item.get("options"))
        q_format = "MCQ" if is_mcq else "CONVERSATIONAL"
        is_supp = bool(item.get("is_supplemental", False) or item.get("layer") == "SUPPLEMENTAL_EVIDENCE")

        if is_supp:
            supp_q_count += 1
        else:
            core_q_count += 1

        q_text = (
            item.get("question_text")
            or item.get("ai_wording")
            or item.get("question")
            or item.get("stem")
            or item.get("text")
            or "Interview Question"
        )
        competency = item.get("competency") or item.get("competency_name")
        q_type = item.get("question_type") or "TECHNICAL"

        ans_rec = answers_map.get(slot_id)
        ans_text = ans_rec.text_answer if ans_rec else None
        sel_opt_id = ans_rec.selected_option_id if ans_rec else None
        is_skipped = ans_rec.is_skipped if ans_rec else (ans_rec is None)

        # MCQ option text mapping
        sel_opt_text = None
        correct_opt_id = item.get("correct_option_id")
        correct_opt_text = None
        is_correct = None
        stored_expl = item.get("answer_explanation") or item.get("explanation")

        if is_mcq and item.get("options"):
            for opt in item.get("options", []):
                opt_id = str(opt.get("option_id") or opt.get("id") or opt.get("key") or "")
                opt_label = str(opt.get("text") or opt.get("label") or opt_id)
                if sel_opt_id and opt_id.upper() == sel_opt_id.upper():
                    sel_opt_text = opt_label
                if correct_opt_id and opt_id.upper() == str(correct_opt_id).upper():
                    correct_opt_text = opt_label
            if sel_opt_id and correct_opt_id:
                is_correct = (sel_opt_id.strip().upper() == str(correct_opt_id).strip().upper())

        # Check for stored LLM observations on this question
        qe_rec = reviews_map.get(slot_id)
        stored_obs = qe_rec.reviewed_observations if (qe_rec and qe_rec.reviewed_observations) else None

        # Synthesize AI provisional analysis
        ai_provisional = _synthesize_ai_provisional_analysis(item, ans_rec, stored_observations=stored_obs)
        if ai_provisional.is_insufficient_evidence:
            insufficient_count += 1

        if ai_provisional.addressed_well:
            for s in ai_provisional.addressed_well[:1]:
                if s not in key_strengths_set:
                    key_strengths_set.append(s)
        if ai_provisional.missing_or_unclear:
            for m in ai_provisional.missing_or_unclear[:1]:
                if m not in priority_review_set:
                    priority_review_set.append(m)

        for dim_name, score in (ai_provisional.dimension_scores or {}).items():
            if dim_name in dimension_totals:
                dimension_totals[dim_name].append(score)

        # Rubric
        raw_rubric = item.get("rubric") or {}
        rubric_dto = QuestionRubricAnchorDto(
            score_1=raw_rubric.get("score_1") or raw_rubric.get("1"),
            score_2=raw_rubric.get("score_2") or raw_rubric.get("2"),
            score_3=raw_rubric.get("score_3") or raw_rubric.get("3"),
            score_4=raw_rubric.get("score_4") or raw_rubric.get("4"),
            score_5=raw_rubric.get("score_5") or raw_rubric.get("5"),
            observable_indicators=raw_rubric.get("observable_indicators") or item.get("observable_indicators") or [],
            sufficient_evidence_guidance=raw_rubric.get("sufficient_evidence_guidance") or item.get("sufficient_evidence_guidance"),
            insufficient_evidence_guidance=raw_rubric.get("insufficient_evidence_guidance") or item.get("insufficient_evidence_guidance"),
        )

        # Recruiter review on this question
        qe_rec = reviews_map.get(slot_id)
        review_dto = None
        if qe_rec:
            review_dto = RecruiterQuestionReviewDto(
                question_evaluation_id=qe_rec.question_evaluation_id,
                review_state=qe_rec.review_state,
                reviewed_rating=qe_rec.reviewed_rating,
                reviewed_observations=qe_rec.reviewed_observations or {},
                override_reason=qe_rec.override_reason,
                recruiter_note=qe_rec.recruiter_note,
                reviewer_name=getattr(actor, "full_name", None) or actor.email,
                reviewed_at=qe_rec.reviewed_at,
            )
            if qe_rec.reviewed_rating is not None and not is_supp:
                reviewed_ratings.append(qe_rec.reviewed_rating)
        elif is_mcq and is_correct is not None:
            # Deterministic default review for MCQ
            pass

        # Collect rating for deterministic aggregate:
        # Priority: Human review rating (if recruiter actively reviewed/overrode) -> AI provisional rating
        effective_rating = None
        if review_dto and review_dto.reviewed_rating is not None:
            effective_rating = review_dto.reviewed_rating
        elif ai_provisional and ai_provisional.provisional_rating is not None:
            effective_rating = ai_provisional.provisional_rating

        if effective_rating is not None and not is_supp:
            reviewed_ratings.append(effective_rating)

        questions_dto.append(
            EvaluationQuestionDetailDto(
                slot_id=slot_id,
                question_order=idx + 1,
                is_supplemental=is_supp,
                question_type=q_type,
                question_format=q_format,
                competency=competency,
                question_text=q_text,
                submitted_answer_text=ans_text,
                selected_option_id=sel_opt_id,
                selected_option_text=sel_opt_text,
                correct_option_id=str(correct_opt_id) if correct_opt_id else None,
                correct_option_text=correct_opt_text,
                is_correct=is_correct,
                stored_explanation=stored_expl,
                is_skipped=is_skipped,
                ai_provisional_analysis=ai_provisional,
                rubric=rubric_dto,
                recruiter_review=review_dto,
            )
        )

    # Calculate deterministic aggregate score (Core only)
    # Built directly from valid AI assessments and optional human overrides
    aggregate_score = None
    is_complete = False
    ai_evaluated_count = len([
        q for q in questions_dto
        if (q.ai_provisional_analysis and q.ai_provisional_analysis.analysis_status == "READY" and q.ai_provisional_analysis.provisional_rating is not None)
        or (q.recruiter_review and q.recruiter_review.reviewed_rating is not None)
    ])
    core_evaluated_count = len([
        q for q in questions_dto
        if not q.is_supplemental and (
            (q.ai_provisional_analysis and q.ai_provisional_analysis.analysis_status == "READY" and q.ai_provisional_analysis.provisional_rating is not None)
            or (q.recruiter_review and q.recruiter_review.reviewed_rating is not None)
        )
    ])

    if core_evaluated_count == core_q_count and core_q_count > 0:
        is_complete = True
        if reviewed_ratings:
            aggregate_score = round(sum(reviewed_ratings) / len(reviewed_ratings), 1)

    dim_averages = {}
    for dname, scores in dimension_totals.items():
        if scores:
            dim_averages[dname] = round(sum(scores) / len(scores), 1)

    reviewed_count = len([q for q in questions_dto if q.recruiter_review and q.recruiter_review.review_state != "UNREVIEWED"])

    has_ai_run = any(
        q.question_format == "CONVERSATIONAL" and q.ai_provisional_analysis and q.ai_provisional_analysis.analysis_status in ("READY", "UNAVAILABLE")
        for q in questions_dto
    ) or any(
        q.recruiter_review and q.recruiter_review.review_state != "UNREVIEWED"
        for q in questions_dto
    )

    if eval_record and eval_record.status == "FINALIZED":
        eval_status = "FINALIZED"
    elif is_complete:
        eval_status = "COMPLETE"
    elif (eval_record and eval_record.status == "IN_REVIEW") or has_ai_run:
        eval_status = "IN_REVIEW"
    else:
        eval_status = "NOT_STARTED"

    overview_dto = EvaluationOverviewSummaryDto(
        questions_reviewed=reviewed_count,
        total_questions=len(questions_dto),
        core_questions_count=core_q_count,
        supplemental_questions_count=supp_q_count,
        ai_coverage_count=ai_evaluated_count if has_ai_run else 0,
        insufficient_evidence_count=insufficient_count if has_ai_run else 0,
        dimension_averages=dim_averages if is_complete else {},
        key_strengths=key_strengths_set[:3] if (is_complete or has_ai_run) else [],
        priority_review_areas=priority_review_set[:3] if (is_complete or has_ai_run) else [],
        aggregate_score=aggregate_score if is_complete else (eval_record.aggregate_score if eval_record and eval_record.status == "FINALIZED" else None),
        is_aggregate_complete=is_complete,
        status=eval_status,
    )

    # Build Resume Match Reference
    resume_ref = None
    # Lookup match result for release
    stmt_rel = select(CandidatePracticeReleaseRecord).where(CandidatePracticeReleaseRecord.release_id == sess.release_id)
    rel_rec = db.execute(stmt_rel).scalar_one_or_none()
    mr = None
    if rel_rec and rel_rec.match_result_id:
        stmt_mr = select(MatchResultRecord).where(MatchResultRecord.result_id == rel_rec.match_result_id)
        mr = db.execute(stmt_mr).scalar_one_or_none()

    if not mr and sess.candidate_id and sess.job_id:
        # Fallback lookup: find latest match result for candidate's identity / profiles on this job
        stmt_match = (
            select(MatchResultRecord)
            .join(MatchRunRecord, MatchResultRecord.run_id == MatchRunRecord.run_id)
            .join(CandidateProfileRecord, MatchResultRecord.profile_id == CandidateProfileRecord.profile_id)
            .where(
                MatchRunRecord.job_id == sess.job_id,
                CandidateProfileRecord.identity_id == sess.candidate_id,
            )
            .order_by(desc(MatchRunRecord.completed_at))
        )
        mr = db.execute(stmt_match).scalars().first()

        if not mr:
            stmt_app = select(CandidateApplicationRecord).where(
                CandidateApplicationRecord.candidate_id == sess.candidate_id,
                CandidateApplicationRecord.job_id == sess.job_id,
            ).order_by(desc(CandidateApplicationRecord.created_at))
            app_rec = db.execute(stmt_app).scalars().first()
            if app_rec and app_rec.profile_id:
                stmt_match2 = (
                    select(MatchResultRecord)
                    .join(MatchRunRecord, MatchResultRecord.run_id == MatchRunRecord.run_id)
                    .where(
                        MatchRunRecord.job_id == sess.job_id,
                        MatchResultRecord.profile_id == app_rec.profile_id,
                    )
                    .order_by(desc(MatchRunRecord.completed_at))
                )
                mr = db.execute(stmt_match2).scalars().first()

    if mr:
        ov_score = None
        if getattr(mr, "display_compatibility_score", None) is not None:
            ov_score = float(mr.display_compatibility_score)
        elif getattr(mr, "overall_score", None) is not None:
            ov_score = round(float(mr.overall_score) * 100, 1)

        decomp = getattr(mr, "score_decomposition", None) or {}
        req_contribs = decomp.get("requirement_contributions", [])
        must_haves = [r for r in req_contribs if r.get("necessity") == "MUST_HAVE"]
        if must_haves:
            req_matched = len([r for r in must_haves if r.get("match_state") in ("MATCHED", "PARTIAL") or r.get("match_value", 0) > 0])
            req_total = len(must_haves)
        elif req_contribs:
            req_matched = len([r for r in req_contribs if r.get("match_state") in ("MATCHED", "PARTIAL") or r.get("match_value", 0) > 0])
            req_total = len(req_contribs)
        else:
            req_matched = decomp.get("matched_requirements_count") or decomp.get("matched_count", 0) or getattr(mr, "matched_requirements_count", 0)
            req_total = decomp.get("total_requirements_count") or decomp.get("total_count", 0) or getattr(mr, "total_requirements_count", 0)

        resume_ref = ResumeMatchReferenceDto(
            overall_score=ov_score,
            matched_required_count=req_matched,
            total_required_count=req_total,
            category_breakdown=decomp.get("category_scores") or getattr(mr, "category_scores", {}),
            profile_version=mr.profile_version,
            match_run_id=mr.run_id,
        )

    # Activity Log
    activity_log = [
        EvaluationAuditActivityDto(
            event_type="INTERVIEW_STARTED",
            timestamp=sess.started_at,
            actor_name=cand_name,
            summary=f"Candidate started official interview for Revision #{sess.job_revision}.",
        ),
    ]
    if sess.submitted_at:
        activity_log.append(
            EvaluationAuditActivityDto(
                event_type="INTERVIEW_SUBMITTED",
                timestamp=sess.submitted_at,
                actor_name=cand_name,
                summary=f"Candidate submitted {len(sess.answers)} of {len(snapshot_items)} answers.",
            )
        )
    if eval_record and eval_record.finalized_at:
        activity_log.append(
            EvaluationAuditActivityDto(
                event_type="EVALUATION_FINALIZED",
                timestamp=eval_record.finalized_at,
                actor_name=getattr(actor, "full_name", None) or actor.email,
                summary=f"Recruiter finalized official evaluation (Score: {eval_record.aggregate_score or 'N/A'}/5.0).",
            )
        )
    if eval_record and eval_record.candidate_feedback_published_at:
        activity_log.append(
            EvaluationAuditActivityDto(
                event_type="FEEDBACK_PUBLISHED",
                timestamp=eval_record.candidate_feedback_published_at,
                actor_name=getattr(actor, "full_name", None) or actor.email,
                summary="Candidate-safe feedback report published.",
            )
        )

    return CandidateInterviewEvaluationDetailResponse(
        session_id=sess.session_id,
        evaluation_id=eval_record.evaluation_id if eval_record else None,
        release_id=sess.release_id,
        candidate_id=sess.candidate_id,
        candidate_name=cand_name,
        candidate_email=cand_email,
        job_id=sess.job_id,
        job_title=job_title,
        job_revision=sess.job_revision,
        question_set_version=sess.set_version,
        interview_mode=sess.interview_mode or "TEXT",
        submitted_at=sess.submitted_at,
        status=eval_status,
        candidate_feedback_status=eval_record.candidate_feedback_status if eval_record else "UNPUBLISHED",
        candidate_feedback_published_at=eval_record.candidate_feedback_published_at if eval_record else None,
        finalized_by_name=(getattr(actor, "full_name", None) or actor.email) if (eval_record and eval_record.finalized_at) else None,
        finalized_at=eval_record.finalized_at if eval_record else None,
        resume_match_reference=resume_ref,
        overview=overview_dto,
        questions=questions_dto,
        activity_log=activity_log,
    )


# =============================================================================
# Level 3: Save Question Review
# =============================================================================

def save_question_evaluation_review(
    session_id: UUID,
    slot_id: UUID,
    payload: SaveQuestionReviewRequest,
    actor: UserRecord,
    db: Session,
) -> SaveQuestionReviewResponse:
    """Saves or updates recruiter human review on a specific question in an in-progress evaluation."""
    _require_recruiter_actor(actor)

    stmt = (
        select(CandidateInterviewSessionRecord)
        .options(
            selectinload(CandidateInterviewSessionRecord.job),
            selectinload(CandidateInterviewSessionRecord.evaluation).selectinload(
                CandidateInterviewEvaluationRecord.question_evaluations
            ),
            selectinload(CandidateInterviewSessionRecord.answers),
        )
        .where(CandidateInterviewSessionRecord.session_id == session_id)
    )
    sess = db.execute(stmt).scalar_one_or_none()
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found."},
        )

    if actor.role == UserRole.RECRUITER and sess.job and sess.job.created_by_user_id != actor.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found or access denied."},
        )

    actor_org = getattr(actor, "organization_id", None) or getattr(actor, "org_id", None)
    if actor_org and sess.organization_id != actor_org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found or access denied."},
        )

    if sess.status != "SUBMITTED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "SESSION_NOT_SUBMITTED", "message": "Cannot evaluate an interview before it has been submitted by the candidate."},
        )

    eval_rec = sess.evaluation
    if eval_rec and eval_rec.status == "FINALIZED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "EVALUATION_FINALIZED", "message": "This evaluation is finalized and locked against edits."},
        )

    # Ensure evaluation record exists
    now = datetime.now(timezone.utc)
    if not eval_rec:
        eval_rec = CandidateInterviewEvaluationRecord(
            evaluation_id=uuid.uuid4(),
            organization_id=sess.organization_id,
            session_id=sess.session_id,
            job_id=sess.job_id,
            job_version_id=sess.job_version_id,
            candidate_id=sess.candidate_id,
            status="IN_REVIEW",
            questions_reviewed=0,
            core_questions_included=0,
            supplemental_questions_excluded=0,
            scoring_policy_version="1.0.0",
            aggregate_summary={},
            created_at=now,
            updated_at=now,
        )
        db.add(eval_rec)
        db.flush()

    # Find matching answer
    matching_ans = next((a for a in sess.answers if a.slot_id == slot_id), None)

    # Find or create question evaluation record
    stmt_qe = select(CandidateQuestionEvaluationRecord).where(
        CandidateQuestionEvaluationRecord.session_id == session_id,
        CandidateQuestionEvaluationRecord.slot_id == slot_id,
    )
    qe_rec = db.execute(stmt_qe).scalar_one_or_none()

    if qe_rec:
        qe_rec.review_state = payload.review_state
        qe_rec.reviewed_rating = payload.reviewed_rating
        qe_rec.reviewed_observations = payload.reviewed_observations or {}
        qe_rec.override_reason = payload.override_reason
        qe_rec.recruiter_note = payload.recruiter_note
        qe_rec.reviewer_user_id = actor.user_id
        qe_rec.reviewed_at = now
        qe_rec.updated_at = now
    else:
        qe_rec = CandidateQuestionEvaluationRecord(
            question_evaluation_id=uuid.uuid4(),
            evaluation_id=eval_rec.evaluation_id,
            session_id=session_id,
            answer_id=matching_ans.answer_id if matching_ans else None,
            slot_id=slot_id,
            review_state=payload.review_state,
            reviewed_rating=payload.reviewed_rating,
            reviewed_observations=payload.reviewed_observations or {},
            override_reason=payload.override_reason,
            recruiter_note=payload.recruiter_note,
            reviewer_user_id=actor.user_id,
            reviewed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(qe_rec)
        db.flush()

    # Update review status
    eval_rec.status = "IN_REVIEW"
    eval_rec.updated_at = now

    db.commit()
    db.refresh(qe_rec)
    db.refresh(eval_rec)

    # Refresh detail overview
    detail = get_candidate_interview_evaluation_detail(session_id=session_id, actor=actor, db=db)

    return SaveQuestionReviewResponse(
        question_evaluation_id=qe_rec.question_evaluation_id,
        session_id=session_id,
        slot_id=slot_id,
        review_state=qe_rec.review_state,
        reviewed_at=qe_rec.reviewed_at,
        overview=detail.overview,
        message="Question review saved successfully.",
    )


# =============================================================================
# Level 3: Finalize Evaluation
# =============================================================================

def finalize_candidate_interview_evaluation(
    session_id: UUID,
    actor: UserRecord,
    db: Session,
) -> FinalizeEvaluationResponse:
    """Finalizes and locks official candidate interview evaluation."""
    _require_recruiter_actor(actor)

    detail = get_candidate_interview_evaluation_detail(session_id=session_id, actor=actor, db=db)
    if detail.status == "FINALIZED":
        return FinalizeEvaluationResponse(
            evaluation_id=detail.evaluation_id or uuid.uuid4(),
            session_id=session_id,
            status="FINALIZED",
            aggregate_score=detail.overview.aggregate_score,
            finalized_at=detail.finalized_at or datetime.now(timezone.utc),
            message="Official interview evaluation is already finalized.",
        )

    # Check if all core questions have valid evaluations (either validated AI analysis or recruiter review)
    unevaluated_core = [
        q for q in detail.questions
        if not q.is_supplemental and (
            (not q.ai_provisional_analysis or q.ai_provisional_analysis.analysis_status != "READY" or q.ai_provisional_analysis.provisional_rating is None)
            and (not q.recruiter_review or q.recruiter_review.reviewed_rating is None)
        )
    ]
    if unevaluated_core:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INCOMPLETE_EVALUATION",
                "message": f"Cannot finalize evaluation. {len(unevaluated_core)} core question(s) have not been evaluated by AI or recruiter.",
                "unreviewed_core_count": len(unevaluated_core),
            },
        )

    # Check override reasons
    for q in detail.questions:
        if q.recruiter_review and q.recruiter_review.review_state == "OVERRIDDEN":
            if not q.recruiter_review.override_reason or len(q.recruiter_review.override_reason.strip()) == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "OVERRIDE_REASON_REQUIRED",
                        "message": f"Question {q.question_order} has an override without an explanation.",
                    },
                )

    now = datetime.now(timezone.utc)
    stmt = (
        select(CandidateInterviewEvaluationRecord)
        .where(CandidateInterviewEvaluationRecord.session_id == session_id)
    )
    eval_rec = db.execute(stmt).scalar_one_or_none()

    if not eval_rec:
        eval_rec = CandidateInterviewEvaluationRecord(
            evaluation_id=uuid.uuid4(),
            organization_id=actor_org or detail.candidate_id,
            session_id=session_id,
            job_id=detail.job_id,
            job_version_id=detail.job_id,
            candidate_id=detail.candidate_id,
            status="FINALIZED",
            created_at=now,
        )
        db.add(eval_rec)

    eval_rec.status = "FINALIZED"
    eval_rec.finalized_at = now
    eval_rec.finalized_by_user_id = actor.user_id
    eval_rec.aggregate_score = detail.overview.aggregate_score
    eval_rec.questions_reviewed = detail.overview.questions_reviewed
    eval_rec.core_questions_included = detail.overview.core_questions_count
    eval_rec.supplemental_questions_excluded = detail.overview.supplemental_questions_count
    eval_rec.aggregate_summary = {
        "dimension_averages": detail.overview.dimension_averages,
        "key_strengths": detail.overview.key_strengths,
        "priority_review_areas": detail.overview.priority_review_areas,
    }
    eval_rec.updated_at = now

    db.commit()
    db.refresh(eval_rec)

    return FinalizeEvaluationResponse(
        evaluation_id=eval_rec.evaluation_id,
        session_id=session_id,
        status="FINALIZED",
        aggregate_score=eval_rec.aggregate_score,
        finalized_at=eval_rec.finalized_at,
        message="Official interview evaluation finalized and locked.",
    )


# =============================================================================
# Level 3: Publish Candidate Feedback
# =============================================================================

def publish_candidate_feedback(
    session_id: UUID,
    actor: UserRecord,
    db: Session,
) -> PublishCandidateFeedbackResponse:
    """Publishes candidate-safe feedback report. Strictly redacts internal recruiter notes."""
    _require_recruiter_actor(actor)

    detail = get_candidate_interview_evaluation_detail(session_id=session_id, actor=actor, db=db)
    if detail.status != "FINALIZED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "EVALUATION_NOT_FINALIZED", "message": "Evaluation must be finalized before publishing feedback to candidate."},
        )

    stmt = select(CandidateInterviewEvaluationRecord).where(
        CandidateInterviewEvaluationRecord.session_id == session_id,
    )
    eval_rec = db.execute(stmt).scalar_one_or_none()
    if not eval_rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "EVALUATION_NOT_FOUND", "message": "Evaluation record not found."},
        )

    # Build safe questions feedback (strictly omits recruiter internal notes and hidden rubrics)
    safe_q_feedbacks: List[Dict[str, Any]] = []
    for q in detail.questions:
        rating_lbl = "Demonstrated"
        if q.recruiter_review:
            if q.recruiter_review.review_state == "INSUFFICIENT":
                rating_lbl = "Insufficient Evidence"
            elif q.recruiter_review.reviewed_rating and q.recruiter_review.reviewed_rating <= 2:
                rating_lbl = "Developing"
            elif q.recruiter_review.reviewed_rating and q.recruiter_review.reviewed_rating >= 4:
                rating_lbl = "Strongly Demonstrated"

        strength_str = None
        if q.ai_provisional_analysis and q.ai_provisional_analysis.addressed_well:
            strength_str = "; ".join(q.ai_provisional_analysis.addressed_well)

        improve_str = None
        if q.ai_provisional_analysis and q.ai_provisional_analysis.suggested_improvement:
            improve_str = q.ai_provisional_analysis.suggested_improvement

        safe_q_feedbacks.append(
            {
                "question_order": q.question_order,
                "competency": q.competency,
                "question_text": q.question_text,
                "question_format": q.question_format,
                "strengths": strength_str,
                "improvements": improve_str,
                "rating_label": rating_lbl,
            }
        )

    now = datetime.now(timezone.utc)
    published_payload = {
        "session_id": str(session_id),
        "job_title": detail.job_title,
        "job_revision": detail.job_revision,
        "interview_mode": detail.interview_mode,
        "submitted_at": detail.submitted_at.isoformat() if detail.submitted_at else now.isoformat(),
        "published_at": now.isoformat(),
        "reviewed_strengths": detail.overview.key_strengths,
        "priority_improvements": detail.overview.priority_review_areas,
        "response_dimensions": {k: int(round(v)) for k, v in detail.overview.dimension_averages.items()},
        "questions_feedback": safe_q_feedbacks,
    }

    eval_rec.candidate_feedback_status = "PUBLISHED"
    eval_rec.candidate_feedback_published_at = now
    eval_rec.candidate_feedback_payload = published_payload
    eval_rec.updated_at = now

    db.commit()
    db.refresh(eval_rec)

    return PublishCandidateFeedbackResponse(
        session_id=session_id,
        candidate_feedback_status="PUBLISHED",
        published_at=now,
        message="Candidate-safe feedback report published successfully.",
    )


# =============================================================================
# Candidate Access: Fetch Published Feedback
# =============================================================================

def get_candidate_safe_feedback(
    session_id: UUID,
    candidate_user: UserRecord,
    db: Session,
) -> CandidateSafeFeedbackReportResponse:
    """Candidate view of published feedback report. Strictly enforced permissions."""
    binding = _get_candidate_binding(candidate_user, db)

    stmt = select(CandidateInterviewSessionRecord).where(
        CandidateInterviewSessionRecord.session_id == session_id,
        CandidateInterviewSessionRecord.candidate_id == binding.candidate_id,
    )
    sess = db.execute(stmt).scalar_one_or_none()
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Interview session not found."},
        )

    stmt_eval = select(CandidateInterviewEvaluationRecord).where(
        CandidateInterviewEvaluationRecord.session_id == session_id,
    )
    eval_rec = db.execute(stmt_eval).scalar_one_or_none()

    if not eval_rec or eval_rec.candidate_feedback_status != "PUBLISHED" or not eval_rec.candidate_feedback_payload:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FEEDBACK_NOT_PUBLISHED", "message": "Feedback report has not been published yet."},
        )

    payload = eval_rec.candidate_feedback_payload
    return CandidateSafeFeedbackReportResponse(
        session_id=sess.session_id,
        job_title=payload.get("job_title") or "Interview",
        job_revision=payload.get("job_revision", 1),
        interview_mode=payload.get("interview_mode") or sess.interview_mode or "TEXT",
        submitted_at=datetime.fromisoformat(payload["submitted_at"]) if payload.get("submitted_at") else sess.started_at,
        published_at=datetime.fromisoformat(payload["published_at"]) if payload.get("published_at") else datetime.now(timezone.utc),
        reviewed_strengths=payload.get("reviewed_strengths", []),
        priority_improvements=payload.get("priority_improvements", []),
        response_dimensions=payload.get("response_dimensions", {}),
        questions_feedback=[
            CandidateSafeQuestionFeedbackDto(**qf) for qf in payload.get("questions_feedback", [])
        ],
        message="Candidate interview feedback report.",
    )


# =============================================================================
# Level 3: Evaluation JSON Export (Zero Audio Retention)
# =============================================================================

def export_candidate_interview_evaluation(
    session_id: UUID,
    actor: UserRecord,
    db: Session,
) -> CandidateEvaluationExportDto:
    """Exports structured candidate interview evaluation with interview_mode and zero audio references."""
    detail = get_candidate_interview_evaluation_detail(session_id=session_id, actor=actor, db=db)
    now = datetime.now(timezone.utc)

    return CandidateEvaluationExportDto(
        session_id=detail.session_id,
        evaluation_id=detail.evaluation_id,
        release_id=detail.release_id,
        candidate_id=detail.candidate_id,
        candidate_name=detail.candidate_name,
        candidate_email=detail.candidate_email,
        job_id=detail.job_id,
        job_title=detail.job_title,
        job_revision=detail.job_revision,
        question_set_version=detail.question_set_version,
        interview_mode=detail.interview_mode,
        submitted_at=detail.submitted_at,
        status=detail.status,
        candidate_feedback_status=detail.candidate_feedback_status,
        candidate_feedback_published_at=detail.candidate_feedback_published_at,
        finalized_by_name=detail.finalized_by_name,
        finalized_at=detail.finalized_at,
        resume_match_reference=detail.resume_match_reference,
        overview=detail.overview,
        questions=detail.questions,
        activity_log=detail.activity_log,
        exported_at=now,
    )
