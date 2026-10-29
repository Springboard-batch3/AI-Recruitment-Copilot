import React, { useEffect, useState } from "react";
import { fetchCandidateJobs, CandidateJobSummary } from "../../api/candidateJobs";
import { Icon } from "../Icon";

interface CandidateOpenJobsProps {
  onSelectJob: (jobId: string, versionId?: string) => void;
}

export function CandidateOpenJobs({ onSelectJob }: CandidateOpenJobsProps) {
  const [jobs, setJobs] = useState<CandidateJobSummary[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [debouncedQuery, setDebouncedQuery] = useState<string>("");
  const [page, setPage] = useState<number>(1);
  const [total, setTotal] = useState<number>(0);
  const limit = 10;

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(searchQuery);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  const loadJobs = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const res = await fetchCandidateJobs({
        q: debouncedQuery.trim() || undefined,
        page,
        limit,
      });
      setJobs(res.items || []);
      setTotal(res.total || 0);
    } catch (err: any) {
      setError(err?.message || "Failed to load open job postings.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadJobs();
  }, [debouncedQuery, page]);

  const formatEmploymentType = (type?: string | null) => {
    if (!type) return null;
    return type.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  };

  const formatWorkplaceType = (type?: string | null) => {
    if (!type) return null;
    return type.replace(/_/g, "-").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  };

  return (
    <div className="candidate-jobs-container" style={{ maxWidth: "960px", margin: "0 auto", padding: "16px 20px" }}>
      {/* Header Section */}
      <div style={{ marginBottom: "24px" }}>
        <h1 style={{ fontSize: "24px", fontWeight: 700, color: "var(--text-primary, #0f172a)", margin: "0 0 8px 0" }}>
          Open Job Opportunities
        </h1>
        <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", margin: 0, lineHeight: 1.5 }}>
          Explore current open positions, review role requirements, and prepare for matching opportunities.
        </p>
      </div>

      {/* Search Bar */}
      <div style={{ display: "flex", gap: "12px", marginBottom: "24px" }}>
        <div style={{ position: "relative", flex: 1 }}>
          <input
            type="text"
            className="input-field"
            placeholder="Search by job title, department, location, or keywords..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              width: "100%",
              padding: "10px 14px 10px 38px",
              fontSize: "14px",
              borderRadius: "var(--radius-md, 8px)",
              border: "1px solid var(--border-medium, #cbd5e1)",
              backgroundColor: "var(--bg-surface, #ffffff)",
              color: "var(--text-primary, #0f172a)",
              boxSizing: "border-box",
            }}
            aria-label="Search open jobs"
          />
          <span style={{ position: "absolute", left: "12px", top: "50%", transform: "translateY(-50%)", color: "var(--text-muted, #94a3b8)", pointerEvents: "none" }}>
            <Icon name="search" size={16} aria-hidden="true" />
          </span>
        </div>

        {searchQuery && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setSearchQuery("")}
            title="Clear search query"
          >
            Clear
          </button>
        )}
      </div>

      {/* Error Banner */}
      {error && (
        <div className="feedback-box error" role="alert" style={{ marginBottom: "20px" }}>
          <Icon name="alert-circle" size={16} />
          <span>{error}</span>
          <button type="button" className="btn btn-sm btn-secondary" onClick={loadJobs} style={{ marginLeft: "auto" }}>
            Retry
          </button>
        </div>
      )}

      {/* Loading State */}
      {isLoading && (
        <div style={{ textAlign: "center", padding: "48px 0" }} role="status" aria-live="polite">
          <div className="loading-spinner" style={{ margin: "0 auto 16px" }} />
          <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", margin: 0 }}>
            Loading available job postings...
          </p>
        </div>
      )}

      {/* Empty State */}
      {!isLoading && !error && jobs.length === 0 && (
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
            {debouncedQuery ? "No matching jobs found" : "No open jobs available"}
          </h2>
          <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", margin: 0, maxWidth: "420px", marginLeft: "auto", marginRight: "auto" }}>
            {debouncedQuery
              ? `No job postings matched "${debouncedQuery}". Try refining your keywords.`
              : "Check back soon for new career opportunities matching your profile."}
          </p>
        </div>
      )}

      {/* Job Cards List */}
      {!isLoading && !error && jobs.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {jobs.map((job) => {
            const empType = formatEmploymentType(job.employmentType);
            const workType = formatWorkplaceType(job.workplaceType);

            return (
              <div
                key={job.jobId}
                style={{
                  backgroundColor: "var(--bg-surface, #ffffff)",
                  borderRadius: "var(--radius-lg, 12px)",
                  border: "1px solid var(--border-subtle, #e2e8f0)",
                  padding: "20px 24px",
                  boxShadow: "var(--shadow-card, 0 1px 3px rgba(16, 24, 40, 0.05))",
                  display: "flex",
                  flexDirection: "column",
                  gap: "12px",
                  transition: "border-color 0.15s ease",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px" }}>
                  <div>
                    <h2
                      style={{
                        fontSize: "18px",
                        fontWeight: 600,
                        color: "var(--text-primary, #0f172a)",
                        margin: "0 0 6px 0",
                      }}
                    >
                      {job.title}
                    </h2>

                    <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", alignItems: "center" }}>
                      {job.department && (
                        <span style={{ fontSize: "13px", color: "var(--text-secondary, #475569)", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                          <Icon name="folder" size={14} />
                          {job.department}
                        </span>
                      )}

                      {job.location && (
                        <span style={{ fontSize: "13px", color: "var(--text-secondary, #475569)", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                          <Icon name="map-pin" size={14} />
                          {job.location}
                        </span>
                      )}

                      {workType && (
                        <span
                          style={{
                            fontSize: "12px",
                            padding: "2px 8px",
                            borderRadius: "var(--radius-full, 9999px)",
                            backgroundColor: "var(--accent-subtle, #eef1fc)",
                            color: "var(--accent-primary)",
                            fontWeight: 500,
                          }}
                        >
                          {workType}
                        </span>
                      )}

                      {empType && (
                        <span
                          style={{
                            fontSize: "12px",
                            padding: "2px 8px",
                            borderRadius: "var(--radius-full, 9999px)",
                            backgroundColor: "var(--bg-surface-subtle, #f1f5f9)",
                            color: "var(--text-secondary, #475569)",
                            fontWeight: 500,
                          }}
                        >
                          {empType}
                        </span>
                      )}
                    </div>
                  </div>

                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onSelectJob(job.jobId, job.jobVersionId)}
                    style={{ whiteSpace: "nowrap", display: "inline-flex", alignItems: "center", gap: "6px" }}
                    aria-label={`View details for ${job.title}`}
                  >
                    <span>View Details</span>
                    <Icon name="chevron-right" size={14} />
                  </button>
                </div>

                {job.summary && (
                  <p
                    style={{
                      fontSize: "14px",
                      color: "var(--text-secondary, #475569)",
                      margin: 0,
                      lineHeight: 1.5,
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}
                  >
                    {job.summary}
                  </p>
                )}
              </div>
            );
          })}

          {/* Pagination Controls */}
          {total > limit && (
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "16px" }}>
              <span style={{ fontSize: "13px", color: "var(--text-muted, #64748b)" }}>
                Showing {(page - 1) * limit + 1}–{Math.min(page * limit, total)} of {total} postings
              </span>

              <div style={{ display: "flex", gap: "8px" }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  aria-label="Previous page"
                >
                  Previous
                </button>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  disabled={page * limit >= total}
                  onClick={() => setPage((p) => p + 1)}
                  aria-label="Next page"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
