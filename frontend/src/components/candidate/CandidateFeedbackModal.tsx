import React, { useEffect, useState } from "react";
import {
  CandidateSafeFeedbackReport,
  getCandidateFeedbackReport,
} from "../../api/candidateEvaluations";

interface CandidateFeedbackModalProps {
  sessionId: string;
  onClose: () => void;
}

export const CandidateFeedbackModal: React.FC<CandidateFeedbackModalProps> = ({
  sessionId,
  onClose,
}) => {
  const [report, setReport] = useState<CandidateSafeFeedbackReport | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    async function load() {
      try {
        setLoading(true);
        setError(null);
        const data = await getCandidateFeedbackReport(sessionId);
        if (mounted) setReport(data);
      } catch (err: any) {
        if (mounted) {
          setError(
            err?.message || "Feedback is currently unavailable or under review."
          );
        }
      } finally {
        if (mounted) setLoading(false);
      }
    }
    load();
    return () => {
      mounted = false;
    };
  }, [sessionId]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4 overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-labelledby="feedback-report-title"
    >
      <div className="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-3xl max-h-[90vh] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="px-6 py-5 border-b border-slate-100 flex items-center justify-between bg-gradient-to-r from-slate-50 to-indigo-50/30">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-600 text-white flex items-center justify-center shadow-sm">
              <svg
                className="w-5 h-5"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
            </div>
            <div>
              <h2
                id="feedback-report-title"
                className="text-lg font-bold text-slate-900"
              >
                Interview Feedback Report
              </h2>
              <p className="text-xs text-slate-500 font-medium">
                Official developmental insights &amp; response assessment
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
            aria-label="Close feedback modal"
          >
            <svg
              className="w-5 h-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="p-6 overflow-y-auto space-y-6 flex-1 text-slate-800">
          {loading && (
            <div className="py-12 flex flex-col items-center justify-center space-y-3">
              <div className="w-8 h-8 border-3 border-indigo-600 border-t-transparent rounded-full animate-spin" />
              <p className="text-sm text-slate-500 font-medium">
                Loading official feedback report...
              </p>
            </div>
          )}

          {error && (
            <div className="p-4 rounded-xl bg-amber-50 border border-amber-200 text-amber-800 text-sm">
              <p className="font-semibold">Feedback Not Available</p>
              <p className="mt-1 text-xs">{error}</p>
            </div>
          )}

          {report && !loading && (
            <>
              {/* Job & Completion Details */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 p-4 rounded-xl bg-slate-50 border border-slate-100 text-sm">
                <div>
                  <span className="text-xs text-slate-400 uppercase tracking-wider font-semibold">
                    Position
                  </span>
                  <p className="font-bold text-slate-900 text-base">
                    {report.job_title}
                  </p>
                </div>
                <div>
                  <span className="text-xs text-slate-400 uppercase tracking-wider font-semibold">
                    Completed
                  </span>
                  <p className="font-medium text-slate-700">
                    {report.completed_at
                      ? new Date(report.completed_at).toLocaleDateString(
                          undefined,
                          {
                            year: "numeric",
                            month: "short",
                            day: "numeric",
                            hour: "2-digit",
                            minute: "2-digit",
                          }
                        )
                      : "Completed"}
                  </p>
                </div>
              </div>

              {/* Strengths & Growth Areas */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="p-4 rounded-xl bg-emerald-50/60 border border-emerald-100 space-y-2">
                  <div className="flex items-center space-x-2 text-emerald-800 font-bold text-sm">
                    <svg
                      className="w-4 h-4 text-emerald-600"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M5 13l4 4L19 7"
                      />
                    </svg>
                    <span>Key Strengths Observed</span>
                  </div>
                  {report.top_strengths.length > 0 ? (
                    <ul className="space-y-1.5 text-xs text-emerald-900 font-medium">
                      {report.top_strengths.map((s, i) => (
                        <li key={i} className="flex items-start space-x-2">
                          <span className="text-emerald-500 font-bold">•</span>
                          <span>{s}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-emerald-700 italic">
                      Responses demonstrated balanced technical readiness.
                    </p>
                  )}
                </div>

                <div className="p-4 rounded-xl bg-indigo-50/60 border border-indigo-100 space-y-2">
                  <div className="flex items-center space-x-2 text-indigo-800 font-bold text-sm">
                    <svg
                      className="w-4 h-4 text-indigo-600"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M13 10V3L4 14h7v7l9-11h-7z"
                      />
                    </svg>
                    <span>Recommended Growth Areas</span>
                  </div>
                  {report.priority_development_areas.length > 0 ? (
                    <ul className="space-y-1.5 text-xs text-indigo-900 font-medium">
                      {report.priority_development_areas.map((d, i) => (
                        <li key={i} className="flex items-start space-x-2">
                          <span className="text-indigo-500 font-bold">•</span>
                          <span>{d}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-indigo-700 italic">
                      Continue deepening practical hands-on architecture examples.
                    </p>
                  )}
                </div>
              </div>

              {/* Dimension Summary */}
              {Object.keys(report.dimension_summary).length > 0 && (
                <div className="p-4 rounded-xl bg-white border border-slate-200 space-y-3">
                  <h3 className="text-xs font-bold text-slate-700 uppercase tracking-wider">
                    Response Dimension Assessment
                  </h3>
                  <div className="space-y-2.5">
                    {Object.entries(report.dimension_summary).map(
                      ([dim, score]) => (
                        <div key={dim} className="space-y-1">
                          <div className="flex justify-between text-xs font-medium text-slate-700">
                            <span>{dim}</span>
                            <span className="text-slate-500">{score} / 5.0</span>
                          </div>
                          <div className="w-full bg-slate-100 rounded-full h-2 overflow-hidden">
                            <div
                              className="bg-indigo-600 h-2 rounded-full transition-all duration-500"
                              style={{
                                width: `${Math.min(100, Math.max(10, (score / 5.0) * 100))}%`,
                              }}
                            />
                          </div>
                        </div>
                      )
                    )}
                  </div>
                </div>
              )}

              {/* Question-by-Question Feedback */}
              <div className="space-y-3">
                <h3 className="text-xs font-bold text-slate-700 uppercase tracking-wider">
                  Question Responses &amp; Guidance
                </h3>
                <div className="space-y-3">
                  {report.questions_feedback.map((q) => (
                    <div
                      key={q.slot_id}
                      className="p-4 rounded-xl border border-slate-200 bg-white hover:border-slate-300 transition-colors space-y-2.5"
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center space-x-2">
                          <span className="text-xs font-bold text-slate-500 bg-slate-100 px-2 py-0.5 rounded">
                            Q{q.question_order}
                          </span>
                          {q.competency_name && (
                            <span className="text-xs font-medium text-slate-600">
                              {q.competency_name}
                            </span>
                          )}
                        </div>
                        <span
                          className={`text-xs px-2.5 py-0.5 rounded-full font-semibold ${
                            q.performance_indicator.includes("Strong")
                              ? "bg-emerald-100 text-emerald-800"
                              : q.performance_indicator.includes("Developing")
                              ? "bg-amber-100 text-amber-800"
                              : "bg-indigo-100 text-indigo-800"
                          }`}
                        >
                          {q.performance_indicator}
                        </span>
                      </div>

                      <p className="text-xs text-slate-800 font-medium line-clamp-2">
                        {q.question_text}
                      </p>

                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs pt-1 border-t border-slate-100">
                        {q.reviewed_strength && (
                          <div className="text-emerald-800 bg-emerald-50/50 p-2 rounded-lg">
                            <span className="font-bold block text-[11px] text-emerald-900">
                              What was done well:
                            </span>
                            <span className="mt-0.5 block">{q.reviewed_strength}</span>
                          </div>
                        )}
                        {q.suggested_growth_area && (
                          <div className="text-indigo-800 bg-indigo-50/50 p-2 rounded-lg">
                            <span className="font-bold block text-[11px] text-indigo-900">
                              Suggested improvement:
                            </span>
                            <span className="mt-0.5 block">{q.suggested_growth_area}</span>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 bg-slate-50 border-t border-slate-100 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-semibold text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-100 transition-colors shadow-sm"
          >
            Close Report
          </button>
        </div>
      </div>
    </div>
  );
};
