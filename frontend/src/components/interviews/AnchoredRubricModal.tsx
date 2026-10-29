import React, { useEffect, useRef } from "react";
import { Icon } from "../Icon";
import { TemplateQuestion } from "../../types/interview";
import { formatCompetencyName } from "./InterviewWorkspace";

interface AnchoredRubricModalProps {
  isOpen?: boolean;
  question: TemplateQuestion | null;
  onClose: () => void;
}

export function AnchoredRubricModal({
  isOpen = true,
  question,
  onClose,
}: AnchoredRubricModalProps) {
  const modalRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  // Focus management & body scroll lock
  useEffect(() => {
    if (!isOpen || !question) return;

    const prevActiveElement = document.activeElement as HTMLElement | null;
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // Focus close button on open
    const focusTimer = setTimeout(() => {
      closeButtonRef.current?.focus();
    }, 50);

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }

      // Focus trap
      if (e.key === "Tab" && modalRef.current) {
        const focusableElements = modalRef.current.querySelectorAll<HTMLElement>(
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

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = originalOverflow;
      clearTimeout(focusTimer);
      prevActiveElement?.focus();
    };
  }, [isOpen, question, onClose]);

  if (!isOpen || !question || !question.rubric) {
    return null;
  }

  const rubric = question.rubric as any;
  const competencyTitle = formatCompetencyName(
    question.competency_name || rubric.target_competency || rubric.competency_definition
  );

  // Extract rating anchors (either from rating_anchors dict or levels array)
  let anchors: Array<{ score: number | string; title?: string; text: string; signals?: string[] }> = [];

  if (rubric.rating_anchors && typeof rubric.rating_anchors === "object") {
    // Sort numeric keys 1, 3, 5
    const entries = Object.entries(rubric.rating_anchors);
    entries.sort((a, b) => Number(a[0]) - Number(b[0]));
    anchors = entries.map(([score, desc]) => ({
      score,
      text: String(desc),
    }));
  } else if (Array.isArray(rubric.levels) && rubric.levels.length > 0) {
    anchors = rubric.levels.map((lvl: any) => ({
      score: lvl.score_range ? lvl.score_range[0] : lvl.level_name,
      title: lvl.level_name,
      text: lvl.description || "",
      signals: lvl.observable_signals || [],
    }));
  }

  // Observable signals from rubric
  const observableSignals: string[] =
    rubric.observable_indicators || rubric.observable_signals || [];
  const positiveSignals: string[] = rubric.positive_signals || [];
  const redFlags: string[] = rubric.red_flags || rubric.prohibited_inferences || [];

  return (
    <div
      className="modal-backdrop fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-labelledby="anchored-rubric-dialog-title"
      aria-describedby="anchored-rubric-dialog-desc"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        ref={modalRef}
        className="modal-card bg-white rounded-lg shadow-xl border border-gray-200 w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden my-auto"
      >
        {/* Header */}
        <div className="modal-header px-6 py-4 border-b border-gray-100 flex justify-between items-start bg-gray-50/80 gap-4">
          <div className="min-w-0 flex-1">
            <h2
              id="anchored-rubric-dialog-title"
              className="text-lg font-semibold text-gray-900 tracking-tight"
            >
              Anchored rubric
            </h2>
            <div className="flex flex-wrap items-center gap-2 mt-1">
              <span className="text-sm font-medium text-gray-800 break-words">
                {competencyTitle}
              </span>
              <span className="text-gray-300">•</span>
              <span className="text-xs px-2 py-0.5 rounded bg-gray-200/80 text-gray-700 font-medium">
                {question.question_type}
              </span>
              {question.estimated_time_minutes && (
                <>
                  <span className="text-gray-300">•</span>
                  <span className="text-xs text-gray-500">
                    {question.estimated_time_minutes} min
                  </span>
                </>
              )}
            </div>
          </div>

          <button
            ref={closeButtonRef}
            type="button"
            className="p-1.5 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-200/60 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-1"
            onClick={onClose}
            aria-label="Close anchored rubric dialog"
          >
            <Icon name="x" size={18} />
          </button>
        </div>

        {/* Scrollable Body */}
        <div
          id="anchored-rubric-dialog-desc"
          className="modal-body p-6 overflow-y-auto space-y-6 text-gray-700"
        >
          {/* Question & Intent Overview */}
          <div className="bg-slate-50 p-4 rounded-lg border border-slate-200 space-y-2">
            <h3 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
              Evaluation Context & Objective
            </h3>
            <p className="text-sm font-medium text-gray-900">
              {question.question_text}
            </p>
            {rubric.question_intent && (
              <p className="text-xs text-gray-600 mt-1">
                <span className="font-semibold text-gray-700">Intent: </span>
                {rubric.question_intent}
              </p>
            )}
            {rubric.competency_definition &&
              rubric.competency_definition !== competencyTitle && (
                <p className="text-xs text-gray-600">
                  <span className="font-semibold text-gray-700">Competency definition: </span>
                  {rubric.competency_definition}
                </p>
              )}
          </div>

          {/* Observable Signals */}
          {observableSignals.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-semibold text-gray-700 uppercase tracking-wider">
                Observable Signals & Indicators
              </h3>
              <ul className="list-disc list-inside space-y-1 text-xs text-gray-600 bg-gray-50/60 p-3 rounded-md border border-gray-100">
                {observableSignals.map((signal, idx) => (
                  <li key={idx} className="break-words">
                    {signal}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Positive Signals & Watchout Indicators */}
          {(positiveSignals.length > 0 || redFlags.length > 0) && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {positiveSignals.length > 0 && (
                <div className="p-3 bg-emerald-50/60 border border-emerald-200 rounded-lg space-y-1.5">
                  <h4 className="text-xs font-semibold text-emerald-900 uppercase tracking-wide">
                    Positive Signals
                  </h4>
                  <ul className="list-disc list-inside space-y-1 text-xs text-emerald-950">
                    {positiveSignals.map((sig, idx) => (
                      <li key={idx} className="break-words">
                        {sig}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {redFlags.length > 0 && (
                <div className="p-3 bg-amber-50/60 border border-amber-200 rounded-lg space-y-1.5">
                  <h4 className="text-xs font-semibold text-amber-900 uppercase tracking-wide">
                    Watchout Indicators
                  </h4>
                  <ul className="list-disc list-inside space-y-1 text-xs text-amber-950">
                    {redFlags.map((flag, idx) => (
                      <li key={idx} className="break-words">
                        {flag}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Score Anchors (Score 1, Score 3, Score 5) */}
          <div className="space-y-3">
            <h3 className="text-xs font-semibold text-gray-700 uppercase tracking-wider">
              Objective Rating Anchors
            </h3>

            {anchors.length > 0 ? (
              <div className="space-y-3">
                {anchors.map((anchor) => {
                  const scoreLabel = String(anchor.score);
                  const isLow = scoreLabel === "1" || scoreLabel.toLowerCase().includes("unsatisfactory") || scoreLabel.toLowerCase().includes("novice");
                  const isMid = scoreLabel === "3" || scoreLabel.toLowerCase().includes("proficient") || scoreLabel.toLowerCase().includes("competent");
                  const isHigh = scoreLabel === "5" || scoreLabel.toLowerCase().includes("expert") || scoreLabel.toLowerCase().includes("advanced");

                  return (
                    <div
                      key={scoreLabel}
                      className="p-4 rounded-lg border border-gray-200 bg-white shadow-xs space-y-2"
                    >
                      <div className="flex items-center gap-2">
                        <span
                          className={`inline-flex items-center px-2.5 py-0.5 rounded text-xs font-semibold ${
                            isHigh
                              ? "bg-emerald-50 text-emerald-800 border border-emerald-200"
                              : isMid
                              ? "bg-blue-50 text-blue-800 border border-blue-200"
                              : isLow
                              ? "bg-amber-50 text-amber-900 border border-amber-200"
                              : "bg-gray-100 text-gray-800 border border-gray-200"
                          }`}
                        >
                          Score {scoreLabel}
                          {anchor.title && anchor.title !== `Score ${scoreLabel}` ? ` — ${anchor.title}` : ""}
                        </span>
                      </div>
                      <p className="text-sm text-gray-800 leading-relaxed break-words">
                        {anchor.text}
                      </p>
                      {anchor.signals && anchor.signals.length > 0 && (
                        <div className="mt-2 pt-2 border-t border-gray-100">
                          <span className="text-xs font-medium text-gray-500">Key signals:</span>
                          <ul className="list-disc list-inside text-xs text-gray-600 space-y-0.5 mt-1">
                            {anchor.signals.map((sig, sIdx) => (
                              <li key={sIdx}>{sig}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-sm text-gray-500 italic">
                Standard behavioral scoring criteria configured.
              </p>
            )}
          </div>

          {/* Evidence Guidance if available */}
          {(rubric.sufficient_evidence_guidance || rubric.insufficient_evidence_guidance) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-2">
              {rubric.sufficient_evidence_guidance && (
                <div className="p-3 bg-emerald-50/50 border border-emerald-100 rounded-md">
                  <h4 className="text-xs font-semibold text-emerald-900 mb-1">
                    Sufficient Evidence Guidance
                  </h4>
                  <p className="text-xs text-emerald-800 leading-relaxed">
                    {rubric.sufficient_evidence_guidance}
                  </p>
                </div>
              )}
              {rubric.insufficient_evidence_guidance && (
                <div className="p-3 bg-amber-50/50 border border-amber-100 rounded-md">
                  <h4 className="text-xs font-semibold text-amber-950 mb-1">
                    Insufficient Evidence Guidance
                  </h4>
                  <p className="text-xs text-amber-900 leading-relaxed">
                    {rubric.insufficient_evidence_guidance}
                  </p>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="modal-footer px-6 py-3 border-t border-gray-100 bg-gray-50 flex justify-end">
          <button
            type="button"
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md shadow-xs hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-1 transition-colors"
            onClick={onClose}
          >
            Close Rubric
          </button>
        </div>
      </div>
    </div>
  );
}
