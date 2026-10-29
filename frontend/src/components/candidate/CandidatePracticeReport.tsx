import React, { useEffect, useRef, useState } from "react";
import {
  CandidatePracticeReportDto,
  getPracticeReport,
  retryPracticeReport,
} from "../../api/candidatePracticeSession";
import { Icon } from "../Icon";

interface CandidatePracticeReportProps {
  sessionId: string;
  onExit: () => void;
  onPracticeAgain?: () => void;
}

export function CandidatePracticeReport({ sessionId, onExit, onPracticeAgain }: CandidatePracticeReportProps) {
  const [report, setReport] = useState<CandidatePracticeReportDto | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRetrying, setIsRetrying] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedQuestionId, setSelectedQuestionId] = useState<string | null>(null);

  const reportHeaderRef = useRef<HTMLHeadingElement>(null);

  const loadReport = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const data = await getPracticeReport(sessionId);
      setReport(data);
    } catch (err: any) {
      setError(err?.message || "Failed to load practice coaching report.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadReport();
  }, [sessionId]);

  useEffect(() => {
    if (report && reportHeaderRef.current) {
      reportHeaderRef.current.focus();
    }
  }, [report]);

  const handleRetry = async () => {
    try {
      setIsRetrying(true);
      setError(null);
      const updated = await retryPracticeReport(sessionId);
      setReport(updated);
    } catch (err: any) {
      setError(err?.message || "Failed to retry summary generation.");
    } finally {
      setIsRetrying(false);
    }
  };

  if (isLoading) {
    return (
      <div className="practice-report-loading" aria-live="polite">
        <div className="loading-spinner" />
        <p style={{ marginTop: "16px", color: "var(--text-secondary, #475569)", fontWeight: 600 }}>
          Answers complete — preparing your coaching report...
        </p>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="practice-report-container" style={{ padding: "24px" }}>
        <div className="feedback-box error" role="alert">
          <Icon name="alert-circle" size={20} />
          <div>
            <strong>Report Unavailable:</strong> {error || "Unable to retrieve report."}
          </div>
        </div>
        <div style={{ marginTop: "16px", display: "flex", gap: "12px" }}>
          <button type="button" className="btn btn-primary" onClick={loadReport}>
            <Icon name="refresh" size={14} />
            <span>Try Again</span>
          </button>
          <button type="button" className="btn btn-secondary" onClick={onExit}>
            Return to Directory
          </button>
        </div>
      </div>
    );
  }

  // Render SVG Competency Radar when 3 to 6 assessable competencies exist
  const assessableCompetencies = report.competency_aggregates.filter(
    (c) => c.numeric_score !== null && c.numeric_score !== undefined
  );

  const renderRadarChart = () => {
    const size = 320;
    const center = size / 2;
    const radius = 100;
    const count = assessableCompetencies.length;
    if (count < 3 || count > 6) return null;

    const angleStep = (2 * Math.PI) / count;

    // Background concentric polygon points for levels 1, 2, 3, 4
    const gridLevels = [1, 2, 3, 4];
    const polygonPoints = (scaleVal: number) => {
      return assessableCompetencies
        .map((_, i) => {
          const angle = i * angleStep - Math.PI / 2;
          const r = (scaleVal / 4) * radius;
          const x = center + r * Math.cos(angle);
          const y = center + r * Math.sin(angle);
          return `${x},${y}`;
        })
        .join(" ");
    };

    // Data polygon points
    const dataPoints = assessableCompetencies.map((comp, i) => {
      const angle = i * angleStep - Math.PI / 2;
      const score = comp.numeric_score || 1;
      const r = (score / 4) * radius;
      const x = center + r * Math.cos(angle);
      const y = center + r * Math.sin(angle);
      return { x, y, name: comp.competency_name, score };
    });

    const dataPointsStr = dataPoints.map((p) => `${p.x},${p.y}`).join(" ");

    return (
      <div className="report-radar-wrapper" style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
        <svg
          data-testid="competency-radar-svg"
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          role="img"
          aria-label={`Competency Radar Chart showing ${assessableCompetencies.map((c) => `${c.competency_name}: ${c.anchored_level}`).join(", ")}`}
        >
          <title>Competency Radar Chart</title>
          <desc>Visual polygon map of demonstrated competency depth across practice turns.</desc>

          {/* Concentric grid rings */}
          {gridLevels.map((lvl) => (
            <polygon
              key={`grid-${lvl}`}
              points={polygonPoints(lvl)}
              fill={lvl === 4 ? "var(--bg-surface-subtle, #F0F2EF)" : "none"}
              stroke="var(--border-subtle, #D6DCD7)"
              strokeWidth="1"
              strokeDasharray={lvl < 4 ? "3,3" : "none"}
            />
          ))}

          {/* Axes from center to outer vertex */}
          {assessableCompetencies.map((_, i) => {
            const angle = i * angleStep - Math.PI / 2;
            const x = center + radius * Math.cos(angle);
            const y = center + radius * Math.sin(angle);
            return <line key={`axis-${i}`} x1={center} y1={center} x2={x} y2={y} stroke="var(--border-control, #7B857F)" strokeWidth="1" />;
          })}

          {/* Data Polygon */}
          <polygon
            points={dataPointsStr}
            fill="rgba(23, 79, 74, 0.15)"
            stroke="var(--accent-primary, #174F4A)"
            strokeWidth="2.5"
          />

          {/* Data Points and Axis Labels */}
          {dataPoints.map((p, i) => {
            const angle = i * angleStep - Math.PI / 2;
            const labelR = radius + 32;
            const lx = center + labelR * Math.cos(angle);
            const ly = center + labelR * Math.sin(angle);

            return (
              <g key={`point-${i}`}>
                <circle cx={p.x} cy={p.y} r={5} fill="var(--accent-primary, #174F4A)" stroke="#ffffff" strokeWidth="2" />
                <text
                  x={lx}
                  y={ly}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize="12"
                  fontWeight="600"
                  fill="var(--text-primary, #202824)"
                >
                  {p.name.length > 18 ? `${p.name.slice(0, 16)}...` : p.name}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    );
  };

  return (
    <div className="practice-report-container" style={{ maxWidth: "1000px", margin: "0 auto", padding: "24px 20px" }}>
      {/* Header Banner */}
      <div className="practice-report-header">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "16px" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
              <span className="practice-report-badge">Practice Coaching Report</span>
              <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)" }}>
                Version {report.report_version}
              </span>
            </div>
            <h1
              ref={reportHeaderRef}
              tabIndex={-1}
              style={{ fontSize: "24px", fontWeight: 800, color: "var(--text-primary, #0f172a)", margin: "0 0 6px 0" }}
            >
              {report.job_title} Practice Summary
            </h1>
            <p style={{ fontSize: "14px", color: "var(--text-secondary, #475569)", margin: 0 }}>
              Completed {new Date(report.created_at).toLocaleDateString()} • {report.completion_counts.total_primary_questions} Questions Practiced
            </p>
          </div>

          <div style={{ display: "flex", gap: "10px" }}>
            {onPracticeAgain && (
              <button type="button" className="btn btn-secondary" onClick={onPracticeAgain}>
                <Icon name="refresh" size={14} />
                <span>Practice Again</span>
              </button>
            )}
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleRetry}
              disabled={isRetrying}
            >
              {isRetrying ? (
                <>
                  <div className="loading-spinner-sm" style={{ marginRight: "6px" }} />
                  <span>Generating Report...</span>
                </>
              ) : (
                <>
                  <Icon name="sparkles" size={14} />
                  <span>Regenerate Report</span>
                </>
              )}
            </button>
            <button type="button" className="btn btn-primary" onClick={onExit}>
              Return to Directory
            </button>
          </div>
        </div>
      </div>

      {/* Summary Narrative Card */}
      <div className="practice-report-card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
          <h2 style={{ fontSize: "16px", fontWeight: 700, color: "#0f172a", margin: 0 }}>
            Coaching Overview
          </h2>
          {report.lifecycle_status === "SUMMARY_FAILED_RETRYABLE" && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleRetry}
              disabled={isRetrying}
            >
              {isRetrying ? (
                <>
                  <div className="loading-spinner-sm" style={{ marginRight: "6px" }} />
                  <span>Retrying Summary...</span>
                </>
              ) : (
                <>
                  <Icon name="refresh" size={12} />
                  <span>Retry Summary</span>
                </>
              )}
            </button>
          )}
        </div>

        {report.lifecycle_status === "SUMMARY_FAILED_RETRYABLE" && (
          <div className="feedback-box warning" style={{ marginBottom: "16px", backgroundColor: "#fffbeb", border: "1px solid #fde68a", color: "#92400e", padding: "12px", borderRadius: "6px" }} role="alert">
            <Icon name="alert-circle" size={16} />
            <div style={{ fontSize: "13px" }}>
              <strong>Summary generation temporarily unavailable.</strong> All your deterministic scores and question feedback below are preserved. You can click "Retry Summary" to generate your AI coaching summary.
            </div>
          </div>
        )}

        {report.summary_text && (
          <p style={{ fontSize: "14px", color: "#334155", lineHeight: 1.6, margin: "0 0 16px 0" }}>
            {report.summary_text}
          </p>
        )}

        {/* Strengths and Priority Improvements Grid */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: "16px" }}>
          {/* Demonstrated Strengths */}
          <div className="report-highlight-box strength">
            <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "10px" }}>
              <Icon name="check-circle" size={16} />
              <h3 style={{ fontSize: "14px", fontWeight: 700, margin: 0 }}>
                What You Demonstrated Well
              </h3>
            </div>
            {report.strongest_areas.length > 0 ? (
              <ul style={{ margin: 0, paddingLeft: "18px", fontSize: "13px", color: "#1e293b", lineHeight: 1.5 }}>
                {report.strongest_areas.map((st, i) => (
                  <li key={`st-${i}`} style={{ marginBottom: "8px" }}>
                    <strong>{st.title}: </strong>
                    <span>{st.observation}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p style={{ fontSize: "13px", color: "#64748b", margin: 0 }}>
                Responses were brief; continue practicing to demonstrate specific technical actions and outcomes.
              </p>
            )}
          </div>

          {/* Priority Improvements */}
          <div className="report-highlight-box improvement">
            <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "10px" }}>
              <Icon name="sparkles" size={16} />
              <h3 style={{ fontSize: "14px", fontWeight: 700, margin: 0 }}>
                Priority Areas to Strengthen
              </h3>
            </div>
            {report.priority_improvements.length > 0 ? (
              <ul style={{ margin: 0, paddingLeft: "18px", fontSize: "13px", color: "#1e293b", lineHeight: 1.5 }}>
                {report.priority_improvements.map((imp, i) => (
                  <li key={`imp-${i}`} style={{ marginBottom: "8px" }}>
                    <strong>{imp.title}: </strong>
                    <span>{imp.observation}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p style={{ fontSize: "13px", color: "#64748b", margin: 0 }}>
                Focus on structured execution: explain the context, the exact action you owned, and verified results.
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Competency Depth Section */}
      <div className="practice-report-card">
        <h2 style={{ fontSize: "16px", fontWeight: 700, color: "#0f172a", marginBottom: "16px" }}>
          Competency Evaluation
        </h2>

        {assessableCompetencies.length >= 3 && assessableCompetencies.length <= 6 && renderRadarChart()}

        {/* Accessible Competency Table */}
        <div className="table-responsive" style={{ marginTop: "16px" }}>
          <table className="data-table" aria-label="Competency Evaluation Summary">
            <thead>
              <tr>
                <th scope="col">Competency</th>
                <th scope="col">Performance Level</th>
                <th scope="col">Assessable Questions</th>
                <th scope="col">Coaching Observation</th>
              </tr>
            </thead>
            <tbody>
              {report.competency_aggregates.map((comp) => (
                <tr key={comp.competency_name}>
                  <td style={{ fontWeight: 600 }}>{comp.competency_name}</td>
                  <td>
                    <span className={`dimension-badge ${(comp.anchored_level || "NOT_ASSESSED").toLowerCase()}`}>
                      {comp.display_band || comp.anchored_level}
                    </span>
                  </td>
                  <td>
                    {comp.assessable_count} of {comp.total_questions || comp.assessable_count + comp.excluded_count}
                  </td>
                  <td style={{ fontSize: "13px", color: "#475569" }}>{comp.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Response Quality Dimensions */}
      <div className="practice-report-card">
        <h2 style={{ fontSize: "16px", fontWeight: 700, color: "#0f172a", marginBottom: "16px" }}>
          Response Quality Breakdown
        </h2>
        <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
          {report.dimension_aggregates.map((dim) => {
            const score = dim.numeric_score || 0;
            const pct = (score / 4) * 100;

            return (
              <div key={dim.dimension_key} style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: "13px", fontWeight: 600, color: "#1e293b" }}>
                    {dim.dimension_label}
                  </span>
                  <span style={{ fontSize: "12px", color: "#64748b" }}>
                    {dim.display_label} ({dim.assessable_count} assessable response{dim.assessable_count !== 1 ? "s" : ""})
                  </span>
                </div>
                <div
                  className="progress-track"
                  role="progressbar"
                  aria-valuenow={score}
                  aria-valuemin={0}
                  aria-valuemax={4}
                  aria-label={`${dim.dimension_label}: ${dim.display_label}`}
                  style={{ height: "8px", backgroundColor: "#e2e8f0", borderRadius: "4px", overflow: "hidden" }}
                >
                  <div
                    style={{
                      height: "100%",
                      width: `${pct}%`,
                      backgroundColor: score >= 3.5 ? "var(--color-primary, #174F4A)" : score >= 2.5 ? "#35566F" : score >= 1.5 ? "var(--color-warning, #76551F)" : "var(--border-control, #7B857F)",
                      borderRadius: "4px",
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Question-by-Question Sequence & Pattern */}
      <div className="practice-report-card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
          <h2 style={{ fontSize: "16px", fontWeight: 700, color: "#0f172a", margin: 0 }}>
            Question-by-Question Progression
          </h2>
          <span style={{ fontSize: "12px", fontWeight: 600, color: "#475569" }}>
            Pattern: {report.trend_classification.replace(/_/g, " ")}
          </span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {report.question_trend.map((q) => {
            const isSelected = selectedQuestionId === q.question_id;

            return (
              <div
                key={q.question_id}
                className={`question-trend-item ${isSelected ? "selected" : ""}`}
                style={{
                  border: "1px solid #e2e8f0",
                  borderRadius: "8px",
                  padding: "12px 16px",
                  backgroundColor: isSelected ? "#f8fafc" : "#ffffff",
                }}
              >
                <div
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", cursor: "pointer" }}
                  onClick={() => setSelectedQuestionId(isSelected ? null : q.question_id)}
                  role="button"
                  tabIndex={0}
                  aria-expanded={isSelected}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelectedQuestionId(isSelected ? null : q.question_id);
                    }
                  }}
                >
                  <div>
                    <span style={{ fontSize: "12px", fontWeight: 700, color: "var(--accent-primary, #174F4A)", textTransform: "uppercase" }}>
                      Question {q.sequence_number} • {q.competency_name}
                    </span>
                    <div style={{ fontSize: "14px", fontWeight: 600, color: "#0f172a", marginTop: "2px" }}>
                      {q.wording}
                    </div>
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: "8px", flexShrink: 0 }}>
                    <span className={`dimension-badge ${q.assessability_status.toLowerCase()}`}>
                      {q.assessability_status.replace(/_/g, " ")}
                    </span>
                    <Icon name={isSelected ? "chevron-up" : "chevron-down"} size={14} />
                  </div>
                </div>

                {/* Expanded Details */}
                {isSelected && (
                  <div style={{ marginTop: "14px", paddingTop: "14px", borderTop: "1px solid #e2e8f0", fontSize: "13px", display: "flex", flexDirection: "column", gap: "12px" }}>
                    {q.question_format === "CONVERSATION" ? (
                      <>
                        {/* Candidate Answer Box */}
                        {q.candidate_answer && (
                          <div style={{ backgroundColor: "#f8fafc", padding: "10px 14px", borderRadius: "6px", borderLeft: "3px solid #6366f1" }}>
                            <div style={{ fontSize: "11px", fontWeight: 700, color: "#475569", textTransform: "uppercase", marginBottom: "4px" }}>
                              Candidate's Submitted Answer:
                            </div>
                            <div style={{ fontSize: "13px", color: "#1e293b", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
                              {q.candidate_answer}
                            </div>
                          </div>
                        )}

                        {/* Answer Summary if available */}
                        {q.answer_summary && (
                          <div style={{ fontSize: "13px", color: "#334155", lineHeight: 1.5 }}>
                            <strong>Analysis: </strong>{q.answer_summary}
                          </div>
                        )}

                        {/* What was done well & What was missing side-by-side or stacked */}
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: "12px" }}>
                          {q.what_was_done_well && q.what_was_done_well.length > 0 && (
                            <div style={{ backgroundColor: "#f0fdfa", border: "1px solid #ccfbf1", borderRadius: "6px", padding: "10px 12px" }}>
                              <div style={{ fontWeight: 700, color: "#0f766e", fontSize: "12px", marginBottom: "6px", display: "flex", alignItems: "center", gap: "4px" }}>
                                <Icon name="check" size={14} />
                                <span>What Was Done Well</span>
                              </div>
                              <ul style={{ margin: 0, paddingLeft: "16px", color: "#134e4a", fontSize: "12px", lineHeight: 1.5 }}>
                                {q.what_was_done_well.map((item, idx) => (
                                  <li key={`well-${idx}`} style={{ marginBottom: "4px" }}>{item}</li>
                                ))}
                              </ul>
                            </div>
                          )}

                          {q.what_was_missing && q.what_was_missing.length > 0 && (
                            <div style={{ backgroundColor: "#fffbeb", border: "1px solid #fef3c7", borderRadius: "6px", padding: "10px 12px" }}>
                              <div style={{ fontWeight: 700, color: "#b45309", fontSize: "12px", marginBottom: "6px", display: "flex", alignItems: "center", gap: "4px" }}>
                                <Icon name="sparkles" size={14} />
                                <span>What Was Missing / Improvement</span>
                              </div>
                              <ul style={{ margin: 0, paddingLeft: "16px", color: "#78350f", fontSize: "12px", lineHeight: 1.5 }}>
                                {q.what_was_missing.map((item, idx) => (
                                  <li key={`miss-${idx}`} style={{ marginBottom: "4px" }}>{item}</li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </div>

                        {/* Stronger Answer Structure recommendation */}
                        {q.stronger_answer_structure && (
                          <div style={{ backgroundColor: "#f1f5f9", padding: "10px 12px", borderRadius: "6px", fontSize: "12px", color: "#334155" }}>
                            <strong style={{ color: "#0f172a" }}>Stronger Answer Structure: </strong>
                            <span>{q.stronger_answer_structure}</span>
                          </div>
                        )}

                        {/* Dimension badges */}
                        <div>
                          <div style={{ fontWeight: 600, color: "#475569", fontSize: "11px", textTransform: "uppercase", marginBottom: "6px" }}>
                            Evaluated Dimensions:
                          </div>
                          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                            {Object.entries(q.dimension_levels || {}).map(([dimKey, level]) => (
                              <span key={dimKey} className={`dimension-badge ${(level || "").toLowerCase()}`}>
                                {dimKey.replace(/_/g, " ")}: {level}
                              </span>
                            ))}
                          </div>
                        </div>
                      </>
                    ) : (
                      <div>
                        <span style={{ fontWeight: 600 }}>MCQ Evaluation: </span>
                        {q.is_mcq_correct !== null ? (
                          <span style={{ color: q.is_mcq_correct ? "#047857" : "#b45309", fontWeight: 600 }}>
                            {q.is_mcq_correct ? "Correct Choice" : "Incorrect Choice"}
                          </span>
                        ) : (
                          <span>Skipped</span>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* MCQ Knowledge Checks Breakdown (when MCQs exist) */}
      {report.mcq_breakdown && report.mcq_breakdown.total_count > 0 && (
        <div className="practice-report-card">
          <h2 style={{ fontSize: "16px", fontWeight: 700, color: "#0f172a", marginBottom: "12px" }}>
            Multiple-Choice Knowledge Checks
          </h2>
          <div style={{ display: "flex", gap: "24px", marginBottom: "16px", backgroundColor: "#f8fafc", padding: "12px 16px", borderRadius: "6px" }}>
            <div>
              <span style={{ fontSize: "11px", color: "#64748b", textTransform: "uppercase", fontWeight: 600 }}>Accuracy</span>
              <div style={{ fontSize: "18px", fontWeight: 800, color: "#0f172a" }}>
                {report.mcq_breakdown.correct_count} / {report.mcq_breakdown.attempted_count} Correct
              </div>
            </div>
            <div style={{ borderLeft: "1px solid #e2e8f0", paddingLeft: "24px" }}>
              <span style={{ fontSize: "11px", color: "#64748b", textTransform: "uppercase", fontWeight: 600 }}>Attempted</span>
              <div style={{ fontSize: "18px", fontWeight: 800, color: "#0f172a" }}>
                {report.mcq_breakdown.attempted_count} of {report.mcq_breakdown.total_count}
              </div>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {report.mcq_breakdown.topics.map((t, idx) => (
              <div key={idx} style={{ border: "1px solid #e2e8f0", borderRadius: "6px", padding: "10px 14px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontWeight: 600, fontSize: "13px", color: "#1e293b" }}>{t.topic_name}</span>
                  <span style={{ fontSize: "12px", color: "#475569" }}>
                    {t.correct_count} / {t.attempted_count} correct
                  </span>
                </div>
                {t.learning_points.length > 0 && (
                  <div style={{ marginTop: "6px", fontSize: "12px", color: "#64748b" }}>
                    <strong>Key takeaway: </strong>{t.learning_points[0]}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Next Practice Action Plan */}
      <div className="practice-report-card" style={{ borderLeft: "4px solid var(--accent-primary, #174F4A)" }}>
        <h2 style={{ fontSize: "16px", fontWeight: 700, color: "#0f172a", marginBottom: "12px" }}>
          Next Practice Plan
        </h2>
        <ol style={{ margin: 0, paddingLeft: "20px", fontSize: "14px", color: "#334155", lineHeight: 1.6 }}>
          {report.next_practice_plan.map((action, i) => (
            <li key={i} style={{ marginBottom: "8px" }}>
              {action}
            </li>
          ))}
        </ol>
      </div>

      {/* Mandatory Disclaimer */}
      <div style={{ textAlign: "center", padding: "16px", fontSize: "12px", color: "#64748b" }}>
        {report.disclaimer}
      </div>
    </div>
  );
}
