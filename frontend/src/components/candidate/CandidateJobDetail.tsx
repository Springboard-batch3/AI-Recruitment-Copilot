import React, { useEffect, useState } from "react";
import { fetchCandidateJobDetail, CandidateJobDetail } from "../../api/candidateJobs";
import { CandidateApplicationForm } from "./CandidateApplicationForm";
import { Icon } from "../Icon";

interface CandidateJobDetailViewProps {
  jobId: string;
  versionId?: string;
  onBack: () => void;
  onNavigateApplication?: (applicationId: string) => void;
}

export function CandidateJobDetailView({
  jobId,
  versionId,
  onBack,
  onNavigateApplication,
}: CandidateJobDetailViewProps) {
  const [job, setJob] = useState<CandidateJobDetail | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [isApplying, setIsApplying] = useState<boolean>(false);
  const [appliedSuccessAppId, setAppliedSuccessAppId] = useState<string | null>(null);

  const loadJob = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const res = await fetchCandidateJobDetail(jobId, versionId);
      setJob(res);
      const activeAppId = res.activeApplicationId || (res as any).active_application_id;
      const hasActive = res.hasActiveApplication || (res as any).has_active_application;
      if (hasActive && activeAppId) {
        setAppliedSuccessAppId(activeAppId);
      }
    } catch (err: any) {
      setError(err?.message || "Job posting not found or no longer available.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadJob();
  }, [jobId, versionId]);

  const handleApplyClick = () => {
    setIsApplying(true);
  };

  const handleApplicationSuccess = (appId: string) => {
    setIsApplying(false);
    setAppliedSuccessAppId(appId);
    setJob((prev) =>
      prev
        ? {
            ...prev,
            hasActiveApplication: true,
            activeApplicationId: appId,
          }
        : null
    );
    if (onNavigateApplication) {
      onNavigateApplication(appId);
    }
  };

  const formatEmploymentType = (type?: string | null) => {
    if (!type) return null;
    return type.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  };

  const formatWorkplaceType = (type?: string | null) => {
    if (!type) return null;
    return type.replace(/_/g, "-").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  };

  return (
    <div className="candidate-job-detail-container" style={{ maxWidth: "960px", margin: "0 auto", padding: "16px 20px" }}>
      {/* Top Navigation */}
      <div style={{ marginBottom: "20px" }}>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={onBack}
          style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
          aria-label="Back to open jobs"
        >
          <Icon name="arrow-left" size={14} />
          <span>Back to Open Jobs</span>
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
            Job Posting Unavailable
          </h2>
          <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", margin: "0 0 20px 0", maxWidth: "420px", marginLeft: "auto", marginRight: "auto" }}>
            {error}
          </p>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onBack}>
            Return to Open Jobs
          </button>
        </div>
      )}

      {/* Loading State */}
      {isLoading && (
        <div style={{ textAlign: "center", padding: "64px 0" }} role="status" aria-live="polite">
          <div className="loading-spinner" style={{ margin: "0 auto 16px" }} />
          <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", margin: 0 }}>
            Loading job details...
          </p>
        </div>
      )}

      {/* Application Form View */}
      {isApplying && job && (
        <CandidateApplicationForm
          job={job}
          onBack={() => setIsApplying(false)}
          onSuccess={handleApplicationSuccess}
        />
      )}

      {/* Job Details Content */}
      {!isLoading && !error && job && !isApplying && (
        <div
          style={{
            backgroundColor: "var(--bg-surface, #ffffff)",
            borderRadius: "var(--radius-lg, 12px)",
            border: "1px solid var(--border-subtle, #e2e8f0)",
            padding: "32px",
            boxShadow: "var(--shadow-card, 0 1px 3px rgba(16, 24, 40, 0.05))",
            display: "flex",
            flexDirection: "column",
            gap: "28px",
          }}
        >
          {/* Header Block with Apply Button */}
          <div style={{ borderBottom: "1px solid var(--border-subtle, #e2e8f0)", paddingBottom: "24px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", marginBottom: "12px" }}>
              <h1
                style={{
                  fontSize: "24px",
                  fontWeight: 700,
                  color: "var(--text-primary, #0f172a)",
                  margin: 0,
                }}
              >
                {job.title}
              </h1>

              {/* Application Action Area */}
              <div>
                {(appliedSuccessAppId || job.hasActiveApplication || (job as any).has_active_application) ? (
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <span
                      style={{
                        fontSize: "13px",
                        padding: "4px 12px",
                        borderRadius: "var(--radius-full, 9999px)",
                        backgroundColor: "var(--status-success-bg, #ecfdf5)",
                        color: "var(--status-success, #059669)",
                        border: "1px solid var(--status-success-border, #a7f3d0)",
                        fontWeight: 600,
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "4px",
                      }}
                    >
                      <Icon name="check-circle" size={14} /> Applied
                    </span>
                    {onNavigateApplication && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => onNavigateApplication((appliedSuccessAppId || job.activeApplicationId || (job as any).active_application_id)!)}
                        aria-label="View Application"
                      >
                        View Application
                      </button>
                    )}
                  </div>
                ) : (
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={handleApplyClick}
                    style={{ minWidth: "120px" }}
                    aria-label="Apply to Position"
                  >
                    Apply Now
                  </button>
                )}
              </div>
            </div>

            <div style={{ display: "flex", flexWrap: "wrap", gap: "16px", alignItems: "center" }}>
              {job.department && (
                <span style={{ fontSize: "14px", color: "var(--text-secondary, #475569)", display: "inline-flex", alignItems: "center", gap: "6px" }}>
                  <Icon name="folder" size={16} />
                  {job.department}
                </span>
              )}

              {job.location && (
                <span style={{ fontSize: "14px", color: "var(--text-secondary, #475569)", display: "inline-flex", alignItems: "center", gap: "6px" }}>
                  <Icon name="map-pin" size={16} />
                  {job.location}
                </span>
              )}

              {formatWorkplaceType(job.workplaceType) && (
                <span
                  style={{
                    fontSize: "12px",
                    padding: "3px 10px",
                    borderRadius: "var(--radius-full, 9999px)",
                    backgroundColor: "var(--accent-subtle, #eef1fc)",
                    color: "var(--accent-primary)",
                    fontWeight: 500,
                  }}
                >
                  {formatWorkplaceType(job.workplaceType)}
                </span>
              )}

              {formatEmploymentType(job.employmentType) && (
                <span
                  style={{
                    fontSize: "12px",
                    padding: "3px 10px",
                    borderRadius: "var(--radius-full, 9999px)",
                    backgroundColor: "var(--bg-surface-subtle, #f1f5f9)",
                    color: "var(--text-secondary, #475569)",
                    fontWeight: 500,
                  }}
                >
                  {formatEmploymentType(job.employmentType)}
                </span>
              )}

              {job.openingsCount && job.openingsCount > 0 && (
                <span style={{ fontSize: "13px", color: "var(--text-muted, #64748b)", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                  <Icon name="user" size={14} />
                  {job.openingsCount} {job.openingsCount === 1 ? "opening" : "openings"}
                </span>
              )}
            </div>
          </div>

          {/* Job Summary */}
          {job.summary && (
            <div>
              <h2 style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: "0 0 10px 0" }}>
                About the Role
              </h2>
              <p style={{ fontSize: "14px", color: "var(--text-secondary, #334155)", margin: 0, lineHeight: 1.6, whiteSpace: "pre-line" }}>
                {job.summary}
              </p>
            </div>
          )}

          {/* Responsibilities */}
          {job.responsibilities && (
            <div>
              <h2 style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: "0 0 10px 0" }}>
                Key Responsibilities
              </h2>
              <p style={{ fontSize: "14px", color: "var(--text-secondary, #334155)", margin: 0, lineHeight: 1.6, whiteSpace: "pre-line" }}>
                {job.responsibilities}
              </p>
            </div>
          )}

          {/* Qualifications & Requirements */}
          {job.qualifications && job.qualifications.length > 0 && (
            <div>
              <h2 style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: "0 0 12px 0" }}>
                Key Qualifications & Skills
              </h2>
              <ul style={{ margin: 0, paddingLeft: "20px", display: "flex", flexDirection: "column", gap: "8px" }}>
                {job.qualifications.map((qual, idx) => (
                  <li key={idx} style={{ fontSize: "14px", color: "var(--text-secondary, #334155)", lineHeight: 1.5 }}>
                    {qual}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
