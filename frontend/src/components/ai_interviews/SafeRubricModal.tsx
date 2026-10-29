import React, { useEffect, useRef } from "react";
import { Icon } from "../Icon";
import { SafeRubricDto } from "../../types/ai_question_set";
import "./ai-interviews.css";

interface SafeRubricModalProps {
  rubric: SafeRubricDto;
  competencyName: string;
  onClose: () => void;
}

export function SafeRubricModal({ rubric, competencyName, onClose }: SafeRubricModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  // Humanize competency name helper
  const humanizeName = (raw: string) => {
    return raw
      .replace(/_/g, " ")
      .toLowerCase()
      .replace(/\b\w/g, (c) => c.toUpperCase());
  };

  // Keyboard navigation: Escape key to close & focus trap
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }

      if (e.key === "Tab" && dialogRef.current) {
        const focusableElements = dialogRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusableElements.length === 0) return;

        const firstElement = focusableElements[0];
        const lastElement = focusableElements[focusableElements.length - 1];

        if (e.shiftKey) {
          if (document.activeElement === firstElement) {
            e.preventDefault();
            lastElement.focus();
          }
        } else {
          if (document.activeElement === lastElement) {
            e.preventDefault();
            firstElement.focus();
          }
        }
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    // Auto-focus on open
    closeBtnRef.current?.focus();

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  // Handle backdrop click
  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  return (
    <div
      className="aiq-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="aiq-rubric-title"
      onClick={handleBackdropClick}
    >
      <div className="aiq-modal-dialog" ref={dialogRef}>
        {/* Modal Header */}
        <div className="aiq-modal-header">
          <h2 id="aiq-rubric-title" className="aiq-modal-title">
            Evaluation rubric: {humanizeName(competencyName)}
          </h2>
          <button
            ref={closeBtnRef}
            type="button"
            className="aiq-modal-close-btn"
            onClick={onClose}
            aria-label="Close Rubric"
          >
            <Icon name="x" size={18} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="aiq-modal-body">
          {/* 1. Competency Name & Definition */}
          <div className="aiq-rubric-block">
            <span className="aiq-rubric-label">Competency</span>
            <div className="aiq-rubric-text" style={{ fontWeight: 600 }}>
              {humanizeName(competencyName)}
            </div>
          </div>

          {/* 2. What this question evaluates */}
          <div className="aiq-rubric-block">
            <span className="aiq-rubric-label">What this question evaluates</span>
            <div className="aiq-rubric-text">
              <p style={{ marginBottom: "8px" }}>{rubric.competency_definition}</p>
              {rubric.question_intent && (
                <p style={{ color: "var(--text-secondary, #475569)", fontSize: "14px" }}>
                  <strong>Intent:</strong> {rubric.question_intent}
                </p>
              )}
            </div>
          </div>

          {/* 3. Evidence to listen for */}
          <div className="aiq-rubric-block">
            <span className="aiq-rubric-label">Evidence to listen for</span>
            {rubric.observable_indicators && rubric.observable_indicators.length > 0 ? (
              <ul className="aiq-probes-list" style={{ paddingLeft: "18px" }}>
                {rubric.observable_indicators.map((ind, i) => (
                  <li key={i}>{ind}</li>
                ))}
              </ul>
            ) : (
              <p className="aiq-rubric-text" style={{ color: "var(--text-muted, #64748b)" }}>
                Standard competency indicators apply.
              </p>
            )}

            {(rubric.sufficient_evidence_guidance || rubric.insufficient_evidence_guidance) && (
              <div className="aiq-evidence-grid" style={{ marginTop: "10px" }}>
                {rubric.sufficient_evidence_guidance && (
                  <div className="aiq-evidence-box sufficient">
                    <strong style={{ display: "block", marginBottom: "4px" }}>Sufficient evidence</strong>
                    {rubric.sufficient_evidence_guidance}
                  </div>
                )}
                {rubric.insufficient_evidence_guidance && (
                  <div className="aiq-evidence-box insufficient">
                    <strong style={{ display: "block", marginBottom: "4px" }}>Insufficient evidence</strong>
                    {rubric.insufficient_evidence_guidance}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* 4. Rating Guide (Stacked Score Rows) */}
          {rubric.rating_anchors && Object.keys(rubric.rating_anchors).length > 0 && (
            <div className="aiq-rubric-block">
              <span className="aiq-rubric-label">Rating guide</span>
              <div className="aiq-score-list">
                {Object.entries(rubric.rating_anchors)
                  .sort(([a], [b]) => Number(a) - Number(b))
                  .map(([score, desc]) => (
                    <div key={score} className="aiq-score-item">
                      <span className="aiq-score-badge">Score {score}</span>
                      <span className="aiq-score-desc">{desc}</span>
                    </div>
                  ))}
              </div>
            </div>
          )}

          {/* 5. Permitted Probes */}
          {rubric.permitted_probes && rubric.permitted_probes.length > 0 && (
            <div className="aiq-rubric-block">
              <span className="aiq-rubric-label">Permitted follow-up probes</span>
              <ul className="aiq-probes-list" style={{ paddingLeft: "18px" }}>
                {rubric.permitted_probes.map((probe, i) => (
                  <li key={i}>{probe}</li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="aiq-modal-footer">
          <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
