import React, { useEffect, useMemo, useState } from "react";
import {
  CandidateInterviewEvaluationDetail,
  EvaluationCandidateSummary,
  EvaluationJobSummary,
  EvaluationQuestionDetail,
  exportCandidateEvaluationJson,
  finalizeInterviewEvaluation,
  getCandidateEvaluationDetail,
  listEvaluationJobs,
  listJobCandidatesForEvaluation,
  publishCandidateFeedbackReport,
  saveQuestionEvaluationReview,
  triggerAiInterviewAnalysis,
} from "../../api/candidateEvaluations";
import { Icon } from "../Icon";
import { Button } from "../common/Button";
import { EmptyState } from "../common/EmptyState";
import { InlineNotice } from "../common/InlineNotice";
import { Dialog } from "../common/Dialog";
import { InterviewDimensionRadar } from "../common/InterviewDimensionRadar";
import "./evaluation.css";

// =============================================================================
// Status filter definitions
// =============================================================================
const JOB_STATUS_FILTERS = [
  { id: "ALL", label: "All" },
  { id: "READY", label: "Ready for review" },
  { id: "PENDING", label: "Analysis pending" },
  { id: "IN_REVIEW", label: "In review" },
  { id: "FINALIZED", label: "Finalized" },
] as const;

const CANDIDATE_STATUS_FILTERS = [
  { id: "ALL", label: "All candidates" },
  { id: "READY", label: "Ready for review" },
  { id: "IN_REVIEW", label: "In review" },
  { id: "FINALIZED", label: "Finalized" },
] as const;

const CANDIDATE_MODE_FILTERS = [
  { id: "TEXT", label: "Text-based" },
  { id: "AUDIO", label: "Audio-based" },
] as const;

// =============================================================================
// Main Evaluation Workspace
// =============================================================================

export const EvaluationWorkspace: React.FC = () => {
  // Level state
  const [level, setLevel] = useState<1 | 2 | 3>(1);

  // Level 1: Jobs Directory
  const [jobs, setJobs] = useState<EvaluationJobSummary[]>([]);
  const [jobsLoading, setJobsLoading] = useState<boolean>(true);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [jobSearch, setJobSearch] = useState<string>("");
  const [jobStatusFilter, setJobStatusFilter] = useState<string>("ALL");

  // Level 2: Job Candidates List
  const [selectedJob, setSelectedJob] = useState<EvaluationJobSummary | null>(null);
  const [candidates, setCandidates] = useState<EvaluationCandidateSummary[]>([]);
  const [candidatesLoading, setCandidatesLoading] = useState<boolean>(false);
  const [candidatesError, setCandidatesError] = useState<string | null>(null);
  const [candidateFilter, setCandidateFilter] = useState<string>("ALL");
  const [candidateModeFilter, setCandidateModeFilter] = useState<string>("TEXT");

  // Level 3: Candidate Evaluation Detail
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CandidateInterviewEvaluationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState<boolean>(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // Active question edit states
  const [editingSlotId, setEditingSlotId] = useState<string | null>(null);
  const [editRating, setEditRating] = useState<number>(3);
  const [editState, setEditState] = useState<string>("ACCEPTED");
  const [overrideReason, setOverrideReason] = useState<string>("");
  const [recruiterNote, setRecruiterNote] = useState<string>("");
  const [savingSlot, setSavingSlot] = useState<boolean>(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // AI Analysis trigger state
  const [analyzingAi, setAnalyzingAi] = useState<boolean>(false);
  const [aiAnalysisError, setAiAnalysisError] = useState<string | null>(null);

  // Evidence citation highlight
  const [highlightCitation, setHighlightCitation] = useState<string | null>(null);

  // Finalization Modal
  const [showFinalizeModal, setShowFinalizeModal] = useState<boolean>(false);
  const [finalizing, setFinalizing] = useState<boolean>(false);
  const [finalizeError, setFinalizeError] = useState<string | null>(null);

  // Publish Feedback
  const [publishing, setPublishing] = useState<boolean>(false);
  const [publishSuccess, setPublishSuccess] = useState<string | null>(null);

  // Expanded question cards
  const [expandedSlots, setExpandedSlots] = useState<Set<string>>(new Set());

  // ---------------------------------------------------------------------------
  // Data Fetching: Level 1
  // ---------------------------------------------------------------------------
  const fetchJobs = async () => {
    try {
      setJobsLoading(true);
      setJobsError(null);
      const data = await listEvaluationJobs();
      setJobs(data);
    } catch (err: any) {
      setJobsError(err?.message || "Failed to load evaluation jobs.");
    } finally {
      setJobsLoading(false);
    }
  };

  useEffect(() => {
    fetchJobs();
  }, []);

  // ---------------------------------------------------------------------------
  // Data Fetching: Level 2
  // ---------------------------------------------------------------------------
  const selectJobForCandidates = async (job: EvaluationJobSummary) => {
    setSelectedJob(job);
    setCandidateModeFilter("TEXT");
    setLevel(2);
    try {
      setCandidatesLoading(true);
      setCandidatesError(null);
      const data = await listJobCandidatesForEvaluation(job.job_id, job.job_revision);
      setCandidates(data);
    } catch (err: any) {
      setCandidatesError(err?.message || "Failed to load candidate evaluations for this job.");
    } finally {
      setCandidatesLoading(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Data Fetching: Level 3
  // ---------------------------------------------------------------------------
  const selectCandidateForDetail = async (sessionId: string) => {
    setSelectedSessionId(sessionId);
    setLevel(3);
    try {
      setDetailLoading(true);
      setDetailError(null);
      const data = await getCandidateEvaluationDetail(sessionId);
      setDetail(data);
      // Auto-expand all questions
      setExpandedSlots(new Set(data.questions.map((q) => q.slot_id)));
    } catch (err: any) {
      setDetailError(err?.message || "Failed to load candidate evaluation workspace.");
    } finally {
      setDetailLoading(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Run AI Analysis Handler
  // ---------------------------------------------------------------------------
  const handleRunAiAnalysis = async () => {
    if (!selectedSessionId) return;
    try {
      setAnalyzingAi(true);
      setAiAnalysisError(null);
      const updated = await triggerAiInterviewAnalysis(selectedSessionId, false);
      setDetail(updated);
      setExpandedSlots(new Set(updated.questions.map((q) => q.slot_id)));
    } catch (err: any) {
      setAiAnalysisError(err?.message || "Failed to complete AI interview analysis.");
    } finally {
      setAnalyzingAi(false);
    }
  };

  const handleRerunFailedAiAnalysis = async () => {
    if (!selectedSessionId) return;
    try {
      setAnalyzingAi(true);
      setAiAnalysisError(null);
      const updated = await triggerAiInterviewAnalysis(selectedSessionId, true);
      setDetail(updated);
      setExpandedSlots(new Set(updated.questions.map((q) => q.slot_id)));
    } catch (err: any) {
      setAiAnalysisError(err?.message || "Failed to re-run failed AI interview analyses.");
    } finally {
      setAnalyzingAi(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Question Review Save Handler
  // ---------------------------------------------------------------------------
  const handleSaveQuestion = async (slotId: string) => {
    if (!selectedSessionId) return;

    if (editState === "OVERRIDDEN" && (!overrideReason || overrideReason.trim().length === 0)) {
      setSaveError("Override reason is required when modifying provisional rating.");
      return;
    }

    try {
      setSavingSlot(true);
      setSaveError(null);
      await saveQuestionEvaluationReview(selectedSessionId, slotId, {
        review_state: editState,
        reviewed_rating: editState === "INSUFFICIENT" ? null : editRating,
        override_reason: editState === "OVERRIDDEN" ? overrideReason : null,
        recruiter_note: recruiterNote || null,
      });

      // Reload fresh evaluation details
      const refreshed = await getCandidateEvaluationDetail(selectedSessionId);
      setDetail(refreshed);
      setEditingSlotId(null);
    } catch (err: any) {
      setSaveError(err?.message || "Failed to save question review.");
    } finally {
      setSavingSlot(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Finalize Evaluation Handler
  // ---------------------------------------------------------------------------
  const handleFinalize = async () => {
    if (!selectedSessionId) return;
    try {
      setFinalizing(true);
      setFinalizeError(null);
      await finalizeInterviewEvaluation(selectedSessionId, {});
      const refreshed = await getCandidateEvaluationDetail(selectedSessionId);
      setDetail(refreshed);
      setShowFinalizeModal(false);
    } catch (err: any) {
      setFinalizeError(err?.message || "Failed to finalize evaluation.");
    } finally {
      setFinalizing(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Download JSON Handler
  // ---------------------------------------------------------------------------
  const handleDownloadJson = async () => {
    if (!detail) return;
    try {
      const sanitizedName = (detail.candidate_name || "candidate")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_");
      const modeSuffix = (detail.interview_mode || "text").toLowerCase();
      const filename = `evaluation_${sanitizedName}_${modeSuffix}_${detail.session_id.slice(0, 8)}.json`;

      let exportData: any;
      try {
        exportData = await exportCandidateEvaluationJson(detail.session_id);
      } catch {
        // Fallback to detail snapshot
        exportData = detail;
      }

      const jsonStr = JSON.stringify(exportData, null, 2);
      const blob = new Blob([jsonStr], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err: any) {
      alert("Failed to export evaluation JSON: " + (err?.message || String(err)));
    }
  };

  // ---------------------------------------------------------------------------
  // Publish Feedback Handler
  // ---------------------------------------------------------------------------
  const handlePublishFeedback = async () => {
    if (!selectedSessionId) return;
    try {
      setPublishing(true);
      setPublishSuccess(null);
      await publishCandidateFeedbackReport(selectedSessionId);
      const refreshed = await getCandidateEvaluationDetail(selectedSessionId);
      setDetail(refreshed);
      setPublishSuccess("Candidate feedback successfully published to candidate portal!");
    } catch (err: any) {
      alert(err?.message || "Failed to publish feedback report.");
    } finally {
      setPublishing(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Toggle question expansion
  // ---------------------------------------------------------------------------
  const toggleSlotExpanded = (slotId: string) => {
    setExpandedSlots((prev) => {
      const next = new Set(prev);
      if (next.has(slotId)) {
        next.delete(slotId);
      } else {
        next.add(slotId);
      }
      return next;
    });
  };

  // ---------------------------------------------------------------------------
  // Filtered Jobs
  // ---------------------------------------------------------------------------
  const filteredJobs = useMemo(() => {
    let result = jobs;
    if (jobSearch) {
      const q = jobSearch.toLowerCase();
      result = result.filter((j) => j.job_title.toLowerCase().includes(q));
    }
    if (jobStatusFilter === "READY") {
      result = result.filter((j) => j.ready_for_review_count > 0);
    } else if (jobStatusFilter === "PENDING") {
      result = result.filter((j) => j.analysis_pending_count > 0);
    } else if (jobStatusFilter === "IN_REVIEW") {
      result = result.filter((j) => j.submitted_count > j.finalized_count);
    } else if (jobStatusFilter === "FINALIZED") {
      result = result.filter((j) => j.finalized_count > 0);
    }
    return result;
  }, [jobs, jobSearch, jobStatusFilter]);

  // Filtered Candidates
  const filteredCandidates = useMemo(() => {
    let list = candidates;
    if (candidateModeFilter !== "ALL") {
      list = list.filter((c) => (c.interview_mode || "TEXT") === candidateModeFilter);
    }
    if (candidateFilter === "READY")
      return list.filter((c) => c.recruiter_evaluation_status === "Ready for review");
    if (candidateFilter === "IN_REVIEW")
      return list.filter((c) => c.recruiter_evaluation_status === "In review");
    if (candidateFilter === "FINALIZED")
      return list.filter((c) => c.recruiter_evaluation_status === "Finalized");
    if (candidateFilter === "UNAVAILABLE")
      return list.filter((c) => c.ai_analysis_status === "Analysis unavailable");
    return list;
  }, [candidates, candidateFilter, candidateModeFilter]);

  // ===========================================================================
  // Question Card Renderer
  // ===========================================================================
  const renderQuestionCard = (q: EvaluationQuestionDetail, isFinalized: boolean) => {
    const isEditing = editingSlotId === q.slot_id;
    const review = q.recruiter_review;
    const isExpanded = expandedSlots.has(q.slot_id);
    const aiAnalysis = q.ai_provisional_analysis || q.ai_provisional;
    const candidateAnswerText = q.submitted_answer_text ?? q.candidate_text_answer;
    const candidateSelectedId = q.selected_option_id ?? q.candidate_selected_option_id;
    const candidateSelectedText = q.selected_option_text ?? q.candidate_selected_option_text;
    const competencyLabel = q.competency ?? q.competency_name;
    const explanationText = q.stored_explanation ?? q.explanation;
    const hasOverride = review && review.review_state === "OVERRIDDEN" && review.reviewed_rating != null;

    return (
      <div
        key={q.slot_id}
        className={`eval-question-card ${isEditing ? "active-review" : ""}`}
      >
        {/* Question Header */}
        <button
          type="button"
          className="eval-question-header"
          onClick={() => toggleSlotExpanded(q.slot_id)}
          aria-expanded={isExpanded}
          aria-label={`Question ${q.question_order}: ${q.question_text}`}
        >
          <div className="eval-question-badges">
            <span className="eval-badge-num">Q{q.question_order}</span>
            <span className="eval-badge-core">{q.question_format}</span>
            {q.is_supplemental && <span className="eval-badge-supp">Supplemental</span>}
            {competencyLabel && (
              <span className="eval-tag-department">{competencyLabel}</span>
            )}
          </div>

          <div className="eval-question-header-right">
            {/* Status Badge: Authoritative AI or Optional Override */}
            {hasOverride ? (
              <span className="eval-status-badge unavailable">
                Override · {review.reviewed_rating!.toFixed(1)} / 5.0
              </span>
            ) : aiAnalysis && (aiAnalysis.analysis_status === "UNAVAILABLE" || aiAnalysis.status === "UNAVAILABLE") ? (
              <span className="eval-status-badge unavailable">AI analysis unavailable</span>
            ) : aiAnalysis && (aiAnalysis.analysis_status === "PENDING" || aiAnalysis.status === "PENDING") ? (
              <span className="eval-status-badge">AI evaluation pending</span>
            ) : aiAnalysis && aiAnalysis.provisional_rating != null ? (
              <span className="eval-status-badge ready">AI evaluated · {aiAnalysis.provisional_rating.toFixed(1)} / 5.0</span>
            ) : (
              <span className="eval-status-badge">AI evaluation pending</span>
            )}
            <Icon name={isExpanded ? "chevron-up" : "chevron-down"} size={16} />
          </div>
        </button>

        {isExpanded && (
          <>
            {/* Question Stem */}
            <div className="eval-question-body">{q.question_text}</div>

            {/* Candidate Answer Box */}
            <div className="eval-answer-box">
              <span className="eval-metric-label">
                {detail?.interview_mode === "AUDIO" ? "Candidate confirmed transcript" : "Candidate submitted response"}
              </span>
              {q.question_format === "MCQ" ? (
                <div className="eval-mcq-answer">
                  <span>
                    Selected option: <strong>{candidateSelectedId || "None"}: {candidateSelectedText || "Option"}</strong>
                  </span>
                  {q.is_correct !== null && q.is_correct !== undefined && (
                    <span className={`eval-status-badge ${q.is_correct ? "finalized" : "unavailable"}`}>
                      {q.is_correct ? "Correct (deterministic)" : "Incorrect (deterministic)"}
                    </span>
                  )}
                  {explanationText && (
                    <p className="eval-mcq-explanation">{explanationText}</p>
                  )}
                </div>
              ) : (
                <p className="eval-answer-text">
                  {candidateAnswerText || (
                    <span className="eval-empty-text">No response submitted / skipped.</span>
                  )}
                </p>
              )}
            </div>

            {/* AI Analysis Box */}
            {aiAnalysis && (
              <div className="eval-ai-provisional-box">
                <div className="eval-ai-header">
                  <span className="eval-ai-tag">
                    <Icon name="sparkles" size={14} />
                    AI analysis
                  </span>
                  {aiAnalysis.provisional_rating != null && (
                    <span className="eval-ai-score-badge">
                      AI rating: <strong>{aiAnalysis.provisional_rating.toFixed(1)} / 5.0</strong>
                    </span>
                  )}
                </div>

                {aiAnalysis.analysis_status === "PENDING" || aiAnalysis.status === "PENDING" ? (
                  <div className="eval-ai-unavailable-notice" style={{ padding: "14px", background: "#f8fafc", borderRadius: "8px", margin: "8px 0", border: "1px dashed #cbd5e1" }}>
                    <p style={{ margin: 0, fontSize: "14px", color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: "8px" }}>
                      <Icon name="sparkles" size={16} />
                      AI evaluation pending. Click "Run AI analysis" to evaluate this response against the rubric.
                    </p>
                  </div>
                ) : aiAnalysis.analysis_status === "UNAVAILABLE" || aiAnalysis.status === "UNAVAILABLE" ? (
                  <div className="eval-ai-unavailable-notice" style={{ padding: "14px", background: "#f8fafc", borderRadius: "8px", margin: "8px 0", border: "1px solid #e2e8f0" }}>
                    <p style={{ margin: 0, fontSize: "14px", color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: "8px" }}>
                      <Icon name="alert-triangle" size={16} />
                      AI analysis is unavailable for this response. Retry the analysis or optionally add a recruiter assessment.
                    </p>
                  </div>
                ) : (
                  <>
                    <div className="eval-ai-content-grid">
                      {(aiAnalysis.addressed_well?.length ?? 0) > 0 && (
                        <div className="eval-ai-section eval-ai-strengths">
                          <span className="eval-metric-label">What was addressed well</span>
                          <ul className="eval-ai-list">
                            {aiAnalysis.addressed_well!.map((s, i) => (
                              <li key={i}>{s}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {(aiAnalysis.missing_or_unclear?.length ?? 0) > 0 && (
                        <div className="eval-ai-section eval-ai-gaps">
                          <span className="eval-metric-label">Missing or unclear elements</span>
                          <ul className="eval-ai-list">
                            {aiAnalysis.missing_or_unclear!.map((m, i) => (
                              <li key={i}>{m}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>

                    {/* Suggested Improvement */}
                    {(aiAnalysis.suggested_improvement || (aiAnalysis.suggested_improvements?.length ?? 0) > 0) && (
                      <div className="eval-ai-section eval-ai-growth" style={{ marginTop: "8px" }}>
                        <span className="eval-metric-label">Suggested growth area</span>
                        <p style={{ margin: "4px 0 0 0", fontSize: "14px", color: "var(--text-secondary)" }}>
                          {aiAnalysis.suggested_improvement || aiAnalysis.suggested_improvements?.join("; ")}
                        </p>
                      </div>
                    )}

                    {/* Dimension scores for this question */}
                    {aiAnalysis.dimension_scores &&
                      Object.keys(aiAnalysis.dimension_scores).length > 0 && (
                        <div className="eval-ai-dimensions">
                          <span className="eval-metric-label">Rubric dimension observations</span>
                          <div className="eval-ai-dimension-pills">
                            {Object.entries(aiAnalysis.dimension_scores).map(([dim, score]) => (
                              <span key={dim} className="eval-ai-dimension-pill">
                                {dim}: <strong>{typeof score === "number" ? score.toFixed(1) : score}/5</strong>
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                    {/* Uncertainty */}
                    {aiAnalysis.uncertainty_notes && (
                      <div className="eval-ai-uncertainty">
                        <Icon name="alert-triangle" size={14} />
                        {aiAnalysis.uncertainty_notes}
                      </div>
                    )}

                    {/* Evidence Citations */}
                    {aiAnalysis.evidence_citations && aiAnalysis.evidence_citations.length > 0 && (
                      <div className="eval-ai-citations">
                        <span className="eval-metric-label">Evidence citations</span>
                        <div className="eval-citation-list">
                          {aiAnalysis.evidence_citations.map((cit, idx) => {
                            const citId = cit.segment_id || cit.citation_id || `cit-${idx}`;
                            const snippet = cit.text || cit.snippet_text || "";
                            return (
                              <button
                                key={citId}
                                type="button"
                                onClick={() =>
                                  setHighlightCitation(
                                    highlightCitation === citId ? null : citId
                                  )
                                }
                                className={`eval-citation-btn ${highlightCitation === citId ? "active" : ""
                                  }`}
                              >
                                &ldquo;{snippet.slice(0, 70)}…&rdquo;
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {/* Optional Recruiter Override Section (Collapsed by Default) */}
            <div className="eval-review-section">
              <div className="eval-override-header-bar">
                <div className="eval-override-summary-left">
                  <span className="eval-metric-label">Optional recruiter override</span>
                  <span className="eval-override-helper">
                    Optional: add an internal note or override this AI assessment.
                  </span>
                </div>
                {!isFinalized && !isEditing && (
                  <Button
                    variant="secondary"
                    size="xs"
                    onClick={() => {
                      setEditingSlotId(q.slot_id);
                      setEditRating(review?.reviewed_rating || 3);
                      setEditState(review?.review_state && review.review_state !== "UNREVIEWED" ? review.review_state : "OVERRIDDEN");
                      setOverrideReason(review?.override_reason || "");
                      setRecruiterNote(review?.recruiter_note || "");
                      setSaveError(null);
                    }}
                  >
                    {review && review.review_state === "OVERRIDDEN" ? "Edit override" : "Add note / override"}
                  </Button>
                )}
              </div>

              {isEditing ? (
                <div className="eval-review-form" style={{ marginTop: "12px" }}>
                  {saveError && (
                    <InlineNotice variant="warning">{saveError}</InlineNotice>
                  )}

                  <div className="eval-review-form-grid">
                    <div className="eval-form-field">
                      <label htmlFor={`review-decision-${q.slot_id}`} className="eval-form-label">
                        Override action
                      </label>
                      <select
                        id={`review-decision-${q.slot_id}`}
                        value={editState}
                        onChange={(e) => setEditState(e.target.value)}
                        className="eval-form-select"
                      >
                        <option value="OVERRIDDEN">Override score / evaluation</option>
                        <option value="ACCEPTED">Add note only (preserve AI score)</option>
                        <option value="INSUFFICIENT">Mark insufficient evidence</option>
                      </select>
                    </div>

                    {editState === "OVERRIDDEN" && (
                      <div className="eval-form-field">
                        <label htmlFor={`human-rating-${q.slot_id}`} className="eval-form-label">
                          Recruiter rating (1 to 5)
                        </label>
                        <select
                          id={`human-rating-${q.slot_id}`}
                          value={editRating}
                          onChange={(e) => setEditRating(Number(e.target.value))}
                          className="eval-form-select"
                        >
                          <option value={1}>1 — Deficient / missing</option>
                          <option value={2}>2 — Marginal / incomplete</option>
                          <option value={3}>3 — Acceptable / satisfactory</option>
                          <option value={4}>4 — Strong / exceeds baseline</option>
                          <option value={5}>5 — Exceptional / comprehensive</option>
                        </select>
                      </div>
                    )}
                  </div>

                  {editState === "OVERRIDDEN" && (
                    <div className="eval-form-field">
                      <label htmlFor={`override-reason-${q.slot_id}`} className="eval-form-label eval-form-label-required">
                        Override reason (required)
                      </label>
                      <input
                        id={`override-reason-${q.slot_id}`}
                        type="text"
                        placeholder="Explain why the AI assessment was overridden…"
                        value={overrideReason}
                        onChange={(e) => setOverrideReason(e.target.value)}
                        className="eval-form-input"
                      />
                    </div>
                  )}

                  <div className="eval-form-field">
                    <label htmlFor={`recruiter-notes-${q.slot_id}`} className="eval-form-label">
                      Internal recruiter note (private to staff)
                    </label>
                    <textarea
                      id={`recruiter-notes-${q.slot_id}`}
                      rows={2}
                      placeholder="Add internal recruiter notes (strictly omitted from candidate feedback)…"
                      value={recruiterNote}
                      onChange={(e) => setRecruiterNote(e.target.value)}
                      className="eval-form-textarea"
                    />
                  </div>

                  <div className="eval-review-actions">
                    <Button variant="secondary" size="xs" onClick={() => setEditingSlotId(null)}>
                      Cancel
                    </Button>
                    <Button
                      variant="primary"
                      size="xs"
                      icon="save"
                      isLoading={savingSlot}
                      onClick={() => handleSaveQuestion(q.slot_id)}
                    >
                      Save optional override
                    </Button>
                  </div>
                </div>
              ) : review && (review.review_state === "OVERRIDDEN" || review.recruiter_note) ? (
                <div className="eval-review-summary" style={{ marginTop: "12px" }}>
                  <div className="eval-review-summary-header">
                    <strong>Recruiter note / override:</strong>
                    {review.reviewed_rating != null && (
                      <span className="eval-override-tag">Score: {review.reviewed_rating}/5.0</span>
                    )}
                  </div>
                  {review.override_reason && (
                    <div className="eval-override-reason">
                      <strong>Override reason:</strong> {review.override_reason}
                    </div>
                  )}
                  {review.recruiter_note && (
                    <p className="eval-recruiter-note">
                      <strong>Note:</strong> {review.recruiter_note}
                    </p>
                  )}
                </div>
              ) : (
                <p className="eval-empty-text" style={{ marginTop: "8px" }}>
                  No recruiter notes or overrides recorded for this response.
                </p>
              )}
            </div>
          </>
        )}
      </div>
    );
  };

  // ===========================================================================
  // LEVEL 1: Evaluation Jobs Directory
  // ===========================================================================
  if (level === 1) {
    return (
      <div className="eval-container">
        {/* Header */}
        <div className="eval-header">
          <div>
            <div className="eval-header-pretitle">
              Recruiter workspace / Evaluation
            </div>
            <h1 className="eval-header-title">Evaluation</h1>
            <p className="eval-header-subtitle">
              Review submitted interviews with question-level evidence and AI-assisted analysis.
            </p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            icon="refresh"
            isLoading={jobsLoading}
            loadingText="Refreshing…"
            onClick={fetchJobs}
            aria-label="Refresh evaluation directory"
          >
            Refresh
          </Button>
        </div>

        {/* Search & Filters */}
        <div className="eval-toolbar">
          <div className="eval-search-box">
            <Icon name="search" size={16} className="eval-search-icon" />
            <input
              type="text"
              placeholder="Search by job title…"
              value={jobSearch}
              onChange={(e) => setJobSearch(e.target.value)}
              className="eval-search-input"
              aria-label="Search evaluation jobs"
            />
            {jobSearch && (
              <button
                type="button"
                onClick={() => setJobSearch("")}
                className="eval-search-clear"
                aria-label="Clear search"
              >
                <Icon name="x" size={14} />
              </button>
            )}
          </div>

          <div className="eval-filter-pills">
            {JOB_STATUS_FILTERS.map((f) => (
              <button
                key={f.id}
                type="button"
                onClick={() => setJobStatusFilter(f.id)}
                className={`eval-filter-pill ${jobStatusFilter === f.id ? "active" : ""}`}
              >
                {f.label}
              </button>
            ))}
            {filteredJobs.length > 0 && (
              <span className="eval-result-count">
                {filteredJobs.length} {filteredJobs.length === 1 ? "job" : "jobs"}
              </span>
            )}
          </div>
        </div>

        {/* Error Alert */}
        {jobsError && (
          <InlineNotice variant="warning" title="Could not load evaluation jobs">
            {jobsError}
          </InlineNotice>
        )}

        {/* Directory Listing */}
        {jobsLoading ? (
          <div className="eval-loading-state">
            <span className="loading-spinner" aria-hidden="true" />
            <p>Loading evaluation directory…</p>
          </div>
        ) : filteredJobs.length === 0 ? (
          jobSearch || jobStatusFilter !== "ALL" ? (
            <EmptyState
              icon="search"
              title="No evaluation jobs match your search"
              description="Try adjusting your search term or status filter."
              secondaryAction={{ label: "Clear filters", onClick: () => { setJobSearch(""); setJobStatusFilter("ALL"); } }}
            />
          ) : (
            <EmptyState
              icon="clipboard-check"
              title="No submitted interviews yet"
              description="Released or in-progress interviews will appear here after candidate submission."
            />
          )
        ) : (
          <div className="eval-card-list">
            {filteredJobs.map((job) => (
              <div
                key={`${job.job_id}-rev-${job.job_revision}`}
                className="eval-job-card"
                role="article"
              >
                <div className="eval-job-main">
                  <div className="eval-job-tags">
                    <span className="eval-tag-revision">Revision {job.job_revision}</span>
                    {job.department && (
                      <span className="eval-tag-department">{job.department}</span>
                    )}
                  </div>
                  <h3 className="eval-job-title">{job.job_title}</h3>
                  <span className="eval-job-meta">
                    {job.last_submission_at
                      ? `Last submission: ${new Date(job.last_submission_at).toLocaleDateString()}`
                      : "No submissions recorded"}
                  </span>
                </div>

                <div className="eval-metrics-group">
                  <div className="eval-metric-box">
                    <span className="eval-metric-label">Released</span>
                    <span className="eval-metric-value">{job.total_releases_count}</span>
                  </div>
                  <div className="eval-metric-box">
                    <span className="eval-metric-label">Submitted</span>
                    <span className="eval-metric-value">{job.submitted_count}</span>
                  </div>
                  <div className="eval-metric-box ready">
                    <span className="eval-metric-label">Ready for review</span>
                    <span className="eval-metric-value">{job.ready_for_review_count}</span>
                  </div>
                  <div className="eval-metric-box finalized">
                    <span className="eval-metric-label">Finalized</span>
                    <span className="eval-metric-value">{job.finalized_count}</span>
                  </div>
                </div>

                <Button
                  variant="primary"
                  size="sm"
                  icon="chevron-right"
                  iconPosition="right"
                  onClick={() => selectJobForCandidates(job)}
                  aria-label={`View evaluations for ${job.job_title}`}
                >
                  View evaluations
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // ===========================================================================
  // LEVEL 2: Job Candidates List for Exact Revision
  // ===========================================================================
  if (level === 2 && selectedJob) {
    return (
      <div className="eval-container">
        {/* Breadcrumb */}
        <div className="eval-breadcrumbs">
          <div className="eval-breadcrumb-trail">
            <button
              type="button"
              onClick={() => setLevel(1)}
              className="eval-breadcrumb-link"
            >
              Evaluation
            </button>
            <span>/</span>
            <span className="eval-breadcrumb-current">
              {selectedJob.job_title} (Rev {selectedJob.job_revision})
            </span>
          </div>
          <button
            type="button"
            onClick={() => setLevel(1)}
            className="eval-back-btn"
          >
            <Icon name="arrow-left" size={14} />
            <span>Back</span>
          </button>
        </div>

        {/* Header */}
        <div className="eval-header">
          <div>
            <div className="eval-job-tags" style={{ marginBottom: "4px" }}>
              <span className="eval-tag-revision">Revision {selectedJob.job_revision}</span>
              {selectedJob.department && (
                <span className="eval-tag-department">{selectedJob.department}</span>
              )}
            </div>
            <h1 className="eval-header-title">{selectedJob.job_title}</h1>
            <p className="eval-header-subtitle">
              Candidate official interview sessions and separate resume match references.
            </p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            icon="refresh"
            isLoading={candidatesLoading}
            loadingText="Refreshing…"
            onClick={() => selectJobForCandidates(selectedJob)}
          >
            Refresh
          </Button>
        </div>

        {/* Filter Pills */}
        <div className="eval-toolbar" style={{ marginTop: "12px", marginBottom: "16px" }}>
          <div className="eval-filter-pills" style={{ marginBottom: 0 }}>
            {CANDIDATE_MODE_FILTERS.map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => setCandidateModeFilter(m.id)}
                className={`eval-filter-pill ${candidateModeFilter === m.id ? "active" : ""}`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <div className="eval-filter-pills" style={{ marginBottom: 0 }}>
            {CANDIDATE_STATUS_FILTERS.map((f) => (
              <button
                key={f.id}
                type="button"
                onClick={() => setCandidateFilter(f.id)}
                className={`eval-filter-pill ${candidateFilter === f.id ? "active" : ""}`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {/* Error Alert */}
        {candidatesError && (
          <InlineNotice variant="warning" title="Could not load candidates">
            {candidatesError}
          </InlineNotice>
        )}

        {/* Candidates List */}
        {candidatesLoading ? (
          <div className="eval-loading-state">
            <span className="loading-spinner" aria-hidden="true" />
            <p>Loading candidate sessions…</p>
          </div>
        ) : filteredCandidates.length === 0 ? (
          <EmptyState
            icon="users"
            title="No candidates in this category"
            description="No interview sessions match the current filter."
          />
        ) : (
          <div className="eval-card-list">
            {filteredCandidates.map((cand) => (
              <div key={cand.release_id} className="eval-candidate-card">
                <div className="eval-candidate-info">
                  <div className="eval-candidate-avatar-wrap">
                    <div className="eval-candidate-avatar">
                      {cand.candidate_name.charAt(0).toUpperCase()}
                    </div>
                    <span
                      className={`eval-submission-indicator-dot ${cand.submitted_at ? "submitted" : "pending"}`}
                      title={cand.submitted_at ? "Interview submitted" : "Interview awaiting submission"}
                      aria-label={cand.submitted_at ? "Submitted" : "Not yet submitted"}
                    />
                  </div>
                  <div className="eval-candidate-details">
                    <div className="eval-candidate-badges">
                      <span className={`eval-status-badge ${cand.interview_mode === "AUDIO" ? "eval-badge-mode-audio" : "eval-badge-mode-text"}`}>
                        {cand.interview_mode === "AUDIO" ? "Audio-based" : "Text-based"}
                      </span>
                      <span className="eval-status-badge eval-status-source">
                        {cand.application_source}
                      </span>
                      {cand.recruiter_evaluation_status && (
                        <span
                          className={`eval-status-badge ${cand.recruiter_evaluation_status === "Finalized"
                            ? "finalized"
                            : cand.recruiter_evaluation_status === "In review"
                              ? "in-review"
                              : cand.recruiter_evaluation_status === "Ready for review"
                                ? "ready"
                                : ""
                            }`}
                        >
                          {cand.recruiter_evaluation_status}
                        </span>
                      )}
                    </div>
                    <h3 className="eval-candidate-name">{cand.candidate_name}</h3>
                    <div className="eval-candidate-sub">
                      <span>
                        {cand.submitted_at
                          ? `Submitted: ${new Date(cand.submitted_at).toLocaleDateString()}`
                          : "Awaiting submission"}
                      </span>
                      <span className="eval-separator">•</span>
                      <span>Answers: {cand.answers_recorded_display}</span>
                      <span className="eval-separator">•</span>
                      <span>AI: {cand.ai_analysis_status}</span>
                    </div>
                  </div>
                </div>

                <div className="eval-candidate-channels">
                  {/* Resume Match Reference (Separate) */}
                  <div className="eval-channel-badge resume">
                    <span className="eval-channel-label">Resume match (ref)</span>
                    <span className="eval-channel-val">
                      {cand.resume_match_score !== null && cand.resume_match_score !== undefined
                        ? `${cand.resume_match_score}%`
                        : "0%"}
                      {cand.required_skills_coverage && (
                        <span className="eval-channel-sub"> ({cand.required_skills_coverage} skills)</span>
                      )}
                    </span>
                  </div>

                  {/* Evaluate Action */}
                  {cand.session_id ? (
                    <Button
                      variant="primary"
                      size="sm"
                      icon="chevron-right"
                      iconPosition="right"
                      onClick={() => selectCandidateForDetail(cand.session_id!)}
                    >
                      {cand.recruiter_evaluation_status === "Finalized"
                        ? "View evaluation"
                        : cand.recruiter_evaluation_status === "In review"
                          ? "Continue evaluation"
                          : "Evaluate interview"}
                    </Button>
                  ) : (
                    <span className="eval-awaiting-label">
                      Awaiting candidate submission
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // ===========================================================================
  // LEVEL 3: Candidate Interview Evaluation Workspace
  // ===========================================================================
  if (level === 3 && detailLoading) {
    return (
      <div className="eval-container">
        <div className="eval-loading-state">
          <span className="loading-spinner" aria-hidden="true" />
          <p>Loading evaluation workspace…</p>
        </div>
      </div>
    );
  }

  if (level === 3 && detailError) {
    return (
      <div className="eval-container">
        <InlineNotice variant="warning" title="Failed to load evaluation">
          {detailError}
        </InlineNotice>
        <Button variant="secondary" icon="arrow-left" onClick={() => setLevel(2)}>
          Back to candidates
        </Button>
      </div>
    );
  }

  if (level === 3 && detail) {
    const isFinalized = detail.status === "FINALIZED";
    const isReadyToFinalize = Boolean(
      detail.overview.is_ready_to_finalize ?? detail.overview.is_aggregate_complete ?? true
    );

    // AI Evaluation status flags
    // Conversational questions require LLM execution. MCQs are graded deterministically.
    const hasAttemptedAi = detail.questions.some(
      (q) => {
        const ai = q.ai_provisional_analysis || q.ai_provisional;
        if (!ai) return false;
        if (q.question_format === "CONVERSATIONAL") {
          return ai.analysis_status === "READY" || ai.analysis_status === "UNAVAILABLE" || ai.provisional_rating != null;
        }
        return false;
      }
    ) || (detail.overview.questions_reviewed > 0) || isFinalized;

    const hasCompletedAi = (
      detail.overview.aggregate_score != null ||
      detail.overview.status === "COMPLETE" ||
      isFinalized ||
      (
        hasAttemptedAi &&
        detail.questions.filter((q) => !q.is_supplemental).every((q) => {
          const ai = q.ai_provisional_analysis || q.ai_provisional;
          return (ai && ai.provisional_rating != null) || (q.recruiter_review && q.recruiter_review.reviewed_rating != null);
        })
      )
    );

    // Compute the radar title based on review state
    const hasReviewedDimensions =
      detail.questions.some((q) => q.recruiter_review && q.recruiter_review.review_state !== "UNREVIEWED");
    const radarTitle = isFinalized
      ? "Reviewed response profile"
      : hasReviewedDimensions
        ? "AI provisional response profile (review in progress)"
        : "AI provisional response profile";

    const hasMatchRef = Boolean(
      detail.resume_match_reference &&
      (detail.resume_match_reference.has_match_reference ||
        detail.resume_match_reference.overall_score !== null ||
        detail.resume_match_reference.overall_compatibility_score !== null)
    );

    const matchOverallScore =
      detail.resume_match_reference?.overall_score ??
      detail.resume_match_reference?.overall_compatibility_score;

    const matchedSkillsCount =
      detail.resume_match_reference?.matched_required_count ??
      detail.resume_match_reference?.required_skills_matched_count ?? 0;

    const totalSkillsCount =
      detail.resume_match_reference?.total_required_count ??
      detail.resume_match_reference?.total_required_skills_count ?? 0;

    const categoryBreakdown =
      detail.resume_match_reference?.category_breakdown ??
      detail.resume_match_reference?.category_scores ?? {};

    return (
      <div className="eval-container">
        {/* Breadcrumb */}
        <div className="eval-breadcrumbs">
          <div className="eval-breadcrumb-trail">
            <button type="button" onClick={() => setLevel(1)} className="eval-breadcrumb-link">
              Evaluation
            </button>
            <span>/</span>
            <button type="button" onClick={() => setLevel(2)} className="eval-breadcrumb-link">
              {detail.job_title} (Rev {detail.job_revision})
            </button>
            <span>/</span>
            <span className="eval-breadcrumb-current">{detail.candidate_name}</span>
          </div>
          <button type="button" onClick={() => setLevel(2)} className="eval-back-btn">
            <Icon name="arrow-left" size={14} />
            <span>Back to candidates</span>
          </button>
        </div>

        {/* Candidate Header Card */}
        <div className="eval-evidence-card">
          <div className="eval-detail-header-row">
            <div className="eval-detail-header-left">
              <div className="eval-candidate-badges">
                <span className={`eval-status-badge ${detail.interview_mode === "AUDIO" ? "eval-badge-mode-audio" : "eval-badge-mode-text"}`}>
                  {detail.interview_mode === "AUDIO" ? "Audio-based" : "Text-based"}
                </span>
                <span className="eval-status-badge ready">Official interview evaluation</span>
                <span className={`eval-status-badge ${isFinalized ? "finalized" : "in-review"}`}>
                  {detail.status === "IN_REVIEW" ? "In review" : detail.status === "FINALIZED" ? "Finalized" : detail.status}
                </span>
                {detail.candidate_feedback_status === "PUBLISHED" && (
                  <span className="eval-status-badge finalized">Feedback published</span>
                )}
              </div>
              <h1 className="eval-header-title">{detail.candidate_name}</h1>
              <p className="eval-header-subtitle">
                {detail.job_title} · Revision {detail.job_revision} · Question set v{detail.question_set_version}
              </p>
            </div>

            <div className="eval-detail-header-actions">
              <Button
                variant="secondary"
                size="sm"
                icon="download"
                onClick={handleDownloadJson}
                title="Download candidate evaluation and analysis as JSON"
              >
                Download JSON
              </Button>
              {!isFinalized ? (
                <>
                  {hasAttemptedAi && detail.questions.some((q) => (q.ai_provisional_analysis || q.ai_provisional)?.analysis_status === "UNAVAILABLE" || (q.ai_provisional_analysis || q.ai_provisional)?.provisional_rating == null) && (
                    <Button
                      variant="secondary"
                      size="sm"
                      icon="refresh"
                      isLoading={analyzingAi}
                      loadingText="Re-running failed…"
                      onClick={handleRerunFailedAiAnalysis}
                      title="Re-run AI analysis only for failed or unanalyzed questions"
                    >
                      Retry AI analysis
                    </Button>
                  )}
                  <Button
                    variant="secondary"
                    size="sm"
                    icon="sparkles"
                    isLoading={analyzingAi}
                    loadingText="Analyzing responses…"
                    onClick={handleRunAiAnalysis}
                    title="Run local LLM answer assessment engine on candidate responses"
                  >
                    Run AI analysis
                  </Button>
                  <Button
                    variant="primary"
                    size="sm"
                    icon="check-circle"
                    disabled={!isReadyToFinalize}
                    onClick={() => setShowFinalizeModal(true)}
                    title={
                      isReadyToFinalize
                        ? "Finalize official evaluation"
                        : "Complete AI evaluation or optional overrides for all core questions to finalize"
                    }
                  >
                    Finalize evaluation
                  </Button>
                </>
              ) : (
                <>
                  <span className="eval-status-badge finalized">
                    <Icon name="lock" size={14} />
                    Evaluation locked
                  </span>
                  {detail.candidate_feedback_status !== "PUBLISHED" ? (
                    <Button
                      variant="primary"
                      size="sm"
                      icon="send"
                      isLoading={publishing}
                      onClick={handlePublishFeedback}
                    >
                      Publish candidate feedback
                    </Button>
                  ) : (
                    <span className="eval-status-badge finalized">
                      Candidate feedback published
                    </span>
                  )}
                </>
              )}
            </div>
          </div>
        </div>

        {aiAnalysisError && (
          <InlineNotice variant="warning" title="AI Analysis Notice">
            {aiAnalysisError}
          </InlineNotice>
        )}

        {publishSuccess && (
          <InlineNotice variant="success">{publishSuccess}</InlineNotice>
        )}

        {/* ===== SUMMARY SECTION ===== */}
        <div className="eval-detail-grid">
          {/* Channel 1: Resume Match Reference (Read-Only) */}
          <div className="eval-evidence-card">
            <div className="eval-evidence-header">
              <Icon name="file-text" size={16} />
              <div>
                <h3 className="eval-evidence-title">Resume match</h3>
                <span className="eval-evidence-subtitle">Read-only deterministic baseline</span>
              </div>
            </div>

            {hasMatchRef ? (
              <div className="eval-resume-match-content">
                <div className="eval-resume-score-box">
                  <span className="eval-resume-score-label">Overall compatibility</span>
                  <span className="eval-resume-score-value">
                    {matchOverallScore !== null && matchOverallScore !== undefined
                      ? `${matchOverallScore}%`
                      : "0%"}
                  </span>
                </div>

                <div className="eval-resume-details">
                  <div className="eval-resume-detail-row">
                    <span>Required skills matched:</span>
                    <strong>
                      {matchedSkillsCount} of {totalSkillsCount}
                    </strong>
                  </div>
                  <div className="eval-resume-detail-row">
                    <span>Profile snapshot version:</span>
                    <span className="eval-mono">v{detail.resume_match_reference?.profile_version ?? 1}</span>
                  </div>
                </div>

                {Object.keys(categoryBreakdown).length > 0 && (
                  <div className="eval-resume-categories">
                    <span className="eval-resume-categories-title">Category breakdown</span>
                    {Object.entries(categoryBreakdown).map(([cat, score]) => (
                      <div key={cat} className="eval-resume-detail-row">
                        <span>{cat}</span>
                        <strong>
                          {typeof score === "number"
                            ? `${score <= 1 ? Math.round(score * 100) : Math.round(score)}%`
                            : String(score)}
                        </strong>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <p className="eval-empty-text">
                No frozen match result attached to this interview release.
              </p>
            )}
          </div>

          {/* Channel 2: Interview Response Assessment & Overview */}
          <div className="eval-evidence-card">
            <div className="eval-evidence-header">
              <Icon name="message-square" size={16} />
              <div>
                <h3 className="eval-evidence-title">Interview response assessment</h3>
                <span className="eval-evidence-subtitle">AI-generated interview evaluation</span>
              </div>
              <div className="eval-progress-indicator">
                <span className="eval-progress-text">
                  {hasAttemptedAi
                    ? detail.questions.filter((q) => (q.ai_provisional_analysis || q.ai_provisional)?.provisional_rating != null || q.recruiter_review?.reviewed_rating != null).length
                    : 0} of {detail.overview.total_questions} AI analyses complete
                </span>
                <div className="eval-progress-bar">
                  <div
                    className="eval-progress-fill"
                    style={{
                      width: `${hasAttemptedAi
                        ? (detail.questions.filter((q) => (q.ai_provisional_analysis || q.ai_provisional)?.provisional_rating != null || q.recruiter_review?.reviewed_rating != null).length / (detail.overview.total_questions || 1)) * 100
                        : 0}%`,
                    }}
                  />
                </div>
              </div>
            </div>

            {/* Overview Metrics */}
            {hasCompletedAi ? (
              <>
                <div className="eval-overview-grid">
                  <div className="eval-overview-item">
                    <span className="eval-metric-label">Deterministic core aggregate</span>
                    <span className="eval-overview-score">
                      {detail.overview.aggregate_score !== null &&
                        detail.overview.aggregate_score !== undefined
                        ? `${detail.overview.aggregate_score.toFixed(1)} / 5.0`
                        : (() => {
                          const ratedCore = detail.questions
                            .filter((q) => !q.is_supplemental)
                            .map((q) => q.recruiter_review?.reviewed_rating ?? (q.ai_provisional_analysis || q.ai_provisional)?.provisional_rating)
                            .filter((r): r is number => typeof r === "number" && !isNaN(r));
                          if (ratedCore.length > 0) {
                            const avg = (ratedCore.reduce((a, b) => a + b, 0) / ratedCore.length).toFixed(1);
                            return `${avg} / 5.0`;
                          }
                          return "AI evaluation pending";
                        })()}
                    </span>
                    <span className="eval-overview-detail">
                      Includes {detail.overview.core_questions_count} core questions (excludes{" "}
                      {detail.overview.supplemental_questions_count} supplemental).
                    </span>
                  </div>

                  {/* Key Strengths */}
                  {(detail.overview.key_strengths?.length ?? 0) > 0 && (
                    <div className="eval-overview-item eval-overview-strengths">
                      <span className="eval-metric-label">Key strengths</span>
                      <span className="eval-overview-detail">
                        {detail.overview.key_strengths.join(", ")}
                      </span>
                    </div>
                  )}

                  {/* Priority Review Areas */}
                  {(detail.overview.priority_review_areas?.length ?? 0) > 0 && (
                    <div className="eval-overview-item eval-overview-growth">
                      <span className="eval-metric-label">Priority growth areas</span>
                      <span className="eval-overview-detail">
                        {detail.overview.priority_review_areas.join(", ")}
                      </span>
                    </div>
                  )}
                </div>

                {/* Dimension Radar Chart */}
                {detail.overview.dimension_averages &&
                  Object.keys(detail.overview.dimension_averages).length > 0 && (
                    <InterviewDimensionRadar
                      dimensions={detail.overview.dimension_averages}
                      title={radarTitle}
                    />
                  )}
              </>
            ) : (
              <div style={{ padding: "1.5rem", background: "#f8fafc", borderRadius: "8px", border: "1px dashed #cbd5e1", marginTop: "1rem" }}>
                <p style={{ margin: 0, fontSize: "14px", color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: "8px" }}>
                  <Icon name="sparkles" size={16} />
                  AI evaluation has not been run for this candidate yet. Click "Run AI analysis" above to generate the interview assessment.
                </p>
              </div>
            )}
          </div>
        </div>

        {/* ===== RESPONSES SECTION ===== */}
        <div className="eval-section">
          {detail.interview_mode === "AUDIO" && (
            <div className="eval-audio-transcript-notice">
              <Icon name="mic" size={16} />
              <span>
                <strong>Audio interview:</strong> Evaluation is conducted strictly on candidate-confirmed transcripts against the question rubric. Raw audio is not retained, and vocal delivery traits (pitch, tone, accent, pronunciation, speed) are not analyzed.
              </span>
            </div>
          )}

          <div className="eval-section-header">
            <h2 className="eval-section-title">
              Submitted responses ({detail.questions.length})
            </h2>
            <span className="eval-section-subtitle">
              {detail.interview_mode === "AUDIO"
                ? "Conversational audio questions (evaluated on confirmed transcript)"
                : "Order: Conversational core → MCQs"}
            </span>
          </div>

          {hasAttemptedAi ? (
            <div className="eval-card-list">
              {detail.questions.map((q) => renderQuestionCard(q, isFinalized))}
            </div>
          ) : (
            <div style={{ padding: "2rem", background: "#f8fafc", borderRadius: "10px", border: "1px dashed #cbd5e1", textAlign: "center", margin: "1rem 0" }}>
              <div style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: "40px", height: "40px", borderRadius: "50%", background: "#f1f5f9", marginBottom: "0.75rem", color: "var(--text-secondary)" }}>
                <Icon name="sparkles" size={20} />
              </div>
              <h3 style={{ margin: "0 0 0.5rem 0", fontSize: "16px", fontWeight: 600, color: "var(--text-primary)" }}>
                Evaluation pending
              </h3>
              <p style={{ margin: "0 auto", maxWidth: "480px", fontSize: "14px", color: "var(--text-secondary)", lineHeight: 1.5 }}>
                Candidate submitted responses are hidden until evaluation is processed. Click <strong>"Run AI analysis"</strong> in the top header to run the rubric assessment.
              </p>
            </div>
          )}
        </div>

        {/* ===== ACTIVITY SECTION ===== */}
        {detail.activity_log && detail.activity_log.length > 0 && (
          <div className="eval-section">
            <h2 className="eval-section-title">Activity log</h2>
            <div className="eval-activity-list">
              {detail.activity_log.map((evt, i) => (
                <div key={i} className="eval-activity-item">
                  <span className="eval-activity-dot" />
                  <div className="eval-activity-content">
                    <span className="eval-activity-summary">{evt.summary}</span>
                    <span className="eval-activity-meta">
                      {evt.actor_name || "System"} · {new Date(evt.timestamp).toLocaleString()}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Finalization Modal */}
        <Dialog
          isOpen={showFinalizeModal}
          onClose={() => setShowFinalizeModal(false)}
          title="Finalize official evaluation"
          footer={
            <div className="eval-modal-footer">
              <Button variant="secondary" size="sm" onClick={() => setShowFinalizeModal(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                icon="lock"
                isLoading={finalizing}
                onClick={handleFinalize}
              >
                Confirm & lock evaluation
              </Button>
            </div>
          }
          maxWidth="540px"
        >
          {finalizeError && (
            <InlineNotice variant="warning">{finalizeError}</InlineNotice>
          )}

          <div className="eval-finalize-summary">
            <div className="eval-resume-detail-row">
              <span>Candidate:</span>
              <strong>{detail.candidate_name}</strong>
            </div>
            <div className="eval-resume-detail-row">
              <span>Job requisition:</span>
              <strong>{detail.job_title} (Rev {detail.job_revision})</strong>
            </div>
            <div className="eval-resume-detail-row">
              <span>Core questions evaluated:</span>
              <strong>{detail.overview.core_questions_count} of {detail.overview.core_questions_count}</strong>
            </div>
            <div className="eval-resume-detail-row">
              <span>Deterministic final aggregate:</span>
              <strong>{detail.overview.aggregate_score?.toFixed(1) || "5.0"} / 5.0</strong>
            </div>
            <div className="eval-resume-detail-row">
              <span>Excluded supplemental:</span>
              <span>{detail.overview.supplemental_questions_count} question(s)</span>
            </div>
          </div>

          <InlineNotice variant="warning" title="Important notice">
            Finalizing will permanently lock this evaluation from further edits.
            Finalization does NOT automatically alter candidate application status
            or publish candidate feedback.
          </InlineNotice>
        </Dialog>
      </div>
    );
  }

  return null;
};
