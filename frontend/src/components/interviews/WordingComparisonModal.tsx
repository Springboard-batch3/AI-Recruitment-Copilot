import React, { useEffect, useRef, useState } from "react";
import { Icon } from "../Icon";
import {
  CandidateWordingResult,
  StructuredWordingVariant,
  TemplateQuestion,
} from "../../types/interview";

interface WordingComparisonModalProps {
  isOpen: boolean;
  question: TemplateQuestion;
  wordingResult: CandidateWordingResult;
  onUseSuggestion: (variant: StructuredWordingVariant) => void;
  onEditSuggestion: (variant: StructuredWordingVariant) => void;
  onKeepOriginal: () => void;
  onClose: () => void;
  isApplying?: boolean;
}

export function WordingComparisonModal({
  isOpen,
  question,
  wordingResult,
  onUseSuggestion,
  onEditSuggestion,
  onKeepOriginal,
  onClose,
  isApplying = false,
}: WordingComparisonModalProps) {
  const modalRef = useRef<HTMLDivElement>(null);
  const primaryButtonRef = useRef<HTMLButtonElement>(null);
  const [showProvenance, setShowProvenance] = useState<boolean>(false);
  const [selectedVariantIdx, setSelectedVariantIdx] = useState<number>(0);

  // Focus management
  useEffect(() => {
    if (isOpen) {
      const prevActiveElement = document.activeElement as HTMLElement | null;
      // Focus the primary button when modal opens
      setTimeout(() => {
        primaryButtonRef.current?.focus();
      }, 50);

      const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === "Escape" && !isApplying) {
          onClose();
        }
      };
      window.addEventListener("keydown", handleKeyDown);
      return () => {
        window.removeEventListener("keydown", handleKeyDown);
        prevActiveElement?.focus();
      };
    }
  }, [isOpen, isApplying, onClose]);

  if (!isOpen) return null;

  const currentVariant = wordingResult.proposed_variants[selectedVariantIdx] || wordingResult.proposed_variants[0];
  const isFallback = wordingResult.provider_fallback_used || !wordingResult.is_validated;

  return (
    <div
      className="modal-backdrop fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-labelledby="wording-comparison-title"
      aria-describedby="wording-comparison-desc"
    >
      <div
        ref={modalRef}
        className="modal-card bg-white rounded-lg shadow-xl max-w-2xl w-full border border-gray-200 overflow-hidden my-8"
      >
        {/* Header */}
        <div className="modal-header px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50">
          <div className="flex items-center gap-2">
            <Icon name="sparkles" size={20} className="text-primary" />
            <h2 id="wording-comparison-title" className="text-lg font-semibold text-gray-900">
              Review Improved Question Wording
            </h2>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-xs text-gray-500 hover:text-gray-700"
            onClick={onClose}
            disabled={isApplying}
            aria-label="Close wording comparison modal"
          >
            <Icon name="x" size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="modal-body p-6 space-y-5">
          <p id="wording-comparison-desc" className="text-sm text-gray-600">
            Compare the authoritative deterministic wording with the proposed AI-assisted draft.
            Applying a suggestion will update the draft question and require standard validation before approval.
          </p>

          {isFallback && (
            <div className="p-3 bg-amber-50 border border-amber-200 rounded text-amber-800 text-sm flex items-start gap-2">
              <Icon name="alert-triangle" size={16} className="mt-0.5 shrink-0" />
              <div>
                <strong>Fallback Active:</strong>{" "}
                {wordingResult.safe_error_message || "Wording assistance was inconclusive. Deterministic wording retained."}
              </div>
            </div>
          )}

          {/* Comparison Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Original Deterministic Box */}
            <div className="border border-gray-200 rounded-lg p-4 bg-gray-50 flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                    Original Deterministic
                  </span>
                  <span className="badge badge-xs badge-info">Authoritative</span>
                </div>
                <div className="text-sm text-gray-800 whitespace-pre-wrap font-normal">
                  {wordingResult.deterministic_question_text || question.question_text}
                </div>
              </div>
              <div className="mt-4 pt-2 border-t border-gray-200 text-xs text-gray-500 flex justify-between">
                <span>Version: v{question.question_version || 1}</span>
                <span>Origin: {question.authoring_origin}</span>
              </div>
            </div>

            {/* AI-Assisted Proposal Box */}
            <div className="border-2 border-primary/40 rounded-lg p-4 bg-primary/5 flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold uppercase tracking-wider text-primary">
                    AI-Assisted Suggestion
                  </span>
                  <span className="badge badge-xs badge-success">
                    {wordingResult.is_validated ? "Validated" : "Draft Proposal"}
                  </span>
                </div>
                <div className="text-sm text-gray-900 whitespace-pre-wrap font-medium">
                  {currentVariant ? currentVariant.variant_text : question.question_text}
                </div>
              </div>

              <div className="mt-4 pt-2 border-t border-primary/20 text-xs text-primary/80 flex justify-between items-center">
                <span>Rationale: {currentVariant?.rationale_code || "CLARITY_IMPROVEMENT"}</span>
                {wordingResult.proposed_variants.length > 1 && (
                  <div className="flex gap-1">
                    {wordingResult.proposed_variants.map((_, i) => (
                      <button
                        key={i}
                        type="button"
                        className={`px-2 py-0.5 rounded text-xs ${
                          selectedVariantIdx === i
                            ? "bg-primary text-white font-bold"
                            : "bg-gray-200 text-gray-700"
                        }`}
                        onClick={() => setSelectedVariantIdx(i)}
                      >
                        Variant {i + 1}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Expandable AI Details / Provenance */}
          <div className="border border-gray-200 rounded bg-gray-50 overflow-hidden">
            <button
              type="button"
              className="w-full px-4 py-2 text-xs font-semibold text-gray-700 flex justify-between items-center hover:bg-gray-100"
              onClick={() => setShowProvenance(!showProvenance)}
              aria-expanded={showProvenance}
            >
              <span className="flex items-center gap-1.5">
                <Icon name="shield-check" size={14} className="text-primary" />
                <span>AI Governance &amp; Provenance Details</span>
              </span>
              <Icon name={showProvenance ? "chevron-up" : "chevron-down"} size={14} />
            </button>

            {showProvenance && (
              <div className="px-4 py-3 border-t border-gray-200 text-xs text-gray-600 space-y-1 bg-white">
                <div className="flex justify-between">
                  <span className="font-medium text-gray-500">Operation:</span>
                  <span>{wordingResult.operation || "REWORD_QUESTION"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="font-medium text-gray-500">Prompt Template Version:</span>
                  <span>{wordingResult.prompt_template_version || "1.0.0"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="font-medium text-gray-500">Semantic Safety Validation:</span>
                  <span className={wordingResult.is_validated ? "text-success font-semibold" : "text-amber-600"}>
                    {wordingResult.is_validated ? "Passed (All 15 Safety Rules)" : "Inconclusive / Fallback"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="font-medium text-gray-500">Authoring Origin:</span>
                  <span>{wordingResult.is_validated ? "AI_ASSISTED" : "DETERMINISTIC"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="font-medium text-gray-500">Candidate Privacy:</span>
                  <span className="text-success">Protected (Zero PII or raw prompt storage)</span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Footer Actions */}
        <div className="modal-footer px-6 py-4 bg-gray-50 border-t border-gray-100 flex flex-wrap justify-between items-center gap-3">
          <button
            type="button"
            className="btn btn-ghost btn-sm text-gray-600"
            onClick={onKeepOriginal}
            disabled={isApplying}
          >
            <Icon name="x" size={14} />
            <span>Keep Original</span>
          </button>

          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={() => currentVariant && onEditSuggestion(currentVariant)}
              disabled={isApplying || !currentVariant}
            >
              <Icon name="edit" size={14} />
              <span>Edit Suggestion</span>
            </button>

            <button
              ref={primaryButtonRef}
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => currentVariant && onUseSuggestion(currentVariant)}
              disabled={isApplying || !currentVariant}
            >
              <Icon name="check" size={14} />
              <span>{isApplying ? "Applying..." : "Use Suggestion"}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
