import React, { useState, useEffect } from "react";
import {
  fetchJobApplicants,
  RecruiterApplicationSummary,
} from "../../api/recruiterApplications";
import { Icon } from "../Icon";

interface JobApplicantsViewProps {
  jobId: string;
  jobTitle?: string;
  onSelectApplication: (applicationId: string) => void;
  onNavigateBack?: () => void;
}

export const JobApplicantsView: React.FC<JobApplicantsViewProps> = ({
  jobId,
  jobTitle,
  onSelectApplication,
  onNavigateBack,
}) => {
  const [applications, setApplications] = useState<RecruiterApplicationSummary[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [activeCount, setActiveCount] = useState<number>(0);
  const [withdrawnCount, setWithdrawnCount] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [statusFilter, setStatusFilter] = useState<string>("SUBMITTED");
  const [reviewStateFilter, setReviewStateFilter] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [page, setPage] = useState<number>(1);
  const limit = 20;

  const loadApplicants = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const res = await fetchJobApplicants(jobId, {
        status: statusFilter !== "ALL" ? statusFilter : undefined,
        reviewState: reviewStateFilter !== "ALL" ? reviewStateFilter : undefined,
        page,
        limit,
      });
      setApplications(res.items || []);
      setTotal(res.total || 0);
      setActiveCount(res.activeCount || 0);
      setWithdrawnCount(res.withdrawnCount || 0);
    } catch (err: any) {
      setError(err?.message || "Failed to load applicants for this job.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadApplicants();
  }, [jobId, statusFilter, reviewStateFilter, page]);

  const filteredApplications = applications.filter((app) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      app.candidateName.toLowerCase().includes(q) ||
      (app.candidateEmail && app.candidateEmail.toLowerCase().includes(q))
    );
  });

  const getInitials = (name?: string | null) => {
    if (!name || !name.trim()) return "??";
    const parts = name.trim().split(/\s+/);
    if (parts.length === 1) return parts[0].substring(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  };

  const formatDate = (isoString?: string | null) => {
    if (!isoString) return "—";
    try {
      return new Date(isoString).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
    } catch {
      return isoString;
    }
  };

  const hasActiveFilters = searchQuery.trim() !== "" || statusFilter !== "ALL" || reviewStateFilter !== "ALL";

  const clearFilters = () => {
    setSearchQuery("");
    setStatusFilter("ALL");
    setReviewStateFilter("ALL");
    setPage(1);
  };

  return (
    <div className="job-applicants-page">
      {/* Top navigation back link */}
      {onNavigateBack && (
        <div className="job-applicants-top-nav">
          <button
            type="button"
            onClick={onNavigateBack}
            className="job-editor-back-nav"
            aria-label="Back to Job Details"
          >
            <Icon name="arrow-left" size={14} />
            <span>Back to Job Details</span>
          </button>
        </div>
      )}

      {/* Header bar */}
      <div className="job-applicants-header-row">
        <div className="job-applicants-title-group">
          <h1 className="job-applicants-title">
            Applicants {jobTitle ? `for ${jobTitle}` : ""}
          </h1>
          <p className="job-applicants-subtitle">
            Job-contextual applicant review and approved interview handoff.
          </p>
        </div>

        {/* Counts summary */}
        <div className="job-applicants-stats-row">
          <span
            className="badge badge-success"
            style={{
              padding: "4px 10px",
              fontSize: "12px",
              fontWeight: 600,
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
            }}
          >
            <Icon name="users" size={12} />
            <span>Active: {activeCount}</span>
          </span>
          <span
            className="badge badge-secondary"
            style={{
              padding: "4px 10px",
              fontSize: "12px",
              fontWeight: 500,
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
            }}
          >
            <span>Withdrawn: {withdrawnCount}</span>
          </span>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="job-applicants-filter-card">
        <div className="job-applicants-search-box">
          <Icon name="search" size={14} style={{ color: "var(--text-muted, #94a3b8)", flexShrink: 0 }} />
          <input
            type="text"
            placeholder="Search applicant name or email..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="job-applicants-search-input"
            aria-label="Search applicants"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery("")}
              style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-muted, #94a3b8)", padding: 0 }}
              aria-label="Clear search query"
            >
              <Icon name="x" size={12} />
            </button>
          )}
        </div>

        <div className="job-applicants-filter-group">
          <label className="job-applicants-filter-label" htmlFor="filter-status-select">Status:</label>
          <select
            id="filter-status-select"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(1);
            }}
            className="job-applicants-select"
          >
            <option value="ALL">All Applications</option>
            <option value="SUBMITTED">Active (Submitted)</option>
            <option value="WITHDRAWN">Withdrawn</option>
          </select>
        </div>

        <div className="job-applicants-filter-group">
          <label className="job-applicants-filter-label" htmlFor="filter-review-select">Review Stage:</label>
          <select
            id="filter-review-select"
            value={reviewStateFilter}
            onChange={(e) => {
              setReviewStateFilter(e.target.value);
              setPage(1);
            }}
            className="job-applicants-select"
          >
            <option value="ALL">All Stages</option>
            <option value="RECEIVED">Received</option>
            <option value="IN_REVIEW">In Review</option>
            <option value="INTERVIEW_READY">Interview Ready</option>
          </select>
        </div>

        {hasActiveFilters && (
          <button
            type="button"
            onClick={clearFilters}
            className="btn btn-secondary btn-xs"
            style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}
          >
            <Icon name="x" size={12} />
            <span>Reset filters</span>
          </button>
        )}
      </div>

      {error && (
        <div className="feedback-box error" role="alert" style={{ display: "flex", alignItems: "center", gap: "8px", padding: "12px 16px" }}>
          <Icon name="alert-triangle" size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* Applicant Roster Table Card */}
      {isLoading ? (
        <div className="job-applicants-table-card" style={{ padding: "48px 24px", textAlign: "center" }}>
          <div className="loading-spinner" style={{ margin: "0 auto 12px" }} />
          <p style={{ color: "var(--text-secondary, #64748b)", fontSize: "14px", margin: 0 }}>
            Loading applicants for this job...
          </p>
        </div>
      ) : filteredApplications.length === 0 ? (
        <div className="job-applicants-table-card" style={{ padding: "48px 24px", textAlign: "center" }}>
          <div
            style={{
              width: "48px",
              height: "48px",
              borderRadius: "50%",
              backgroundColor: "#f1f5f9",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto 16px",
              color: "#94a3b8",
            }}
          >
            <Icon name="users" size={24} />
          </div>
          <h3 style={{ fontSize: "16px", fontWeight: 700, margin: "0 0 6px", color: "var(--text-primary, #0f172a)" }}>
            No applicants found
          </h3>
          <p style={{ fontSize: "13px", color: "var(--text-secondary, #64748b)", maxWidth: "440px", margin: "0 auto 16px", lineHeight: 1.5 }}>
            {hasActiveFilters
              ? "No applications match your selected filter criteria. Try adjusting or clearing your filters."
              : "No candidates have submitted applications for this job requisition yet."}
          </p>
          {hasActiveFilters && (
            <button type="button" onClick={clearFilters} className="btn btn-secondary btn-sm">
              Clear all filters
            </button>
          )}
        </div>
      ) : (
        <div className="job-applicants-table-card">
          <div style={{ overflowX: "auto" }}>
            <table className="job-applicants-table">
              <thead>
                <tr>
                  <th style={{ minWidth: "220px" }}>Applicant</th>
                  <th style={{ minWidth: "120px" }}>Submitted</th>
                  <th style={{ minWidth: "110px" }}>Status</th>
                  <th style={{ minWidth: "140px" }}>Review Stage</th>
                  <th style={{ minWidth: "160px" }}>Deterministic Match</th>
                  <th style={{ minWidth: "130px" }}>Interview</th>
                  <th style={{ minWidth: "140px", textAlign: "right" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredApplications.map((app) => {
                  const initials = getInitials(app.candidateName);
                  const isSubmitted = app.status === "SUBMITTED";
                  const isInterviewReady = app.recruiterReviewState === "INTERVIEW_READY";
                  const isReleased = app.interviewStatus === "RELEASED" || Boolean(app.releaseId);
                  const isMatchEvaluated =
                    (app.matchStatus === "Evaluated" || app.matchStatus === "MATCHED") &&
                    app.matchScore !== null &&
                    app.matchScore !== undefined;

                  return (
                    <tr key={app.applicationId}>
                      <td>
                        <div className="applicant-identity-cell">
                          <div className="applicant-avatar-circle" aria-hidden="true">
                            {initials}
                          </div>
                          <div>
                            <div className="applicant-name-primary">{app.candidateName}</div>
                            <div className="applicant-email-subtle">{app.candidateEmail || "No email provided"}</div>
                          </div>
                        </div>
                      </td>
                      <td style={{ color: "var(--text-secondary, #475569)", whiteSpace: "nowrap" }}>
                        {formatDate(app.submittedAt)}
                      </td>
                      <td>
                        <span
                          className={`badge ${isSubmitted ? "badge-success" : "badge-secondary"}`}
                          style={{
                            fontSize: "11px",
                            padding: "2px 8px",
                            borderRadius: "4px",
                            fontWeight: 500,
                            display: "inline-flex",
                            alignItems: "center",
                            gap: "4px",
                          }}
                        >
                          {isSubmitted && <Icon name="check" size={10} />}
                          <span>{isSubmitted ? "Submitted" : app.status || "Withdrawn"}</span>
                        </span>
                      </td>
                      <td>
                        <span
                          className={`badge ${
                            isInterviewReady
                              ? "badge-success"
                              : app.recruiterReviewState === "IN_REVIEW"
                              ? "badge-info"
                              : "badge-secondary"
                          }`}
                          style={{
                            fontSize: "11px",
                            padding: "2px 8px",
                            borderRadius: "4px",
                            fontWeight: 500,
                            display: "inline-flex",
                            alignItems: "center",
                            gap: "4px",
                          }}
                        >
                          {isInterviewReady ? (
                            <Icon name="badge-check" size={11} />
                          ) : app.recruiterReviewState === "IN_REVIEW" ? (
                            <Icon name="clock" size={11} />
                          ) : (
                            <Icon name="file-text" size={11} />
                          )}
                          <span>
                            {app.recruiterReviewState === "INTERVIEW_READY"
                              ? "Interview Ready"
                              : app.recruiterReviewState === "IN_REVIEW"
                              ? "In Review"
                              : "Received"}
                          </span>
                        </span>
                      </td>
                      <td>
                        {isMatchEvaluated ? (
                          <div className="match-score-badge">
                            <span>{app.matchScore!.toFixed(1)}%</span>
                            {app.confidenceTier && <span className="tier-tag">{app.confidenceTier}</span>}
                          </div>
                        ) : (
                          <span style={{ fontSize: "12px", color: "var(--text-muted, #94a3b8)", fontStyle: "italic" }}>
                            Not evaluated
                          </span>
                        )}
                      </td>
                      <td>
                        {isReleased ? (
                          <span
                            className="badge badge-success"
                            style={{
                              fontSize: "11px",
                              padding: "2px 8px",
                              borderRadius: "4px",
                              fontWeight: 500,
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "4px",
                            }}
                          >
                            <Icon name="lock" size={11} />
                            <span>Released</span>
                          </span>
                        ) : (
                          <span
                            className="badge badge-secondary"
                            style={{
                              fontSize: "11px",
                              padding: "2px 8px",
                              borderRadius: "4px",
                              fontWeight: 500,
                              color: "var(--text-muted, #64748b)",
                            }}
                          >
                            Not released
                          </span>
                        )}
                      </td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        <button
                          type="button"
                          onClick={() => onSelectApplication(app.applicationId)}
                          className="btn btn-secondary btn-sm"
                          style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                        >
                          <Icon name="file-text" size={13} />
                          <span>View Application</span>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
