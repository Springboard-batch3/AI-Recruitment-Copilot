import React, { useState } from "react";
import { Icon } from "./Icon";
import { AtsImportModal } from "./ats/AtsImportModal";

export type CandidateSummary = {
  artifactId: string;
  originalFilename: string;
  detectedFormat: string | null;
  validationStatus: string;
  createdAt: string;
  hasExtraction: boolean;
  latestProfileVersion: number | null;
  latestProfileId?: string | null;
  fullName: string | null;
  headline: string | null;
  reviewRequired: boolean;
  provenance: string | null;
};

interface CandidatesDirectoryProps {
  candidates: CandidateSummary[];
  isLoading: boolean;
  onSelectCandidate: (artifactId: string) => void;
  onNavigateUpload: () => void;
  onRefreshCandidates?: () => void;
}

export function CandidatesDirectory({
  candidates,
  isLoading,
  onSelectCandidate,
  onNavigateUpload,
  onRefreshCandidates,
}: CandidatesDirectoryProps) {
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [isImportModalOpen, setIsImportModalOpen] = useState<boolean>(false);

  const filteredCandidates = candidates.filter((c) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    const nameMatch = c.fullName?.toLowerCase().includes(q);
    const headlineMatch = c.headline?.toLowerCase().includes(q);
    const filenameMatch = c.originalFilename.toLowerCase().includes(q);
    return Boolean(nameMatch || headlineMatch || filenameMatch);
  });

  const getInitials = (name?: string | null) => {
    if (!name || !name.trim()) return "??";
    const parts = name.trim().split(/\s+/);
    if (parts.length === 1) return parts[0].substring(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  };

  const formatDate = (isoString: string) => {
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

  return (
    <div className="candidates-directory-container">
      {/* Directory Header Bar */}
      <div className="directory-header-row">
        <div>
          <h2 className="directory-title">Candidates</h2>
          <p className="directory-subtitle">
            Processed candidate profiles extracted from verified resume documents.
          </p>
        </div>

        <div style={{ display: "flex", gap: "10px" }}>
          <button
            type="button"
            onClick={() => setIsImportModalOpen(true)}
            className="btn btn-secondary btn-md"
          >
            <Icon name="external-link" size={15} /> Import from ATS
          </button>
          <button
            type="button"
            onClick={onNavigateUpload}
            className="btn btn-primary btn-md"
          >
            <Icon name="upload" size={15} /> Upload Resume
          </button>
        </div>
      </div>

      <AtsImportModal
        isOpen={isImportModalOpen}
        onClose={() => setIsImportModalOpen(false)}
        onImportComplete={() => {
          setIsImportModalOpen(false);
          if (onRefreshCandidates) {
            onRefreshCandidates();
          }
        }}
      />

      {/* Search Bar & Filters */}
      <div className="directory-toolbar">
        <div className="search-input-wrapper">
          <Icon name="search" size={16} className="search-icon" />
          <input
            type="text"
            className="search-input"
            placeholder="Search candidates by name, headline, or filename..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="Search candidates"
          />
          {searchQuery && (
            <button
              type="button"
              className="search-clear-btn"
              onClick={() => setSearchQuery("")}
              aria-label="Clear search"
            >
              <Icon name="x" size={14} />
            </button>
          )}
        </div>

        <div className="directory-count-badge">
          {filteredCandidates.length} candidate{filteredCandidates.length === 1 ? "" : "s"}
        </div>
      </div>

      {/* Directory Table / Cards */}
      {isLoading ? (
        <div className="loading-state-card">
          <div className="loading-spinner" />
          <p className="loading-text">Loading candidate records...</p>
        </div>
      ) : candidates.length === 0 ? (
        <div className="empty-state-card">
          <div className="empty-icon-circle">
            <Icon name="user" size={32} />
          </div>
          <h3 className="empty-title">No candidate profiles yet</h3>
          <p className="empty-description">
            Upload your first text-based PDF or native DOCX resume, or scan resumes from the Local Folder ATS demo.
          </p>
          <div style={{ display: "flex", gap: "10px", justifyContent: "center" }}>
            <button
              type="button"
              onClick={() => setIsImportModalOpen(true)}
              className="btn btn-secondary btn-md"
            >
              <Icon name="external-link" size={15} /> Import from ATS
            </button>
            <button
              type="button"
              onClick={onNavigateUpload}
              className="btn btn-primary btn-md"
            >
              <Icon name="upload" size={15} /> Upload Resume
            </button>
          </div>
        </div>
      ) : filteredCandidates.length === 0 ? (
        <div className="empty-state-card">
          <Icon name="search" size={28} className="text-muted" />
          <h3 className="empty-title">No candidates match your search</h3>
          <p className="empty-description">
            Try adjusting your search terms or clearing the filter.
          </p>
          <button
            type="button"
            onClick={() => setSearchQuery("")}
            className="btn btn-secondary btn-sm"
          >
            Clear Search
          </button>
        </div>
      ) : (
        <div className="candidates-grid">
          {filteredCandidates.map((c) => {
            const isHumanCorrected = c.provenance === "HUMAN_CORRECTED";
            const isLocalAtsDemo =
              c.provenance === "Local ATS Demo" ||
              c.provenance === "Local Folder ATS Demo" ||
              (c.provenance && (c.provenance.includes("Local") || c.provenance.includes("ATS")));
            const initials = getInitials(c.fullName);

            return (
              <div
                key={c.artifactId}
                className="candidate-card"
                onClick={() => onSelectCandidate(c.artifactId)}
                tabIndex={0}
                role="button"
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectCandidate(c.artifactId);
                  }
                }}
              >
                <div className="candidate-card-header">
                  <div className="candidate-avatar">
                    <span>{initials}</span>
                  </div>

                  <div className="candidate-header-info">
                    <h3 className="candidate-name">
                      {c.fullName || "Candidate Document"}
                    </h3>
                    <p className="candidate-headline">
                      {c.headline || c.originalFilename}
                    </p>
                  </div>
                </div>

                <div className="candidate-card-meta">
                  <div className="meta-tag-group">
                    {c.latestProfileVersion && (
                      <span className="badge badge-version">v{c.latestProfileVersion}</span>
                    )}

                    {isLocalAtsDemo ? (
                      <span className="badge badge-provenance-parser" style={{ backgroundColor: "#f0fdf4", color: "#166534" }}>
                        Source: Local Folder ATS Demo
                      </span>
                    ) : isHumanCorrected ? (
                      <span className="badge badge-provenance-corrected">Corrected</span>
                    ) : (
                      <span className="badge badge-provenance-parser">Parser</span>
                    )}
                  </div>

                  <span className="candidate-date">{formatDate(c.createdAt)}</span>
                </div>

                <div className="candidate-card-footer">
                  <span className="candidate-filename text-truncate">
                    <Icon name="file-text" size={13} /> {c.originalFilename}
                  </span>

                  <span className="candidate-open-action">
                    Open Profile <Icon name="chevron-right" size={14} />
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
