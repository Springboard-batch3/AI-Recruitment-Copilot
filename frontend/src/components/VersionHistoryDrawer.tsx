import React, { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";

export type ProfileSummary = {
  profileId: string;
  profileVersion: number;
  schemaVersion: string;
  parserPipelineVersion: string;
  provenance: string;
  reviewRequired: boolean;
  createdAt: string;
  parentProfileId?: string | null;
  parentProfileVersion?: number | null;
  correctionSummary?: {
    correctionId: string;
    reasonCategory: string;
    reviewerNote?: string | null;
    operationCount: number;
  } | null;
};

interface VersionHistoryDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  historyList: ProfileSummary[];
  currentVersion: number;
  onSelectVersion: (version: number) => void;
}

export function VersionHistoryDrawer({
  isOpen,
  onClose,
  historyList,
  currentVersion,
  onSelectVersion,
}: VersionHistoryDrawerProps) {
  const [expandedTechVersion, setExpandedTechVersion] = useState<number | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    closeBtnRef.current?.focus();

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const formatDate = (isoString: string) => {
    try {
      const d = new Date(isoString);
      return d.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return isoString;
    }
  };

  const getReasonLabel = (category: string) => {
    switch (category) {
      case "INCORRECT_EXTRACTION":
        return "Incorrect Extraction";
      case "MISSING_INFORMATION":
        return "Missing Information";
      case "WRONG_RECORD_ASSOCIATION":
        return "Wrong Record Association";
      case "DUPLICATE_RECORD":
        return "Duplicate Record";
      case "INCORRECT_NORMALIZATION":
        return "Incorrect Normalization";
      case "INCORRECT_DATE":
        return "Incorrect Date";
      case "OTHER":
        return "Manual Reviewer Correction";
      default:
        return category.replace(/_/g, " ");
    }
  };

  // Sort descending by profileVersion (latest first)
  const sortedHistory = [...historyList].sort((a, b) => b.profileVersion - a.profileVersion);

  return (
    <div className="drawer-backdrop" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="history-drawer-title">
      <div className="drawer-panel drawer-panel-wide" onClick={(e) => e.stopPropagation()} tabIndex={-1}>
        {/* Drawer Header */}
        <div className="drawer-header">
          <div className="drawer-title-group">
            <div className="drawer-badge-row">
              <span className="badge badge-indigo">
                <Icon name="history" size={13} className="badge-icon" />
                Audit Trail
              </span>
              <span className="badge badge-neutral">{historyList.length} Version(s)</span>
            </div>
            <h3 id="history-drawer-title" className="drawer-title">
              Profile Version Lineage
            </h3>
          </div>

          <button
            type="button"
            ref={closeBtnRef}
            onClick={onClose}
            className="btn-icon drawer-close-btn"
            aria-label="Close version history"
          >
            <Icon name="x" size={18} />
          </button>
        </div>

        {/* Drawer Body */}
        <div className="drawer-body">
          <p className="drawer-intro-text">
            Every candidate profile modification generates an immutable, audited version. You can inspect any past profile snapshot in read-only mode.
          </p>

          <div className="history-timeline">
            {sortedHistory.map((item, idx) => {
              const isSelected = item.profileVersion === currentVersion;
              const isCorrected = item.provenance === "HUMAN_CORRECTED";
              const isTechOpen = expandedTechVersion === item.profileVersion;

              return (
                <div
                  key={item.profileVersion}
                  className={`history-entry ${isSelected ? "history-entry-active" : ""}`}
                >
                  <div className="history-bullet-col">
                    <div className={`history-bullet ${isSelected ? "bullet-active" : ""}`} />
                    {idx !== sortedHistory.length - 1 && <div className="history-line" />}
                  </div>

                  <div className="history-entry-content">
                    <div className="history-entry-header">
                      <div className="history-version-row">
                        <span className="history-version-pill">Version {item.profileVersion}</span>
                        <span
                          className={`badge ${
                            isCorrected ? "badge-provenance-corrected" : "badge-provenance-parser"
                          }`}
                        >
                          {isCorrected ? "Human Corrected" : "Parser Generated"}
                        </span>
                        {isSelected && <span className="badge badge-active-indicator">Viewing</span>}
                      </div>
                      <span className="history-date-text">{formatDate(item.createdAt)}</span>
                    </div>

                    {/* Correction details */}
                    {item.correctionSummary ? (
                      <div className="history-audit-box">
                        <div className="audit-detail-row">
                          <span className="audit-label">Reason:</span>
                          <span className="audit-value font-medium">
                            {getReasonLabel(item.correctionSummary.reasonCategory)}
                          </span>
                        </div>
                        {item.correctionSummary.reviewerNote && (
                          <div className="audit-detail-row">
                            <span className="audit-label">Note:</span>
                            <span className="audit-value italic">
                              &ldquo;{item.correctionSummary.reviewerNote}&rdquo;
                            </span>
                          </div>
                        )}
                        <div className="audit-detail-row">
                          <span className="audit-label">Modifications:</span>
                          <span className="audit-value">
                            {item.correctionSummary.operationCount} field operation(s) applied
                          </span>
                        </div>
                      </div>
                    ) : (
                      <div className="history-audit-box history-audit-box-initial">
                        <span className="audit-initial-text">Initial structured profile assembled from resume document.</span>
                      </div>
                    )}

                    {/* Action Row */}
                    <div className="history-action-row">
                      {!isSelected ? (
                        <button
                          type="button"
                          onClick={() => {
                            onSelectVersion(item.profileVersion);
                            onClose();
                          }}
                          className="btn btn-secondary btn-xs"
                        >
                          <Icon name="eye" size={13} /> View Snapshot v{item.profileVersion}
                        </button>
                      ) : (
                        <span className="history-viewing-label">Active Version</span>
                      )}

                      <button
                        type="button"
                        onClick={() =>
                          setExpandedTechVersion((prev) =>
                            prev === item.profileVersion ? null : item.profileVersion
                          )
                        }
                        className="btn-tech-link"
                        aria-expanded={isTechOpen}
                      >
                        <Icon name={isTechOpen ? "chevron-down" : "chevron-right"} size={12} />
                        Technical Audit Details
                      </button>
                    </div>

                    {/* Technical Audit Panel */}
                    {isTechOpen && (
                      <div className="tech-details-panel" style={{ marginTop: "10px" }}>
                        <div className="tech-row">
                          <span className="tech-key">Profile ID:</span>
                          <span className="tech-val mono">{item.profileId}</span>
                        </div>
                        {item.parentProfileVersion && (
                          <div className="tech-row">
                            <span className="tech-key">Parent Version:</span>
                            <span className="tech-val mono">v{item.parentProfileVersion} ({item.parentProfileId})</span>
                          </div>
                        )}
                        <div className="tech-row">
                          <span className="tech-key">Parser Pipeline:</span>
                          <span className="tech-val mono">{item.parserPipelineVersion}</span>
                        </div>
                        <div className="tech-row">
                          <span className="tech-key">Schema Version:</span>
                          <span className="tech-val mono">v{item.schemaVersion}</span>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Drawer Footer */}
        <div className="drawer-footer">
          <button type="button" onClick={onClose} className="btn btn-secondary btn-sm">
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
