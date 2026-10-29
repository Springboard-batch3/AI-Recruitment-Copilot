import React, { useState } from "react";
import { Icon } from "./Icon";

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

export type ExtractionResultData = {
  artifactId: string;
  extractionId: string;
  extractorVersion: string;
  reviewRequired: boolean;
  blockCount: number;
  blocks: ExtractionBlock[];
};

interface ExtractionReviewProps {
  extraction: ExtractionResultData;
  onNavigateBack: () => void;
}

function formatBlockType(type: string): string {
  switch (type.toUpperCase()) {
    case "PARAGRAPH":
      return "Paragraph";
    case "HEADING":
      return "Heading";
    case "LIST_ITEM":
      return "List item";
    case "TABLE_CELL":
      return "Table cell";
    case "HEADER":
      return "Header";
    case "FOOTER":
      return "Footer";
    default:
      return type;
  }
}

function formatExtractionMethod(method: string): string {
  switch (method.toUpperCase()) {
    case "PDF_TEXT":
      return "Direct PDF text";
    case "DOCX_TEXT":
      return "Direct DOCX text";
    case "PDF_LAYOUT":
      return "PDF layout";
    case "OCR":
      return "OCR text";
    default:
      return method;
  }
}

function renderConfidenceBadge(confidence: number | null | undefined) {
  if (
    typeof confidence !== "number" ||
    isNaN(confidence) ||
    confidence < 0 ||
    confidence > 1
  ) {
    return null;
  }
  const pct = Math.round(confidence * 100);
  return (
    <span className="badge badge-confidence">
      {pct}% confidence
    </span>
  );
}

export function ExtractionReview({ extraction, onNavigateBack }: ExtractionReviewProps) {
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [filterType, setFilterType] = useState<string>("ALL");
  const [copiedBlockId, setCopiedBlockId] = useState<string | null>(null);

  const blockTypes = Array.from(new Set(extraction.blocks.map((b) => b.blockType))).sort();

  const filteredBlocks = extraction.blocks.filter((b) => {
    if (filterType !== "ALL" && b.blockType !== filterType) return false;
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return b.rawText.toLowerCase().includes(q) || b.normalizedText.toLowerCase().includes(q);
  });

  const handleCopyText = (text: string, blockId: string) => {
    navigator.clipboard.writeText(text);
    setCopiedBlockId(blockId);
    setTimeout(() => setCopiedBlockId(null), 2000);
  };

  return (
    <div className="extraction-review-container">
      {/* Top Header Row */}
      <div className="workspace-header-bar">
        <button type="button" onClick={onNavigateBack} className="btn-back-link">
          <Icon name="arrow-left" size={16} />
          <span>Back to Candidate Profile</span>
        </button>

        <div className="extraction-header-meta">
          <span className="badge badge-indigo">
            <Icon name="file-text" size={13} className="badge-icon" />
            Raw Extraction &amp; Normalization
          </span>
          <span className="badge badge-neutral">{extraction.blockCount} Total Blocks</span>
        </div>
      </div>

      {/* Review Required Banner */}
      {extraction.reviewRequired && (
        <div className="review-required-banner" role="alert">
          <div className="review-banner-icon">
            <Icon name="alert-triangle" size={18} />
          </div>
          <div className="review-banner-content">
            <strong>Formatting or Quality Review Flagged</strong>
            <p>
              Potential formatting anomalies or non-standard characters were detected during document ingestion. Flagged blocks are highlighted below.
            </p>
          </div>
        </div>
      )}

      {/* Filter & Search Toolbar */}
      <div className="extraction-toolbar">
        <div className="search-input-wrapper flex-grow">
          <Icon name="search" size={16} className="search-icon" />
          <input
            type="text"
            className="search-input"
            placeholder="Search block contents..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="Search extraction blocks"
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

        <div className="filter-select-wrapper">
          <label htmlFor="block-type-filter" className="sr-only">Filter by block type</label>
          <select
            id="block-type-filter"
            className="field-input select-input"
            value={filterType}
            onChange={(e) => setFilterType(e.target.value)}
          >
            <option value="ALL">All Block Types ({extraction.blocks.length})</option>
            {blockTypes.map((t) => (
              <option key={t} value={t}>
                {formatBlockType(t)} ({extraction.blocks.filter((b) => b.blockType === t).length})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Blocks List */}
      <div className="blocks-list" role="feed" aria-label="Extraction Blocks">
        {filteredBlocks.length === 0 ? (
          <div className="empty-state-card">
            <Icon name="search" size={28} className="text-muted" />
            <h3 className="empty-title">No blocks match your search</h3>
            <p className="empty-description">Try adjusting your filters or query.</p>
          </div>
        ) : (
          filteredBlocks.map((block) => {
            const isIdentical = block.rawText === block.normalizedText;
            const isCopied = copiedBlockId === (block.blockId || String(block.sequenceIndex));
            const confBadge = renderConfidenceBadge(block.confidence);

            return (
              <article
                key={block.blockId || block.sequenceIndex}
                className={`extraction-block-card ${block.reviewRequired ? "block-flagged" : ""}`}
                aria-labelledby={`block-title-${block.sequenceIndex}`}
              >
                <div className="block-card-header">
                  <div className="block-tag-group">
                    <span className="badge badge-index">#{block.sequenceIndex}</span>
                    <span className="badge badge-type">{formatBlockType(block.blockType)}</span>
                    <span className="badge badge-method">{formatExtractionMethod(block.extractionMethod)}</span>
                    {block.pageNumber !== null && (
                      <span className="badge badge-location">Page {block.pageNumber}</span>
                    )}
                    {confBadge}
                  </div>

                  <div className="block-action-group">
                    {block.reviewRequired && (
                      <span className="badge badge-review-warn">Review Required</span>
                    )}
                    <button
                      type="button"
                      onClick={() =>
                        handleCopyText(block.normalizedText, block.blockId || String(block.sequenceIndex))
                      }
                      className="btn-copy-subtle"
                      aria-label="Copy block text"
                    >
                      <Icon name={isCopied ? "check" : "copy"} size={13} />
                      <span>{isCopied ? "Copied" : "Copy"}</span>
                    </button>
                  </div>
                </div>

                <div className="block-content-grid">
                  <div className="text-pane raw-pane">
                    <span className="pane-label">Immutable Raw Text</span>
                    <pre className="text-content">{block.rawText}</pre>
                  </div>

                  <div className="text-pane norm-pane">
                    <div className="pane-label-row">
                      <span className="pane-label">Normalized Output</span>
                      {isIdentical ? (
                        <span className="norm-badge-unchanged">Identical</span>
                      ) : (
                        <span className="norm-badge-changed">Normalized</span>
                      )}
                    </div>
                    <pre className="text-content">{block.normalizedText}</pre>
                  </div>
                </div>
              </article>
            );
          })
        )}
      </div>
    </div>
  );
}
