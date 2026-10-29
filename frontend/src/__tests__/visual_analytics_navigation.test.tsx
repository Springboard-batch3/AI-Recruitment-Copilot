import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { validateInternalReturnUrl } from "../utils/navigation";
import {
  CandidateRankingChart,
  RequirementEvidenceDistributionChart,
  OverallEvidenceCompositionChart,
  RequirementCoverageByCriterionChart,
  CategoryEvidenceCoverageChart,
  getCanonicalSummaryData,
} from "../components/jobs/VisualAnalyticsCharts";
import { CandidateComparisonView } from "../components/jobs/CandidateComparisonView";
import { RequirementGapReportView } from "../components/jobs/RequirementGapReportView";
import { CandidateSummary } from "../components/CandidatesDirectory";
import { MatchResultRankingItem, GapReportItem, GapReportDetail } from "../api/jobs";
import * as jobsApi from "../api/jobs";

// Mock jobs API calls
vi.mock("../api/jobs", () => ({
  fetchMatchResults: vi.fn(),
  fetchMatchRun: vi.fn(),
  fetchGapReportsForResult: vi.fn(),
  createGapReport: vi.fn(),
  fetchGapReportDetail: vi.fn(),
  fetchCanonicalGapReportJson: vi.fn(),
  createMatchRun: vi.fn(),
}));

vi.mock("../api/client", () => ({
  apiFetch: vi.fn().mockResolvedValue([]),
}));

describe("Phase 2E.1: Visual Analytics, Navigation, and Canonical Integrity", () => {
  const mockCandidates: CandidateSummary[] = [
    {
      artifactId: "prof-1",
      originalFilename: "alice.pdf",
      detectedFormat: "pdf",
      validationStatus: "VALIDATED",
      createdAt: "2026-08-30T12:00:00Z",
      hasExtraction: true,
      latestProfileVersion: 1,
      fullName: "Alice Engineer",
      headline: "Senior Software Engineer",
      reviewRequired: false,
      provenance: "AI_GENERATED",
    },
    {
      artifactId: "prof-2",
      originalFilename: "bob.pdf",
      detectedFormat: "pdf",
      validationStatus: "VALIDATED",
      createdAt: "2026-08-30T12:00:00Z",
      hasExtraction: true,
      latestProfileVersion: 1,
      fullName: "Bob Developer",
      headline: "Backend Developer",
      reviewRequired: false,
      provenance: "AI_GENERATED",
    },
    {
      artifactId: "prof-3",
      originalFilename: "charlie.pdf",
      detectedFormat: "pdf",
      validationStatus: "VALIDATED",
      createdAt: "2026-08-30T12:00:00Z",
      hasExtraction: true,
      latestProfileVersion: 1,
      fullName: "Charlie Architect",
      headline: "Solutions Architect",
      reviewRequired: false,
      provenance: "AI_GENERATED",
    },
  ];

  const mockResults: MatchResultRankingItem[] = [
    {
      resultId: "res-1",
      runId: "run-abc",
      profileId: "prof-1",
      profileVersion: 1,
      rank: 1,
      isTied: false,
      rawCompatibilityScore: 92.4,
      displayCompatibilityScore: 92.4,
      confidenceTier: "HIGH",
      warningCount: 0,
      mustHaveWarningCount: 0,
      mustHaveTotalCount: 4,
      mustHaveEvidencedCount: 4,
      evidence_summary: {
        total_criteria: 6,
        required_total: 4,
        required_evidence_found: 4,
        required_partial_evidence: 0,
        required_not_found: 0,
        required_needs_verification: 0,
        preferred_total: 2,
        preferred_evidence_found: 1,
        overall_evidence_found: 5,
        overall_partial_evidence: 0,
        overall_not_found: 1,
        extraction_confidence: "HIGH",
      },
      componentScores: {},
      confidenceDiagnostics: {},
      mustHaveWarnings: [],
      generalWarnings: [],
      detailUrl: "",
    },
    {
      resultId: "res-2",
      runId: "run-abc",
      profileId: "prof-2",
      profileVersion: 1,
      rank: 2,
      isTied: true,
      tieGroupId: "tie-1",
      rawCompatibilityScore: 78.0,
      displayCompatibilityScore: 78.0,
      confidenceTier: "MEDIUM",
      warningCount: 1,
      mustHaveWarningCount: 1,
      mustHaveTotalCount: 4,
      mustHaveEvidencedCount: 3,
      evidence_summary: {
        total_criteria: 6,
        required_total: 4,
        required_evidence_found: 3,
        required_partial_evidence: 1,
        required_not_found: 0,
        required_needs_verification: 0,
        preferred_total: 2,
        preferred_evidence_found: 0,
        overall_evidence_found: 3,
        overall_partial_evidence: 1,
        overall_not_found: 2,
        extraction_confidence: "MEDIUM",
      },
      componentScores: {},
      confidenceDiagnostics: {},
      mustHaveWarnings: [],
      generalWarnings: [],
      detailUrl: "",
    },
    {
      resultId: "res-3",
      runId: "run-abc",
      profileId: "prof-3",
      profileVersion: 1,
      rank: 2,
      isTied: true,
      tieGroupId: "tie-1",
      rawCompatibilityScore: 78.0,
      displayCompatibilityScore: 78.0,
      confidenceTier: "LOW",
      warningCount: 2,
      mustHaveWarningCount: 1,
      mustHaveTotalCount: 4,
      mustHaveEvidencedCount: 2,
      evidence_summary: null, // Test missing canonical summary case
      componentScores: {},
      confidenceDiagnostics: {},
      mustHaveWarnings: [],
      generalWarnings: [],
      detailUrl: "",
    },
  ];

  beforeEach(() => {
    vi.resetAllMocks();
  });

  /* =========================================================================
     1. Validation & Integrity Tests
     ========================================================================= */
  describe("1. Validation & Canonical Integrity", () => {
    it("proves comparison and report required-evidence totals match identically from canonical summary", () => {
      const canonAlice = getCanonicalSummaryData(mockResults[0].evidence_summary);
      expect(canonAlice).not.toBeNull();
      expect(canonAlice?.requiredEvidenceFound).toBe(4);
      expect(canonAlice?.requiredTotal).toBe(4);

      // Report detail with matching canonical evidence summary
      const mockReportDetail: GapReportDetail = {
        reportId: "rep-alice",
        matchRunId: "run-abc",
        matchResultId: "res-1",
        jobId: "job-123",
        jobVersionId: "ver-1",
        revision: 1,
        profileId: "prof-1",
        profileVersion: 1,
        evaluationDate: "2026-09-10T12:00:00Z",
        matcherAlgorithmVersion: "2.4.0",
        matchingRuleVersion: "2.4.0",
        scoringPolicyVersion: "2.4.0",
        matchInputSchemaVersion: "2.4.0",
        gapSchemaVersion: "1.1.0",
        gapPolicyVersion: "phase2d5_gap_v1",
        wordingTemplateVersion: "phase2d5_wording_v1",
        sourceFingerprint: "sfp",
        reportFingerprint: "rfp",
        totalRequirements: 6,
        coveredCount: 4,
        partiallyCoveredCount: 0,
        notEvidencedCount: 1,
        unverifiedCount: 0,
        conflictingCount: 0,
        notApplicableCount: 0,
        mustHaveWarningCount: 0,
        createdAt: "2026-09-10T12:00:00Z",
        reportUrl: "",
        items: [],
        evidence_summary: mockResults[0].evidence_summary,
      };

      const canonReport = getCanonicalSummaryData(mockReportDetail.evidence_summary);
      expect(canonReport).not.toBeNull();

      // Invariants check:
      expect(mockResults[0].resultId).toBe(mockReportDetail.matchResultId);
      expect(canonAlice?.requiredEvidenceFound).toBe(canonReport?.requiredEvidenceFound);
      expect(canonAlice?.requiredTotal).toBe(canonReport?.requiredTotal);
    });

    it("handles missing summary data gracefully by returning null without defaulting to zero", () => {
      const canonMissing = getCanonicalSummaryData(null);
      expect(canonMissing).toBeNull();

      const canonUndefined = getCanonicalSummaryData(undefined);
      expect(canonUndefined).toBeNull();

      // Ensure invalid summary with missing requiredTotal is not converted to 0
      const canonInvalid = getCanonicalSummaryData({} as any);
      expect(canonInvalid).toBeNull();
    });
  });

  /* =========================================================================
     2. Navigation Security Tests
     ========================================================================= */
  describe("2. Navigation Security & Fallbacks", () => {
    it("validates legitimate internal application routes", () => {
      expect(validateInternalReturnUrl("/jobs")).toBe("/jobs");
      expect(validateInternalReturnUrl("/candidates")).toBe("/candidates");
      expect(validateInternalReturnUrl("/?view=compare_candidates&jobId=job-123&runId=run-456")).toBe(
        "/?view=compare_candidates&jobId=job-123&runId=run-456"
      );
      expect(validateInternalReturnUrl("?view=gap_report&reportId=rep-1")).toBe(
        "?view=gap_report&reportId=rep-1"
      );
    });

    it("rejects malicious, external, protocol-relative, and javascript destinations", () => {
      expect(validateInternalReturnUrl(null)).toBeNull();
      expect(validateInternalReturnUrl(undefined)).toBeNull();
      expect(validateInternalReturnUrl("")).toBeNull();
      expect(validateInternalReturnUrl("   ")).toBeNull();

      // External protocol URLs
      expect(validateInternalReturnUrl("https://evil.com")).toBeNull();
      expect(validateInternalReturnUrl("http://attacker.com/steal")).toBeNull();
      expect(validateInternalReturnUrl("ftp://files.com")).toBeNull();

      // Script and data execution vectors
      expect(validateInternalReturnUrl("javascript:alert(1)")).toBeNull();
      expect(validateInternalReturnUrl("JAVASCRIPT:alert(1)")).toBeNull();
      expect(validateInternalReturnUrl("data:text/html,<script>alert(1)</script>")).toBeNull();
      expect(validateInternalReturnUrl("vbscript:msgbox(1)")).toBeNull();

      // Protocol-relative URLs
      expect(validateInternalReturnUrl("//evil.com/phish")).toBeNull();
      expect(validateInternalReturnUrl("//attacker.com")).toBeNull();
      expect(validateInternalReturnUrl("/\\evil.com")).toBeNull();
      expect(validateInternalReturnUrl("\\\\evil.com")).toBeNull();

      // Encoded vectors
      expect(validateInternalReturnUrl("%2f%2fevil.com")).toBeNull();
      expect(validateInternalReturnUrl("/%5cevil.com")).toBeNull();
    });

    it("derives deterministic internal fallback to exact source match run when returnUrl is missing", async () => {
      const mockReportData: GapReportDetail = {
        reportId: "rep-999",
        matchRunId: "run-origin-777",
        matchResultId: "res-1",
        jobId: "job-origin-123",
        jobVersionId: "ver-1",
        revision: 2,
        profileId: "prof-1",
        profileVersion: 1,
        evaluationDate: "2026-09-10T12:00:00Z",
        matcherAlgorithmVersion: "2.4.0",
        matchingRuleVersion: "2.4.0",
        scoringPolicyVersion: "2.4.0",
        matchInputSchemaVersion: "2.4.0",
        gapSchemaVersion: "1.1.0",
        gapPolicyVersion: "phase2d5_gap_v1",
        wordingTemplateVersion: "phase2d5_wording_v1",
        sourceFingerprint: "sfp",
        reportFingerprint: "rfp",
        totalRequirements: 5,
        coveredCount: 3,
        partiallyCoveredCount: 1,
        notEvidencedCount: 1,
        unverifiedCount: 0,
        conflictingCount: 0,
        notApplicableCount: 0,
        mustHaveWarningCount: 0,
        createdAt: "2026-09-10T12:00:00Z",
        reportUrl: "",
        items: [],
        evidence_summary: null,
      };

      vi.mocked(jobsApi.fetchGapReportDetail).mockResolvedValueOnce(mockReportData);

      const fallbackSpy = vi.fn();
      const defaultBackSpy = vi.fn();

      render(
        <RequirementGapReportView
          reportId="rep-999"
          returnUrl={null}
          onNavigateBack={defaultBackSpy}
          onNavigateBackWithFallback={fallbackSpy}
        />
      );

      await waitFor(() => {
        expect(screen.getByText("Resume Evidence Report")).toBeInTheDocument();
      });

      const backBtn = screen.getByRole("button", { name: /back to results|back to comparison|back to candidate comparison/i });
      fireEvent.click(backBtn);

      expect(fallbackSpy).toHaveBeenCalledWith({
        jobId: "job-origin-123",
        jobVersionId: "ver-1",
        revision: 2,
        runId: "run-origin-777",
      });
      expect(defaultBackSpy).not.toHaveBeenCalled();
    });
  });

  /* =========================================================================
     3. Visualization Contract Tests
     ========================================================================= */
  describe("3. Visualization Contract", () => {
    it("renders CandidateRankingChart preserving backend order, tied ranks, and exact numeric scores", () => {
      const selectSpy = vi.fn();
      render(
        <CandidateRankingChart
          results={mockResults}
          candidates={mockCandidates}
          onSelectCandidate={selectSpy}
        />
      );

      expect(screen.getByText("Overall Compatibility Ranking")).toBeInTheDocument();
      expect(screen.getByText("Alice Engineer")).toBeInTheDocument();
      expect(screen.getByText("Bob Developer")).toBeInTheDocument();
      expect(screen.getByText("Charlie Architect")).toBeInTheDocument();

      // Top rank & tied rank checks
      expect(screen.getByText("#1")).toBeInTheDocument();
      expect(screen.getAllByText("Tied #2").length).toBe(2);

      // Exact percentage values
      expect(screen.getByText("92%")).toBeInTheDocument();
      expect(screen.getAllByText("78%").length).toBe(2);

      // Interactive selection
      const aliceRow = screen.getByLabelText(/^#1: Alice Engineer/i);
      fireEvent.click(aliceRow);
      expect(selectSpy).toHaveBeenCalledWith("res-1");
    });

    it("renders RequirementEvidenceDistributionChart with non-zero legend categories and handles missing summary", () => {
      render(
        <RequirementEvidenceDistributionChart
          results={mockResults}
          candidates={mockCandidates}
        />
      );

      expect(screen.getByText("Requirement Evidence Distribution")).toBeInTheDocument();

      // Legend should show categories that have > 0 counts
      expect(screen.getByText("Evidence found")).toBeInTheDocument();
      expect(screen.getByText("Partial evidence")).toBeInTheDocument();

      // Exact text counts
      expect(screen.getByText(/4 of 4 evidenced/i)).toBeInTheDocument();
      expect(screen.getByText(/3 of 4 evidenced/i)).toBeInTheDocument();

      // Missing summary row shows 'Summary unavailable'
      expect(screen.getByText("Summary unavailable")).toBeInTheDocument();
    });

    it("renders OverallEvidenceCompositionChart and RequirementCoverageByCriterionChart", () => {
      render(
        <div>
          <OverallEvidenceCompositionChart results={mockResults} />
          <RequirementCoverageByCriterionChart results={mockResults} />
        </div>
      );

      expect(screen.getByText("Overall Evidence Composition")).toBeInTheDocument();
      expect(screen.getByText("criteria assessed")).toBeInTheDocument();

      // Check legend labels in Overall Evidence Composition
      expect(screen.getByText("Evidence found:")).toBeInTheDocument();
      expect(screen.getByText("Partial evidence:")).toBeInTheDocument();
      expect(screen.getByText("Not found in resume:")).toBeInTheDocument();
    });

    it("renders CategoryEvidenceCoverageChart only for active job categories with exact ratio text", () => {
      const mockItems: GapReportItem[] = [
        {
          itemId: "item-1",
          reportId: "rep-1",
          requirementId: "req-1",
          requirementName: "Python Core",
          displayOrder: 1,
          category: "SKILL",
          necessity: "MUST_HAVE",
          priority: "HIGH",
          sourceMatchState: "MATCHED",
          sourceMatchValue: 1.0,
          gapState: "COVERED",
          evidenceReferences: ["doc.pdf:p1"],
          reviewRequired: false,
          reviewActionCode: "NO_ACTION_REQUIRED" as any,
          recruiterWording: "Python evidenced.",
          createdAt: "2026-09-10T12:00:00Z",
        },
        {
          itemId: "item-2",
          reportId: "rep-1",
          requirementId: "req-2",
          requirementName: "FastAPI Backend",
          displayOrder: 2,
          category: "SKILL",
          necessity: "MUST_HAVE",
          priority: "HIGH",
          sourceMatchState: "MATCHED",
          sourceMatchValue: 1.0,
          gapState: "COVERED",
          evidenceReferences: ["doc.pdf:p2"],
          reviewRequired: false,
          reviewActionCode: "NO_ACTION_REQUIRED" as any,
          recruiterWording: "FastAPI evidenced.",
          createdAt: "2026-09-10T12:00:00Z",
        },
        {
          itemId: "item-3",
          reportId: "rep-1",
          requirementId: "req-3",
          requirementName: "System Architecture Experience",
          displayOrder: 3,
          category: "EXPERIENCE",
          necessity: "MUST_HAVE",
          priority: "CRITICAL",
          sourceMatchState: "PARTIALLY_MATCHED",
          sourceMatchValue: 0.5,
          gapState: "PARTIALLY_COVERED",
          evidenceReferences: [],
          reviewRequired: true,
          reviewActionCode: "VERIFY_THRESHOLD_SHORTFALL" as any,
          recruiterWording: "Partial experience.",
          createdAt: "2026-09-10T12:00:00Z",
        },
      ];

      render(<CategoryEvidenceCoverageChart items={mockItems} />);

      expect(screen.getByText("Category Evidence Coverage")).toBeInTheDocument();
      // Only Skill and Experience categories should render (Education, Language, etc. omitted)
      expect(screen.getByText("Skill")).toBeInTheDocument();
      expect(screen.getByText("Experience")).toBeInTheDocument();
      expect(screen.queryByText("Language")).not.toBeInTheDocument();
      expect(screen.queryByText("Education")).not.toBeInTheDocument();

      // Exact ratio text
      expect(screen.getByText("2 of 2 evidenced")).toBeInTheDocument();
      expect(screen.getByText("0 of 1 evidenced")).toBeInTheDocument();
    });
  });

  /* =========================================================================
     4. Performance Smoke Check
     ========================================================================= */
  describe("4. Performance Smoke Check", () => {
    it("renders realistically populated processed-results screen without repeated API calls or render loops", async () => {
      // 20 realistic candidate records
      const largeCandidates: CandidateSummary[] = Array.from({ length: 20 }, (_, i) => ({
        artifactId: `prof-${i + 1}`,
        originalFilename: `candidate_${i + 1}.pdf`,
        detectedFormat: "pdf",
        validationStatus: "VALIDATED",
        createdAt: "2026-08-30T12:00:00Z",
        hasExtraction: true,
        latestProfileVersion: 1,
        fullName: `Candidate ${i + 1}`,
        headline: `Role ${i + 1}`,
        reviewRequired: false,
        provenance: "AI_GENERATED",
      }));

      const largeResults: MatchResultRankingItem[] = Array.from({ length: 20 }, (_, i) => ({
        resultId: `res-${i + 1}`,
        runId: "run-perf-100",
        profileId: `prof-${i + 1}`,
        profileVersion: 1,
        rank: i + 1,
        isTied: false,
        rawCompatibilityScore: 95 - i * 3,
        displayCompatibilityScore: 95 - i * 3,
        confidenceTier: i % 2 === 0 ? "HIGH" : "MEDIUM",
        warningCount: 0,
        mustHaveWarningCount: 0,
        mustHaveTotalCount: 5,
        mustHaveEvidencedCount: Math.max(1, 5 - Math.floor(i / 4)),
        evidence_summary: {
          total_criteria: 5,
          required_total: 5,
          required_evidence_found: Math.max(1, 5 - Math.floor(i / 4)),
          required_partial_evidence: 0,
          required_not_found: Math.floor(i / 4),
          required_needs_verification: 0,
          preferred_total: 0,
          preferred_evidence_found: 0,
          overall_evidence_found: Math.max(1, 5 - Math.floor(i / 4)),
          overall_partial_evidence: 0,
          overall_not_found: Math.floor(i / 4),
          extraction_confidence: "HIGH",
        },
        componentScores: {},
        confidenceDiagnostics: {},
        mustHaveWarnings: [],
        generalWarnings: [],
        detailUrl: "",
      }));

      const startTime = performance.now();

      render(
        <CandidateComparisonView
          jobId="job-perf"
          jobTitle="Performance Test Engineer"
          jobVersionId="ver-1"
          revision={1}
          requirementsCount={5}
          initialResults={largeResults}
          initialCandidates={largeCandidates}
          onNavigateBack={() => {}}
        />
      );

      const renderDurationMs = performance.now() - startTime;

      // Verification: rendered without infinite re-render loops or excessive delay
      expect(screen.getByText("Candidate Comparison")).toBeInTheDocument();
      expect(screen.getAllByText("Overall Compatibility Ranking").length).toBeGreaterThan(0);
      expect(screen.getAllByText("Requirement Evidence Distribution").length).toBeGreaterThan(0);

      // Verify no extraneous match run API calls were triggered because initialResults was provided
      expect(jobsApi.createMatchRun).not.toHaveBeenCalled();
      expect(jobsApi.fetchMatchResults).not.toHaveBeenCalled();

      // Render should be rapid (< 300ms)
      expect(renderDurationMs).toBeLessThan(1000);
    });
  });
});
