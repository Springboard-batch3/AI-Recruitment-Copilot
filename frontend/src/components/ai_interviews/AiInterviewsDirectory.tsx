import React, { useEffect, useState } from "react";
import { apiFetch } from "../../api/client";
import { Icon } from "../Icon";
import {
  AiQuestionSetDirectory,
  AiQuestionSetDetail,
  EligibleJobItem,
  EligibleJobsResponse,
  InterviewMode,
} from "../../types/ai_question_set";
import "./ai-interviews.css";

interface AiInterviewsDirectoryProps {
  onSelectTemplate: (templateId: string) => void;
  onNavigateToJobs?: () => void;
  initialSelectedJobId?: string | null;
  initialMode?: InterviewMode | null;
}

export function AiInterviewsDirectory({
  onSelectTemplate,
  onNavigateToJobs,
  initialSelectedJobId = null,
  initialMode = "TEXT",
}: AiInterviewsDirectoryProps) {
  const [selectedMode, setSelectedMode] = useState<InterviewMode>(initialMode || "TEXT");
  const [eligibleJobs, setEligibleJobs] = useState<EligibleJobItem[]>([]);
  const [recentDirectory, setRecentDirectory] = useState<AiQuestionSetDirectory | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(initialSelectedJobId);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [isLoadingJobs, setIsLoadingJobs] = useState<boolean>(false);
  const [isGenerating, setIsGenerating] = useState<boolean>(false);
  const [generationError, setGenerationError] = useState<string | null>(null);
  const [isLocalAiError, setIsLocalAiError] = useState<boolean>(false);

  const loadData = async (mode: InterviewMode) => {
    setIsLoadingJobs(true);
    setGenerationError(null);
    try {
      const [jobsRes, recentRes] = await Promise.allSettled([
        apiFetch<EligibleJobsResponse>(`/api/v1/ai-interviews/eligible-jobs?mode=${mode}`),
        apiFetch<AiQuestionSetDirectory>(`/api/v1/ai-interviews?mode=${mode}`),
      ]);

      if (jobsRes.status === "fulfilled" && jobsRes.value?.items) {
        setEligibleJobs(jobsRes.value.items);
      } else if (jobsRes.status === "rejected") {
        try {
          const altRes = await apiFetch<EligibleJobsResponse>(`/api/v1/ai-question-sets/eligible-jobs?mode=${mode}`);
          setEligibleJobs(altRes.items || []);
        } catch {
          setEligibleJobs([]);
        }
      }

      if (recentRes.status === "fulfilled" && recentRes.value) {
        setRecentDirectory(recentRes.value);
      }
    } catch {
      setEligibleJobs([]);
    } finally {
      setIsLoadingJobs(false);
    }
  };

  useEffect(() => {
    loadData(selectedMode);
  }, [selectedMode]);

  // Exclude jobs that already have an AI question set in the selected mode
  const existingJobIds = new Set(
    recentDirectory?.sets
      ?.filter((s) => (s.generated_question_count ?? 0) > 0)
      .map((s) => s.job_id) || []
  );
  const selectableJobs = eligibleJobs.filter(
    (job) => !existingJobIds.has(job.job_id) && !job.current_ai_set_id
  );

  // Clear selection if it becomes ineligible
  useEffect(() => {
    if (selectedJobId && !selectableJobs.some((j) => j.job_id === selectedJobId)) {
      setSelectedJobId(null);
    }
  }, [selectableJobs, selectedJobId]);

  const selectedJob = selectableJobs.find((j) => j.job_id === selectedJobId);

  const handleAction = async () => {
    if (!selectedJob || isGenerating || !selectedMode) return;

    setIsGenerating(true);
    setGenerationError(null);
    setIsLocalAiError(false);

    try {
      const res = await apiFetch<AiQuestionSetDetail>("/api/v1/ai-interviews/generate", {
        method: "POST",
        body: JSON.stringify({
          job_id: selectedJob.job_id,
          interview_mode: selectedMode,
        }),
      });

      if (res && (res.ai_set_id || res.template_id)) {
        loadData(selectedMode);
        onSelectTemplate(res.ai_set_id || res.template_id || "");
      }
    } catch (err: any) {
      const msg = err?.message || "";
      if (
        msg.toLowerCase().includes("local ai") ||
        msg.toLowerCase().includes("model") ||
        msg.toLowerCase().includes("lm studio") ||
        msg.toLowerCase().includes("unavailable") ||
        msg.toLowerCase().includes("503")
      ) {
        setIsLocalAiError(true);
        setGenerationError("The local AI service is currently unavailable.");
      } else if (
        msg.toLowerCase().includes("requirements") ||
        msg.toLowerCase().includes("not ready") ||
        msg.toLowerCase().includes("400") ||
        msg.toLowerCase().includes("404")
      ) {
        setGenerationError("This job needs approved requirements before questions can be generated.");
      } else {
        setGenerationError("The local AI service is currently unavailable.");
        setIsLocalAiError(true);
      }
    } finally {
      setIsGenerating(false);
    }
  };

  const filteredJobs = selectableJobs.filter((job) =>
    job.job_title.toLowerCase().includes(searchQuery.toLowerCase().trim())
  );

  const formatStatus = (status: string, isStale?: boolean, isLegacy?: boolean) => {
    if (isLegacy) return "Legacy set";
    if (isStale || status === "STALE") return "Stale";
    if (status === "APPROVED") return "Approved";
    if (status === "VALIDATED") return "Ready for Review";
    return "Draft";
  };

  const formatLifecycleAction = (status: string, isStale?: boolean, isLegacy?: boolean) => {
    if (isLegacy) return "Review / Upgrade";
    if (isStale || status === "STALE") return "Update questions";
    if (status === "APPROVED") return "View questions";
    if (status === "VALIDATED") return "Review and approve";
    return "Continue review";
  };

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return "Recently";
    try {
      return new Date(dateStr).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
    } catch {
      return "Recently";
    }
  };

  const actionButtonLabel = () => {
    if (isGenerating) return "Generating questions…";
    return selectedMode === "AUDIO" ? "Generate audio questions" : "Generate AI questions";
  };

  const hasRecentSets = Boolean(recentDirectory?.sets && recentDirectory.sets.length > 0);

  // Render Directory View for the Selected Mode
  return (
    <div className="aiq-page">
      {/* Page Header with Mode Selector Tabs */}
      <header className="aiq-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "16px" }}>
        <div>
          <h1 className="aiq-title">AI Interviews</h1>
          <p className="aiq-subtitle">
            Choose a job, generate interview question sets, and manage candidate interview practice.
          </p>
        </div>

        {/* Mode Switcher Tabs */}
        <div
          role="tablist"
          aria-label="Interview Mode"
          style={{
            display: "inline-flex",
            padding: "3px",
            backgroundColor: "var(--bg-surface-subtle, #f1f5f9)",
            borderRadius: "8px",
            border: "1px solid var(--border-subtle, #e2e8f0)",
          }}
        >
          <button
            type="button"
            role="tab"
            aria-selected={selectedMode === "TEXT"}
            data-testid="mode-tab-text"
            className="btn btn-sm"
            onClick={() => {
              if (selectedMode !== "TEXT") {
                setSelectedMode("TEXT");
                setSelectedJobId(null);
              }
            }}
            style={{
              padding: "6px 14px",
              fontSize: "13px",
              fontWeight: 600,
              borderRadius: "6px",
              border: "none",
              backgroundColor: selectedMode === "TEXT" ? "#ffffff" : "transparent",
              color: selectedMode === "TEXT" ? "var(--text-primary, #0f172a)" : "var(--text-secondary, #64748b)",
              boxShadow: selectedMode === "TEXT" ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
              cursor: "pointer",
              transition: "all 0.15s ease",
            }}
          >
            Text-based
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={selectedMode === "AUDIO"}
            data-testid="mode-tab-audio"
            className="btn btn-sm"
            onClick={() => {
              if (selectedMode !== "AUDIO") {
                setSelectedMode("AUDIO");
                setSelectedJobId(null);
              }
            }}
            style={{
              padding: "6px 14px",
              fontSize: "13px",
              fontWeight: 600,
              borderRadius: "6px",
              border: "none",
              backgroundColor: selectedMode === "AUDIO" ? "#ffffff" : "transparent",
              color: selectedMode === "AUDIO" ? "var(--accent-primary)" : "var(--text-secondary, #64748b)",
              boxShadow: selectedMode === "AUDIO" ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
              cursor: "pointer",
              transition: "all 0.15s ease",
            }}
          >
            Audio-based
          </button>
        </div>
      </header>

      {/* Screen Reader Progress Announcement */}
      <div className="sr-only" aria-live="polite">
        {isGenerating ? "Generating questions for selected job..." : ""}
      </div>

      {/* Global Empty State when no jobs and no existing sets exist for this mode */}
      {!isLoadingJobs && selectableJobs.length === 0 && !hasRecentSets && (
        <div className="aiq-panel" style={{ textAlign: "center", padding: "48px 24px" }}>
          <h2 className="aiq-panel-title" style={{ marginBottom: "8px" }}>
            No jobs are ready yet
          </h2>
          <p className="aiq-panel-desc" style={{ marginBottom: "20px" }}>
            {selectedMode === "TEXT"
              ? "Complete a job and add approved requirements before generating text interview questions."
              : "Complete a job and add approved requirements before generating audio interview questions."}
          </p>
          {onNavigateToJobs && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={onNavigateToJobs}
            >
              Go to Jobs
            </button>
          )}
        </div>
      )}

      {/* Loading State */}
      {isLoadingJobs && (
        <div className="aiq-panel" style={{ textAlign: "center", padding: "48px 24px" }}>
          <div className="loading-spinner" style={{ margin: "0 auto 12px auto" }} />
          <p className="aiq-panel-desc">Loading available jobs...</p>
        </div>
      )}

      {/* Compact Notice when all ready jobs already have question sets in this mode */}
      {!isLoadingJobs && selectableJobs.length === 0 && hasRecentSets && (
        <div
          className="aiq-panel"
          style={{
            padding: "20px 24px",
            marginBottom: "32px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "16px",
            flexWrap: "wrap",
          }}
        >
          <div>
            <h2 className="aiq-panel-title" style={{ fontSize: "17px", marginBottom: "4px" }}>
              All ready jobs have {selectedMode === "TEXT" ? "text" : "audio"} question sets
            </h2>
            <p className="aiq-panel-desc" style={{ fontSize: "14px", margin: 0 }}>
              To generate questions for another role, create a job requisition and approve its requirements.
            </p>
          </div>
          {onNavigateToJobs && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={onNavigateToJobs}
            >
              Go to Jobs
            </button>
          )}
        </div>
      )}

      {/* Two-Column Creation Workspace */}
      {!isLoadingJobs && selectableJobs.length > 0 && (
        <div className="aiq-create-layout">
          {/* Left Column: Job Selection List */}
          <section className="aiq-job-selection-panel aiq-panel" aria-labelledby="choose-job-heading">
            <div className="aiq-panel-header">
              <div className="aiq-panel-title-row">
                <h2 id="choose-job-heading" className="aiq-panel-title">
                  Choose a job
                </h2>
                <span className="aiq-panel-count">
                  {filteredJobs.length} {filteredJobs.length === 1 ? "job ready" : "jobs ready"}
                </span>
              </div>
              <p className="aiq-panel-desc">
                Select a job with approved requirements to generate grounded interview questions.
              </p>
            </div>

            {selectableJobs.length > 5 && (
              <div className="aiq-search-box">
                <label htmlFor="aiq-job-search" className="aiq-search-label">
                  Filter jobs
                </label>
                <input
                  id="aiq-job-search"
                  type="search"
                  className="aiq-search-input"
                  placeholder="Search by job title…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                />
              </div>
            )}

            <fieldset className="aiq-job-fieldset">
              <legend className="sr-only">Available jobs for AI interview generation</legend>
              <div className="aiq-job-list" role="radiogroup" aria-label="Available jobs">
                {filteredJobs.length === 0 ? (
                  <div className="aiq-no-results">No jobs match your search.</div>
                ) : (
                  filteredJobs.map((job) => {
                    const isSelected = selectedJobId === job.job_id;
                    return (
                      <label
                        key={job.job_id}
                        htmlFor={`aiq-job-${job.job_id}`}
                        className={`aiq-job-row ${isSelected ? "aiq-selected" : ""}`}
                        onClick={() => {
                          setSelectedJobId(job.job_id);
                          setGenerationError(null);
                        }}
                      >
                        <div className="aiq-job-left">
                          <input
                            type="radio"
                            id={`aiq-job-${job.job_id}`}
                            name="selected_job"
                            value={job.job_id}
                            checked={isSelected}
                            onChange={() => {
                              setSelectedJobId(job.job_id);
                              setGenerationError(null);
                            }}
                            className="aiq-radio-input"
                          />
                          <div className="aiq-job-title-group">
                            <span className="aiq-job-name">{job.job_title}</span>
                            <span
                              className={`aiq-candidate-release-badge ${
                                (job.applied_candidate_count ?? 0) === 0
                                  ? "none"
                                  : (job.released_candidate_count ?? 0) === (job.applied_candidate_count ?? 0)
                                  ? "all"
                                  : (job.released_candidate_count ?? 0) > 0
                                  ? "partial"
                                  : "unreleased"
                              }`}
                              title={`${job.released_candidate_count ?? 0} of ${job.applied_candidate_count ?? 0} applied candidates received this ${selectedMode.toLowerCase()} interview`}
                            >
                              {(job.applied_candidate_count ?? 0) > 0
                                ? `${job.released_candidate_count ?? 0}/${job.applied_candidate_count} received`
                                : "0 applied"}
                            </span>
                          </div>
                        </div>
                        <span className="aiq-job-req-count">
                          {job.requirement_count}{" "}
                          {job.requirement_count === 1 ? "requirement" : "requirements"}
                        </span>
                      </label>
                    );
                  })
                )}
              </div>
            </fieldset>
          </section>

          {/* Right Column: Generation Panel */}
          <aside className="aiq-generation-panel aiq-panel" aria-labelledby="generate-panel-heading">
            <div className="aiq-panel-header">
              <h2 id="generate-panel-heading" className="aiq-panel-title">
                {selectedMode === "AUDIO" ? "Generate audio questions" : "Generate AI questions"}
              </h2>
              <p className="aiq-panel-desc">
                {selectedMode === "AUDIO"
                  ? "Approved requirements will generate spoken conversational questions."
                  : "The approved requirements will guide the local AI."}
              </p>
            </div>

            {/* Selection Summary or Placeholder */}
            {selectedJob ? (
              <div className="aiq-selection-summary">
                <div className="aiq-selection-label">Selected job</div>
                <div className="aiq-selection-job-title">{selectedJob.job_title}</div>
                <div className="aiq-selection-reqs">
                  {selectedJob.requirement_count}{" "}
                  {selectedJob.requirement_count === 1 ? "requirement" : "requirements"}
                </div>
              </div>
            ) : (
              <div className="aiq-unselected-placeholder">
                Select a job on the left to start generating interview questions.
              </div>
            )}

            {/* Error Alert */}
            {generationError && (
              <div className="feedback-box error" role="alert" style={{ margin: "0" }}>
                <div style={{ display: "flex", alignItems: "flex-start", gap: "8px", flex: 1 }}>
                  <Icon name="alert-triangle" size={16} />
                  <span>{generationError}</span>
                </div>
                {isLocalAiError && (
                  <div style={{ display: "flex", gap: "6px", marginLeft: "auto" }}>
                    <button
                      type="button"
                      className="btn btn-secondary btn-xs"
                      onClick={handleAction}
                      disabled={isGenerating}
                    >
                      Try again
                    </button>
                    <button
                      type="button"
                      className="btn btn-secondary btn-xs"
                      onClick={() => {
                        setGenerationError(null);
                        setIsLocalAiError(false);
                      }}
                      disabled={isGenerating}
                    >
                      Cancel
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Action Section */}
            <div className="aiq-action-container">
              <button
                type="button"
                className="aiq-generate-btn"
                disabled={!selectedJobId || isGenerating}
                onClick={handleAction}
              >
                {isGenerating ? (
                  <>
                    <span className="loading-spinner" style={{ width: "16px", height: "16px" }} />
                    <span>Generating questions…</span>
                  </>
                ) : (
                  <span>{actionButtonLabel()}</span>
                )}
              </button>

              <p className="aiq-reassurance-text">
                You’ll review every question before approval.
              </p>
            </div>
          </aside>
        </div>
      )}

      {/* Recent Sets: "Continue Reviewing" Section */}
      {!isLoadingJobs && hasRecentSets && recentDirectory && (
        <section className="aiq-recent-section" aria-labelledby="continue-reviewing-heading">
          <h2 id="continue-reviewing-heading" className="aiq-recent-heading">
            Continue reviewing
          </h2>

          <div className="aiq-recent-list">
            {recentDirectory.sets.map((set) => {
              const isLegacy = set.is_legacy;
              const status = formatStatus(set.lifecycle_status, set.is_source_stale, isLegacy);
              const actionLabel = formatLifecycleAction(set.lifecycle_status, set.is_source_stale, isLegacy);
              const badgeClass =
                isLegacy
                  ? "legacy"
                  : status === "Approved"
                    ? "approved"
                    : status === "Ready for Review"
                      ? "validated"
                      : status === "Stale"
                        ? "stale"
                        : "draft";

              return (
                <div key={set.ai_set_id} className="aiq-recent-row">
                  <div className="aiq-recent-info">
                    <div className="aiq-recent-title-row">
                      <span className="aiq-recent-job-title">{set.job_title}</span>
                      <span
                        className={`aiq-candidate-release-badge ${
                          (set.applied_candidate_count ?? 0) === 0
                            ? "none"
                            : (set.released_candidate_count ?? 0) === (set.applied_candidate_count ?? 0)
                            ? "all"
                            : (set.released_candidate_count ?? 0) > 0
                            ? "partial"
                            : "unreleased"
                        }`}
                        title={`${set.released_candidate_count ?? 0} of ${set.applied_candidate_count ?? 0} applied candidates received this ${selectedMode.toLowerCase()} interview`}
                      >
                        {(set.applied_candidate_count ?? 0) > 0
                          ? `${set.released_candidate_count ?? 0}/${set.applied_candidate_count} received`
                          : "0 applied"}
                      </span>
                    </div>
                    <div className="aiq-recent-meta">
                      <span>
                        {set.generated_question_count}{" "}
                        {set.generated_question_count === 1 ? "question" : "questions"}
                      </span>
                      <span className="aiq-dot">·</span>
                      <span className={`aiq-status-badge ${badgeClass}`}>{status}</span>
                      <span className="aiq-dot">·</span>
                      <span>Updated {formatDate(set.updated_at)}</span>
                    </div>
                  </div>

                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onSelectTemplate(set.ai_set_id || set.template_id || "")}
                    aria-label={`${actionLabel} for ${set.job_title}`}
                  >
                    {actionLabel}
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
