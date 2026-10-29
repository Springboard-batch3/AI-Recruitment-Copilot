import React, { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";

export type EvidenceSpan = {
  evidenceId: string;
  resumeArtifactId: string;
  pageNumber: number | null;
  blockNumber: number | null;
  characterStart: number;
  characterEnd: number;
  originalText: string;
  extractionMethod: string;
};

export type ExtractionBlock = {
  blockId: string;
  sequenceIndex: number;
  pageNumber: number | null;
  structuralLocation: string | null;
  blockType: string;
  extractionMethod: string;
  rawText: string;
  normalizedText: string;
  confidence: number | null;
  reviewRequired: boolean;
};

interface EvidenceDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  fieldLabel: string;
  fieldValue?: string | null;
  evidenceIds: string[];
  evidenceMap: Map<string, EvidenceSpan>;
  rawBlocks?: ExtractionBlock[];
}

export function EvidenceDrawer({
  isOpen,
  onClose,
  fieldLabel,
  fieldValue,
  evidenceIds,
  evidenceMap,
  rawBlocks = [],
}: EvidenceDrawerProps) {
  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const [showTechnicalDetails, setShowTechnicalDetails] = useState<boolean>(false);
  const drawerRef = useRef<HTMLDivElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  const matchingSpans = evidenceIds
    .map((id) => evidenceMap.get(id))
    .filter((s): s is EvidenceSpan => Boolean(s));

  useEffect(() => {
    setCurrentIndex(0);
    setShowTechnicalDetails(false);
  }, [fieldLabel, evidenceIds]);

  // Trap focus and handle Escape
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

  const currentSpan = matchingSpans[currentIndex];
  const blockNumber = currentSpan?.blockNumber ?? null;
  const relatedBlock = blockNumber !== null ? rawBlocks.find((b) => b.sequenceIndex === blockNumber) : null;

  return (
    <div className="drawer-backdrop" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="evidence-drawer-title">
      <div
        className="drawer-panel"
        ref={drawerRef}
        onClick={(e) => e.stopPropagation()}
        tabIndex={-1}
      >
        {/* Drawer Header */}
        <div className="drawer-header">
          <div className="drawer-title-group">
            <div className="drawer-badge-row">
              <span className="badge badge-indigo">
                <Icon name="search" size={13} className="badge-icon" />
                Evidence Inspector
              </span>
              {matchingSpans.length > 1 && (
                <span className="badge badge-neutral">
                  Span {currentIndex + 1} of {matchingSpans.length}
                </span>
              )}
            </div>
            <h3 id="evidence-drawer-title" className="drawer-title">
              {fieldLabel}
            </h3>
          </div>

          <button
            type="button"
            ref={closeBtnRef}
            onClick={onClose}
            className="btn-icon drawer-close-btn"
            aria-label="Close evidence inspector"
          >
            <Icon name="x" size={18} />
          </button>
        </div>

        {/* Drawer Content */}
        <div className="drawer-body">
          {/* Target Value Box */}
          {fieldValue && (
            <div className="drawer-section">
              <span className="drawer-label">Extracted Profile Value</span>
              <div className="drawer-value-card">
                <span className="drawer-value-text">{fieldValue}</span>
              </div>
            </div>
          )}

          {matchingSpans.length === 0 ? (
            <div className="drawer-empty-state">
              <Icon name="info" size={24} className="text-muted" />
              <p className="drawer-empty-text">
                No direct document evidence span is linked to this field. This may be a human-provided correction or an inferred section.
              </p>
            </div>
          ) : (
            <>
              {/* Active Span Details */}
              {currentSpan && (
                <div className="drawer-section">
                  <div className="drawer-section-header">
                    <span className="drawer-label">Document Evidence Snippet</span>
                    <div className="drawer-meta-tags">
                      {currentSpan.pageNumber !== null && (
                        <span className="badge badge-page">Page {currentSpan.pageNumber}</span>
                      )}
                      {currentSpan.blockNumber !== null && (
                        <span className="badge badge-block">Block #{currentSpan.blockNumber}</span>
                      )}
                    </div>
                  </div>

                  <div className="drawer-snippet-card">
                    <div className="snippet-quote-mark">&ldquo;</div>
                    <div className="drawer-snippet-text">{currentSpan.originalText}</div>
                  </div>

                  {/* Character Position Information */}
                  <div className="drawer-info-grid">
                    <div className="info-item">
                      <span className="info-label">Character Range</span>
                      <span className="info-value mono">
                        [{currentSpan.characterStart} : {currentSpan.characterEnd}]
                      </span>
                    </div>
                    <div className="info-item">
                      <span className="info-label">Length</span>
                      <span className="info-value">{currentSpan.characterEnd - currentSpan.characterStart} characters</span>
                    </div>
                  </div>

                  {/* Related Context Block */}
                  {relatedBlock && (
                    <div className="drawer-section" style={{ marginTop: "16px" }}>
                      <span className="drawer-label">Source Document Context</span>
                      <div className="source-block-context">
                        <pre className="source-block-text">{relatedBlock.rawText}</pre>
                      </div>
                    </div>
                  )}

                  {/* Multi-span Navigation */}
                  {matchingSpans.length > 1 && (
                    <div className="drawer-nav-row">
                      <button
                        type="button"
                        disabled={currentIndex === 0}
                        onClick={() => setCurrentIndex((prev) => Math.max(0, prev - 1))}
                        className="btn btn-secondary btn-sm"
                      >
                        <Icon name="chevron-left" size={14} /> Previous Span
                      </button>
                      <span className="span-counter">
                        {currentIndex + 1} / {matchingSpans.length}
                      </span>
                      <button
                        type="button"
                        disabled={currentIndex === matchingSpans.length - 1}
                        onClick={() => setCurrentIndex((prev) => Math.min(matchingSpans.length - 1, prev + 1))}
                        className="btn btn-secondary btn-sm"
                      >
                        Next Span <Icon name="chevron-right" size={14} />
                      </button>
                    </div>
                  )}

                  {/* Optional Technical Details Disclosure */}
                  <div className="drawer-tech-disclosure">
                    <button
                      type="button"
                      className="btn-tech-toggle"
                      onClick={() => setShowTechnicalDetails((prev) => !prev)}
                      aria-expanded={showTechnicalDetails}
                    >
                      <Icon
                        name={showTechnicalDetails ? "chevron-down" : "chevron-right"}
                        size={14}
                      />
                      <span>Technical Audit Details</span>
                    </button>

                    {showTechnicalDetails && (
                      <div className="tech-details-panel">
                        <div className="tech-row">
                          <span className="tech-key">Evidence ID:</span>
                          <span className="tech-val mono">{currentSpan.evidenceId}</span>
                        </div>
                        <div className="tech-row">
                          <span className="tech-key">Extraction Method:</span>
                          <span className="tech-val mono">{currentSpan.extractionMethod}</span>
                        </div>
                        <div className="tech-row">
                          <span className="tech-key">Artifact ID:</span>
                          <span className="tech-val mono">{currentSpan.resumeArtifactId}</span>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {/* Drawer Footer */}
        <div className="drawer-footer">
          <button type="button" onClick={onClose} className="btn btn-secondary btn-sm">
            Close Inspector
          </button>
        </div>
      </div>
    </div>
  );
}
