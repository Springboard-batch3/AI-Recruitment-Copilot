import React, { useEffect, useState, useMemo, useCallback } from "react";
import { apiFetch } from "../../api/client";
import { Icon } from "../Icon";
import {
  ReviewWorkspaceResponse,
  ReviewWorkspaceQuestionItem,
  SimulationAnswerSegmentItem,
  AssessmentReviewItem,
  DeterministicAggregate,
  FinalizationEligibility,
  InterviewFinalizeResponse,
} from "../../types/interview";
import { formatCompetencyName } from "./InterviewWorkspace";

interface InterviewReviewWorkspaceProps {
  attemptId: string;
  onNavigateBack: () => void;
  onFinalized?: () => void;
}

type ReviewActionModal = "NONE" | "EDIT" | "OVERRIDE" | "REJECT" | "INSUFFICIENT" | "FINALIZE";

export function InterviewReviewWorkspace({
  attemptId,
  onNavigateBack,
  onFinalized,
}: InterviewReviewWorkspaceProps) {
  const [data, setData] = useState<ReviewWorkspaceResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Active question index
  const [activeQuestionIndex, setActiveQuestionIndex] = useState<number>(0);

  // Active highlighted segment ID in the submitted answer
  const [highlightedSegmentId, setHighlightedSegmentId] = useState<string | null>(null);

  // Action modals & forms
  const [activeActionModal, setActiveActionModal] = useState<ReviewActionModal>("NONE");
  const [isPerformingAction, setIsPerformingAction] = useState<boolean>(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  // Edit Observation Form State
  const [editObservationText, setEditObservationText] = useState<string>("");
  const [editSelectedSegmentIds, setEditSelectedSegmentIds] = useState<string[]>([]);
  const [editRubricLevel, setEditRubricLevel] = useState<number | null>(null);

  // Override Rubric Level Form State
  const [overrideLevel, setOverrideLevel] = useState<number>(3);
  const [overrideReason, setOverrideReason] = useState<string>("");

  // Reject Observation Form State
  const [rejectReason, setRejectReason] = useState<string>("");

  // Mark Insufficient Form State
  const [insufficientReason, setInsufficientReason] = useState<string>("");

  // Finalize Form State
  const [finalizeReason, setFinalizeReason] = useState<string>("");

  // Aggregate drawer collapse
  const [showAggregateDrawer, setShowAggregateDrawer] = useState<boolean>(false);

  // Fetch workspace review data
  const loadReviewData = useCallback(async () => {
    try {
      setIsLoading(true);
      setLoadError(null);
      const res = await apiFetch<ReviewWorkspaceResponse>(
        `/api/v1/interviews/simulations/${attemptId}/review/progress`
      );
      setData(res);
    } catch (err: any) {
      setLoadError(err?.message || "Failed to load review workspace. Please try again.");
    } finally {
      setIsLoading(false);
    }
  }, [attemptId]);

  useEffect(() => {
    loadReviewData();
  }, [loadReviewData]);

  const activeQuestion: ReviewWorkspaceQuestionItem | null = useMemo(() => {
    if (!data?.questions || data.questions.length === 0) return null;
    return data.questions[activeQuestionIndex] || data.questions[0];
  }, [data, activeQuestionIndex]);

  // Count reviewed questions
  const reviewProgress = useMemo(() => {
    if (!data?.questions) return { reviewedCount: 0, totalCount: 0, coreReviewedCount: 0, coreTotalCount: 0 };
    const totalCount = data.questions.length;
    const reviewedCount = data.questions.filter((q) => !!q.latest_review).length;
    const coreQuestions = data.questions.filter((q) => q.layer === "COMMON_CORE");
    const coreReviewedCount = coreQuestions.filter((q) => !!q.latest_review).length;
    return {
      reviewedCount,
      totalCount,
      coreReviewedCount,
      coreTotalCount: coreQuestions.length,
    };
  }, [data]);

  // Core rating distribution (human-reviewed core ratings only)
  const ratingDistribution = useMemo(() => {
    const dist: Record<number, number> = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
    let totalScored = 0;
    if (data?.questions) {
      data.questions
        .filter((q) => q.layer === "COMMON_CORE" && q.latest_review?.reviewed_rubric_level != null)
        .forEach((q) => {
          const lvl = q.latest_review?.reviewed_rubric_level;
          if (lvl && lvl >= 1 && lvl <= 5) {
            dist[lvl] = (dist[lvl] || 0) + 1;
            totalScored += 1;
          }
        });
    }
    return { ...dist, totalScored };
  }, [data]);

  // Open Edit modal initialized with provisional or latest review
  const openEditModal = () => {
    if (!activeQuestion) return;
    const initialText =
      activeQuestion.latest_review?.reviewed_observation ||
      activeQuestion.provisional_assessment?.criterion_observations?.[0]?.observation_text ||
      activeQuestion.provisional_assessment?.criterion_observations?.[0]?.observation ||
      "";
    const initialCitations =
      activeQuestion.latest_review?.retained_citations ||
      activeQuestion.provisional_assessment?.cited_segment_ids ||
      (activeQuestion.answer_segments.length > 0 ? [activeQuestion.answer_segments[0].segment_id] : []);
    const initialLevel =
      activeQuestion.latest_review?.reviewed_rubric_level ??
      activeQuestion.provisional_assessment?.provisional_level ??
      3;

    setEditObservationText(initialText);
    setEditSelectedSegmentIds(initialCitations);
    setEditRubricLevel(initialLevel);
    setActionError(null);
    setActiveActionModal("EDIT");
  };

  // Open Override modal
  const openOverrideModal = () => {
    if (!activeQuestion) return;
    const currentLvl =
      activeQuestion.latest_review?.reviewed_rubric_level ??
      activeQuestion.provisional_assessment?.provisional_level ??
      3;
    setOverrideLevel(currentLvl);
    setOverrideReason("");
    setActionError(null);
    setActiveActionModal("OVERRIDE");
  };

  // Open Reject modal
  const openRejectModal = () => {
    setRejectReason("");
    setActionError(null);
    setActiveActionModal("REJECT");
  };

  // Open Insufficient modal
  const openInsufficientModal = () => {
    setInsufficientReason("");
    setActionError(null);
    setActiveActionModal("INSUFFICIENT");
  };

  // Close modals
  const closeModal = () => {
    setActiveActionModal("NONE");
    setActionError(null);
  };

  // Command 1: Accept Provisional Assessment
  const handleAcceptProvisional = async () => {
    if (!activeQuestion) return;
    try {
      setIsPerformingAction(true);
      setActionError(null);
      await apiFetch(
        `/api/v1/interviews/simulations/${attemptId}/questions/${activeQuestion.question_id}/review/accept`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_review_version: activeQuestion.latest_review?.review_version,
          }),
        }
      );
      setActionSuccess("Provisional assessment accepted without alteration.");
      await loadReviewData();
    } catch (err: any) {
      setActionError(err?.message || "Failed to accept provisional assessment.");
    } finally {
      setIsPerformingAction(false);
    }
  };

  // Command 2: Submit Edit
  const handleSubmitEdit = async () => {
    if (!activeQuestion) return;
    if (!editObservationText.trim()) {
      setActionError("Reviewed observation cannot be blank.");
      return;
    }
    if (editSelectedSegmentIds.length === 0) {
      setActionError("At least one valid cited answer segment is required for edited observations.");
      return;
    }

    try {
      setIsPerformingAction(true);
      setActionError(null);
      await apiFetch(
        `/api/v1/interviews/simulations/${attemptId}/questions/${activeQuestion.question_id}/review/edit`,
        {
          method: "POST",
          body: JSON.stringify({
            reviewed_observation: editObservationText.trim(),
            cited_segment_ids: editSelectedSegmentIds,
            reviewed_rubric_level: editRubricLevel,
            expected_review_version: activeQuestion.latest_review?.review_version,
          }),
        }
      );
      setActionSuccess("Recruiter-edited observation saved.");
      closeModal();
      await loadReviewData();
    } catch (err: any) {
      setActionError(err?.message || "Failed to save edited observation.");
    } finally {
      setIsPerformingAction(false);
    }
  };

  // Command 3: Submit Override
  const handleSubmitOverride = async () => {
    if (!activeQuestion) return;
    if (!overrideReason.trim()) {
      setActionError("A non-empty justification reason is required to override the rubric level.");
      return;
    }

    try {
      setIsPerformingAction(true);
      setActionError(null);
      await apiFetch(
        `/api/v1/interviews/simulations/${attemptId}/questions/${activeQuestion.question_id}/review/override`,
        {
          method: "POST",
          body: JSON.stringify({
            reviewed_rubric_level: overrideLevel,
            reason: overrideReason.trim(),
            expected_review_version: activeQuestion.latest_review?.review_version,
          }),
        }
      );
      setActionSuccess(`Rubric level overridden to ${overrideLevel}.`);
      closeModal();
      await loadReviewData();
    } catch (err: any) {
      setActionError(err?.message || "Failed to override rubric level.");
    } finally {
      setIsPerformingAction(false);
    }
  };

  // Command 4: Submit Reject
  const handleSubmitReject = async () => {
    if (!activeQuestion) return;
    if (!rejectReason.trim()) {
      setActionError("A non-empty reason is required to reject the provisional observation.");
      return;
    }

    try {
      setIsPerformingAction(true);
      setActionError(null);
      await apiFetch(
        `/api/v1/interviews/simulations/${attemptId}/questions/${activeQuestion.question_id}/review/reject`,
        {
          method: "POST",
          body: JSON.stringify({
            reason: rejectReason.trim(),
            expected_review_version: activeQuestion.latest_review?.review_version,
          }),
        }
      );
      setActionSuccess("Provisional observation rejected. Original AI record preserved.");
      closeModal();
      await loadReviewData();
    } catch (err: any) {
      setActionError(err?.message || "Failed to reject provisional observation.");
    } finally {
      setIsPerformingAction(false);
    }
  };

  // Command 5: Mark Insufficient
  const handleSubmitInsufficient = async () => {
    if (!activeQuestion) return;
    try {
      setIsPerformingAction(true);
      setActionError(null);
      await apiFetch(
        `/api/v1/interviews/simulations/${attemptId}/questions/${activeQuestion.question_id}/review/mark-insufficient`,
        {
          method: "POST",
          body: JSON.stringify({
            reason: insufficientReason.trim() || undefined,
            expected_review_version: activeQuestion.latest_review?.review_version,
          }),
        }
      );
      setActionSuccess("Question marked as insufficient evidence (neutral outcome).");
      closeModal();
      await loadReviewData();
    } catch (err: any) {
      setActionError(err?.message || "Failed to mark insufficient evidence.");
    } finally {
      setIsPerformingAction(false);
    }
  };

  // Command 6: Finalize Assessment
  const handleFinalize = async () => {
    if (!data) return;
    try {
      setIsPerformingAction(true);
      setActionError(null);
      const res = await apiFetch<InterviewFinalizeResponse>(
        `/api/v1/interviews/simulations/${attemptId}/finalize`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_attempt_version: data.record_version,
            reason: finalizeReason.trim() || undefined,
          }),
        }
      );
      setActionSuccess("Interview assessment finalized and locked as immutable.");
      closeModal();
      onFinalized?.();
      await loadReviewData();
    } catch (err: any) {
      setActionError(err?.message || "Failed to finalize interview assessment.");
    } finally {
      setIsPerformingAction(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center p-16 space-x-3 text-slate-500">
        <Icon name="refresh" size={24} className="animate-spin text-slate-700" />
        <span className="text-sm font-medium">Loading review workspace…</span>
      </div>
    );
  }

  if (loadError || !data) {
    return (
      <div className="p-8 max-w-2xl mx-auto space-y-4">
        <div className="p-4 bg-slate-100 border border-slate-300 rounded text-slate-800 text-sm">
          {loadError || "Unable to load review workspace."}
        </div>
        <button type="button" className="btn btn-secondary btn-sm" onClick={onNavigateBack}>
          <Icon name="arrow-left" size={14} />
          <span>Back to Interviews</span>
        </button>
      </div>
    );
  }

  const isFinalized = data.is_finalized || data.lifecycle_status === "FINALIZED";
  const activeRubricAnchors = activeQuestion?.rubric?.rating_anchors || {};

  return (
    <div className="review-workspace-container max-w-7xl mx-auto p-4 md:p-6 space-y-6">
      {/* Top Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-slate-200">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn btn-ghost btn-xs text-slate-600 hover:text-slate-900"
              onClick={onNavigateBack}
              aria-label="Back to Interview workspace"
            >
              <Icon name="arrow-left" size={14} />
              <span>Back</span>
            </button>
            <span className="text-slate-300">/</span>
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Recruiter Evidence Review
            </span>
          </div>
          <h1 className="text-xl font-bold text-slate-900 tracking-tight m-0">
            {data.candidate_name || "Candidate"} &mdash; {data.job_title || "Interview Review"}
          </h1>
        </div>

        <div className="flex items-center gap-3">
          {/* Status Badge */}
          {isFinalized ? (
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-slate-900 text-white border border-slate-800 shadow-sm">
              <Icon name="lock" size={12} />
              <span>Finalized &amp; Locked</span>
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-teal-50 text-teal-800 border border-teal-200">
              <Icon name="clock" size={12} />
              <span>Review In Progress ({reviewProgress.coreReviewedCount}/{reviewProgress.coreTotalCount} Core)</span>
            </span>
          )}

          {/* Aggregate Breakdown Drawer Button */}
          {data.aggregate && (
            <button
              type="button"
              className="btn btn-secondary btn-sm flex items-center gap-1.5"
              onClick={() => setShowAggregateDrawer(!showAggregateDrawer)}
              aria-expanded={showAggregateDrawer}
            >
              <Icon name="bar-chart" size={14} />
              <span>Core Aggregate: <strong>{data.aggregate.aggregate_score != null ? `${data.aggregate.aggregate_score.toFixed(2)}/5` : "N/A"}</strong></span>
            </button>
          )}

          {/* Finalize Button */}
          {!isFinalized && (
            <button
              type="button"
              className="btn btn-primary btn-sm flex items-center gap-1.5"
              onClick={() => {
                setActionError(null);
                setActiveActionModal("FINALIZE");
              }}
              disabled={!data.eligibility?.is_eligible}
              title={
                !data.eligibility?.is_eligible
                  ? `Finalization blocked: ${data.eligibility?.blocking_reasons?.join("; ") || "Reviews incomplete"}`
                  : "Finalize assessment"
              }
            >
              <Icon name="check-circle" size={14} />
              <span>Finalize Assessment</span>
            </button>
          )}
        </div>
      </div>

      {/* Success Notification */}
      {actionSuccess && (
        <div className="p-3 bg-teal-50 border border-teal-200 text-teal-900 text-xs rounded flex items-center justify-between">
          <span>{actionSuccess}</span>
          <button type="button" className="text-teal-700 hover:text-teal-900" onClick={() => setActionSuccess(null)}>
            <Icon name="x" size={12} />
          </button>
        </div>
      )}

      {/* Finalized Banner if Locked */}
      {isFinalized && (
        <div className="p-4 bg-slate-50 border border-slate-300 rounded-lg text-slate-800 space-y-1 shadow-sm">
          <div className="flex items-center gap-2 font-bold text-sm text-slate-900">
            <Icon name="shield-check" size={16} className="text-slate-800" />
            <span>Authoritative Assessment Finalized</span>
          </div>
          <p className="text-xs text-slate-600 m-0">
            This assessment has been finalized by recruiter review and is permanently immutable. Match records, rankings and raw scores were not altered.
          </p>
        </div>
      )}

      {/* Phase 3D.4 Part D: Visual Summaries Grid */}
      <div className="visual-summary-grid">
        {/* Card 1: Review Progress & Core vs Supplemental */}
        <div className="visual-summary-card">
          <div className="visual-summary-title">Review Progress &amp; Core Separation</div>
          <div className="space-y-2 text-xs">
            <div className="flex justify-between items-center">
              <span className="text-slate-600 font-medium">Core Questions:</span>
              <span className="font-bold text-slate-900">
                {reviewProgress.coreReviewedCount} of {reviewProgress.coreTotalCount} reviewed
              </span>
            </div>
            <div className="rating-bar-track">
              <div
                className="rating-bar-fill"
                style={{
                  width: `${reviewProgress.coreTotalCount > 0 ? (reviewProgress.coreReviewedCount / reviewProgress.coreTotalCount) * 100 : 0}%`,
                }}
              />
            </div>
            <div className="pt-1 flex justify-between items-center text-[11px] text-slate-500">
              <span>Supplemental Questions: {data.questions.filter(q => q.layer !== "COMMON_CORE").length}</span>
              <span className="italic">Scores strictly segregated</span>
            </div>
          </div>
        </div>

        {/* Card 2: Human-Reviewed Core Rating Distribution */}
        <div className="visual-summary-card">
          <div className="visual-summary-title">Core Rating Distribution (Human-Reviewed)</div>
          <div className="space-y-1.5 text-xs">
            {[5, 4, 3, 2, 1].map((lvl) => {
              const count = (ratingDistribution as any)[lvl] || 0;
              const pct = ratingDistribution.totalScored > 0 ? (count / ratingDistribution.totalScored) * 100 : 0;
              return (
                <div key={lvl} className="rating-distribution-bar-row">
                  <span className="w-12 text-slate-600 font-mono text-[11px]">Level {lvl}</span>
                  <div className="rating-bar-track">
                    <div className="rating-bar-fill" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="w-6 text-right font-semibold text-slate-700 font-mono text-[11px]">{count}</span>
                </div>
              );
            })}
            <div className="text-[10px] text-slate-500 pt-1 text-right">
              {ratingDistribution.totalScored} of {reviewProgress.coreTotalCount} core scored
            </div>
          </div>
        </div>

        {/* Card 3: Provenance Timeline */}
        <div className="visual-summary-card">
          <div className="visual-summary-title">Assessment Provenance Timeline</div>
          <div className="space-y-1">
            <div className="provenance-timeline-step active">
              <div className="provenance-timeline-dot completed" />
              <span>1. Candidate Answer Submitted</span>
            </div>
            <div className={`provenance-timeline-step ${data.lifecycle_status !== "SUBMITTED" ? "active" : ""}`}>
              <div className={`provenance-timeline-dot ${data.lifecycle_status !== "SUBMITTED" ? "completed" : "current"}`} />
              <span>2. Provisional Local AI Assessment</span>
            </div>
            <div className={`provenance-timeline-step ${reviewProgress.reviewedCount > 0 ? "active" : ""}`}>
              <div className={`provenance-timeline-dot ${reviewProgress.coreReviewedCount === reviewProgress.coreTotalCount ? "completed" : "current"}`} />
              <span>3. Recruiter Human Review ({reviewProgress.coreReviewedCount}/{reviewProgress.coreTotalCount})</span>
            </div>
            <div className={`provenance-timeline-step ${isFinalized ? "active" : ""}`}>
              <div className={`provenance-timeline-dot ${isFinalized ? "completed" : ""}`} />
              <span>4. Finalized &amp; Immutable Lock</span>
            </div>
          </div>
        </div>
      </div>

      {/* Aggregate Score Details Drawer */}
      {showAggregateDrawer && data.aggregate && (
        <div className="p-5 bg-white border border-slate-200 rounded-lg space-y-4 shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 pb-3">
            <div className="flex items-center gap-2">
              <Icon name="bar-chart" size={16} className="text-slate-700" />
              <h3 className="text-sm font-bold text-slate-900 m-0">Deterministic Common-Core Aggregate</h3>
            </div>
            <span className="text-xs text-slate-500 font-mono">Policy: v{data.aggregate.policy_version}</span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
            <div className="p-3 bg-slate-50 border border-slate-200 rounded">
              <span className="text-slate-500 block">Aggregate Rating</span>
              <span className="text-lg font-bold text-slate-900">
                {data.aggregate.aggregate_score != null ? `${data.aggregate.aggregate_score.toFixed(2)} / 5.00` : "N/A"}
              </span>
            </div>
            <div className="p-3 bg-slate-50 border border-slate-200 rounded">
              <span className="text-slate-500 block">Formula Inputs</span>
              <span className="text-slate-800 font-mono">
                {data.aggregate.numerator ?? 0} / {data.aggregate.denominator ?? 0}
              </span>
            </div>
            <div className="p-3 bg-slate-50 border border-slate-200 rounded">
              <span className="text-slate-500 block">Scored Core Questions</span>
              <span className="text-slate-800 font-semibold">
                {data.aggregate.scored_core_questions} of {data.aggregate.total_core_questions}
              </span>
            </div>
            <div className="p-3 bg-slate-50 border border-slate-200 rounded">
              <span className="text-slate-500 block">Supplemental Excluded</span>
              <span className="text-slate-800 font-semibold">
                {data.aggregate.supplemental_questions_excluded} clarification(s)
              </span>
            </div>
          </div>

          {/* Breakdown Table */}
          <div className="space-y-2 text-xs">
            <span className="font-semibold text-slate-700 block">Rated Competency Inputs:</span>
            <div className="border border-slate-200 rounded overflow-hidden">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 border-b border-slate-200 text-slate-600 font-semibold">
                  <tr>
                    <th className="p-2">Competency</th>
                    <th className="p-2">Reviewed Level</th>
                    <th className="p-2">Weight</th>
                    <th className="p-2">Inclusion Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.aggregate.included_items.map((item, idx) => (
                    <tr key={idx} className="hover:bg-slate-50/50">
                      <td className="p-2 font-medium text-slate-900">{formatCompetencyName(item.competency)}</td>
                      <td className="p-2">
                        <span className="px-2 py-0.5 rounded font-bold bg-teal-50 text-teal-800 border border-teal-200">
                          Level {item.level}
                        </span>
                      </td>
                      <td className="p-2 text-slate-600 font-mono">{item.weight}x</td>
                      <td className="p-2 text-teal-700 font-medium">Included in Core Score</td>
                    </tr>
                  ))}
                  {data.aggregate.excluded_items.map((item, idx) => (
                    <tr key={`ex-${idx}`} className="bg-slate-50/70 text-slate-500">
                      <td className="p-2 font-medium">{formatCompetencyName(item.competency)}</td>
                      <td className="p-2 text-slate-400">&mdash;</td>
                      <td className="p-2 font-mono">0x</td>
                      <td className="p-2 text-slate-500 italic">Excluded: {item.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <p className="text-[11px] text-slate-500 m-0">
            * Note: This core score is deterministically calculated from human review decisions. It is never mapped to automated hiring decisions.
          </p>
        </div>
      )}

      {/* Main Review Workspace Layout: Navigation List (Left/Top) + Detail (Right) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: Question Navigator */}
        <div className="lg:col-span-4 space-y-3">
          <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-slate-500 px-1">
            <span>Questions ({data.questions.length})</span>
            <span>Reviewed: {reviewProgress.reviewedCount}/{reviewProgress.totalCount}</span>
          </div>

          <div className="space-y-2" role="tablist" aria-label="Question selector">
            {data.questions.map((q, idx) => {
              const isSelected = idx === activeQuestionIndex;
              const hasReview = !!q.latest_review;
              const isCore = q.layer === "COMMON_CORE";
              const provLevel = q.provisional_assessment?.provisional_level;

              let decisionBadge = (
                <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-slate-100 text-slate-600 border border-slate-200">
                  Awaiting review
                </span>
              );

              if (hasReview) {
                const dec = q.latest_review?.decision;
                if (dec === "ACCEPTED") {
                  decisionBadge = (
                    <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-teal-50 text-teal-800 border border-teal-200">
                      Accepted (L{q.latest_review?.reviewed_rubric_level})
                    </span>
                  );
                } else if (dec === "OVERRIDDEN") {
                  decisionBadge = (
                    <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-indigo-50 text-indigo-800 border border-indigo-200">
                      Overridden (L{q.latest_review?.reviewed_rubric_level})
                    </span>
                  );
                } else if (dec === "EDITED") {
                  decisionBadge = (
                    <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-slate-100 text-slate-800 border border-slate-300">
                      Edited
                    </span>
                  );
                } else if (dec === "REJECTED") {
                  decisionBadge = (
                    <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-slate-200 text-slate-700 border border-slate-300">
                      Rejected
                    </span>
                  );
                } else if (dec === "INSUFFICIENT") {
                  decisionBadge = (
                    <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-amber-50 text-amber-800 border border-amber-200">
                      Insufficient
                    </span>
                  );
                }
              }

              return (
                <button
                  key={q.question_id}
                  type="button"
                  role="tab"
                  aria-selected={isSelected}
                  className={`w-full text-left p-3.5 rounded-lg border transition-all space-y-1.5 ${
                    isSelected
                      ? "bg-white border-slate-900 shadow-sm ring-1 ring-slate-900"
                      : "bg-white border-slate-200 hover:border-slate-300 text-slate-700"
                  }`}
                  onClick={() => {
                    setActiveQuestionIndex(idx);
                    setHighlightedSegmentId(null);
                  }}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-bold text-xs text-slate-900">
                      Q{idx + 1}: {formatCompetencyName(q.competency_name)}
                    </span>
                    <span className="text-[10px] text-slate-400">
                      {isCore ? "Core" : "Clarification"}
                    </span>
                  </div>

                  <p className="text-xs text-slate-600 line-clamp-2 m-0 leading-relaxed">
                    {q.question_text}
                  </p>

                  <div className="flex items-center justify-between pt-1">
                    {decisionBadge}
                    {provLevel && !hasReview && (
                      <span className="text-[11px] text-slate-500">AI: L{provLevel}</span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Right Column: Evidence & Review Detail */}
        {activeQuestion && (
          <div className="lg:col-span-8 space-y-5">
            {/* Question & Anchor Header */}
            <div className="p-5 bg-white border border-slate-200 rounded-lg space-y-3 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="px-2.5 py-0.5 rounded font-bold text-xs bg-slate-900 text-white">
                    Question {activeQuestionIndex + 1}
                  </span>
                  <span className="font-semibold text-sm text-slate-800">
                    {formatCompetencyName(activeQuestion.competency_name)}
                  </span>
                  <span className="text-xs text-slate-400">&bull;</span>
                  <span className="text-xs text-slate-500 font-medium">
                    {activeQuestion.layer === "COMMON_CORE" ? "Common Core Blueprint" : "Supplemental Evidence Clarification"}
                  </span>
                </div>
                <div className="text-xs text-slate-500">
                  Target: <strong>{activeQuestion.estimated_time_minutes} min</strong>
                </div>
              </div>

              <h2 className="text-base font-semibold text-slate-900 leading-relaxed m-0">
                {activeQuestion.question_text}
              </h2>

              {/* Rubric Anchors Preview */}
              {Object.keys(activeRubricAnchors).length > 0 && (
                <div className="mt-3 pt-3 border-t border-slate-100 text-xs space-y-1.5">
                  <span className="font-semibold text-slate-700 block">Approved Rubric Anchors:</span>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    {Object.entries(activeRubricAnchors).map(([lvl, desc]) => (
                      <div key={lvl} className="p-2 bg-slate-50 border border-slate-200 rounded text-[11px] space-y-0.5">
                        <span className="font-bold text-slate-900 block">Level {lvl}</span>
                        <span className="text-slate-600 leading-tight block line-clamp-3">{desc}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Submitted Answer & Answer Segments */}
            <div className="p-5 bg-white border border-slate-200 rounded-lg space-y-3 shadow-sm">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Icon name="file-text" size={16} className="text-slate-700" />
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-800 m-0">
                    Submitted Candidate Answer
                  </h3>
                </div>
                <span className="text-xs text-slate-500">
                  {activeQuestion.answer_segments.length} segment(s) indexed
                </span>
              </div>

              {activeQuestion.submitted_answer ? (
                <div className="space-y-3">
                  {/* Highlighted text presentation */}
                  <div className="p-4 bg-slate-50/60 border border-slate-200 rounded-lg text-sm text-slate-800 leading-relaxed whitespace-pre-wrap">
                    {activeQuestion.submitted_answer}
                  </div>

                  {/* Available Segments list */}
                  <div className="space-y-1.5 pt-1">
                    <span className="text-xs font-semibold text-slate-700 block">
                      Stored Answer Evidence Segments:
                    </span>
                    <div className="space-y-1">
                      {activeQuestion.answer_segments.map((seg, sIdx) => {
                        const isHighlighted = highlightedSegmentId === seg.segment_id;
                        const isCitedInReview = activeQuestion.latest_review?.retained_citations.includes(seg.segment_id);
                        const isCitedInProvisional = activeQuestion.provisional_assessment?.cited_segment_ids?.includes(seg.segment_id);

                        return (
                          <div
                            key={seg.segment_id}
                            className={`p-2.5 rounded border text-xs cursor-pointer transition-colors ${
                              isHighlighted
                                ? "bg-teal-50 border-teal-300 text-teal-900 shadow-sm"
                                : "bg-white border-slate-200 hover:border-slate-300 text-slate-700"
                            }`}
                            onClick={() => setHighlightedSegmentId(isHighlighted ? null : seg.segment_id)}
                          >
                            <div className="flex items-center justify-between mb-1">
                              <span className="font-mono text-[10px] text-slate-500">
                                Segment #{seg.sequence_order} (chars {seg.start_char}&ndash;{seg.end_char})
                              </span>
                              <div className="flex items-center gap-1.5">
                                {isCitedInReview && (
                                  <span className="px-1.5 py-0.2 rounded text-[10px] font-semibold bg-teal-100 text-teal-800">
                                    Cited in Review
                                  </span>
                                )}
                                {isCitedInProvisional && !isCitedInReview && (
                                  <span className="px-1.5 py-0.2 rounded text-[10px] font-semibold bg-slate-100 text-slate-700">
                                    Cited in AI
                                  </span>
                                )}
                              </div>
                            </div>
                            <p className="m-0 italic text-slate-800">&ldquo;{seg.segment_text}&rdquo;</p>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="p-4 bg-slate-50 border border-slate-200 rounded text-xs text-slate-500 italic">
                  No answer submitted for this question.
                </div>
              )}
            </div>

            {/* Provisional AI Assessment Area */}
            <div className="p-5 bg-white border border-slate-200 rounded-lg space-y-4 shadow-sm">
              <div className="flex items-center justify-between border-b border-slate-100 pb-3">
                <div className="flex items-center gap-2">
                  <Icon name="sparkles" size={16} className="text-slate-700" />
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-800 m-0">
                    Provisional AI Assessment
                  </h3>
                </div>
                {(() => {
                  const meta = activeQuestion.provisional_assessment?.model_invocation_metadata || (activeQuestion.provisional_assessment as any)?.model_provenance;
                  const label = meta?.model_name
                    ? `${meta.model_name}${meta.provider_class ? ` (${meta.provider_class})` : ""}`
                    : "Local-model evaluation";
                  return (
                    <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-slate-100 text-slate-600 border border-slate-200">
                      {label}
                    </span>
                  );
                })()}
              </div>

              {activeQuestion.provisional_assessment ? (
                <div className="space-y-3 text-xs text-slate-700">
                  <div className="flex items-center justify-between p-3 bg-slate-50 border border-slate-200 rounded">
                    <div>
                      <span className="text-slate-500 block">Proposed Rubric Level</span>
                      <span className="text-base font-bold text-slate-900">
                        {activeQuestion.provisional_assessment.provisional_level != null
                          ? `Proposed Level ${activeQuestion.provisional_assessment.provisional_level}`
                          : (activeQuestion.provisional_assessment as any).proposed_rubric_level != null
                          ? `Proposed Level ${(activeQuestion.provisional_assessment as any).proposed_rubric_level}`
                          : "Insufficient Evidence"}
                      </span>
                    </div>
                    <div className="text-right">
                      <span className="text-slate-500 block">Status</span>
                      <span className="font-semibold text-slate-800">
                        {activeQuestion.provisional_assessment.assessment_status || "PROVISIONAL"}
                      </span>
                    </div>
                  </div>

                  {/* Provisional Observation Text */}
                  {(() => {
                    const observations = activeQuestion.provisional_assessment.criterion_observations?.length
                      ? activeQuestion.provisional_assessment.criterion_observations
                      : (activeQuestion.provisional_assessment as any).provisional_observation
                      ? [
                          {
                            criterion_id: activeQuestion.competency_name,
                            observation: (activeQuestion.provisional_assessment as any).provisional_observation,
                            observation_text: (activeQuestion.provisional_assessment as any).provisional_observation,
                            cited_segment_ids: activeQuestion.provisional_assessment.cited_segment_ids || [],
                          },
                        ]
                      : [];

                    return observations.map((obs, oIdx) => (
                      <div key={oIdx} className="p-3 bg-slate-50/80 border border-slate-200 rounded space-y-1.5">
                        <span className="font-semibold text-slate-800 block">
                          Model Observation ({formatCompetencyName(obs.criterion_id)}):
                        </span>
                        <p className="m-0 text-slate-800 leading-relaxed">
                          {obs.observation_text || obs.observation || "No observation recorded."}
                        </p>
                        {obs.cited_segment_ids?.length > 0 && (
                          <div className="pt-1 flex items-center gap-1.5 flex-wrap">
                            <span className="text-slate-500 text-[11px]">Cited Segments:</span>
                            {obs.cited_segment_ids.map((cId) => (
                              <span key={cId} className="px-1.5 py-0.5 rounded bg-white border border-slate-200 text-slate-700 font-mono text-[10px]">
                                {cId.slice(0, 8)}…
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    ));
                  })()}

                  {/* Uncertainty & Insufficient Warning */}
                  {activeQuestion.provisional_assessment.uncertainty_codes?.length > 0 && (
                    <div className="p-2.5 bg-amber-50 border border-amber-200 rounded text-amber-900 text-xs">
                      <strong>Uncertainty Indicators:</strong> {activeQuestion.provisional_assessment.uncertainty_codes.join(", ")}
                    </div>
                  )}

                  <p className="text-[11px] text-slate-500 m-0 italic">
                    AI assistance is provisional until recruiter review.
                  </p>
                </div>
              ) : (
                <p className="text-xs text-slate-500 italic m-0">No provisional assessment recorded for this question.</p>
              )}
            </div>

            {/* Human Recruiter Review Area */}
            <div className="p-5 bg-white border border-slate-300 rounded-lg space-y-4 shadow-sm">
              <div className="flex items-center justify-between border-b border-slate-200 pb-3">
                <div className="flex items-center gap-2">
                  <Icon name="check-circle" size={16} className="text-slate-800" />
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-900 m-0">
                    Recruiter Human Review Decision
                  </h3>
                </div>
                {activeQuestion.latest_review ? (
                  <span className="px-2.5 py-0.5 rounded text-xs font-bold bg-teal-50 text-teal-800 border border-teal-200">
                    {activeQuestion.latest_review.decision} (v{activeQuestion.latest_review.review_version})
                  </span>
                ) : (
                  <span className="text-xs text-amber-800 bg-amber-50 px-2 py-0.5 rounded border border-amber-200 font-medium">
                    Pending Decision
                  </span>
                )}
              </div>

              {/* Current Recorded Decision Display */}
              {activeQuestion.latest_review ? (
                <div className="space-y-3 p-3.5 bg-slate-50 border border-slate-200 rounded text-xs text-slate-800">
                  <div className="flex items-center justify-between">
                    <span>
                      Reviewed Rubric Rating:{" "}
                      <strong>
                        {activeQuestion.latest_review.reviewed_rubric_level != null
                          ? `Level ${activeQuestion.latest_review.reviewed_rubric_level}`
                          : "None"}
                      </strong>
                    </span>
                    <span className="text-slate-500 font-mono text-[11px]">
                      {new Date(activeQuestion.latest_review.updated_at).toLocaleString()}
                    </span>
                  </div>

                  <div className="space-y-1">
                    <span className="font-semibold text-slate-700 block">Reviewed Observation:</span>
                    <p className="m-0 p-2.5 bg-white border border-slate-200 rounded text-slate-900 leading-relaxed">
                      {activeQuestion.latest_review.reviewed_observation}
                    </p>
                  </div>

                  {activeQuestion.latest_review.reason && (
                    <div className="space-y-0.5">
                      <span className="font-semibold text-slate-600 block">Documented Reason / Justification:</span>
                      <p className="m-0 text-slate-700 italic">{activeQuestion.latest_review.reason}</p>
                    </div>
                  )}

                  <div className="flex items-center gap-1.5 flex-wrap pt-1">
                    <span className="text-slate-600 font-semibold">Retained Citations:</span>
                    {activeQuestion.latest_review.retained_citations.length > 0 ? (
                      activeQuestion.latest_review.retained_citations.map((cId) => (
                        <span key={cId} className="px-1.5 py-0.5 rounded bg-white border border-slate-300 text-slate-800 font-mono text-[10px]">
                          {cId.slice(0, 8)}…
                        </span>
                      ))
                    ) : (
                      <span className="text-slate-500 italic">None</span>
                    )}
                  </div>
                </div>
              ) : (
                <p className="text-xs text-slate-600 m-0">
                  Review this question’s submitted evidence and select a human review action below.
                </p>
              )}

              {/* Action Buttons (Disabled if Finalized) */}
              {!isFinalized && (
                <div className="flex flex-wrap items-center gap-2 pt-2">
                  <button
                    type="button"
                    className="btn btn-primary btn-sm flex items-center gap-1.5"
                    onClick={handleAcceptProvisional}
                    disabled={isPerformingAction || !activeQuestion.provisional_assessment}
                    title="Accept provisional observation, rubric level and evidence citations without modification"
                  >
                    <Icon name="check" size={13} />
                    <span>Accept Provisional</span>
                  </button>

                  <button
                    type="button"
                    className="btn btn-secondary btn-sm flex items-center gap-1.5"
                    onClick={openEditModal}
                    disabled={isPerformingAction}
                    title="Edit observation text while retaining valid answer citations"
                  >
                    <Icon name="edit" size={13} />
                    <span>Edit Observation</span>
                  </button>

                  <button
                    type="button"
                    className="btn btn-secondary btn-sm flex items-center gap-1.5"
                    onClick={openOverrideModal}
                    disabled={isPerformingAction}
                    title="Override rubric level with mandatory non-empty reason"
                  >
                    <Icon name="edit" size={13} />
                    <span>Override Level</span>
                  </button>

                  <button
                    type="button"
                    className="btn btn-ghost btn-sm flex items-center gap-1.5 text-slate-700 hover:text-slate-900"
                    onClick={openRejectModal}
                    disabled={isPerformingAction || !activeQuestion.provisional_assessment}
                    title="Reject provisional observation with reason without altering underlying provisional record"
                  >
                    <Icon name="x" size={13} />
                    <span>Reject Observation</span>
                  </button>

                  <button
                    type="button"
                    className="btn btn-ghost btn-sm flex items-center gap-1.5 text-amber-800 hover:text-amber-900"
                    onClick={openInsufficientModal}
                    disabled={isPerformingAction}
                    title="Mark evidence as insufficient (neutral outcome, not scored as zero)"
                  >
                    <Icon name="info" size={13} />
                    <span>Mark Insufficient</span>
                  </button>
                </div>
              )}
            </div>

            {/* Bottom Nav between questions */}
            <div className="flex items-center justify-between pt-2 border-t border-slate-200 text-xs">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => {
                  setActiveQuestionIndex((prev) => Math.max(0, prev - 1));
                  setHighlightedSegmentId(null);
                }}
                disabled={activeQuestionIndex === 0}
              >
                <Icon name="chevron-left" size={14} />
                <span>Previous Question</span>
              </button>
              <span className="text-slate-500">
                Question {activeQuestionIndex + 1} of {data.questions.length}
              </span>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => {
                  setActiveQuestionIndex((prev) => Math.min(data.questions.length - 1, prev + 1));
                  setHighlightedSegmentId(null);
                }}
                disabled={activeQuestionIndex === data.questions.length - 1}
              >
                <span>Next Question</span>
                <Icon name="chevron-right" size={14} />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* =========================================================================
          MODALS FOR ACTIONS (Edit, Override, Reject, Insufficient, Finalize)
          ========================================================================= */}

      {/* 1. Edit Observation Modal */}
      {activeActionModal === "EDIT" && activeQuestion && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-white rounded-lg max-w-xl w-full p-6 space-y-4 shadow-xl border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="text-base font-bold text-slate-900 m-0">
                Edit Assessment Observation
              </h3>
              <button type="button" className="text-slate-400 hover:text-slate-600" onClick={closeModal}>
                <Icon name="x" size={16} />
              </button>
            </div>

            {actionError && (
              <div className="p-3 bg-slate-100 border border-slate-300 text-slate-800 text-xs rounded">
                {actionError}
              </div>
            )}

            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-700 block">
                Reviewed Observation (Plain Text):
              </label>
              <textarea
                className="w-full min-h-[110px] p-3 text-xs border border-slate-300 rounded focus:ring-1 focus:ring-slate-800 text-slate-900"
                value={editObservationText}
                onChange={(e) => setEditObservationText(e.target.value)}
                placeholder="Enter refined observation supported by cited answer segments..."
              />
            </div>

            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-700 block">
                Select Supporting Answer Citations (At least 1 required):
              </label>
              <div className="max-h-48 overflow-y-auto space-y-1.5 border border-slate-200 rounded p-2">
                {activeQuestion.answer_segments.map((seg) => {
                  const isChecked = editSelectedSegmentIds.includes(seg.segment_id);
                  return (
                    <label
                      key={seg.segment_id}
                      className={`flex items-start gap-2 p-2 rounded border text-xs cursor-pointer ${
                        isChecked ? "bg-teal-50 border-teal-200 text-teal-900" : "bg-white border-slate-100 text-slate-700"
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="mt-0.5"
                        checked={isChecked}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setEditSelectedSegmentIds([...editSelectedSegmentIds, seg.segment_id]);
                          } else {
                            setEditSelectedSegmentIds(editSelectedSegmentIds.filter((id) => id !== seg.segment_id));
                          }
                        }}
                      />
                      <span className="flex-1 italic">&ldquo;{seg.segment_text}&rdquo;</span>
                    </label>
                  );
                })}
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-3 border-t border-slate-100">
              <button type="button" className="btn btn-secondary btn-sm" onClick={closeModal} disabled={isPerformingAction}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleSubmitEdit}
                disabled={isPerformingAction || editSelectedSegmentIds.length === 0}
              >
                {isPerformingAction ? "Saving…" : "Save Edited Observation"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 2. Override Level Modal */}
      {activeActionModal === "OVERRIDE" && activeQuestion && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-white rounded-lg max-w-md w-full p-6 space-y-4 shadow-xl border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="text-base font-bold text-slate-900 m-0">Override Rubric Level</h3>
              <button type="button" className="text-slate-400 hover:text-slate-600" onClick={closeModal}>
                <Icon name="x" size={16} />
              </button>
            </div>

            {actionError && (
              <div className="p-3 bg-slate-100 border border-slate-300 text-slate-800 text-xs rounded">
                {actionError}
              </div>
            )}

            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-700 block">Select Overridden Rubric Level (1-5):</label>
              <div className="flex gap-2">
                {[1, 2, 3, 4, 5].map((lvl) => (
                  <button
                    key={lvl}
                    type="button"
                    className={`flex-1 py-2 rounded text-xs font-bold border transition-colors ${
                      overrideLevel === lvl
                        ? "bg-slate-900 text-white border-slate-900"
                        : "bg-white text-slate-700 border-slate-200 hover:bg-slate-50"
                    }`}
                    onClick={() => setOverrideLevel(lvl)}
                  >
                    Level {lvl}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-700 block">
                Required Justification Reason:
              </label>
              <textarea
                className="w-full min-h-[90px] p-2.5 text-xs border border-slate-300 rounded focus:ring-1 focus:ring-slate-800 text-slate-900"
                value={overrideReason}
                onChange={(e) => setOverrideReason(e.target.value)}
                placeholder="Explain why this answer warrants a level change..."
              />
            </div>

            <div className="flex justify-end gap-2 pt-3 border-t border-slate-100">
              <button type="button" className="btn btn-secondary btn-sm" onClick={closeModal} disabled={isPerformingAction}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleSubmitOverride}
                disabled={isPerformingAction || !overrideReason.trim()}
              >
                {isPerformingAction ? "Overriding…" : "Confirm Level Override"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 3. Reject Observation Modal */}
      {activeActionModal === "REJECT" && activeQuestion && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-white rounded-lg max-w-md w-full p-6 space-y-4 shadow-xl border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="text-base font-bold text-slate-900 m-0">Reject Provisional Observation</h3>
              <button type="button" className="text-slate-400 hover:text-slate-600" onClick={closeModal}>
                <Icon name="x" size={16} />
              </button>
            </div>

            <p className="text-xs text-slate-600 m-0 leading-relaxed">
              Rejecting will exclude the provisional observation from the final human assessment. The original AI record is safely preserved for audit.
            </p>

            {actionError && (
              <div className="p-3 bg-slate-100 border border-slate-300 text-slate-800 text-xs rounded">
                {actionError}
              </div>
            )}

            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-700 block">
                Required Rejection Reason:
              </label>
              <textarea
                className="w-full min-h-[90px] p-2.5 text-xs border border-slate-300 rounded focus:ring-1 focus:ring-slate-800 text-slate-900"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Explain why this provisional observation is rejected..."
              />
            </div>

            <div className="flex justify-end gap-2 pt-3 border-t border-slate-100">
              <button type="button" className="btn btn-secondary btn-sm" onClick={closeModal} disabled={isPerformingAction}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleSubmitReject}
                disabled={isPerformingAction || !rejectReason.trim()}
              >
                {isPerformingAction ? "Rejecting…" : "Confirm Rejection"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 4. Mark Insufficient Modal */}
      {activeActionModal === "INSUFFICIENT" && activeQuestion && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-white rounded-lg max-w-md w-full p-6 space-y-4 shadow-xl border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="text-base font-bold text-slate-900 m-0">Mark Insufficient Evidence</h3>
              <button type="button" className="text-slate-400 hover:text-slate-600" onClick={closeModal}>
                <Icon name="x" size={16} />
              </button>
            </div>

            <p className="text-xs text-slate-600 m-0 leading-relaxed">
              Insufficient evidence is a neutral outcome, not a punitive zero score. It excludes this question from the core numerical aggregate unless an approved policy specifies otherwise.
            </p>

            {actionError && (
              <div className="p-3 bg-slate-100 border border-slate-300 text-slate-800 text-xs rounded">
                {actionError}
              </div>
            )}

            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-700 block">
                Optional Notes / Reason:
              </label>
              <textarea
                className="w-full min-h-[70px] p-2.5 text-xs border border-slate-300 rounded focus:ring-1 focus:ring-slate-800 text-slate-900"
                value={insufficientReason}
                onChange={(e) => setInsufficientReason(e.target.value)}
                placeholder="Details on why evidence is insufficient..."
              />
            </div>

            <div className="flex justify-end gap-2 pt-3 border-t border-slate-100">
              <button type="button" className="btn btn-secondary btn-sm" onClick={closeModal} disabled={isPerformingAction}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleSubmitInsufficient}
                disabled={isPerformingAction}
              >
                {isPerformingAction ? "Saving…" : "Confirm Insufficient Evidence"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 5. Finalize Assessment Modal */}
      {activeActionModal === "FINALIZE" && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-white rounded-lg max-w-lg w-full p-6 space-y-4 shadow-xl border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="text-base font-bold text-slate-900 m-0">Finalize Interview Assessment</h3>
              <button type="button" className="text-slate-400 hover:text-slate-600" onClick={closeModal}>
                <Icon name="x" size={16} />
              </button>
            </div>

            <div className="p-3 bg-slate-50 border border-slate-200 rounded text-xs space-y-2 text-slate-800">
              <div className="flex justify-between">
                <span>Core questions reviewed:</span>
                <strong>{reviewProgress.coreReviewedCount} of {reviewProgress.coreTotalCount}</strong>
              </div>
              <div className="flex justify-between">
                <span>Deterministic core aggregate:</span>
                <strong>
                  {data.aggregate?.aggregate_score != null
                    ? `${data.aggregate.aggregate_score.toFixed(2)} / 5.00`
                    : "None"}
                </strong>
              </div>
            </div>

            <p className="text-xs text-slate-600 m-0 leading-relaxed">
              Finalizing will permanently lock this assessment record as immutable. All provisional evaluations and human review iterations remain preserved for audit compliance.
            </p>

            {actionError && (
              <div className="p-3 bg-slate-100 border border-slate-300 text-slate-800 text-xs rounded">
                {actionError}
              </div>
            )}

            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-700 block">
                Optional Finalization Audit Notes:
              </label>
              <textarea
                className="w-full min-h-[70px] p-2.5 text-xs border border-slate-300 rounded focus:ring-1 focus:ring-slate-800 text-slate-900"
                value={finalizeReason}
                onChange={(e) => setFinalizeReason(e.target.value)}
                placeholder="Final recruiter assessment notes or closing summary..."
              />
            </div>

            <div className="flex justify-end gap-2 pt-3 border-t border-slate-100">
              <button type="button" className="btn btn-secondary btn-sm" onClick={closeModal} disabled={isPerformingAction}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleFinalize}
                disabled={isPerformingAction}
              >
                {isPerformingAction ? "Finalizing…" : "Lock & Finalize Assessment"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
