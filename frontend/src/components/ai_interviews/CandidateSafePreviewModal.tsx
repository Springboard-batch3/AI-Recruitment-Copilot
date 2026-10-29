import React, { useEffect, useRef, useState } from "react";
import { Icon } from "../Icon";
import { AiQuestionSetDetail } from "../../types/ai_question_set";
import "./ai-interviews.css";

interface CandidateSafePreviewModalProps {
  isOpen: boolean;
  detail: AiQuestionSetDetail;
  onClose: () => void;
}

export function CandidateSafePreviewModal({
  isOpen,
  detail,
  onClose,
}: CandidateSafePreviewModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [selectedAnswers, setSelectedAnswers] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const conversationalSlots = detail.slots.filter(
    (s) => s.question_format === "CONVERSATION" || !s.question_format
  );
  const mcqSlots = detail.slots.filter(
    (s) => s.question_format === "MCQ_SINGLE_ANSWER"
  );

  const handleSelectOption = (slotId: string, optionId: string) => {
    setSelectedAnswers((prev) => ({ ...prev, [slotId]: optionId }));
  };

  const humanize = (str?: string | null) => {
    if (!str) return "";
    return str
      .replace(/_/g, " ")
      .toLowerCase()
      .replace(/\b\w/g, (c) => c.toUpperCase());
  };

  return (
    <div
      className="aiq-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="preview-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="aiq-modal-dialog"
        ref={dialogRef}
        style={{ maxWidth: "780px", maxHeight: "90vh", display: "flex", flexDirection: "column" }}
      >
        <div className="aiq-modal-header">
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
              <span className="badge badge-info" style={{ fontSize: "11px", textTransform: "uppercase" }}>
                Candidate View
              </span>
              <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)" }}>
                Safe Practice Interview Preview
              </span>
            </div>
            <h2 id="preview-modal-title" className="aiq-modal-title">
              {detail.job_title}
            </h2>
          </div>
          <button
            type="button"
            className="aiq-modal-close-btn"
            onClick={onClose}
            aria-label="Close preview"
          >
            <Icon name="x" size={18} />
          </button>
        </div>

        <div className="aiq-modal-body" style={{ overflowY: "auto", flex: 1, padding: "20px 24px" }}>
          <div
            className="aiq-panel"
            style={{
              backgroundColor: "var(--accent-subtle, #f0f4ff)",
              borderColor: "var(--accent-border, #d9e2ec)",
              marginBottom: "20px",
              padding: "12px 16px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 600, color: "var(--accent-primary, #3b82f6)" }}>
              <Icon name="info" size={16} />
              <span>Candidate Privacy & Safe Discovery</span>
            </div>
            <p style={{ margin: "4px 0 0 0", fontSize: "13px", color: "var(--text-secondary, #475569)" }}>
              This preview shows exactly what candidates will see. Evaluation rubrics, scoring anchors, recruiter notes, and correct MCQ answers are strictly omitted.
            </p>
          </div>

          {/* Section 1: Conversational Questions */}
          <div style={{ marginBottom: "28px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px", borderBottom: "1px solid var(--border-subtle, #e2e8f0)", paddingBottom: "8px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: 0 }}>
                Part 1: Conversational Questions ({conversationalSlots.length})
              </h3>
              <span style={{ fontSize: "13px", color: "var(--text-muted, #64748b)" }}>
                Audio / Text Responses
              </span>
            </div>

            {conversationalSlots.length === 0 ? (
              <p style={{ color: "var(--text-muted, #64748b)", fontSize: "14px" }}>
                No conversational questions in this practice set.
              </p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                {conversationalSlots.map((slot, index) => (
                  <div
                    key={slot.slot_id}
                    className="aiq-panel"
                    style={{ padding: "16px", backgroundColor: "var(--surface-base, #ffffff)" }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                      <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--accent-primary, #3b82f6)", textTransform: "uppercase" }}>
                        Question {index + 1} · {humanize(slot.competency_name)}
                      </span>
                      <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)" }}>
                        ~{slot.estimated_time_minutes} min
                      </span>
                    </div>
                    <p style={{ fontSize: "15px", color: "var(--text-primary, #0f172a)", margin: 0, lineHeight: 1.5, fontWeight: 500 }}>
                      {slot.ai_wording || "Question pending generation."}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Section 2: Multiple Choice Questions */}
          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px", borderBottom: "1px solid var(--border-subtle, #e2e8f0)", paddingBottom: "8px" }}>
              <h3 style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: 0 }}>
                Part 2: Multiple Choice Questions ({mcqSlots.length})
              </h3>
              <span style={{ fontSize: "13px", color: "var(--text-muted, #64748b)" }}>
                Knowledge Assessment
              </span>
            </div>

            {mcqSlots.length === 0 ? (
              <p style={{ color: "var(--text-muted, #64748b)", fontSize: "14px" }}>
                No multiple choice questions in this practice set.
              </p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
                {mcqSlots.map((slot, index) => (
                  <div
                    key={slot.slot_id}
                    className="aiq-panel"
                    style={{ padding: "16px", backgroundColor: "var(--surface-base, #ffffff)" }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                      <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--accent-primary, #3b82f6)", textTransform: "uppercase" }}>
                        MCQ {index + 1} · {humanize(slot.competency_name)}
                      </span>
                      {slot.difficulty && (
                        <span className={`badge badge-${slot.difficulty.toLowerCase()}`} style={{ fontSize: "11px" }}>
                          {humanize(slot.difficulty)}
                        </span>
                      )}
                    </div>
                    <p style={{ fontSize: "15px", color: "var(--text-primary, #0f172a)", margin: "0 0 12px 0", lineHeight: 1.5, fontWeight: 500 }}>
                      {slot.ai_wording}
                    </p>

                    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                      {slot.options?.map((opt) => {
                        const isChosen = selectedAnswers[slot.slot_id] === opt.option_id;
                        return (
                          <label
                            key={opt.option_id}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "10px",
                              padding: "10px 14px",
                              borderRadius: "6px",
                              border: isChosen ? "1px solid var(--accent-primary, #3b82f6)" : "1px solid var(--border-subtle, #e2e8f0)",
                              backgroundColor: isChosen ? "rgba(59, 130, 246, 0.05)" : "transparent",
                              cursor: "pointer",
                              transition: "all 0.15s ease",
                            }}
                          >
                            <input
                              type="radio"
                              name={`preview-${slot.slot_id}`}
                              checked={isChosen}
                              onChange={() => handleSelectOption(slot.slot_id, opt.option_id)}
                              style={{ cursor: "pointer" }}
                            />
                            <span style={{ fontWeight: 600, color: "var(--text-secondary, #475569)" }}>
                              {opt.option_id}.
                            </span>
                            <span style={{ fontSize: "14px", color: "var(--text-primary, #0f172a)" }}>
                              {opt.text}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="aiq-modal-footer">
          <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
            Close Preview
          </button>
        </div>
      </div>
    </div>
  );
}
