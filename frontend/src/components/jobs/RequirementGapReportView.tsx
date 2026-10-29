import React, { useState, useEffect, useMemo } from "react";
import {
  GapReportDetail,
  GapReportItem,
  fetchGapReportDetail,
  fetchCanonicalGapReportJson,
} from "../../api/jobs";
import { Icon } from "../Icon";
import { CategoryEvidenceCoverageChart, getCanonicalSummaryData } from "./VisualAnalyticsCharts";
import { validateInternalReturnUrl, validateProcessedResultsReturnUrl } from "../../utils/navigation";

interface RequirementGapReportViewProps {
  reportId: string;
  candidateName?: string;
  jobTitle?: string;
  returnUrl?: string | null;
  onNavigateBack: () => void;
  onNavigateBackWithFallback?: (fallbackState: {
    jobId: string;
    jobVersionId: string;
    revision: number;
    runId: string;
  }) => void;
  onOpenEvidenceDrawer?: (candidateId: string, requirementLabel?: string) => void;
}

export function RequirementGapReportView({
  reportId,
  candidateName,
  jobTitle,
  returnUrl,
  onNavigateBack,
  onNavigateBackWithFallback,
  onOpenEvidenceDrawer,
}: RequirementGapReportViewProps) {
  const [report, setReport] = useState<GapReportDetail | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Filters state
  const [selectedStateFilter, setSelectedStateFilter] = useState<string>("ALL");
  const [selectedNecessityFilter, setSelectedNecessityFilter] = useState<string>("ALL");
  const [onlyReviewRequired, setOnlyReviewRequired] = useState<boolean>(false);
  const [donutScope, setDonutScope] = useState<"ALL" | "MUST_HAVE" | "PREFERRED">("ALL");

  // Active evidence modal/drawer state
  const [isDownloadingJson, setIsDownloadingJson] = useState<boolean>(false);

  // Collapsible accordion for 'How this works' advisory and 'Analysis details' panel
  const [showHowItWorks, setShowHowItWorks] = useState<boolean>(false);
  const [showAnalysisDetails, setShowAnalysisDetails] = useState<boolean>(false);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());

  const toggleRowExpanded = (rowId: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(rowId)) {
        next.delete(rowId);
      } else {
        next.add(rowId);
      }
      return next;
    });
  };

  useEffect(() => {
    let isMounted = true;
    async function loadReport() {
      try {
        setIsLoading(true);
        setError(null);
        const data = await fetchGapReportDetail(reportId);
        if (isMounted) {
          setReport(data);
        }
      } catch (err: any) {
        if (isMounted) {
          setError(err?.message || "Failed to load requirement gap report.");
        }
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    }
    loadReport();
    return () => {
      isMounted = false;
    };
  }, [reportId]);

  // Handle smart Back Navigation with internal URL validation and deterministic fallback
  const handleBackNavigation = () => {
    const safeReturn = validateProcessedResultsReturnUrl(
      returnUrl,
      report?.jobId,
      report?.matchRunId
    );

    if (safeReturn) {
      onNavigateBack();
      return;
    }

    if (report && onNavigateBackWithFallback) {
      onNavigateBackWithFallback({
        jobId: report.jobId,
        jobVersionId: report.jobVersionId,
        revision: report.revision,
        runId: report.matchRunId,
      });
      return;
    }

    onNavigateBack();
  };

  // Summary counts check
  const stateCounts = useMemo(() => {
    if (!report) {
      return {
        covered: 0,
        partiallyCovered: 0,
        notEvidenced: 0,
        unverified: 0,
        conflicting: 0,
        notApplicable: 0,
        total: 0,
      };
    }
    const r = report as any;
    return {
      covered: report.coveredCount ?? r.covered_count ?? r.summary?.reconciled_state_counts?.COVERED ?? 0,
      partiallyCovered: report.partiallyCoveredCount ?? r.partially_covered_count ?? r.summary?.reconciled_state_counts?.PARTIALLY_COVERED ?? 0,
      notEvidenced: report.notEvidencedCount ?? r.not_evidenced_count ?? r.summary?.reconciled_state_counts?.NOT_EVIDENCED ?? 0,
      unverified: report.unverifiedCount ?? r.unverified_count ?? r.summary?.reconciled_state_counts?.UNVERIFIED ?? 0,
      conflicting: report.conflictingCount ?? r.conflicting_count ?? r.summary?.reconciled_state_counts?.CONFLICTING ?? 0,
      notApplicable: report.notApplicableCount ?? r.not_applicable_count ?? r.summary?.reconciled_state_counts?.NOT_APPLICABLE ?? 0,
      total: report.totalRequirements ?? r.total_requirements ?? r.summary?.total_requirements ?? 0,
    };
  }, [report]);

  const rawItems: GapReportItem[] = useMemo(() => {
    if (!report) return [];
    return report.items || (report as any).gap_items || (report as any).gaps || [];
  }, [report]);

  // Filtered items (does not mutate total report summary counts)
  const filteredItems = useMemo(() => {
    return rawItems.filter((it: any) => {
      const state = it.gapState || it.reconciled_state || it.gap_state;
      const necessity = it.necessity || it.requirement_type;
      const reviewRequired = it.reviewRequired ?? it.review_required ?? (state !== "COVERED");

      if (selectedStateFilter !== "ALL" && state !== selectedStateFilter) {
        return false;
      }
      if (selectedNecessityFilter !== "ALL" && necessity !== selectedNecessityFilter) {
        return false;
      }
      if (onlyReviewRequired && !reviewRequired) {
        return false;
      }
      return true;
    });
  }, [rawItems, selectedStateFilter, selectedNecessityFilter, onlyReviewRequired]);

  // Handle JSON Download
  const handleDownloadJson = async () => {
    try {
      setIsDownloadingJson(true);
      const canonicalData = await fetchCanonicalGapReportJson(reportId);
      const jsonString = `data:text/json;charset=utf-8,${encodeURIComponent(
        JSON.stringify(canonicalData, null, 2)
      )}`;
      const downloadAnchor = document.createElement("a");
      downloadAnchor.setAttribute("href", jsonString);
      downloadAnchor.setAttribute("download", `gap-report-${reportId}.json`);
      document.body.appendChild(downloadAnchor);
      downloadAnchor.click();
      downloadAnchor.remove();
    } catch (err: any) {
      alert("Failed to download canonical JSON report: " + (err?.message || ""));
    } finally {
      setIsDownloadingJson(false);
    }
  };

  // Handle Print
  const handlePrint = () => {
    window.print();
  };

  if (isLoading) {
    return (
      <div className="gap-report-view-container" aria-busy="true">
        <div className="empty-state-card" style={{ padding: "48px 24px" }}>
          <Icon name="refresh" size={24} className="spin-icon" />
          <p style={{ marginTop: "12px", color: "var(--text-secondary)" }}>
            Loading requirement gap report…
          </p>
        </div>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="gap-report-view-container">
        <div className="gap-report-header">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleBackNavigation}
            aria-label="Back to results"
          >
            <Icon name="arrow-left" size={14} />
            <span>Back to results</span>
          </button>
        </div>
        <div className="conflict-alert-card" role="alert" style={{ marginTop: "16px" }}>
          <div className="conflict-icon-wrap">
            <Icon name="alert-circle" size={18} />
          </div>
          <div>
            <h4 className="conflict-title">Report Unavailable</h4>
            <p className="conflict-desc">
              {error || "Requirement gap report could not be found."}
            </p>
            <button
              type="button"
              className="btn btn-secondary btn-xs"
              style={{ marginTop: "8px" }}
              onClick={handleBackNavigation}
              aria-label="Back to results"
            >
              Back to results
            </button>
          </div>
        </div>
      </div>
    );
  }

  const formattedDate = new Date(report.evaluationDate || report.createdAt).toLocaleDateString(
    undefined,
    { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
  );

  // Canonical Evidence Summary counts
  const summaryObj = report.evidence_summary || (report as any).evidenceSummary;
  const canon = getCanonicalSummaryData(summaryObj);

  const isSummaryUnavailable = !canon && (report.mustHaveTotalCount === undefined || report.mustHaveTotalCount === null);
  const reqTotal = canon ? canon.requiredTotal : (report.mustHaveTotalCount ?? 0);
  const reqEvidenced = canon ? canon.requiredEvidenceFound : (report.mustHaveEvidencedCount ?? 0);
  const prefTotal = canon ? canon.preferredTotal : 0;
  const prefEvidenced = canon ? canon.preferredEvidenceFound : 0;

  return (
    <div className="gap-report-view-container" aria-label="Resume Evidence &amp; Criteria Comparison">
      {/* 1. Header and Actions Row */}
      <div className="gap-report-header">
        <div className="gap-report-title-wrap">
          <button
            type="button"
            className="btn btn-secondary btn-sm no-print"
            onClick={handleBackNavigation}
            aria-label="Back to results"
          >
            <Icon name="arrow-left" size={14} />
            <span>Back to results</span>
          </button>
          <div>
            <h2 className="gap-report-title">
              Resume Evidence Report
            </h2>
            <p style={{ fontSize: "12px", color: "var(--text-secondary)", margin: "2px 0 0 0" }}>
              {candidateName || `Candidate #${report.profileId.substring(0, 8)}`} &bull; {jobTitle || `Job #${report.jobId.substring(0, 8)}`} (Rev #{report.revision})
            </p>
          </div>
        </div>

        <div className="gap-filter-group no-print">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleDownloadJson}
            disabled={isDownloadingJson}
            aria-label="Download Canonical Report JSON"
          >
            <Icon name="download" size={13} />
            <span>{isDownloadingJson ? "Exporting…" : "Download JSON"}</span>
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handlePrint}
            aria-label="Print Report"
          >
            <Icon name="printer" size={13} />
            <span>Print report</span>
          </button>
        </div>
      </div>

      {/* Legacy Report Notice Banner */}
      {report.legacyReportNotice && (
        <div className="conflict-alert-card" role="note" style={{ borderLeft: "4px solid var(--accent-primary)" }}>
          <div className="conflict-icon-wrap" style={{ color: "var(--accent-primary)" }}>
            <Icon name="info" size={18} />
          </div>
          <div>
            <h4 className="conflict-title" style={{ color: "var(--text-primary)" }}>Legacy Report Version</h4>
            <p className="conflict-desc">
              {report.legacyReportNotice}
            </p>
          </div>
        </div>
      )}

      {/* 2. Compact Information Banner with 'How this works' Disclosure */}
      <div className="gap-limitation-banner" role="note" aria-label="Advisory Notice" style={{ flexDirection: "column", gap: "8px" }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: "10px" }}>
          <div className="gap-limitation-icon">
            <Icon name="info" size={16} />
          </div>
          <div style={{ flex: 1 }}>
            <span>
              Compares documented resume evidence with this job’s criteria. Missing resume evidence does not mean the candidate lacks the qualification.
            </span>{" "}
            <button
              type="button"
              className="btn-link-action"
              onClick={() => setShowHowItWorks((prev) => !prev)}
              aria-expanded={showHowItWorks}
              style={{ fontSize: "12px", marginLeft: "6px" }}
            >
              {showHowItWorks ? "Hide details ▲" : "How this works ▼"}
            </button>
          </div>
        </div>

        {showHowItWorks && (
          <div style={{ marginTop: "6px", paddingTop: "8px", borderTop: "1px solid var(--border-subtle)", fontSize: "12px", lineHeight: "1.5", color: "var(--text-secondary)" }}>
            This requirement-evidence report compares documented professional evidence against recruiter-approved job criteria based on an immutable match result. It is an advisory diagnostic to guide human review and interview preparation—not an automated hiring or employment decision. Missing evidence in a resume indicates that the criterion was not documented in the provided profile; it does not prove a candidate lacks the skill.
          </div>
        )}
      </div>

      {/* 3. Summary Cards: Resume match, Required evidence, Preferred evidence */}
      <div className="gap-metrics-grid" aria-label="Summary Metrics">
        <div className="gap-metric-card">
          <span className="gap-metric-label">Resume match</span>
          <span className="gap-metric-value">
            {report.compatibilityScore !== null && report.compatibilityScore !== undefined
              ? `${Math.round(Number(report.compatibilityScore))}%`
              : "Recorded"}
          </span>
          <span className="gap-metric-subtext">Overall criteria match percentage</span>
        </div>

        <div className="gap-metric-card">
          <span className="gap-metric-label">Required evidence</span>
          <span className="gap-metric-value">
            {isSummaryUnavailable ? "Summary unavailable" : `${reqEvidenced} of ${reqTotal}`}
          </span>
          <span className="gap-metric-subtext">
            {isSummaryUnavailable
              ? "Canonical evidence summary unavailable"
              : reqEvidenced === reqTotal
              ? "All required criteria have supporting evidence"
              : `${reqTotal - reqEvidenced} required criteria lack full evidence`}
          </span>
        </div>

        {prefTotal > 0 && (
          <div className="gap-metric-card">
            <span className="gap-metric-label">Preferred evidence</span>
            <span className="gap-metric-value">
              {prefEvidenced} of {prefTotal}
            </span>
            <span className="gap-metric-subtext">Supporting evidence for optional criteria</span>
          </div>
        )}
      </div>

      {/* 3B. Non-Bar Visual 1: Evidence Coverage Donut & Visual 2: Requirement Status Grid */}
      {rawItems.length > 0 && (() => {
        // Filter items based on donutScope ("ALL", "MUST_HAVE", "PREFERRED")
        const scopeItems = rawItems.filter((it: any) => {
          const nec = it.necessity || it.requirement_type;
          if (donutScope === "MUST_HAVE") return nec === "MUST_HAVE";
          if (donutScope === "PREFERRED") return nec === "PREFERRED";
          return true;
        });

        // Exclude NOT_APPLICABLE from coverage denominator
        const applicableItems = scopeItems.filter((it: any) => {
          const state = it.gapState || it.reconciled_state || it.gap_state;
          return state !== "NOT_APPLICABLE";
        });

        const totalEvaluated = applicableItems.length;

        let countFound = 0;
        let countPartial = 0;
        let countVerify = 0;
        let countNotFound = 0;

        applicableItems.forEach((it: any) => {
          const state = it.gapState || it.reconciled_state || it.gap_state;
          if (state === "COVERED") {
            countFound += 1;
          } else if (state === "PARTIALLY_COVERED") {
            countPartial += 1;
          } else if (state === "UNVERIFIED" || state === "CONFLICTING_REVIEW_REQUIRED") {
            countVerify += 1;
          } else {
            countNotFound += 1;
          }
        });

        const pctFound = totalEvaluated > 0 ? Math.round((countFound / totalEvaluated) * 100) : 0;
        const pctPartial = totalEvaluated > 0 ? Math.round((countPartial / totalEvaluated) * 100) : 0;
        const pctVerify = totalEvaluated > 0 ? Math.round((countVerify / totalEvaluated) * 100) : 0;
        const pctNotFound = totalEvaluated > 0 ? Math.round((countNotFound / totalEvaluated) * 100) : 0;

        // Degree stops for CSS conic gradient
        const degFound = totalEvaluated > 0 ? (countFound / totalEvaluated) * 360 : 0;
        const degPartial = totalEvaluated > 0 ? (countPartial / totalEvaluated) * 360 : 0;
        const degVerify = totalEvaluated > 0 ? (countVerify / totalEvaluated) * 360 : 0;

        const donutGradStops = totalEvaluated > 0 ? [
          `var(--gap-covered) 0deg ${degFound}deg`,
          `var(--gap-unverified) ${degFound}deg ${degFound + degPartial}deg`,
          `var(--gap-unverified) ${degFound + degPartial}deg ${degFound + degPartial + degVerify}deg`,
          `var(--gap-partial) ${degFound + degPartial + degVerify}deg 360deg`,
        ].join(", ") : "#ebe7dc 0deg 360deg";

        // Group requirements for Requirement Status Grid
        const requiredTiles = rawItems.filter((it: any) => (it.necessity || it.requirement_type) === "MUST_HAVE");
        const preferredTiles = rawItems.filter((it: any) => (it.necessity || it.requirement_type) === "PREFERRED");

        const getTileStatusMeta = (it: any) => {
          const state = it.gapState || it.reconciled_state || it.gap_state;
          if (state === "COVERED") {
            return { label: "Evidence found", color: "var(--gap-covered)", bg: "var(--gap-covered-bg)", border: "var(--gap-covered-border)" };
          }
          if (state === "PARTIALLY_COVERED") {
            return { label: "Partial evidence", color: "var(--gap-unverified)", bg: "var(--gap-unverified-bg)", border: "var(--gap-unverified-border)" };
          }
          if (state === "UNVERIFIED" || state === "CONFLICTING_REVIEW_REQUIRED") {
            return { label: "Needs verification", color: "var(--gap-unverified)", bg: "var(--gap-unverified-bg)", border: "var(--gap-unverified-border)" };
          }
          if (state === "NOT_APPLICABLE") {
            return { label: "Excluded", color: "#6d7772", bg: "#f3f0e6", border: "#cbc8ba" };
          }
          return { label: "Not evidenced", color: "var(--gap-partial)", bg: "var(--gap-partial-bg)", border: "var(--gap-partial-border)" };
        };

        return (
          <div className="skill-gap-visuals-panel" aria-label="Evidence Visual Summaries">
            {/* Visual A: Evidence Coverage Donut */}
            <div className="visual-analytics-card" role="region" aria-label="Evidence Coverage Donut Chart">
              <div className="chart-header">
                <div>
                  <h4 className="chart-title">Evidence Coverage Breakdown</h4>
                  <p className="chart-subtitle">
                    Distribution of requirement evidence for {donutScope === "MUST_HAVE" ? "required criteria" : donutScope === "PREFERRED" ? "preferred criteria" : "all evaluated criteria"} (excluding not-applicable items)
                  </p>
                </div>
                {prefTotal > 0 && (
                  <div className="skill-gap-scope-pills" role="group" aria-label="Filter donut scope">
                    <button
                      type="button"
                      className={`skill-gap-scope-btn ${donutScope === "ALL" ? "active" : ""}`}
                      onClick={() => setDonutScope("ALL")}
                      aria-pressed={donutScope === "ALL"}
                    >
                      All ({rawItems.length})
                    </button>
                    <button
                      type="button"
                      className={`skill-gap-scope-btn ${donutScope === "MUST_HAVE" ? "active" : ""}`}
                      onClick={() => setDonutScope("MUST_HAVE")}
                      aria-pressed={donutScope === "MUST_HAVE"}
                    >
                      Required Only
                    </button>
                    <button
                      type="button"
                      className={`skill-gap-scope-btn ${donutScope === "PREFERRED" ? "active" : ""}`}
                      onClick={() => setDonutScope("PREFERRED")}
                      aria-pressed={donutScope === "PREFERRED"}
                    >
                      Preferred Only
                    </button>
                  </div>
                )}
              </div>

              <div className="skill-gap-donut-row">
                <div
                  className="skill-gap-donut-wheel"
                  style={{ background: `conic-gradient(${donutGradStops})` }}
                  role="img"
                  aria-label={`Donut chart: ${totalEvaluated} evaluated criteria. ${countFound} (${pctFound}%) evidence found, ${countPartial} (${pctPartial}%) partial, ${countVerify} (${pctVerify}%) needs verification, ${countNotFound} (${pctNotFound}%) not evidenced.`}
                >
                  <div className="skill-gap-donut-hole">
                    <span className="doughnut-center-count">{totalEvaluated}</span>
                    <span className="doughnut-center-label">Evaluated Criteria</span>
                  </div>
                </div>

                <div className="skill-gap-legend-list" role="list" aria-label="Evidence status breakdown">
                  <div className="skill-gap-legend-row" role="listitem">
                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      <span className="legend-swatch swatch-solid-teal" />
                      <span className="legend-label-text">Evidence found:</span>
                    </div>
                    <span className="legend-count-val">{countFound} ({pctFound}%)</span>
                  </div>
                  {countPartial > 0 && (
                    <div className="skill-gap-legend-row" role="listitem">
                      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <span className="legend-swatch swatch-diagonal-indigo" />
                        <span className="legend-label-text">Partial evidence:</span>
                      </div>
                      <span className="legend-count-val">{countPartial} ({pctPartial}%)</span>
                    </div>
                  )}
                  {countVerify > 0 && (
                    <div className="skill-gap-legend-row" role="listitem">
                      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <span className="legend-swatch swatch-dotted-amber" />
                        <span className="legend-label-text">Needs verification:</span>
                      </div>
                      <span className="legend-count-val">{countVerify} ({pctVerify}%)</span>
                    </div>
                  )}
                  <div className="skill-gap-legend-row" role="listitem">
                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      <span className="legend-swatch swatch-solid-slate" />
                      <span className="legend-label-text">Not evidenced:</span>
                    </div>
                    <span className="legend-count-val">{countNotFound} ({pctNotFound}%)</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Visual B: Requirement Status Grid */}
            <div className="visual-analytics-card" role="region" aria-label="Requirement Status Grid">
              <div className="chart-header">
                <div>
                  <h4 className="chart-title">Requirement Status Grid</h4>
                  <p className="chart-subtitle">
                    Interactive overview of individual job requirements grouped by necessity
                  </p>
                </div>
              </div>

              <div className="skill-gap-grid-section">
                {/* Required Criteria Group */}
                {requiredTiles.length > 0 && (
                  <div>
                    <h5 className="skill-gap-group-title">
                      <span>Required Criteria ({requiredTiles.length})</span>
                    </h5>
                    <div className="skill-gap-tiles-wrap" style={{ marginTop: "8px" }} role="list" aria-label="Required requirements status list">
                      {requiredTiles.map((tile: any, idx: number) => {
                        const meta = getTileStatusMeta(tile);
                        const name = tile.requirementName || (tile as any).requirement_name || tile.category || `Criterion #${idx + 1}`;
                        const labelText = `${name}: ${meta.label} (Required)`;
                        return (
                          <div
                            key={tile.itemId || tile.requirementId || `req-tile-${idx}`}
                            role="listitem"
                            tabIndex={0}
                            className="skill-gap-tile"
                            aria-label={labelText}
                            title={labelText}
                            style={{ borderColor: meta.border, backgroundColor: meta.bg }}
                          >
                            <span className="skill-gap-tile-dot" style={{ backgroundColor: meta.color }} />
                            <span className="skill-gap-tile-name">{name}</span>
                            <span className="skill-gap-tile-status" style={{ color: meta.color }}>
                              • {meta.label}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Preferred Criteria Group */}
                {preferredTiles.length > 0 && (
                  <div style={{ marginTop: "10px" }}>
                    <h5 className="skill-gap-group-title">
                      <span>Preferred Criteria ({preferredTiles.length})</span>
                    </h5>
                    <div className="skill-gap-tiles-wrap" style={{ marginTop: "8px" }} role="list" aria-label="Preferred requirements status list">
                      {preferredTiles.map((tile: any, idx: number) => {
                        const meta = getTileStatusMeta(tile);
                        const name = tile.requirementName || (tile as any).requirement_name || tile.category || `Criterion #${idx + 1}`;
                        const labelText = `${name}: ${meta.label} (Preferred)`;
                        return (
                          <div
                            key={tile.itemId || tile.requirementId || `pref-tile-${idx}`}
                            role="listitem"
                            tabIndex={0}
                            className="skill-gap-tile"
                            aria-label={labelText}
                            title={labelText}
                            style={{ borderColor: meta.border, backgroundColor: meta.bg }}
                          >
                            <span className="skill-gap-tile-dot" style={{ backgroundColor: meta.color }} />
                            <span className="skill-gap-tile-name">{name}</span>
                            <span className="skill-gap-tile-status" style={{ color: meta.color }}>
                              • {meta.label}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Text legend for keyboard accessibility */}
                <div className="skill-gap-grid-legend" role="list" aria-label="Status grid legend">
                  <span className="skill-gap-grid-legend-item" role="listitem">
                    <span className="legend-swatch swatch-solid-teal" />
                    <span>Evidence found</span>
                  </span>
                  <span className="skill-gap-grid-legend-item" role="listitem">
                    <span className="legend-swatch swatch-diagonal-indigo" />
                    <span>Partial evidence</span>
                  </span>
                  <span className="skill-gap-grid-legend-item" role="listitem">
                    <span className="legend-swatch swatch-dotted-amber" />
                    <span>Needs verification</span>
                  </span>
                  <span className="skill-gap-grid-legend-item" role="listitem">
                    <span className="legend-swatch swatch-solid-slate" />
                    <span>Not evidenced</span>
                  </span>
                </div>
              </div>
            </div>
          </div>
        );
      })()}

      {/* 4. Visualization: Category Evidence Coverage Chart */}
      {rawItems.length > 0 && (
        <div style={{ marginBottom: "20px" }}>
          <CategoryEvidenceCoverageChart items={rawItems} />
        </div>
      )}

      {/* 5. Collapsed 'Analysis details' Panel */}
      <div className="gap-breakdown-card" aria-label="Analysis Details Panel">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <h4 className="gap-breakdown-title" style={{ display: "inline-block", marginRight: "10px" }}>
              Technical Analysis Details
            </h4>
            <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
              Extraction confidence: <strong>{report.confidenceTier || canon?.extractionConfidence || "MEDIUM"}</strong>
            </span>
          </div>
          <button
            type="button"
            className="btn-link-action"
            onClick={() => setShowAnalysisDetails((prev) => !prev)}
            aria-expanded={showAnalysisDetails}
            style={{ fontSize: "12px" }}
          >
            {showAnalysisDetails ? "Hide analysis details ▲" : "View analysis details ▼"}
          </button>
        </div>

        {showAnalysisDetails && (
          <div style={{ marginTop: "10px", paddingTop: "10px", borderTop: "1px solid var(--border-subtle)", display: "flex", flexDirection: "column", gap: "10px" }}>
            <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", fontSize: "12px", color: "var(--text-secondary)" }}>
              <span>
                Extraction confidence:{" "}
                <strong title="How confidently the system extracted and connected evidence from the uploaded resume. This is not a candidate rating." style={{ cursor: "help" }}>
                  {report.confidenceTier || canon?.extractionConfidence || "MEDIUM"}
                </strong>
              </span>
              <span>Total criteria: <strong>{report.totalRequirements}</strong></span>
              <span>Report ID: <code>{report.reportId ? report.reportId.substring(0, 8) : "N/A"}</code></span>
              <span>Run ID: <code>{report.matchRunId ? report.matchRunId.substring(0, 8) : "N/A"}</code></span>
              <span>Evaluated: <strong>{formattedDate}</strong></span>
              <span>Schema version: <strong>{report.gapSchemaVersion}</strong></span>
              <span>Policy version: <strong>{report.gapPolicyVersion}</strong></span>
              <span>Algorithm version: <strong>{report.matcherAlgorithmVersion}</strong></span>
              <span>Rule version: <strong>{report.matchingRuleVersion}</strong></span>
            </div>

            {/* Non-zero state chips only */}
            <div className="gap-breakdown-chips" style={{ marginTop: "6px" }}>
              {stateCounts.covered > 0 && (
                <span className="gap-state-chip chip-covered">
                  <Icon name="check" size={13} />
                  <span>Evidence found: {stateCounts.covered}</span>
                </span>
              )}
              {stateCounts.partiallyCovered > 0 && (
                <span className="gap-state-chip chip-partial">
                  <Icon name="clock" size={13} />
                  <span>Partial evidence found: {stateCounts.partiallyCovered}</span>
                </span>
              )}
              {stateCounts.notEvidenced > 0 && (
                <span className="gap-state-chip chip-not-evidenced">
                  <Icon name="file-text" size={13} />
                  <span>Not found in resume: {stateCounts.notEvidenced}</span>
                </span>
              )}
              {stateCounts.unverified > 0 && (
                <span className="gap-state-chip chip-unverified">
                  <Icon name="shield" size={13} />
                  <span>Needs verification: {stateCounts.unverified}</span>
                </span>
              )}
              {stateCounts.conflicting > 0 && (
                <span className="gap-state-chip chip-conflicting">
                  <Icon name="alert-circle" size={13} />
                  <span>Conflicting resume evidence: {stateCounts.conflicting}</span>
                </span>
              )}
              {stateCounts.notApplicable > 0 && (
                <span className="gap-state-chip chip-not-applicable">
                  <Icon name="info" size={13} />
                  <span>Excluded from score: {stateCounts.notApplicable}</span>
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 6. Filter Controls */}
      <div className="gap-filters-bar no-print" aria-label="Filter Controls">
        <div className="gap-filter-group">
          <label htmlFor="gap-state-select" className="gap-filter-label">Assessment:</label>
          <select
            id="gap-state-select"
            className="form-select-sm"
            value={selectedStateFilter}
            onChange={(e) => setSelectedStateFilter(e.target.value)}
          >
            <option value="ALL">All Assessments ({report.totalRequirements})</option>
            {stateCounts.covered > 0 && <option value="COVERED">Evidence found ({stateCounts.covered})</option>}
            {stateCounts.partiallyCovered > 0 && <option value="PARTIALLY_COVERED">Partial evidence found ({stateCounts.partiallyCovered})</option>}
            {stateCounts.notEvidenced > 0 && <option value="NOT_EVIDENCED">Not found in resume ({stateCounts.notEvidenced})</option>}
            {stateCounts.unverified > 0 && <option value="UNVERIFIED">Needs verification ({stateCounts.unverified})</option>}
            {stateCounts.conflicting > 0 && <option value="CONFLICTING_REVIEW_REQUIRED">Conflicting resume evidence ({stateCounts.conflicting})</option>}
            {stateCounts.notApplicable > 0 && <option value="NOT_APPLICABLE">Excluded from score ({stateCounts.notApplicable})</option>}
          </select>
        </div>

        <div className="gap-filter-group">
          <label htmlFor="gap-necessity-select" className="gap-filter-label">Requirement type:</label>
          <select
            id="gap-necessity-select"
            className="form-select-sm"
            value={selectedNecessityFilter}
            onChange={(e) => setSelectedNecessityFilter(e.target.value)}
          >
            <option value="ALL">All Criteria</option>
            <option value="MUST_HAVE">Required Only</option>
            <option value="PREFERRED">Preferred Only</option>
          </select>
        </div>

        <div className="gap-filter-group">
          <label className="checkbox-item-label">
            <input
              type="checkbox"
              checked={onlyReviewRequired}
              onChange={(e) => setOnlyReviewRequired(e.target.checked)}
            />
            <span>Needs Verification Only</span>
          </label>
        </div>
      </div>

      {/* 7. Simplified 4-Column Table: Requirement, Resume evidence, Assessment, Details */}
      <div className="gap-table-card">
        {filteredItems.length === 0 ? (
          <div className="empty-state-card" style={{ padding: "36px 16px" }}>
            <p style={{ color: "var(--text-secondary)", margin: 0 }}>
              No criteria match the selected filters.
            </p>
          </div>
        ) : (
          <table className="gap-items-table" aria-label="Resume Evidence &amp; Criteria Table">
            <thead>
              <tr>
                <th style={{ width: "30%" }}>Requirement</th>
                <th style={{ width: "28%" }}>Resume evidence</th>
                <th style={{ width: "22%" }}>Assessment</th>
                <th style={{ width: "20%", textAlign: "right" }}>Details</th>
              </tr>
            </thead>
            <tbody>
              {filteredItems.map((item, index) => {
                const isRequired = item.necessity === "MUST_HAVE";
                const rowKey = item.itemId || item.requirementId || `row-${index}`;
                const isExpanded = expandedRows.has(rowKey);

                // Domain assessment mapping
                let assessmentLabel = "Evidence found";
                let chipClass = "chip-covered";
                if (item.gapState === "PARTIALLY_COVERED") {
                  assessmentLabel = "Partial evidence found";
                  chipClass = "chip-partial";
                } else if (item.gapState === "NOT_EVIDENCED") {
                  assessmentLabel = "Not found in resume";
                  chipClass = "chip-not-evidenced";
                } else if (item.gapState === "UNVERIFIED") {
                  assessmentLabel = "Needs verification";
                  chipClass = "chip-unverified";
                } else if (item.gapState === "CONFLICTING_REVIEW_REQUIRED") {
                  assessmentLabel = "Conflicting resume evidence";
                  chipClass = "chip-conflicting";
                } else if (item.gapState === "NOT_APPLICABLE") {
                  assessmentLabel = "Excluded from score";
                  chipClass = "chip-not-applicable";
                }

                const reqName = item.requirementName || (item as any).requirement_name || item.category || "Requirement";
                const categoryLabel = item.category ? `${item.category.charAt(0).toUpperCase()}${item.category.slice(1).toLowerCase()}` : "Skill";
                const prioLabel = item.priority ? `${item.priority.charAt(0).toUpperCase()}${item.priority.slice(1).toLowerCase()}` : "Normal";

                // Resume evidence cell display
                let docDisplay = item.documentedValue || (item as any).documented_value;
                if (!docDisplay || docDisplay.trim().toLowerCase() in { "none documented": true, "none documented.": true, "no usable duration found": true }) {
                  if (item.gapState === "COVERED") {
                    docDisplay = "Documented and evidenced";
                  } else {
                    docDisplay = "Not found in uploaded resume";
                  }
                }

                const expertsCount = (item.evidenceReferences || []).length;
                const guidance = isRequired && item.gapState === "NOT_EVIDENCED"
                  ? "Verify this criterion during review."
                  : null;

                return (
                  <React.Fragment key={rowKey}>
                    <tr>
                      <td>
                        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                          <span style={{ fontWeight: 600, color: "var(--text-primary)", fontSize: "13px" }}>
                            {reqName}
                          </span>
                          <span style={{ fontSize: "11px", color: "var(--text-secondary)" }}>
                            {isRequired ? "Required" : "Preferred"} &bull; {categoryLabel} &bull; Priority: {prioLabel}
                          </span>
                        </div>
                      </td>
                      <td>
                        <div style={{ display: "flex", flexDirection: "column", gap: "3px", fontSize: "12px" }}>
                          <span style={{ color: item.gapState === "NOT_EVIDENCED" ? "var(--text-secondary)" : "var(--text-primary)" }}>
                            {docDisplay}
                          </span>
                          {item.measurableShortfallValue !== null && item.measurableShortfallValue !== undefined && (
                            <span style={{ fontSize: "11px", color: "var(--gap-partial)" }}>
                              {item.measurableShortfallValue} {item.shortfallUnit || "months"} difference to verify
                            </span>
                          )}
                          {guidance && (
                            <span style={{ fontSize: "11px", color: "var(--text-muted)", fontStyle: "italic" }}>
                              {guidance}
                            </span>
                          )}
                        </div>
                      </td>
                      <td>
                        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                          <span className={`gap-state-chip ${chipClass}`} style={{ width: "fit-content" }}>
                            {assessmentLabel}
                          </span>
                        </div>
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {expertsCount > 0 ? (
                          <button
                            type="button"
                            className="gap-evidence-btn"
                            onClick={() => toggleRowExpanded(rowKey)}
                            aria-expanded={isExpanded}
                            aria-label={`${isExpanded ? "Collapse" : "View"} ${expertsCount} evidence excerpt${expertsCount === 1 ? "" : "s"}`}
                          >
                            <Icon name="file-text" size={12} />
                            <span>
                              {isExpanded ? "Hide excerpts ▲" : `View ${expertsCount} evidence excerpt${expertsCount === 1 ? "" : "s"} ▼`}
                            </span>
                          </button>
                        ) : (
                          <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>No evidence excerpt</span>
                        )}
                      </td>
                    </tr>

                    {/* Expandable Evidence Excerpt Details */}
                    {isExpanded && expertsCount > 0 && (
                      <tr style={{ backgroundColor: "var(--bg-surface-subtle)" }}>
                        <td colSpan={4} style={{ padding: "12px 18px", borderBottom: "1px solid var(--border-medium)" }}>
                          <div style={{ display: "flex", flexDirection: "column", gap: "8px", fontSize: "12px" }}>
                            <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                              Document experts &amp; Citations ({expertsCount}):
                            </div>
                            <ul style={{ listStyle: "disc", paddingLeft: "20px", display: "flex", flexDirection: "column", gap: "6px", margin: 0 }}>
                              {item.evidenceReferences.map((ref, rIdx) => (
                                <li key={rIdx} style={{ color: "var(--text-primary)", wordBreak: "break-all" }}>
                                  <code style={{ fontSize: "11px", backgroundColor: "var(--bg-surface)", border: "1px solid var(--border-subtle)", padding: "2px 6px", borderRadius: "4px" }}>
                                    {ref}
                                  </code>
                                </li>
                              ))}
                            </ul>
                            <div style={{ marginTop: "4px", fontSize: "11px", color: "var(--text-secondary)" }}>
                              {item.recruiterWording}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
