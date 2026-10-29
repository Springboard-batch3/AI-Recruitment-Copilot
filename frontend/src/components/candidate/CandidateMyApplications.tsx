import React, { useEffect, useState } from "react";
import {
  fetchMyApplications,
  withdrawApplication,
  CandidateApplication,
} from "../../api/candidateApplications";
import { Icon } from "../Icon";

interface CandidateMyApplicationsProps {
  onSelectApplication: (applicationId: string) => void;
  onNavigateJobs: () => void;
}

export function CandidateMyApplications({
  onSelectApplication,
  onNavigateJobs,
}: CandidateMyApplicationsProps) {
  const [applications, setApplications] = useState<CandidateApplication[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [withdrawingAppId, setWithdrawingAppId] = useState<string | null>(null);
  const [confirmWithdrawApp, setConfirmWithdrawApp] = useState<CandidateApplication | null>(null);
  const [withdrawError, setWithdrawError] = useState<string | null>(null);

  const loadApplications = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const res = await fetchMyApplications();
      setApplications(res.items || []);
    } catch (err: any) {
      setError(err?.message || "Failed to load your applications.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadApplications();
  }, []);

  const handleWithdrawConfirm = async () => {
    if (!confirmWithdrawApp) return;

    try {
      setWithdrawingAppId(confirmWithdrawApp.applicationId);
      setWithdrawError(null);
      const updated = await withdrawApplication(confirmWithdrawApp.applicationId);
      setApplications((prev) =>
        prev.map((app) => (app.applicationId === updated.applicationId ? updated : app))
      );
      setConfirmWithdrawApp(null);
    } catch (err: any) {
      setWithdrawError(err?.message || "Failed to withdraw application.");
    } finally {
      setWithdrawingAppId(null);
    }
  };

  return (
    <div className="candidate-applications-container" style={{ maxWidth: "960px", margin: "0 auto", padding: "16px 20px" }}>
      {/* Header Section */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "24px" }}>
        <div>
          <h1 style={{ fontSize: "24px", fontWeight: 700, color: "var(--text-primary, #0f172a)", margin: "0 0 8px 0" }}>
            My Job Applications
          </h1>
          <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", margin: 0, lineHeight: 1.5 }}>
            Track the status of your submitted job applications or withdraw applications if your plans change.
          </p>
        </div>

        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={loadApplications}
          disabled={isLoading}
          title="Refresh applications list"
          style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
        >
          <Icon name="refresh" size={14} aria-hidden="true" />
          <span>Refresh</span>
        </button>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="feedback-box error" role="alert" style={{ marginBottom: "20px" }}>
          <Icon name="alert-circle" size={16} />
          <span>{error}</span>
          <button type="button" className="btn btn-sm btn-secondary" onClick={loadApplications} style={{ marginLeft: "auto" }}>
            Retry
          </button>
        </div>
      )}

      {/* Loading State */}
      {isLoading && (
        <div style={{ textAlign: "center", padding: "48px 0" }} role="status" aria-live="polite">
          <div className="loading-spinner" style={{ margin: "0 auto 16px" }} />
          <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", margin: 0 }}>
            Loading your applications...
          </p>
        </div>
      )}

      {/* Empty State */}
      {!isLoading && !error && applications.length === 0 && (
        <div
          style={{
            textAlign: "center",
            padding: "48px 24px",
            backgroundColor: "var(--bg-surface, #ffffff)",
            borderRadius: "var(--radius-lg, 12px)",
            border: "1px solid var(--border-subtle, #e2e8f0)",
          }}
        >
          <div style={{ color: "var(--text-muted, #94a3b8)", marginBottom: "12px" }}>
            <Icon name="briefcase" size={36} aria-hidden="true" />
          </div>
          <h2 style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: "0 0 6px 0" }}>
            No applications submitted yet
          </h2>
          <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", margin: "0 0 20px 0", maxWidth: "420px", marginLeft: "auto", marginRight: "auto" }}>
            Browse our open job postings and submit your application when you find a matching role.
          </p>
          <button type="button" className="btn btn-primary btn-sm" onClick={onNavigateJobs}>
            Explore Open Jobs
          </button>
        </div>
      )}

      {/* Applications List */}
      {!isLoading && !error && applications.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {applications.map((app) => {
            const isSubmitted = app.status === "SUBMITTED";
            const isWithdrawn = app.status === "WITHDRAWN";
            const isProcessing = app.status === "RESUME_PROCESSING";
            const isReady = app.status === "READY_TO_SUBMIT";
            const isFailed = app.status === "PROCESSING_FAILED";
            const isDraft = app.status === "DRAFT";

            const dateStr = app.submittedAt
              ? new Date(app.submittedAt).toLocaleDateString(undefined, {
                  year: "numeric",
                  month: "short",
                  day: "numeric",
                })
              : app.createdAt
              ? new Date(app.createdAt).toLocaleDateString(undefined, {
                  year: "numeric",
                  month: "short",
                  day: "numeric",
                })
              : "Recently";

            let statusLabel = "Submitted";
            let statusBg = "var(--status-success-bg, #ecfdf5)";
            let statusColor = "var(--status-success, #059669)";
            let statusBorder = "var(--status-success-border, #a7f3d0)";

            if (isWithdrawn) {
              statusLabel = "Withdrawn";
              statusBg = "var(--bg-surface-subtle, #f1f5f9)";
              statusColor = "var(--text-muted, #64748b)";
              statusBorder = "var(--border-subtle, #e2e8f0)";
            } else if (isProcessing) {
              statusLabel = "Processing Resume";
              statusBg = "var(--accent-subtle, #eef1fc)";
              statusColor = "var(--accent-primary)";
              statusBorder = "var(--accent-subtle-border, #d0e0fd)";
            } else if (isReady) {
              statusLabel = "Ready to Submit";
              statusBg = "var(--accent-subtle, #eef1fc)";
              statusColor = "var(--accent-primary)";
              statusBorder = "var(--accent-subtle-border, #d0e0fd)";
            } else if (isFailed) {
              statusLabel = "Resume Error";
              statusBg = "var(--status-danger-bg, #eadfe7)";
              statusColor = "var(--status-danger, #63445b)";
              statusBorder = "var(--status-danger-border, #c9b5c3)";
            } else if (isDraft) {
              statusLabel = "Draft";
              statusBg = "var(--bg-surface-subtle, #f1f5f9)";
              statusColor = "var(--text-secondary, #475569)";
              statusBorder = "var(--border-subtle, #e2e8f0)";
            }

            return (
              <div
                key={app.applicationId}
                style={{
                  backgroundColor: "var(--bg-surface, #ffffff)",
                  borderRadius: "var(--radius-lg, 12px)",
                  border: "1px solid var(--border-subtle, #e2e8f0)",
                  padding: "20px 24px",
                  boxShadow: "var(--shadow-card, 0 1px 3px rgba(16, 24, 40, 0.05))",
                  display: "flex",
                  flexDirection: "column",
                  gap: "12px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px" }}>
                  <div>
                    <h2 style={{ fontSize: "18px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: "0 0 6px 0" }}>
                      {app.job.title}
                    </h2>

                    <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", alignItems: "center" }}>
                      {app.job.department && (
                        <span style={{ fontSize: "13px", color: "var(--text-secondary, #475569)", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                          <Icon name="folder" size={14} />
                          {app.job.department}
                        </span>
                      )}

                      <span style={{ fontSize: "13px", color: "var(--text-muted, #64748b)", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                        <Icon name="calendar" size={14} />
                        {app.submittedAt ? `Submitted on ${dateStr}` : `Started on ${dateStr}`}
                      </span>

                      {/* Status Badge */}
                      <span
                        style={{
                          fontSize: "12px",
                          padding: "2px 8px",
                          borderRadius: "var(--radius-full, 9999px)",
                          backgroundColor: statusBg,
                          color: statusColor,
                          border: `1px solid ${statusBorder}`,
                          fontWeight: 600,
                        }}
                      >
                        {statusLabel}
                      </span>
                    </div>
                  </div>

                  <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => onSelectApplication(app.applicationId)}
                      style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}
                      aria-label={`View application details for ${app.job.title}`}
                    >
                      <span>View Details</span>
                      <Icon name="chevron-right" size={14} />
                    </button>

                    {app.canWithdraw && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => {
                          setWithdrawError(null);
                          setConfirmWithdrawApp(app);
                        }}
                        aria-label={`Withdraw application for ${app.job.title}`}
                        style={{ color: "var(--text-secondary, #475569)" }}
                      >
                        Withdraw
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Withdrawal Confirmation Modal */}
      {confirmWithdrawApp && (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="withdraw-dialog-title"
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
              <h2 id="withdraw-dialog-title" style={{ fontSize: "18px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: 0 }}>
                Withdraw Application?
              </h2>
            </div>

            <p style={{ fontSize: "14px", color: "var(--text-secondary, #475569)", margin: "0 0 12px 0", lineHeight: 1.5 }}>
              Are you sure you want to withdraw your application for <strong>{confirmWithdrawApp.job.title}</strong>?
            </p>

            <p style={{ fontSize: "13px", color: "var(--text-muted, #64748b)", margin: "0 0 20px 0", lineHeight: 1.4 }}>
              Withdrawing removes your application from active recruiter consideration. A permanent record of this submission is retained for auditing purposes.
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
                onClick={() => setConfirmWithdrawApp(null)}
                disabled={withdrawingAppId !== null}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleWithdrawConfirm}
                disabled={withdrawingAppId !== null}
                style={{ backgroundColor: "var(--status-warn, #d97706)", borderColor: "var(--status-warn, #d97706)" }}
              >
                {withdrawingAppId ? "Withdrawing..." : "Confirm Withdrawal"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
