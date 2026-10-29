import React, { useEffect, useState } from "react";
import {
  fetchApplicationDetail,
  withdrawApplication,
  CandidateApplication,
} from "../../api/candidateApplications";
import { Icon } from "../Icon";

interface CandidateApplicationDetailViewProps {
  applicationId: string;
  onBack: () => void;
  onNavigateJobs: () => void;
  onNavigateInterviews?: () => void;
}

export function CandidateApplicationDetailView({
  applicationId,
  onBack,
  onNavigateJobs,
  onNavigateInterviews,
}: CandidateApplicationDetailViewProps) {
  const [application, setApplication] = useState<CandidateApplication | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [isWithdrawing, setIsWithdrawing] = useState<boolean>(false);
  const [isConfirmModalOpen, setIsConfirmModalOpen] = useState<boolean>(false);
  const [withdrawError, setWithdrawError] = useState<string | null>(null);

  const loadDetail = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const res = await fetchApplicationDetail(applicationId);
      setApplication(res);
    } catch (err: any) {
      setError(err?.message || "Application not found or unavailable.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadDetail();
  }, [applicationId]);

  const handleWithdraw = async () => {
    if (!application || isWithdrawing) return;

    try {
      setIsWithdrawing(true);
      setWithdrawError(null);
      const updated = await withdrawApplication(application.applicationId);
      setApplication(updated);
      setIsConfirmModalOpen(false);
    } catch (err: any) {
      setWithdrawError(err?.message || "Failed to withdraw application.");
    } finally {
      setIsWithdrawing(false);
    }
  };

  const isSubmitted = application?.status === "SUBMITTED";

  return (
    <div className="candidate-application-detail-container" style={{ maxWidth: "960px", margin: "0 auto", padding: "16px 20px" }}>
      {/* Top Navigation */}
      <div style={{ marginBottom: "20px" }}>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={onBack}
          style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
          aria-label="Back to My Applications"
        >
          <Icon name="arrow-left" size={14} />
          <span>Back to My Applications</span>
        </button>
      </div>

      {/* Error State */}
      {error && (
        <div
          style={{
            textAlign: "center",
            padding: "48px 24px",
            backgroundColor: "var(--bg-surface, #ffffff)",
            borderRadius: "var(--radius-lg, 12px)",
            border: "1px solid var(--border-subtle, #e2e8f0)",
          }}
        >
          <div style={{ color: "var(--status-warn, #d97706)", marginBottom: "12px" }}>
            <Icon name="alert-triangle" size={36} aria-hidden="true" />
          </div>
          <h2 style={{ fontSize: "18px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: "0 0 8px 0" }}>
            Application Unavailable
          </h2>
          <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", margin: "0 0 20px 0" }}>
            {error}
          </p>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onBack}>
            Return to My Applications
          </button>
        </div>
      )}

      {/* Loading State */}
      {isLoading && (
        <div style={{ textAlign: "center", padding: "64px 0" }} role="status" aria-live="polite">
          <div className="loading-spinner" style={{ margin: "0 auto 16px" }} />
          <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", margin: 0 }}>
            Loading application details...
          </p>
        </div>
      )}

      {/* Application Details Card */}
      {!isLoading && !error && application && (
        <div
          style={{
            backgroundColor: "var(--bg-surface, #ffffff)",
            borderRadius: "var(--radius-lg, 12px)",
            border: "1px solid var(--border-subtle, #e2e8f0)",
            padding: "32px",
            boxShadow: "var(--shadow-card, 0 1px 3px rgba(16, 24, 40, 0.05))",
            display: "flex",
            flexDirection: "column",
            gap: "24px",
          }}
        >
          {/* Header */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", borderBottom: "1px solid var(--border-subtle, #e2e8f0)", paddingBottom: "20px" }}>
            <div>
              <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", display: "block", marginBottom: "4px" }}>
                Application #{application.applicationId.slice(0, 8)}
              </span>
              <h1 style={{ fontSize: "24px", fontWeight: 700, color: "var(--text-primary, #0f172a)", margin: "0 0 8px 0" }}>
                {application.job.title}
              </h1>

              <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", alignItems: "center" }}>
                {application.job.department && (
                  <span style={{ fontSize: "13px", color: "var(--text-secondary, #475569)", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                    <Icon name="folder" size={14} />
                    {application.job.department}
                  </span>
                )}

                {application.job.location && (
                  <span style={{ fontSize: "13px", color: "var(--text-secondary, #475569)", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                    <Icon name="map-pin" size={14} />
                    {application.job.location}
                  </span>
                )}
              </div>
            </div>

            {/* Status & Withdrawal Button */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "8px" }}>
              <span
                style={{
                  fontSize: "13px",
                  padding: "4px 12px",
                  borderRadius: "var(--radius-full, 9999px)",
                  backgroundColor:
                    application.status === "SUBMITTED"
                      ? "var(--status-success-bg, #ecfdf5)"
                      : application.status === "WITHDRAWN"
                      ? "var(--bg-surface-subtle, #f1f5f9)"
                      : application.status === "PROCESSING_FAILED"
                      ? "var(--status-danger-bg, #eadfe7)"
                      : "var(--accent-subtle, #dce8e3)",
                  color:
                    application.status === "SUBMITTED"
                      ? "var(--status-success, #2f6b4f)"
                      : application.status === "WITHDRAWN"
                      ? "var(--text-muted, #6d7772)"
                      : application.status === "PROCESSING_FAILED"
                      ? "var(--status-danger, #63445b)"
                      : "var(--accent-primary)",
                  border: `1px solid ${
                    application.status === "SUBMITTED"
                      ? "var(--status-success-border, #aad1b7)"
                      : application.status === "PROCESSING_FAILED"
                      ? "var(--status-danger-border, #c9b5c3)"
                      : "var(--border-subtle, #e0ddd2)"
                  }`,
                  fontWeight: 600,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "4px",
                }}
              >
                <Icon name={application.status === "SUBMITTED" ? "check-circle" : "file-text"} size={14} />
                {application.status === "SUBMITTED"
                  ? "Submitted"
                  : application.status === "WITHDRAWN"
                  ? "Withdrawn"
                  : application.status === "READY_TO_SUBMIT"
                  ? "Ready to Submit"
                  : application.status === "RESUME_PROCESSING"
                  ? "Processing Resume"
                  : application.status === "PROCESSING_FAILED"
                  ? "Resume Error"
                  : "Draft"}
              </span>

              {application.canWithdraw && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    setWithdrawError(null);
                    setIsConfirmModalOpen(true);
                  }}
                  aria-label="Withdraw application"
                >
                  Withdraw Application
                </button>
              )}
            </div>
          </div>

          {/* Submission Info Grid */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "16px" }}>
            <div style={{ backgroundColor: "var(--bg-surface-subtle, #f8fafc)", padding: "16px", borderRadius: "var(--radius-md, 8px)" }}>
              <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", display: "block", marginBottom: "4px" }}>
                {application.submittedAt ? "Submission Date" : "Created Date"}
              </span>
              <span style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-primary, #0f172a)" }}>
                {application.submittedAt
                  ? new Date(application.submittedAt).toLocaleString()
                  : application.createdAt
                  ? new Date(application.createdAt).toLocaleString()
                  : "N/A"}
              </span>
            </div>

            <div style={{ backgroundColor: "var(--bg-surface-subtle, #f8fafc)", padding: "16px", borderRadius: "var(--radius-md, 8px)" }}>
              <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", display: "block", marginBottom: "4px" }}>
                Job Revision Snapshot
              </span>
              <span style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-primary, #0f172a)" }}>
                {application.jobRevision ? `Revision #${application.jobRevision}` : "Active Revision"}
              </span>
            </div>

            <div style={{ backgroundColor: "var(--bg-surface-subtle, #f8fafc)", padding: "16px", borderRadius: "var(--radius-md, 8px)" }}>
              <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", display: "block", marginBottom: "4px" }}>
                Profile Version Frozen
              </span>
              <span style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-primary, #0f172a)" }}>
                {application.profileVersion ? `Version #${application.profileVersion}` : "Pending Submission"}
              </span>
            </div>

            {application.withdrawnAt && (
              <div style={{ backgroundColor: "var(--bg-surface-subtle, #f8fafc)", padding: "16px", borderRadius: "var(--radius-md, 8px)" }}>
                <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", display: "block", marginBottom: "4px" }}>
                  Withdrawn On
                </span>
                <span style={{ fontSize: "14px", fontWeight: 600, color: "var(--status-warn, #d97706)" }}>
                  {new Date(application.withdrawnAt).toLocaleString()}
                </span>
              </div>
            )}
          </div>

          {/* Job Overview Excerpt */}
          {application.job.summary && (
            <div>
              <h2 style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: "0 0 8px 0" }}>
                Role Summary
              </h2>
              <p style={{ fontSize: "14px", color: "var(--text-secondary, #334155)", margin: 0, lineHeight: 1.6 }}>
                {application.job.summary}
              </p>
            </div>
          )}

          {/* Interview Access Link when Submitted */}
          {isSubmitted && onNavigateInterviews && (
            <div
              style={{
                padding: "16px 20px",
                borderRadius: "var(--radius-md, 8px)",
                backgroundColor: "var(--bg-surface-subtle, #f8fafc)",
                border: "1px solid var(--border-subtle, #e2e8f0)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: "12px",
              }}
            >
              <div>
                <div style={{ fontWeight: 600, fontSize: "14px", color: "var(--text-primary, #0f172a)", display: "flex", alignItems: "center", gap: "6px" }}>
                  <Icon name="message-square" size={16} style={{ color: "var(--accent-primary)" }} />
                  <span>Assigned AI Interviews</span>
                </div>
                <p style={{ margin: "2px 0 0 0", fontSize: "13px", color: "var(--text-secondary, #64748b)" }}>
                  Check if a recruiter has released an official interview for this application.
                </p>
              </div>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={onNavigateInterviews}
                style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
              >
                <span>Go to Interviews</span>
                <Icon name="chevron-right" size={14} />
              </button>
            </div>
          )}

          {/* Safe Note */}
          <div
            style={{
              padding: "16px",
              borderRadius: "var(--radius-md, 8px)",
              backgroundColor: "var(--accent-subtle, #eef1fc)",
              border: "1px solid var(--accent-border, #d4dbf8)",
              fontSize: "13px",
              color: "var(--text-secondary, #475569)",
              lineHeight: 1.5,
            }}
          >
            <p style={{ margin: 0 }}>
              <strong>Application Status Notice:</strong> Your application has been received. Our hiring team reviews submissions periodically. If your qualifications align with our current hiring needs, you will be invited to the next stage.
            </p>
          </div>
        </div>
      )}

      {/* Withdrawal Modal */}
      {isConfirmModalOpen && application && (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="withdraw-detail-title"
          style={{
            position: "fixed",
            inset: 0,
            backgroundColor: "rgba(15, 23, 42, 0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
            padding: "16px",
          }}
        >
          <div
            style={{
              backgroundColor: "var(--bg-surface, #ffffff)",
              borderRadius: "var(--radius-lg, 12px)",
              maxWidth: "480px",
              width: "100%",
              padding: "24px",
              boxShadow: "var(--shadow-md, 0 4px 6px -1px rgba(16, 24, 40, 0.1))",
              border: "1px solid var(--border-subtle, #e2e8f0)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "16px" }}>
              <div style={{ color: "var(--status-warn, #d97706)" }}>
                <Icon name="alert-triangle" size={24} aria-hidden="true" />
              </div>
              <h2 id="withdraw-detail-title" style={{ fontSize: "18px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: 0 }}>
                Withdraw Application?
              </h2>
            </div>

            <p style={{ fontSize: "14px", color: "var(--text-secondary, #475569)", margin: "0 0 12px 0", lineHeight: 1.5 }}>
              Are you sure you want to withdraw your application for <strong>{application.job.title}</strong>?
            </p>

            <p style={{ fontSize: "13px", color: "var(--text-muted, #64748b)", margin: "0 0 20px 0", lineHeight: 1.4 }}>
              Withdrawing removes your application from active recruiter consideration.
            </p>

            {withdrawError && (
              <div className="feedback-box error" role="alert" style={{ marginBottom: "16px" }}>
                <Icon name="alert-circle" size={14} />
                <span>{withdrawError}</span>
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setIsConfirmModalOpen(false)}
                disabled={isWithdrawing}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleWithdraw}
                disabled={isWithdrawing}
                style={{ backgroundColor: "var(--status-warn, #d97706)", borderColor: "var(--status-warn, #d97706)" }}
              >
                {isWithdrawing ? "Withdrawing..." : "Confirm Withdrawal"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
