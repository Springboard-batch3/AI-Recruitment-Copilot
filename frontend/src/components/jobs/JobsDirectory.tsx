import React, { useState } from "react";
import { JobSummary, deleteJob } from "../../api/jobs";
import { Icon } from "../Icon";

interface JobsDirectoryProps {
  jobs: JobSummary[];
  isLoading: boolean;
  error?: string | null;
  onSelectJob: (jobId: string) => void;
  onCreateJob: () => void;
  onRefresh: () => void;
}

export type JobLifecycleTab = "ALL" | "NEEDS_REQUIREMENTS" | "DRAFT" | "APPROVED";

export function JobsDirectory({
  jobs = [],
  isLoading,
  error,
  onSelectJob,
  onCreateJob,
  onRefresh,
}: JobsDirectoryProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<JobLifecycleTab>("ALL");
  const [openMenuJobId, setOpenMenuJobId] = useState<string | null>(null);
  const [jobToDelete, setJobToDelete] = useState<JobSummary | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const safeJobs = Array.isArray(jobs) ? jobs : [];

  const getDerivedState = (versionStatus: string, reqCount: number): "NEEDS_REQUIREMENTS" | "DRAFT" | "APPROVED" => {
    if (reqCount === 0) {
      return "NEEDS_REQUIREMENTS";
    }
    if (versionStatus === "READY_FOR_MATCHING" || versionStatus === "APPROVED") {
      return "APPROVED";
    }
    return "DRAFT";
  };

  const filteredJobs = safeJobs.filter((j) => {
    const v = j.currentVersion;
    const reqCount = v.requirementsCount || 0;
    const derivedState = getDerivedState(v.versionStatus, reqCount);

    if (statusFilter !== "ALL" && derivedState !== statusFilter) {
      return false;
    }

    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase().trim();
    const titleMatch = v.title.toLowerCase().includes(q);
    const deptMatch = v.department?.toLowerCase().includes(q);
    const locMatch = v.location?.toLowerCase().includes(q);
    const refMatch = v.internalReference?.toLowerCase().includes(q);
    return Boolean(titleMatch || deptMatch || locMatch || refMatch);
  });

  const formatDate = (isoString?: string) => {
    if (!isoString) return "";
    try {
      const d = new Date(isoString);
      return d.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
    } catch {
      return isoString;
    }
  };

  const getWorkplaceLabel = (type?: string | null) => {
    if (!type) return null;
    switch (type) {
      case "ON_SITE":
        return "On-site";
      case "HYBRID":
        return "Hybrid";
      case "REMOTE":
        return "Remote";
      default:
        return type.replace(/_/g, " ");
    }
  };

  const getContextualAction = (derivedState: "NEEDS_REQUIREMENTS" | "DRAFT" | "APPROVED") => {
    switch (derivedState) {
      case "NEEDS_REQUIREMENTS":
        return "Add requirements";
      case "DRAFT":
        return "Continue editing";
      case "APPROVED":
        return "View job";
    }
  };

  const handleDeleteJob = async () => {
    if (!jobToDelete) return;
    setIsDeleting(true);
    setDeleteError(null);
    try {
      await deleteJob(jobToDelete.jobId);
      setJobToDelete(null);
      setOpenMenuJobId(null);
      onRefresh();
    } catch (err: any) {
      setDeleteError(err?.message || "Failed to delete job requisition.");
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="jobs-directory-container" role="region" aria-label="Jobs Directory" onClick={() => setOpenMenuJobId(null)}>
      {/* Page Header Bar */}
      <div className="directory-header-row">
        <div>
          <h1 className="directory-title">Job requisitions</h1>
          <p className="directory-subtitle">
            Manage structured job requisitions, versioned drafts, and defensible criteria.
          </p>
        </div>

        <button
          type="button"
          onClick={onCreateJob}
          className="btn btn-primary btn-md"
        >
          <Icon name="plus" size={15} />
          <span>Create job</span>
        </button>
      </div>

      {/* Toolbar / Search, 4 Lifecycle Tabs & Live Count */}
      <div className="directory-toolbar">
        <div className="search-input-wrapper">
          <Icon name="search" size={16} className="search-icon" />
          <input
            type="text"
            className="search-input"
            placeholder="Title, department, location or reference"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="Search jobs"
          />
          {searchQuery && (
            <button
              type="button"
              className="search-clear-btn"
              onClick={() => setSearchQuery("")}
              aria-label="Clear job search"
            >
              <Icon name="x" size={14} />
            </button>
          )}
        </div>

        <div className="status-filter-group" role="tablist" aria-label="Filter by status">
          <button
            type="button"
            role="tab"
            aria-selected={statusFilter === "ALL"}
            className={`status-filter-btn ${statusFilter === "ALL" ? "active" : ""}`}
            onClick={() => setStatusFilter("ALL")}
          >
            All
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={statusFilter === "NEEDS_REQUIREMENTS"}
            className={`status-filter-btn ${statusFilter === "NEEDS_REQUIREMENTS" ? "active" : ""}`}
            onClick={() => setStatusFilter("NEEDS_REQUIREMENTS")}
          >
            Needs requirements
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={statusFilter === "DRAFT"}
            className={`status-filter-btn ${statusFilter === "DRAFT" ? "active" : ""}`}
            onClick={() => setStatusFilter("DRAFT")}
          >
            Draft
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={statusFilter === "APPROVED"}
            className={`status-filter-btn ${statusFilter === "APPROVED" ? "active" : ""}`}
            onClick={() => setStatusFilter("APPROVED")}
          >
            Approved
          </button>
        </div>

        <div className="directory-count-badge" aria-live="polite">
          {filteredJobs.length} {filteredJobs.length === 1 ? "job" : "jobs"}
        </div>
      </div>

      {/* Feedback / Error State */}
      {error && (
        <div className="feedback-box error" role="alert" style={{ marginBottom: "16px" }}>
          <Icon name="alert-triangle" size={16} />
          <p className="feedback-text error">{error}</p>
          <button
            type="button"
            onClick={onRefresh}
            className="btn btn-secondary btn-sm"
            style={{ marginLeft: "auto" }}
          >
            Retry
          </button>
        </div>
      )}

      {/* Loading Skeleton Rows */}
      {isLoading ? (
        <div className="jobs-list-surface" aria-busy="true" aria-label="Loading jobs">
          <div className="job-row-skeleton" />
          <div className="job-row-skeleton" />
          <div className="job-row-skeleton" />
        </div>
      ) : safeJobs.length === 0 ? (
        /* Empty State: No jobs created yet */
        <div className="empty-state-card">
          <div className="empty-icon-circle">
            <Icon name="briefcase" size={32} />
          </div>
          <h2 className="empty-title">No job requisitions created yet</h2>
          <p className="empty-description">
            Create your first job requisition to define structured technical requirements, qualifications, and evidence criteria.
          </p>
          <button
            type="button"
            onClick={onCreateJob}
            className="btn btn-primary btn-md"
          >
            <Icon name="plus" size={15} />
            <span>Create job</span>
          </button>
        </div>
      ) : filteredJobs.length === 0 ? (
        /* Empty State: No results for search */
        <div className="empty-state-card">
          <Icon name="search" size={28} className="text-muted" />
          <h2 className="empty-title">No jobs match your search</h2>
          <p className="empty-description">Try adjusting your search terms or clearing the filter.</p>
          <button
            type="button"
            onClick={() => {
              setSearchQuery("");
              setStatusFilter("ALL");
            }}
            className="btn btn-secondary btn-sm"
          >
            Clear filters
          </button>
        </div>
      ) : (
        /* Clean Two-Line Semantic List Surface */
        <div className="jobs-list-surface" role="list" aria-label="Job requisitions list">
          {filteredJobs.map((j) => {
            const v = j.currentVersion;
            const reqCount = v.requirementsCount || 0;
            const derivedState = getDerivedState(v.versionStatus, reqCount);
            const isApproved = derivedState === "APPROVED";
            const actionLabel = getContextualAction(derivedState);

            // Line 1 metadata items
            const metaTokens = [v.department, v.location, getWorkplaceLabel(v.workplaceType)].filter(Boolean);

            return (
              <div
                key={j.jobId}
                className={`job-list-row ${isApproved ? "status-approved" : "status-draft"}`}
                role="listitem"
                tabIndex={0}
                onClick={() => onSelectJob(j.jobId)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectJob(j.jobId);
                  }
                }}
              >
                {/* Line 1: Title, Meta Tokens & Status Indicator */}
                <div className="job-row-line-1">
                  <div className="job-title-group" style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                    <span className="job-row-title">{v.title}</span>

                    {metaTokens.length > 0 && (
                      <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
                        ({metaTokens.join(" · ")})
                      </span>
                    )}

                    <span className="job-row-status-summary">
                      {derivedState === "NEEDS_REQUIREMENTS" ? (
                        <span className="status-needs-reqs" title="Draft requisition has no requirements">
                          <Icon name="alert-triangle" size={13} aria-hidden="true" />
                          <span>Needs requirements</span>
                        </span>
                      ) : derivedState === "APPROVED" ? (
                        <span className="status-summary-text text-approved">
                          <span className="status-summary-dot dot-approved" aria-hidden="true" />
                          <span>Approved · {reqCount} {reqCount === 1 ? "requirement" : "requirements"}</span>
                        </span>
                      ) : (
                        <span className="status-summary-text text-draft">
                          <span className="status-summary-dot dot-draft" aria-hidden="true" />
                          <span>Draft · {reqCount} {reqCount === 1 ? "requirement" : "requirements"}</span>
                        </span>
                      )}
                    </span>

                    {/* Subtle Release Status Indicator */}
                    {j.publicationStatus === "RELEASED" ? (
                      <span className="job-release-indicator released" title="Released to candidates: Live for discovery & applications">
                        <span className="job-release-dot released" aria-hidden="true" />
                        <span>Released</span>
                      </span>
                    ) : (
                      <span className="job-release-indicator unreleased" title="Not released to candidates: Hidden from candidate portal">
                        <span className="job-release-dot unreleased" aria-hidden="true" />
                        <span>Not released</span>
                      </span>
                    )}
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }} onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      className="job-quiet-action-btn"
                      onClick={() => onSelectJob(j.jobId)}
                      aria-label={`${actionLabel} ${v.title}`}
                    >
                      <span>{actionLabel}</span>
                      <span className="action-arrow" aria-hidden="true">→</span>
                    </button>

                    {/* 3-Dot Overflow Menu */}
                    <div style={{ position: "relative" }}>
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        style={{ padding: "4px 8px", fontSize: "14px", lineHeight: "1" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenMenuJobId(openMenuJobId === j.jobId ? null : j.jobId);
                        }}
                        aria-label="Job options"
                        aria-expanded={openMenuJobId === j.jobId}
                      >
                        ···
                      </button>

                      {openMenuJobId === j.jobId && (
                        <div
                          style={{
                            position: "absolute",
                            right: 0,
                            top: "100%",
                            marginTop: "4px",
                            backgroundColor: "var(--bg-surface)",
                            border: "1px solid var(--border-subtle)",
                            borderRadius: "var(--radius-md)",
                            boxShadow: "var(--shadow-md)",
                            zIndex: 20,
                            minWidth: "140px",
                            overflow: "hidden",
                          }}
                        >
                          <button
                            type="button"
                            style={{
                              width: "100%",
                              padding: "8px 12px",
                              textAlign: "left",
                              background: "none",
                              border: "none",
                              fontSize: "13px",
                              color: "var(--status-danger)",
                              cursor: "pointer",
                              display: "flex",
                              alignItems: "center",
                              gap: "6px",
                            }}
                            onClick={(e) => {
                              e.stopPropagation();
                              setJobToDelete(j);
                              setOpenMenuJobId(null);
                            }}
                          >
                            <Icon name="trash" size={13} />
                            <span>Delete job</span>
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {/* Line 2: Reference, Version & Revision, Updated Date */}
                <div className="job-row-line-2">
                  {v.internalReference && (
                    <>
                      <span className="meta-ref">{v.internalReference}</span>
                      <span className="meta-sep" aria-hidden="true">·</span>
                    </>
                  )}

                  <span className="meta-version">
                    Version {v.versionNumber} · Revision {v.revision}
                  </span>

                  <span className="meta-sep" aria-hidden="true">·</span>

                  <span className="meta-updated">
                    Updated {formatDate(v.updatedAt)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Soft-Delete Confirmation Modal */}
      {jobToDelete && (
        <div
          className="modal-backdrop"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(15, 23, 42, 0.6)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "16px",
          }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-job-modal-title"
          onClick={(e) => e.stopPropagation()}
        >
          <div
            className="modal-surface"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderRadius: "var(--radius-lg)",
              maxWidth: "460px",
              width: "100%",
              padding: "24px",
              boxShadow: "var(--shadow-lg)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
              <div
                style={{
                  width: "36px",
                  height: "36px",
                  borderRadius: "50%",
                  backgroundColor: "var(--status-danger-bg)",
                  border: "1px solid var(--status-danger-border)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "var(--status-danger)",
                }}
              >
                <Icon name="alert-triangle" size={18} />
              </div>
              <h3 id="delete-job-modal-title" style={{ fontSize: "16px", fontWeight: 600, margin: 0, color: "var(--text-primary)" }}>
                Delete job?
              </h3>
            </div>

            <p style={{ fontSize: "14px", color: "var(--text-secondary)", margin: "0 0 16px 0", lineHeight: "1.5" }}>
              <strong>{jobToDelete.currentVersion?.title || "This job"}</strong> will be removed from active workflows. Historical records will be preserved.
            </p>

            {deleteError && (
              <div className="feedback-box error" role="alert" style={{ marginBottom: "16px" }}>
                <Icon name="alert-triangle" size={16} />
                <p className="feedback-text error">{deleteError}</p>
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button
                type="button"
                className="btn btn-secondary btn-md"
                onClick={() => {
                  setJobToDelete(null);
                  setDeleteError(null);
                }}
                disabled={isDeleting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-md"
                style={{
                  backgroundColor: "var(--status-danger)",
                  color: "#ffffff",
                  border: "none",
                  fontWeight: 600,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "6px",
                }}
                onClick={handleDeleteJob}
                disabled={isDeleting}
              >
                <Icon name="trash" size={14} />
                <span>{isDeleting ? "Deleting..." : "Delete job"}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
