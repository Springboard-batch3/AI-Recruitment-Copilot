import React, { useState, useEffect } from "react";
import {
  fetchJobApplicantDetail,
  updateApplicantReviewState,
  releaseApplicantInterview,
  RecruiterApplicationDetail,
  RecruiterReviewState,
} from "../../api/recruiterApplications";
import { Icon } from "../Icon";

interface JobApplicationDetailViewProps {
  jobId: string;
  applicationId: string;
  onNavigateBack: () => void;
  onOpenAiInterviews?: (jobId: string) => void;
  onOpenMatchingComparison?: () => void;
}

export const JobApplicationDetailView: React.FC<JobApplicationDetailViewProps> = ({
  jobId,
  applicationId,
  onNavigateBack,
  onOpenAiInterviews,
  onOpenMatchingComparison,
}) => {
  const [selectedMode, setSelectedMode] = useState<"TEXT" | "AUDIO">("TEXT");
  const [detail, setDetail] = useState<RecruiterApplicationDetail | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [isUpdatingState, setIsUpdatingState] = useState<boolean>(false);
  const [isReleasing, setIsReleasing] = useState<boolean>(false);
  const [showReleaseConfirm, setShowReleaseConfirm] = useState<boolean>(false);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const loadDetail = async (mode = selectedMode) => {
    try {
      setIsLoading(true);
      setError(null);
      const res = await fetchJobApplicantDetail(jobId, applicationId, mode);
      setDetail(res);
    } catch (err: any) {
      setError(err?.message || "Failed to load application details.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadDetail(selectedMode);
  }, [jobId, applicationId, selectedMode]);

  const handleReviewStateTransition = async (targetState: RecruiterReviewState) => {
    if (!detail) return;
    try {
      setIsUpdatingState(true);
      setActionSuccess(null);
      setError(null);
      const updated = await updateApplicantReviewState(
        jobId,
        applicationId,
        targetState,
        detail.recruiterReviewState
      );
      setDetail(updated);
      setActionSuccess(`Review state updated to ${targetState}.`);
    } catch (err: any) {
      setError(err?.message || "Failed to update review state.");
    } finally {
      setIsUpdatingState(false);
    }
  };

  const handleReleaseInterview = async () => {
    if (!detail) return;
    try {
      setIsReleasing(true);
      setShowReleaseConfirm(false);
      setActionSuccess(null);
      setError(null);
      const res = await releaseApplicantInterview(
        jobId,
        applicationId,
        detail.interviewEligibility.questionSetId || undefined
      );
      setActionSuccess(res.message || `${selectedMode} interview released successfully.`);
      await loadDetail(selectedMode);
    } catch (err: any) {
      setError(err?.message || `Failed to release ${selectedMode} interview.`);
    } finally {
      setIsReleasing(false);
    }
  };

  if (isLoading) {
    return (
      <div className="applicant-detail-page" style={{ textAlign: "center", padding: "64px 24px" }}>
        <div className="loading-spinner" style={{ margin: "0 auto 12px" }} />
        <p style={{ color: "var(--text-secondary, #64748b)", fontSize: "14px" }}>
          Loading applicant details...
        </p>
      </div>
    );
  }

  if (error && !detail) {
    return (
      <div className="applicant-detail-page">
        <button
          type="button"
          onClick={onNavigateBack}
          className="btn btn-secondary btn-sm"
          style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: "6px" }}
        >
          <Icon name="arrow-left" size={14} /> <span>Back to Applicants</span>
        </button>
        <div className="feedback-box error" style={{ padding: "16px" }}>
          <p style={{ margin: 0, fontWeight: 600 }}>{error}</p>
        </div>
      </div>
    );
  }

  if (!detail) return null;

  const isWithdrawn = detail.status === "WITHDRAWN";
  const { interviewEligibility, matchEvidence } = detail;

  return (
    <div className="applicant-detail-page">
      {/* Navigation & Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "12px" }}>
        <button
          type="button"
          onClick={onNavigateBack}
          className="btn btn-secondary btn-sm"
          style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
        >
          <Icon name="arrow-left" size={14} /> <span>Back to Applicants</span>
        </button>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span
            className={`badge ${detail.status === "SUBMITTED" ? "badge-success" : "badge-secondary"}`}
            style={{ padding: "3px 10px", fontSize: "12px", fontWeight: 600 }}
          >
            {detail.status === "SUBMITTED" ? "Active (Submitted)" : detail.status}
          </span>
          <span
            className="badge badge-info"
            style={{ padding: "3px 10px", fontSize: "12px", fontWeight: 600 }}
          >
            Review: {detail.recruiterReviewState}
          </span>
        </div>
      </div>

      {actionSuccess && (
        <div className="feedback-box success" style={{ padding: "12px 16px", display: "flex", alignItems: "center", gap: "8px" }}>
          <Icon name="check-circle" size={16} />
          <span>{actionSuccess}</span>
        </div>
      )}

      {error && (
        <div className="feedback-box error" style={{ padding: "12px 16px", display: "flex", alignItems: "center", gap: "8px" }}>
          <Icon name="alert-triangle" size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* Main Applicant Identity Card */}
      <div className="applicant-detail-card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "16px" }}>
          <div>
            <h1 style={{ fontSize: "24px", fontWeight: 700, margin: "0 0 6px 0", color: "var(--text-primary, #0f172a)" }}>
              {detail.candidateName}
            </h1>
            <p style={{ margin: 0, fontSize: "14px", color: "var(--text-secondary, #64748b)" }}>
              Applied to <strong style={{ color: "var(--text-primary, #0f172a)" }}>{detail.jobTitle}</strong> (Submitted Revision: Rev #{detail.jobRevision})
            </p>
          </div>
          <div style={{ textAlign: "right", fontSize: "12px", color: "var(--text-muted, #64748b)" }}>
            <div>Submitted: {new Date(detail.submittedAt).toLocaleString()}</div>
            {detail.withdrawnAt && (
              <div style={{ color: "#d97706", marginTop: "4px" }}>
                Withdrawn: {new Date(detail.withdrawnAt).toLocaleString()}
              </div>
            )}
          </div>
        </div>

        <div className="applicant-meta-grid">
          <div className="applicant-meta-item">
            <span className="applicant-meta-label">Email</span>
            <span className="applicant-meta-value">{detail.candidateEmail || "Not provided"}</span>
          </div>
          <div className="applicant-meta-item">
            <span className="applicant-meta-label">Phone</span>
            <span className="applicant-meta-value">{detail.candidatePhone || "Not provided"}</span>
          </div>
          <div className="applicant-meta-item">
            <span className="applicant-meta-label">Profile Version</span>
            <span className="applicant-meta-value">v{detail.profileVersion}</span>
          </div>
        </div>
      </div>

      {/* Internal Recruiter Review Controls */}
      <div className="applicant-detail-card">
        <h2 className="applicant-detail-card-title">
          <Icon name="user-check" size={18} style={{ color: "var(--accent-primary)" }} />
          <span>Internal Review Stage</span>
        </h2>
        <p style={{ margin: 0, fontSize: "13px", color: "var(--text-secondary, #64748b)", lineHeight: 1.5 }}>
          Move the application through recruiter review stages to track your recruitment workflow.
        </p>

        <div className="applicant-state-btn-row">
          {detail.recruiterReviewState === "RECEIVED" && (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => handleReviewStateTransition("IN_REVIEW")}
              disabled={isUpdatingState || isWithdrawn}
              style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
            >
              <Icon name="play" size={14} />
              <span>Start Review (Move to IN_REVIEW)</span>
            </button>
          )}

          {detail.recruiterReviewState === "IN_REVIEW" && (
            <>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => handleReviewStateTransition("INTERVIEW_READY")}
                disabled={isUpdatingState || isWithdrawn}
                style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
              >
                <Icon name="badge-check" size={14} />
                <span>Mark as INTERVIEW_READY</span>
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => handleReviewStateTransition("RECEIVED")}
                disabled={isUpdatingState || isWithdrawn}
                style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
              >
                <Icon name="arrow-left" size={14} />
                <span>Reset to RECEIVED</span>
              </button>
            </>
          )}

          {detail.recruiterReviewState === "INTERVIEW_READY" && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => handleReviewStateTransition("IN_REVIEW")}
              disabled={isUpdatingState || isWithdrawn}
              style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
            >
              <Icon name="arrow-left" size={14} />
              <span>Move back to IN_REVIEW</span>
            </button>
          )}

          {isWithdrawn && (
            <span style={{ fontSize: "12px", color: "#d97706", fontStyle: "italic" }}>
              Application is withdrawn; review state transitions are restricted.
            </span>
          )}
        </div>
      </div>

      {/* Deterministic Match & Skill Gap Section */}
      <div className="applicant-detail-card">
        <div className="applicant-detail-card-header">
          <h2 className="applicant-detail-card-title">
            <Icon name="sparkles" size={18} style={{ color: "#7c3aed" }} />
            <span>Deterministic Match & Skill Gap Evidence</span>
          </h2>
          {onOpenMatchingComparison && (
            <button
              type="button"
              onClick={onOpenMatchingComparison}
              className="btn btn-secondary btn-xs"
              style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}
            >
              <span>Open Match Workspace</span>
              <Icon name="external-link" size={12} />
            </button>
          )}
        </div>

        {matchEvidence ? (
          <div
            style={{
              padding: "16px",
              backgroundColor: "#faf5ff",
              border: "1px solid #e9d5ff",
              borderRadius: "8px",
              display: "flex",
              flexDirection: "column",
              gap: "12px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                <span style={{ fontSize: "24px", fontWeight: 700, color: "#581c87" }}>
                  {matchEvidence.displayScore.toFixed(1)}%
                </span>
                <span
                  style={{
                    backgroundColor: "#ede9fe",
                    color: "#6d28d9",
                    padding: "3px 8px",
                    borderRadius: "6px",
                    fontSize: "12px",
                    fontWeight: 700,
                  }}
                >
                  {matchEvidence.confidenceTier} Tier
                </span>
              </div>
              <span style={{ fontSize: "12px", color: "#94a3b8" }}>
                Rank #{matchEvidence.rank} • Evaluated: {new Date(matchEvidence.computedAt).toLocaleDateString()}
              </span>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: "8px",
                fontSize: "12px",
                color: "#475569",
                borderTop: "1px solid #e9d5ff",
                paddingTop: "10px",
              }}
            >
              <div>Warnings: <strong>{matchEvidence.warningCount}</strong></div>
              <div>Must-Have Warnings: <strong>{matchEvidence.mustHaveWarningCount}</strong></div>
            </div>
          </div>
        ) : (
          <div
            style={{
              padding: "16px",
              backgroundColor: "#f8fafc",
              border: "1px solid #e2e8f0",
              borderRadius: "8px",
              fontSize: "13px",
              color: "#64748b",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <Icon name="info" size={16} />
              <span>Not evaluated. Matching has not been computed for this job version / candidate pair yet.</span>
            </div>
            {onOpenMatchingComparison && (
              <button
                type="button"
                onClick={onOpenMatchingComparison}
                className="btn btn-secondary btn-xs"
              >
                Run Match
              </button>
            )}
          </div>
        )}
      </div>

      {/* AI Interview Access & Release Section */}
      <div className="applicant-detail-card">
        <div className="applicant-detail-card-header">
          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
            <h2 className="applicant-detail-card-title" style={{ margin: 0 }}>
              <Icon name="sparkles" size={18} style={{ color: "var(--accent-primary)" }} />
              <span>Interview Access</span>
            </h2>
            <div className="nav-tab-group" style={{ display: "inline-flex", gap: "4px", backgroundColor: "#f1f5f9", padding: "3px", borderRadius: "6px" }}>
              <button
                type="button"
                className={`btn btn-xs ${selectedMode === "TEXT" ? "btn-primary" : "btn-quiet"}`}
                onClick={() => setSelectedMode("TEXT")}
                style={{ padding: "3px 10px", fontSize: "12px" }}
              >
                Text Interview
              </button>
              <button
                type="button"
                className={`btn btn-xs ${selectedMode === "AUDIO" ? "btn-primary" : "btn-quiet"}`}
                onClick={() => setSelectedMode("AUDIO")}
                style={{ padding: "3px 10px", fontSize: "12px" }}
              >
                Audio Interview
              </button>
            </div>
          </div>
          {onOpenAiInterviews && (
            <button
              type="button"
              onClick={() => onOpenAiInterviews(jobId)}
              className="btn btn-secondary btn-xs"
              style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}
            >
              <span>Open AI Interviews (Rev #{detail.jobRevision})</span>
              <Icon name="external-link" size={12} />
            </button>
          )}
        </div>

        <div
          style={{
            padding: "16px",
            backgroundColor: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            display: "flex",
            flexDirection: "column",
            gap: "14px",
          }}
        >
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: "10px" }}>
            <div style={{ backgroundColor: "#ffffff", padding: "10px 12px", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <span style={{ fontSize: "11px", textTransform: "uppercase", color: "#64748b", display: "block" }}>Job Revision</span>
              <strong style={{ fontSize: "14px", color: "#0f172a" }}>Rev #{detail.jobRevision}</strong>
            </div>
            <div style={{ backgroundColor: "#ffffff", padding: "10px 12px", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <span style={{ fontSize: "11px", textTransform: "uppercase", color: "#64748b", display: "block" }}>Selected Mode</span>
              <strong style={{ fontSize: "14px", color: "#0f172a" }}>{selectedMode}</strong>
            </div>
            <div style={{ backgroundColor: "#ffffff", padding: "10px 12px", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <span style={{ fontSize: "11px", textTransform: "uppercase", color: "#64748b", display: "block" }}>Approved Questions</span>
              <strong style={{ fontSize: "14px", color: "#0f172a" }}>{interviewEligibility.questionCount} Questions</strong>
            </div>
            <div style={{ backgroundColor: "#ffffff", padding: "10px 12px", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <span style={{ fontSize: "11px", textTransform: "uppercase", color: "#64748b", display: "block" }}>Official Status</span>
              <strong style={{ fontSize: "14px", color: interviewEligibility.existingReleaseId ? "#059669" : "#475569" }}>
                {interviewEligibility.existingReleaseId ? "RELEASED" : "NOT RELEASED"}
              </strong>
            </div>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: "8px", borderTop: "1px solid #e2e8f0", flexWrap: "wrap", gap: "10px" }}>
            <div style={{ fontSize: "13px", color: "#475569" }}>
              {interviewEligibility.message}
            </div>
            {interviewEligibility.isEligible && (
              <button
                type="button"
                onClick={() => setShowReleaseConfirm(true)}
                disabled={isReleasing || isWithdrawn}
                className="btn btn-primary btn-sm"
                style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
              >
                <Icon name="sparkles" size={14} />
                <span>Release Interview</span>
              </button>
            )}
          </div>

          {!interviewEligibility.isEligible && !interviewEligibility.existingReleaseId && (
            <div style={{ fontSize: "12px", color: "#92400e", backgroundColor: "#fef3c7", padding: "8px 12px", borderRadius: "6px", border: "1px solid #fde68a", display: "flex", alignItems: "center", gap: "6px" }}>
              <Icon name="alert-triangle" size={14} />
              <span>{interviewEligibility.message}</span>
            </div>
          )}

          {interviewEligibility.existingReleaseId && (
            <div style={{ fontSize: "12px", color: "#065f46", backgroundColor: "#ecfdf5", padding: "10px 14px", borderRadius: "6px", border: "1px solid #a7f3d0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Icon name="check-circle" size={16} />
                <span>Interview released and active for candidate on Interviews portal.</span>
              </div>
              <span style={{ fontWeight: 700, textTransform: "uppercase", fontSize: "11px" }}>
                Interview Released
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Confirmation Dialog Modal */}
      {showReleaseConfirm && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="release-modal-title"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(15, 23, 42, 0.4)",
            backdropFilter: "blur(2px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1100,
            padding: "16px",
          }}
        >
          <div
            style={{
              backgroundColor: "#ffffff",
              borderRadius: "12px",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.2)",
              maxWidth: "480px",
              width: "100%",
              padding: "24px",
              border: "1px solid #e2e8f0",
              display: "flex",
              flexDirection: "column",
              gap: "16px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <div
                style={{
                  width: "40px",
                  height: "40px",
                  borderRadius: "50%",
                  backgroundColor: "var(--accent-subtle)",
                  border: "1px solid #c7d2fe",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "var(--accent-primary)",
                  flexShrink: 0,
                }}
              >
                <Icon name="sparkles" size={20} />
              </div>
              <div>
                <h3 id="release-modal-title" style={{ fontSize: "16px", fontWeight: 700, margin: 0, color: "#0f172a" }}>
                  Release Official Interview
                </h3>
                <p style={{ margin: "2px 0 0 0", fontSize: "12px", color: "#64748b" }}>
                  Application for {detail.candidateName}
                </p>
              </div>
            </div>

            <div
              style={{
                backgroundColor: "#f8fafc",
                borderRadius: "8px",
                padding: "12px 14px",
                fontSize: "13px",
                color: "#334155",
                display: "flex",
                flexDirection: "column",
                gap: "6px",
                border: "1px solid #e2e8f0",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#64748b" }}>Job Title:</span>
                <strong style={{ color: "#0f172a" }}>{detail.jobTitle}</strong>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#64748b" }}>Job Revision:</span>
                <strong style={{ color: "#0f172a" }}>Rev #{detail.jobRevision}</strong>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#64748b" }}>Approved Questions:</span>
                <strong style={{ color: "#0f172a" }}>{interviewEligibility.questionCount} Questions</strong>
              </div>
            </div>

            <p style={{ fontSize: "13px", color: "#64748b", margin: 0, lineHeight: 1.5 }}>
              Releasing this official interview will freeze these exact approved questions to the candidate's application and make it available in their candidate portal.
            </p>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px", paddingTop: "8px", borderTop: "1px solid #f1f5f9" }}>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setShowReleaseConfirm(false)}
                disabled={isReleasing}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleReleaseInterview}
                disabled={isReleasing}
                style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
              >
                {isReleasing ? (
                  <>
                    <Icon name="spinner" size={14} className="animate-spin" />
                    <span>Releasing...</span>
                  </>
                ) : (
                  <>
                    <Icon name="check" size={14} />
                    <span>Confirm Release</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
