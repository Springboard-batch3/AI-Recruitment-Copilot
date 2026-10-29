import React, { useState } from "react";
import { MatchResultRankingItem, GapReportItem, EvidenceSummary } from "../../api/jobs";
import { CandidateSummary } from "../CandidatesDirectory";
import { Icon } from "../Icon";

// Helper to safely extract canonical evidence summary without defaulting missing data to 0
export function getCanonicalSummaryData(summary: EvidenceSummary | null | undefined) {
  if (!summary) return null;
  const s = summary as any;
  const reqTotal = s.requiredTotal ?? s.required_total;
  const reqFound = s.requiredEvidenceFound ?? s.required_evidence_found;
  const reqPartial = s.requiredPartialEvidence ?? s.required_partial_evidence ?? 0;
  const reqNotFound = s.requiredNotFound ?? s.required_not_found ?? 0;
  const reqVerification = s.requiredNeedsVerification ?? s.required_needs_verification ?? 0;

  const prefTotal = s.preferredTotal ?? s.preferred_total ?? 0;
  const prefFound = s.preferredEvidenceFound ?? s.preferred_evidence_found ?? 0;

  const overallFound = s.overallEvidenceFound ?? s.overall_evidence_found ?? (reqFound !== undefined ? (reqFound + prefFound) : 0);
  const overallPartial = s.overallPartialEvidence ?? s.overall_partial_evidence ?? reqPartial;
  const overallNotFound = s.overallNotFound ?? s.overall_not_found ?? reqNotFound;
  const totalCriteria = s.totalCriteria ?? s.total_criteria ?? (reqTotal !== undefined ? (reqTotal + prefTotal) : undefined);

  if (reqTotal === undefined || reqTotal === null || reqFound === undefined || reqFound === null) {
    return null;
  }

  return {
    totalCriteria: totalCriteria ?? reqTotal,
    requiredTotal: reqTotal,
    requiredEvidenceFound: reqFound,
    requiredPartialEvidence: reqPartial,
    requiredNotFound: reqNotFound,
    requiredNeedsVerification: reqVerification,
    preferredTotal: prefTotal,
    preferredEvidenceFound: prefFound,
    overallEvidenceFound: overallFound,
    overallPartialEvidence: overallPartial,
    overallNotFound: overallNotFound,
    extractionConfidence: s.extractionConfidence ?? s.extraction_confidence ?? "MEDIUM",
  };
}

/* =========================================================================
   1. Candidate Resume-Match Ranking Chart
   ========================================================================= */

interface CandidateRankingChartProps {
  results: MatchResultRankingItem[];
  candidates: CandidateSummary[];
  onSelectCandidate?: (resultId: string) => void;
  selectedResultId?: string | null;
}

export function CandidateRankingChart({
  results,
  candidates,
  onSelectCandidate,
  selectedResultId,
}: CandidateRankingChartProps) {
  if (!results || results.length === 0) {
    return null;
  }

  return (
    <div
      className="visual-analytics-card ranking-chart-card"
      role="region"
      aria-label="Overall Compatibility Ranking Chart"
    >
      <div className="chart-header">
        <div>
          <h4 className="chart-title">Overall Compatibility Ranking</h4>
          <p className="chart-subtitle">
            Ranked deterministically by stored backend compatibility scores ({results.length} candidate{results.length === 1 ? "" : "s"})
          </p>
        </div>
      </div>

      <div className="ranking-bars-list" role="list" aria-label="Candidate match score rankings">
        {results.map((item) => {
          const candidate = candidates.find((c) => c.artifactId === item.profileId);
          const candidateName = candidate?.fullName || `Candidate #${item.profileId.substring(0, 6)}`;
          const score = Number(item.displayCompatibilityScore);
          const clampedPercent = Math.max(0, Math.min(100, isNaN(score) ? 0 : score));
          const rankText = item.isTied ? `Tied #${item.rank}` : `#${item.rank}`;
          const isSelected = selectedResultId === item.resultId;

          return (
            <div
              key={item.resultId || item.profileId}
              role="listitem"
              className={`ranking-bar-row ${isSelected ? "selected" : ""} ${onSelectCandidate && item.resultId ? "interactive" : ""}`}
              onClick={() => {
                if (onSelectCandidate && item.resultId) {
                  onSelectCandidate(item.resultId);
                }
              }}
              onKeyDown={(e) => {
                if ((e.key === "Enter" || e.key === " ") && onSelectCandidate && item.resultId) {
                  e.preventDefault();
                  onSelectCandidate(item.resultId);
                }
              }}
              tabIndex={onSelectCandidate && item.resultId ? 0 : undefined}
              aria-label={`${rankText}: ${candidateName} — ${Math.round(clampedPercent)}% resume match`}
            >
              <div className="ranking-row-meta">
                <span className="rank-badge">
                  {rankText}
                </span>
                <span className="candidate-name-label" title={candidateName}>
                  {candidateName}
                </span>
                <span className="exact-score-text">
                  {Math.round(clampedPercent)}%
                </span>
              </div>

              <div
                className="ranking-bar-track"
                role="progressbar"
                aria-valuenow={Math.round(clampedPercent)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={`Match score: ${Math.round(clampedPercent)}% for ${candidateName}`}
              >
                <div
                  className="ranking-bar-fill"
                  style={{
                    width: `${clampedPercent}%`,
                    backgroundColor: "var(--accent-primary)",
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* =========================================================================
   2. Requirement Evidence Distribution Chart (100% Stacked Bar with Patterns)
   ========================================================================= */

interface RequirementEvidenceDistributionChartProps {
  results: MatchResultRankingItem[];
  candidates: CandidateSummary[];
  onSelectCandidate?: (resultId: string) => void;
}

export function RequirementEvidenceDistributionChart({
  results,
  candidates,
  onSelectCandidate,
}: RequirementEvidenceDistributionChartProps) {
  if (!results || results.length === 0) {
    return null;
  }

  // Determine which categories have at least one non-zero occurrence across all rendered candidates
  let hasCovered = false;
  let hasPartial = false;
  let hasVerification = false;
  let hasNotFound = false;
  let hasExcluded = false;

  const rowsData = results.map((item) => {
    const candidate = candidates.find((c) => c.artifactId === item.profileId);
    const candidateName = candidate?.fullName || `Candidate #${item.profileId.substring(0, 6)}`;
    const summary = item.evidence_summary || (item as any).evidenceSummary;
    const canon = getCanonicalSummaryData(summary);

    if (!canon) {
      return {
        item,
        candidateName,
        isUnavailable: true,
        covered: 0,
        partial: 0,
        verification: 0,
        notFound: 0,
        excluded: 0,
        total: 0,
      };
    }

    const covered = canon.requiredEvidenceFound;
    const partial = canon.requiredPartialEvidence;
    const verification = canon.requiredNeedsVerification;
    const notFound = canon.requiredNotFound;
    const excluded = (canon as any).not_applicable_count ?? 0;
    const total = canon.requiredTotal || (covered + partial + verification + notFound + excluded) || 1;

    if (covered > 0) hasCovered = true;
    if (partial > 0) hasPartial = true;
    if (verification > 0) hasVerification = true;
    if (notFound > 0) hasNotFound = true;
    if (excluded > 0) hasExcluded = true;

    return {
      item,
      candidateName,
      isUnavailable: false,
      covered,
      partial,
      verification,
      notFound,
      excluded,
      total,
      canon,
    };
  });

  return (
    <div
      className="visual-analytics-card evidence-distribution-card"
      role="region"
      aria-label="Requirement Evidence Distribution Chart"
    >
      <div className="chart-header">
        <div>
          <h4 className="chart-title">Requirement Evidence Distribution</h4>
          <p className="chart-subtitle">
            100% evidence breakdown for evaluated required criteria across candidates
          </p>
        </div>

        {/* Dynamic Legend: Omit zero-value categories */}
        <div className="chart-legend" aria-label="Evidence distribution categories">
          {hasCovered && (
            <span className="legend-item">
              <span className="legend-swatch swatch-solid-teal" />
              <span>Evidence found</span>
            </span>
          )}
          {hasPartial && (
            <span className="legend-item">
              <span className="legend-swatch swatch-diagonal-indigo" />
              <span>Partial evidence</span>
            </span>
          )}
          {hasVerification && (
            <span className="legend-item">
              <span className="legend-swatch swatch-dotted-amber" />
              <span>Needs verification</span>
            </span>
          )}
          {hasNotFound && (
            <span className="legend-item">
              <span className="legend-swatch swatch-solid-slate" />
              <span>Not found in resume</span>
            </span>
          )}
          {hasExcluded && (
            <span className="legend-item">
              <span className="legend-swatch swatch-outline-slate" />
              <span>Excluded from score</span>
            </span>
          )}
        </div>
      </div>

      <div className="distribution-bars-list" role="list" aria-label="Candidate requirement distributions">
        {rowsData.map((row) => {
          const { item, candidateName, isUnavailable, covered, partial, verification, notFound, excluded, total } = row;

          if (isUnavailable) {
            return (
              <div
                key={item.resultId || item.profileId}
                className="distribution-bar-row"
                role="listitem"
                aria-label={`${candidateName}: Summary unavailable`}
              >
                <div className="distribution-row-meta">
                  <span className="candidate-name-label">{candidateName}</span>
                  <span className="summary-unavailable-badge">Summary unavailable</span>
                </div>
                <div className="distribution-bar-track unavailable-track">
                  <div className="unavailable-bar-fill" />
                </div>
              </div>
            );
          }

          const pctCovered = (covered / total) * 100;
          const pctPartial = (partial / total) * 100;
          const pctVerification = (verification / total) * 100;
          const pctNotFound = (notFound / total) * 100;
          const pctExcluded = (excluded / total) * 100;

          const desc = `${candidateName}: ${covered} of ${total} evidenced, ${partial} partial, ${verification} require verification, ${notFound} not found in resume${excluded > 0 ? `, ${excluded} excluded` : ""}`;

          return (
            <div
              key={item.resultId || item.profileId}
              className={`distribution-bar-row ${onSelectCandidate && item.resultId ? "interactive" : ""}`}
              role="listitem"
              onClick={() => {
                if (onSelectCandidate && item.resultId) {
                  onSelectCandidate(item.resultId);
                }
              }}
              onKeyDown={(e) => {
                if ((e.key === "Enter" || e.key === " ") && onSelectCandidate && item.resultId) {
                  e.preventDefault();
                  onSelectCandidate(item.resultId);
                }
              }}
              tabIndex={onSelectCandidate && item.resultId ? 0 : undefined}
              aria-label={desc}
            >
              <div className="distribution-row-meta">
                <span className="candidate-name-label" title={candidateName}>
                  {candidateName}
                </span>
                <span className="exact-counts-text">
                  {covered} of {total} evidenced
                  {verification > 0 && ` • ${verification} verify`}
                  {partial > 0 && ` • ${partial} partial`}
                </span>
              </div>

              <div
                className="distribution-bar-track structured-track"
                role="img"
                aria-label={desc}
              >
                {pctCovered > 0 && (
                  <div
                    className="segment segment-covered-pattern"
                    style={{ width: `${pctCovered}%` }}
                    title={`Evidence found: ${covered} (${Math.round(pctCovered)}%)`}
                    aria-label={`Evidence found: ${covered}`}
                  />
                )}
                {pctPartial > 0 && (
                  <div
                    className="segment segment-partial-pattern"
                    style={{ width: `${pctPartial}%` }}
                    title={`Partial evidence found: ${partial} (${Math.round(pctPartial)}%)`}
                    aria-label={`Partial evidence: ${partial}`}
                  />
                )}
                {pctVerification > 0 && (
                  <div
                    className="segment segment-verification-pattern"
                    style={{ width: `${pctVerification}%` }}
                    title={`Needs verification: ${verification} (${Math.round(pctVerification)}%)`}
                    aria-label={`Needs verification: ${verification}`}
                  />
                )}
                {pctNotFound > 0 && (
                  <div
                    className="segment segment-not-found-pattern"
                    style={{ width: `${pctNotFound}%` }}
                    title={`Not found in resume: ${notFound} (${Math.round(pctNotFound)}%)`}
                    aria-label={`Not found in resume: ${notFound}`}
                  />
                )}
                {pctExcluded > 0 && (
                  <div
                    className="segment segment-excluded-pattern"
                    style={{ width: `${pctExcluded}%` }}
                    title={`Excluded from score: ${excluded} (${Math.round(pctExcluded)}%)`}
                    aria-label={`Excluded: ${excluded}`}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* =========================================================================
   3. Overall Evidence Composition Doughnut Chart
   ========================================================================= */

interface OverallEvidenceCompositionProps {
  results: MatchResultRankingItem[];
}

export function OverallEvidenceCompositionChart({ results }: OverallEvidenceCompositionProps) {
  if (!results || results.length === 0) {
    return null;
  }

  let totalEvaluated = 0;
  let totalFound = 0;
  let totalPartial = 0;
  let totalVerification = 0;
  let totalNotFound = 0;
  let totalExcluded = 0;
  let hasValidData = false;

  results.forEach((item) => {
    const summary = item.evidence_summary || (item as any).evidenceSummary;
    const canon = getCanonicalSummaryData(summary);
    if (canon) {
      hasValidData = true;
      const found = canon.requiredEvidenceFound;
      const partial = canon.requiredPartialEvidence;
      const verify = canon.requiredNeedsVerification;
      const notFound = canon.requiredNotFound;
      const excl = (canon as any).not_applicable_count ?? 0;
      const subtotal = canon.requiredTotal || (found + partial + verify + notFound + excl);

      totalEvaluated += subtotal;
      totalFound += found;
      totalPartial += partial;
      totalVerification += verify;
      totalNotFound += notFound;
      totalExcluded += excl;
    }
  });

  if (!hasValidData || totalEvaluated === 0) {
    return (
      <div className="visual-analytics-card doughnut-card" role="region" aria-label="Overall Evidence Composition">
        <div className="chart-header">
          <h4 className="chart-title">Overall Evidence Composition</h4>
        </div>
        <p className="summary-unavailable-badge" style={{ padding: "16px" }}>
          Visual summary unavailable for this completed run.
        </p>
      </div>
    );
  }

  // Calculate percentages
  const pFound = Math.round((totalFound / totalEvaluated) * 100);
  const pPartial = Math.round((totalPartial / totalEvaluated) * 100);
  const pVerify = Math.round((totalVerification / totalEvaluated) * 100);
  const pNotFound = Math.round((totalNotFound / totalEvaluated) * 100);
  const pExcluded = Math.round((totalExcluded / totalEvaluated) * 100);

  // SVG conic/arc breakdown
  const degFound = (totalFound / totalEvaluated) * 360;
  const degPartial = (totalPartial / totalEvaluated) * 360;
  const degVerify = (totalVerification / totalEvaluated) * 360;
  const degNotFound = (totalNotFound / totalEvaluated) * 360;
  const degExcl = (totalExcluded / totalEvaluated) * 360;

  const gradStops = [
    `var(--gap-covered) 0deg ${degFound}deg`,
    `var(--gap-unverified) ${degFound}deg ${degFound + degPartial}deg`,
    `var(--gap-unverified) ${degFound + degPartial}deg ${degFound + degPartial + degVerify}deg`,
    `var(--gap-partial) ${degFound + degPartial + degVerify}deg ${degFound + degPartial + degVerify + degNotFound}deg`,
    `#6d7772 ${degFound + degPartial + degVerify + degNotFound}deg 360deg`,
  ].join(", ");

  const conicStyle = {
    background: `conic-gradient(${gradStops})`,
  };

  const srSummary = `Overall Evidence Composition: ${totalEvaluated} total criteria assessed across ${results.length} candidates. ${totalFound} (${pFound}%) evidence found, ${totalPartial} (${pPartial}%) partial evidence, ${totalVerification} (${pVerify}%) needs verification, ${totalNotFound} (${pNotFound}%) not found in resume, ${totalExcluded} (${pExcluded}%) excluded.`;

  return (
    <div
      className="visual-analytics-card doughnut-card"
      role="region"
      aria-label="Overall Evidence Composition Chart"
    >
      <div className="chart-header">
        <div>
          <h4 className="chart-title">Overall Evidence Composition</h4>
          <p className="chart-subtitle">
            Aggregate assessment breakdown across evaluated criteria ({results.length} candidate{results.length === 1 ? "" : "s"})
          </p>
        </div>
      </div>

      <div className="doughnut-content-row">
        {/* Visual CSS Conic Doughnut */}
        <div
          className="doughnut-wheel-wrapper"
          role="img"
          aria-label={srSummary}
        >
          <div className="doughnut-wheel" style={conicStyle}>
            <div className="doughnut-hole">
              <span className="doughnut-center-count">{totalEvaluated}</span>
              <span className="doughnut-center-label">criteria assessed</span>
            </div>
          </div>
        </div>

        {/* Detailed Non-Color Accessible Legend */}
        <div className="doughnut-legend-list" role="list" aria-label="Evidence status counts">
          <div className="doughnut-legend-row" role="listitem">
            <span className="legend-swatch swatch-solid-teal" />
            <span className="legend-label-text">Evidence found:</span>
            <span className="legend-count-val">{totalFound} ({pFound}%)</span>
          </div>
          {totalPartial > 0 && (
            <div className="doughnut-legend-row" role="listitem">
              <span className="legend-swatch swatch-diagonal-indigo" />
              <span className="legend-label-text">Partial evidence:</span>
              <span className="legend-count-val">{totalPartial} ({pPartial}%)</span>
            </div>
          )}
          {totalVerification > 0 && (
            <div className="doughnut-legend-row" role="listitem">
              <span className="legend-swatch swatch-dotted-amber" />
              <span className="legend-label-text">Needs verification:</span>
              <span className="legend-count-val">{totalVerification} ({pVerify}%)</span>
            </div>
          )}
          <div className="doughnut-legend-row" role="listitem">
            <span className="legend-swatch swatch-solid-slate" />
            <span className="legend-label-text">Not found in resume:</span>
            <span className="legend-count-val">{totalNotFound} ({pNotFound}%)</span>
          </div>
          {totalExcluded > 0 && (
            <div className="doughnut-legend-row" role="listitem">
              <span className="legend-swatch swatch-outline-slate" />
              <span className="legend-label-text">Excluded from score:</span>
              <span className="legend-count-val">{totalExcluded} ({pExcluded}%)</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* =========================================================================
   4. Requirement Coverage by Criterion Horizontal Chart
   ========================================================================= */

interface RequirementCoverageByCriterionProps {
  results: MatchResultRankingItem[];
}

interface RequirementAggregateItem {
  id: string;
  name: string;
  necessity: "REQUIRED" | "PREFERRED";
  displayOrder: number;
  totalCandidates: number;
  evidenceFoundCount: number;
  partialCount: number;
  needsVerificationCount: number;
  notFoundCount: number;
}

export function RequirementCoverageByCriterionChart({ results }: RequirementCoverageByCriterionProps) {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);

  if (!results || results.length === 0) {
    return null;
  }

  // Derive requirement-level aggregations from stored candidate requirement contributions
  const reqMap = new Map<string, RequirementAggregateItem>();
  const totalCands = results.length;

  results.forEach((r) => {
    const contribs = r.requirement_contributions || r.requirementContributions || [];
    contribs.forEach((c: any, index: number) => {
      const id = String(c.requirement_id || c.requirementId || `req-${index}`);
      const name = String(c.label || c.requirement_name || c.requirementName || `Criterion #${index + 1}`);
      const necessity: "REQUIRED" | "PREFERRED" =
        (c.necessity || "").toUpperCase() === "PREFERRED" ? "PREFERRED" : "REQUIRED";
      const displayOrder = Number(c.display_order ?? c.displayOrder ?? index);

      const existing = reqMap.get(id) || {
        id,
        name,
        necessity,
        displayOrder,
        totalCandidates: 0,
        evidenceFoundCount: 0,
        partialCount: 0,
        needsVerificationCount: 0,
        notFoundCount: 0,
      };

      existing.totalCandidates += 1;

      const state = (c.match_state || c.matchState || c.gap_state || c.gapState || c.result_state || c.resultState || "").toUpperCase();
      if (
        state === "MATCHED" ||
        state === "EVIDENCED" ||
        state === "COVERED" ||
        state === "FULLY_SATISFIED"
      ) {
        existing.evidenceFoundCount += 1;
      } else if (
        state === "PARTIALLY_MATCHED" ||
        state === "PARTIALLY_EVIDENCED" ||
        state === "PARTIALLY_COVERED" ||
        state === "PARTIALLY_SATISFIED"
      ) {
        existing.partialCount += 1;
      } else if (
        state === "INSUFFICIENT_EVIDENCE" ||
        state === "CONFLICTING_EVIDENCE" ||
        state === "UNVERIFIED" ||
        state === "NEEDS_VERIFICATION" ||
        state === "CONFLICTING_REVIEW_REQUIRED"
      ) {
        existing.needsVerificationCount += 1;
      } else {
        existing.notFoundCount += 1;
      }

      reqMap.set(id, existing);
    });
  });

  const criteriaList = Array.from(reqMap.values()).sort((a, b) => a.displayOrder - b.displayOrder);

  if (criteriaList.length === 0) {
    return null;
  }

  const initialDisplayLimit = 5;
  const displayedCriteria = isExpanded ? criteriaList : criteriaList.slice(0, initialDisplayLimit);

  return (
    <div
      className="visual-analytics-card requirement-coverage-card"
      role="region"
      aria-label="Requirement Coverage by Criterion Chart"
    >
      <div className="chart-header">
        <div>
          <h4 className="chart-title">Requirement Coverage by Criterion</h4>
          <p className="chart-subtitle">
            Proportion of candidate resumes containing supporting evidence for active job criteria ({criteriaList.length} criteria)
          </p>
        </div>
        {criteriaList.length > initialDisplayLimit && (
          <button
            type="button"
            onClick={() => setIsExpanded((prev) => !prev)}
            className="btn btn-secondary btn-xs"
            aria-expanded={isExpanded}
          >
            {isExpanded ? "Show Less" : `View All (${criteriaList.length})`}
          </button>
        )}
      </div>

      <div className="coverage-criteria-list" role="list" aria-label="Criterion coverage items">
        {displayedCriteria.map((crit) => {
          const pct = Math.round((crit.evidenceFoundCount / crit.totalCandidates) * 100);
          const ratioDesc = `${crit.evidenceFoundCount} of ${crit.totalCandidates} candidates have supporting resume evidence`;
          const srLabel = `${crit.name} (${crit.necessity.toLowerCase()}): ${ratioDesc} (${pct}%).`;

          return (
            <div
              key={crit.id}
              className="coverage-criterion-row"
              role="listitem"
              aria-label={srLabel}
            >
              <div className="coverage-criterion-meta">
                <div className="criterion-title-group">
                  <span className="criterion-name" title={crit.name}>{crit.name}</span>
                  <span className={`badge ${crit.necessity === "REQUIRED" ? "badge-necessity-must" : "badge-necessity-pref"}`}>
                    {crit.necessity === "REQUIRED" ? "Required" : "Preferred"}
                  </span>
                </div>
                <span className="criterion-ratio-text">{ratioDesc}</span>
              </div>

              <div
                className="coverage-bar-track"
                role="progressbar"
                aria-valuenow={crit.evidenceFoundCount}
                aria-valuemin={0}
                aria-valuemax={crit.totalCandidates}
                aria-label={srLabel}
              >
                <div
                  className="coverage-bar-fill"
                  style={{
                    width: `${pct}%`,
                    backgroundColor: "var(--gap-covered)",
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* =========================================================================
   5. Category Evidence Coverage Chart (Detailed Report)
   ========================================================================= */

interface CategoryEvidenceCoverageChartProps {
  items: GapReportItem[];
}

export function CategoryEvidenceCoverageChart({ items }: CategoryEvidenceCoverageChartProps) {
  if (!items || items.length === 0) {
    return null;
  }

  // Aggregate by requirement category
  const categoriesMap = new Map<
    string,
    {
      category: string;
      total: number;
      covered: number;
      partial: number;
      verification: number;
      notFound: number;
    }
  >();

  items.forEach((it) => {
    const rawCat = (it.category || "OTHER").toUpperCase();
    const existing = categoriesMap.get(rawCat) || {
      category: rawCat,
      total: 0,
      covered: 0,
      partial: 0,
      verification: 0,
      notFound: 0,
    };

    existing.total += 1;
    const state = it.gapState || (it as any).reconciled_state || (it as any).gap_state;

    if (state === "COVERED") {
      existing.covered += 1;
    } else if (state === "PARTIALLY_COVERED") {
      existing.partial += 1;
    } else if (state === "UNVERIFIED" || state === "CONFLICTING_REVIEW_REQUIRED") {
      existing.verification += 1;
    } else {
      existing.notFound += 1;
    }

    categoriesMap.set(rawCat, existing);
  });

  // Filter only categories that have active requirements (count > 0)
  const activeCategories = Array.from(categoriesMap.values()).filter((c) => c.total > 0);

  if (activeCategories.length === 0) {
    return null;
  }

  return (
    <div
      className="visual-analytics-card category-coverage-card"
      role="region"
      aria-label="Category Evidence Coverage Chart"
    >
      <div className="chart-header">
        <div>
          <h4 className="chart-title">Category Evidence Coverage</h4>
          <p className="chart-subtitle">
            Resume evidence coverage across active requirement categories (diagnostic only)
          </p>
        </div>
      </div>

      <div className="category-bars-list" role="list" aria-label="Category evidence breakdown">
        {activeCategories.map((cat) => {
          const catName = `${cat.category.charAt(0).toUpperCase()}${cat.category.slice(1).toLowerCase()}`;
          const pctCovered = Math.round((cat.covered / cat.total) * 100);
          const ratioText = `${cat.covered} of ${cat.total} evidenced`;
          const desc = `${catName}: ${cat.covered} of ${cat.total} evidenced (${pctCovered}%). ${cat.notFound} not evidenced in resume.`;

          return (
            <div
              key={cat.category}
              className="category-bar-row"
              role="listitem"
              aria-label={desc}
            >
              <div className="category-row-meta">
                <span className="category-name-label">{catName}</span>
                <span className="exact-coverage-text">{ratioText}</span>
              </div>

              <div
                className="category-bar-track"
                role="progressbar"
                aria-valuenow={cat.covered}
                aria-valuemin={0}
                aria-valuemax={cat.total}
                aria-label={`${catName}: ${ratioText}`}
              >
                <div
                  className="category-bar-fill"
                  style={{
                    width: `${(cat.covered / cat.total) * 100}%`,
                    backgroundColor: "var(--gap-covered)",
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>

      <p className="category-chart-disclaimer" style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "8px", fontStyle: "italic" }}>
        Missing resume evidence indicates an unevidenced criterion in the supplied profile—it does not establish lack of candidate ability or create a threshold.
      </p>
    </div>
  );
}
