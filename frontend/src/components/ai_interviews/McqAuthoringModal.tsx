import React, { useEffect, useRef, useState } from "react";
import { Icon } from "../Icon";
import { McqOptionDto, QuestionDifficulty } from "../../types/ai_question_set";
import "./ai-interviews.css";

export interface McqFormData {
  question_id?: string;
  slot_id?: string;
  question_text: string;
  competency_name: string;
  options: McqOptionDto[];
  correct_option_id: string;
  answer_explanation: string;
  difficulty: QuestionDifficulty;
  estimated_time_minutes: number;
}

interface McqAuthoringModalProps {
  isOpen: boolean;
  initialData?: McqFormData | null;
  onSave: (data: McqFormData) => Promise<void>;
  onClose: () => void;
}

export function McqAuthoringModal({
  isOpen,
  initialData,
  onSave,
  onClose,
}: McqAuthoringModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [competencyName, setCompetencyName] = useState<string>("");
  const [questionText, setQuestionText] = useState<string>("");
  const [difficulty, setDifficulty] = useState<QuestionDifficulty>("INTERMEDIATE");
  const [estimatedTime, setEstimatedTime] = useState<number>(2);
  const [options, setOptions] = useState<McqOptionDto[]>([
    { option_id: "A", text: "" },
    { option_id: "B", text: "" },
    { option_id: "C", text: "" },
    { option_id: "D", text: "" },
  ]);
  const [correctOptionId, setCorrectOptionId] = useState<string>("A");
  const [answerExplanation, setAnswerExplanation] = useState<string>("");
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  useEffect(() => {
    if (initialData) {
      setCompetencyName(initialData.competency_name || "");
      setQuestionText(initialData.question_text || "");
      setDifficulty(initialData.difficulty || "INTERMEDIATE");
      setEstimatedTime(initialData.estimated_time_minutes || 2);
      setOptions(
        initialData.options && initialData.options.length >= 2
          ? initialData.options
          : [
              { option_id: "A", text: "" },
              { option_id: "B", text: "" },
              { option_id: "C", text: "" },
              { option_id: "D", text: "" },
            ]
      );
      setCorrectOptionId(initialData.correct_option_id || initialData.options?.[0]?.option_id || "A");
      setAnswerExplanation(initialData.answer_explanation || "");
    } else {
      setCompetencyName("");
      setQuestionText("");
      setDifficulty("INTERMEDIATE");
      setEstimatedTime(2);
      setOptions([
        { option_id: "A", text: "" },
        { option_id: "B", text: "" },
        { option_id: "C", text: "" },
        { option_id: "D", text: "" },
      ]);
      setCorrectOptionId("A");
      setAnswerExplanation("");
    }
    setValidationError(null);
  }, [initialData, isOpen]);

  // Keyboard navigation & focus trap
  useEffect(() => {
    if (!isOpen) return;
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
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleOptionTextChange = (index: number, text: string) => {
    const updated = [...options];
    updated[index] = { ...updated[index], text };
    setOptions(updated);
  };

  const handleAddOption = () => {
    if (options.length >= 5) return;
    const nextChar = String.fromCharCode(65 + options.length);
    setOptions([...options, { option_id: nextChar, text: "" }]);
  };

  const handleRemoveOption = (index: number) => {
    if (options.length <= 2) return;
    const removedId = options[index].option_id;
    const updated = options.filter((_, i) => i !== index);
    setOptions(updated);
    if (correctOptionId === removedId) {
      setCorrectOptionId(updated[0]?.option_id || "A");
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setValidationError(null);

    if (!competencyName.trim()) {
      setValidationError("Competency name is required.");
      return;
    }
    if (!questionText.trim()) {
      setValidationError("Question text is required.");
      return;
    }
    if (options.length < 2 || options.length > 5) {
      setValidationError("An MCQ must have between 2 and 5 options.");
      return;
    }
    const emptyOption = options.some((opt) => !opt.text.trim());
    if (emptyOption) {
      setValidationError("All options must have text filled in.");
      return;
    }
    const optionTexts = options.map((opt) => opt.text.trim().toLowerCase());
    const uniqueTexts = new Set(optionTexts);
    if (uniqueTexts.size !== optionTexts.length) {
      setValidationError("Each option must have distinct text.");
      return;
    }
    if (!correctOptionId || !options.some((opt) => opt.option_id === correctOptionId)) {
      setValidationError("Please select a valid correct answer option.");
      return;
    }
    if (!answerExplanation.trim()) {
      setValidationError("An answer explanation is required for recruiter review.");
      return;
    }

    try {
      setIsSubmitting(true);
      await onSave({
        question_id: initialData?.question_id,
        slot_id: initialData?.slot_id,
        competency_name: competencyName.trim(),
        question_text: questionText.trim(),
        difficulty,
        estimated_time_minutes: estimatedTime,
        options: options.map((opt) => ({
          option_id: opt.option_id,
          text: opt.text.trim(),
        })),
        correct_option_id: correctOptionId,
        answer_explanation: answerExplanation.trim(),
      });
      onClose();
    } catch (err: any) {
      setValidationError(err?.message || "Failed to save MCQ.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      className="aiq-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="mcq-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="aiq-modal-dialog" ref={dialogRef} style={{ maxWidth: "680px" }}>
        <div className="aiq-modal-header">
          <h2 id="mcq-modal-title" className="aiq-modal-title">
            {initialData?.slot_id ? "Edit Multiple Choice Question" : "Add Multiple Choice Question"}
          </h2>
          <button
            type="button"
            className="aiq-modal-close-btn"
            onClick={onClose}
            aria-label="Close modal"
          >
            <Icon name="x" size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="aiq-modal-body" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {validationError && (
            <div className="feedback-box error" role="alert" style={{ marginBottom: "8px" }}>
              <Icon name="alert-triangle" size={16} />
              <span>{validationError}</span>
            </div>
          )}

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
            <div>
              <label className="aiq-form-label" htmlFor="mcq-competency">
                Competency / Topic <span style={{ color: "var(--accent-primary)" }}>*</span>
              </label>
              <input
                id="mcq-competency"
                type="text"
                className="aiq-input"
                value={competencyName}
                onChange={(e) => setCompetencyName(e.target.value)}
                placeholder="e.g. System Design, Python, React"
                required
              />
            </div>

            <div>
              <label className="aiq-form-label" htmlFor="mcq-difficulty">
                Difficulty Level
              </label>
              <select
                id="mcq-difficulty"
                className="aiq-input"
                value={difficulty}
                onChange={(e) => setDifficulty(e.target.value as QuestionDifficulty)}
              >
                <option value="FOUNDATIONAL">Foundational</option>
                <option value="INTERMEDIATE">Intermediate</option>
                <option value="ADVANCED">Advanced</option>
              </select>
            </div>
          </div>

          <div>
            <label className="aiq-form-label" htmlFor="mcq-question-text">
              Question Text <span style={{ color: "var(--accent-primary)" }}>*</span>
            </label>
            <textarea
              id="mcq-question-text"
              className="aiq-wording-textarea"
              rows={3}
              value={questionText}
              onChange={(e) => setQuestionText(e.target.value)}
              placeholder="State a clear, objective question assessing practical knowledge..."
              required
            />
          </div>

          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <label className="aiq-form-label" style={{ margin: 0 }}>
                Answer Options (2 to 5) · Select Correct Answer <span style={{ color: "var(--accent-primary)" }}>*</span>
              </label>
              {options.length < 5 && (
                <button
                  type="button"
                  className="btn btn-secondary btn-xs"
                  onClick={handleAddOption}
                >
                  <Icon name="plus" size={12} />
                  <span>Add Option</span>
                </button>
              )}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              {options.map((option, idx) => {
                const isSelected = correctOptionId === option.option_id;
                return (
                  <div
                    key={option.option_id || idx}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "10px",
                      padding: "8px 12px",
                      borderRadius: "6px",
                      border: isSelected ? "1px solid #10b981" : "1px solid var(--border-subtle, #e2e8f0)",
                      backgroundColor: isSelected ? "rgba(16, 185, 129, 0.05)" : "transparent",
                    }}
                  >
                    <input
                      type="radio"
                      id={`correct-option-${option.option_id}`}
                      name="correct-option-group"
                      checked={isSelected}
                      onChange={() => setCorrectOptionId(option.option_id)}
                      style={{ cursor: "pointer" }}
                      aria-label={`Mark option ${option.option_id} as correct answer`}
                    />
                    <span style={{ fontWeight: 600, minWidth: "24px", color: "var(--text-secondary, #475569)" }}>
                      {option.option_id}.
                    </span>
                    <input
                      type="text"
                      className="aiq-input"
                      value={option.text}
                      onChange={(e) => handleOptionTextChange(idx, e.target.value)}
                      placeholder={`Option ${option.option_id} text`}
                      style={{ flex: 1 }}
                      required
                    />
                    {options.length > 2 && (
                      <button
                        type="button"
                        className="btn-icon-subtle"
                        onClick={() => handleRemoveOption(idx)}
                        aria-label={`Remove option ${option.option_id}`}
                        title="Remove option"
                      >
                        <Icon name="trash" size={14} />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <div>
            <label className="aiq-form-label" htmlFor="mcq-explanation">
              Answer Explanation (Recruiter only) <span style={{ color: "var(--accent-primary)" }}>*</span>
            </label>
            <textarea
              id="mcq-explanation"
              className="aiq-wording-textarea"
              rows={2}
              value={answerExplanation}
              onChange={(e) => setAnswerExplanation(e.target.value)}
              placeholder="Explain why the designated option is correct and why other distractors are incorrect..."
              required
            />
          </div>

          <div className="aiq-modal-footer" style={{ padding: "12px 0 0 0", marginTop: "8px" }}>
            <button type="button" className="btn btn-secondary btn-sm" onClick={onClose} disabled={isSubmitting}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary btn-sm" disabled={isSubmitting}>
              <Icon name="check" size={14} />
              <span>{isSubmitting ? "Saving..." : "Save Question"}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
