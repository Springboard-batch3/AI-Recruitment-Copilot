import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { CandidateComparisonView } from '../components/jobs/CandidateComparisonView';
import * as jobsApi from '../api/jobs';

// Mock the API calls
vi.mock('../api/jobs', () => ({
  fetchGapReportsForResult: vi.fn(),
  createGapReport: vi.fn(),
  fetchMatchResults: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));

describe('CandidateComparisonView Gap Workflow', () => {
  const mockOnOpenGapReport = vi.fn();
  const mockOnNavigateBack = vi.fn();

  const mockInitialResults: any[] = [
    {
      resultId: 'res-alice-111',
      runId: 'run-1',
      profileId: 'prof-alice',
      profileVersion: 1,
      rank: 1,
      isTied: false,
      rawCompatibilityScore: 82.5,
      displayCompatibilityScore: 82.5,
      confidenceTier: 'HIGH',
      warningCount: 3,
      mustHaveWarningCount: 1,
      mustHaveTotalCount: 5,
      mustHaveEvidencedCount: 3,
      evidence_summary: {
        total_criteria: 5,
        required_total: 5,
        required_evidence_found: 3,
        required_partial_evidence: 1,
        required_not_found: 1,
        required_needs_verification: 0,
        preferred_total: 0,
        preferred_evidence_found: 0,
        overall_evidence_found: 3,
        overall_partial_evidence: 1,
        overall_not_found: 1,
        extraction_confidence: 'HIGH',
      },
      componentScores: {},
      confidenceDiagnostics: {},
      mustHaveWarnings: [
        {
          rule_code: 'MUST_1',
          requirement_id: 'req-1',
          requirement_name: 'Python',
          safe_explanation: 'Missing 1 year',
        },
      ],
      generalWarnings: [],
      detailUrl: '',
    },
    {
      resultId: 'res-bob-222',
      runId: 'run-1',
      profileId: 'prof-bob',
      profileVersion: 1,
      rank: 2,
      isTied: false,
      rawCompatibilityScore: 45.0,
      displayCompatibilityScore: 45.0,
      confidenceTier: 'LOW',
      warningCount: 5,
      mustHaveWarningCount: 3,
      mustHaveTotalCount: 5,
      mustHaveEvidencedCount: 2,
      evidence_summary: {
        total_criteria: 5,
        required_total: 5,
        required_evidence_found: 2,
        required_partial_evidence: 0,
        required_not_found: 3,
        required_needs_verification: 1,
        preferred_total: 0,
        preferred_evidence_found: 0,
        overall_evidence_found: 2,
        overall_partial_evidence: 0,
        overall_not_found: 3,
        extraction_confidence: 'LOW',
      },
      componentScores: {},
      confidenceDiagnostics: {},
      mustHaveWarnings: [],
      generalWarnings: [],
      detailUrl: '',
    },
    {
      resultId: '', // Legacy result with no ID
      runId: 'run-1',
      profileId: 'prof-charlie',
      profileVersion: 1,
      rank: 3,
      isTied: false,
      rawCompatibilityScore: 60.0,
      displayCompatibilityScore: 60.0,
      confidenceTier: 'MEDIUM',
      warningCount: 0,
      mustHaveWarningCount: 0,
      componentScores: {},
      confidenceDiagnostics: {},
      mustHaveWarnings: [],
      generalWarnings: [],
      detailUrl: '',
    },
  ];

  const mockCandidates: any[] = [
    { artifactId: 'prof-alice', fullName: 'Alice Smith', latestProfileVersion: 1 },
    { artifactId: 'prof-bob', fullName: 'Bob Jones', latestProfileVersion: 1 },
    { artifactId: 'prof-charlie', fullName: 'Charlie Brown', latestProfileVersion: 1 },
  ];

  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders table headers and row items with non-red terminology', () => {
    render(
      <CandidateComparisonView
        jobId="job-123"
        jobTitle="Senior Software Engineer"
        jobVersionId="ver-1"
        revision={1}
        requirementsCount={5}
        onNavigateBack={mockOnNavigateBack}
        onOpenGapReport={mockOnOpenGapReport}
        initialResults={mockInitialResults}
        initialCandidates={mockCandidates}
      />
    );

    expect(screen.getByText('Candidate Comparison')).toBeInTheDocument();
    expect(screen.getAllByText(/required criteria have supporting resume evidence/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/3 of 5/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/2 of 5/i).length).toBeGreaterThan(0);

    const buttons = screen.getAllByRole('button', { name: /^review\b|review evidence|view gap report/i });
    expect(buttons.length).toBe(2); // Charlie has no resultId -> Summary unavailable
    expect(screen.getAllByText('Summary unavailable').length).toBeGreaterThan(0);
  });

  it('reuses existing gap report without calling createGapReport', async () => {
    vi.mocked(jobsApi.fetchGapReportsForResult).mockResolvedValueOnce([
      {
        reportId: 'rep-existing-999',
        matchRunId: 'run-1',
        matchResultId: 'res-alice-111',
        jobId: 'job-123',
        jobVersionId: 'ver-1',
        revision: 1,
        profileId: 'prof-alice',
        profileVersion: 1,
        evaluationDate: '2026-09-10T12:00:00Z',
        matcherAlgorithmVersion: '2.4.0',
        matchingRuleVersion: '2.4.0',
        scoringPolicyVersion: '2.4.0',
        matchInputSchemaVersion: '2.4.0',
        gapSchemaVersion: '1.0.0',
        gapPolicyVersion: '1.0.0',
        wordingTemplateVersion: '1.0.0',
        sourceFingerprint: 'fp',
        reportFingerprint: 'rfp',
        totalRequirements: 5,
        coveredCount: 3,
        partiallyCoveredCount: 1,
        notEvidencedCount: 1,
        unverifiedCount: 0,
        conflictingCount: 0,
        notApplicableCount: 0,
        mustHaveWarningCount: 1,
        reused: false,
        createdAt: '2026-09-10T12:00:00Z',
        reportUrl: '',
      },
    ]);

    render(
      <CandidateComparisonView
        jobId="job-123"
        jobTitle="Senior Software Engineer"
        jobVersionId="ver-1"
        revision={1}
        requirementsCount={5}
        onNavigateBack={mockOnNavigateBack}
        onOpenGapReport={mockOnOpenGapReport}
        initialResults={mockInitialResults}
        initialCandidates={mockCandidates}
      />
    );

    const buttons = screen.getAllByRole('button', { name: /^review\b|review evidence|view gap report/i });
    fireEvent.click(buttons[0]); // Click Alice

    await waitFor(() => {
      expect(jobsApi.fetchGapReportsForResult).toHaveBeenCalledWith('res-alice-111');
      expect(jobsApi.createGapReport).not.toHaveBeenCalled();
      expect(mockOnOpenGapReport).toHaveBeenCalledWith('rep-existing-999', expect.any(Object));
    });
  });

  it('creates gap report when none exists and navigates with returned reportId', async () => {
    vi.mocked(jobsApi.fetchGapReportsForResult).mockResolvedValueOnce([]);
    vi.mocked(jobsApi.createGapReport).mockResolvedValueOnce({
      reportId: 'rep-new-created-888',
      matchRunId: 'run-1',
      matchResultId: 'res-bob-222',
      jobId: 'job-123',
      jobVersionId: 'ver-1',
      revision: 1,
      profileId: 'prof-bob',
      profileVersion: 1,
      evaluationDate: '2026-09-10T12:05:00Z',
      matcherAlgorithmVersion: '2.4.0',
      matchingRuleVersion: '2.4.0',
      scoringPolicyVersion: '2.4.0',
      matchInputSchemaVersion: '2.4.0',
      gapSchemaVersion: '1.0.0',
      gapPolicyVersion: '1.0.0',
      wordingTemplateVersion: '1.0.0',
      sourceFingerprint: 'fp',
      reportFingerprint: 'rfp',
      totalRequirements: 5,
      coveredCount: 2,
      partiallyCoveredCount: 1,
      notEvidencedCount: 2,
      unverifiedCount: 0,
      conflictingCount: 0,
      notApplicableCount: 0,
      mustHaveWarningCount: 3,
      reused: false,
      createdAt: '2026-09-10T12:05:00Z',
      reportUrl: '',
    });

    render(
      <CandidateComparisonView
        jobId="job-123"
        jobTitle="Senior Software Engineer"
        jobVersionId="ver-1"
        revision={1}
        requirementsCount={5}
        onNavigateBack={mockOnNavigateBack}
        onOpenGapReport={mockOnOpenGapReport}
        initialResults={mockInitialResults}
        initialCandidates={mockCandidates}
      />
    );

    const buttons = screen.getAllByRole('button', { name: /^review\b|review evidence|view gap report/i });
    fireEvent.click(buttons[1]); // Click Bob

    await waitFor(() => {
      expect(jobsApi.fetchGapReportsForResult).toHaveBeenCalledWith('res-bob-222');
      expect(jobsApi.createGapReport).toHaveBeenCalledWith('res-bob-222', 'gap-res-bob-222');
      expect(mockOnOpenGapReport).toHaveBeenCalledWith('rep-new-created-888', expect.any(Object));
    });
  });

  it('handles rapid double-clicking by firing only one request', async () => {
    vi.mocked(jobsApi.fetchGapReportsForResult).mockResolvedValue([]);
    vi.mocked(jobsApi.createGapReport).mockResolvedValue({ reportId: 'rep-single-click' } as any);

    render(
      <CandidateComparisonView
        jobId="job-123"
        jobTitle="Senior Software Engineer"
        jobVersionId="ver-1"
        revision={1}
        requirementsCount={5}
        onNavigateBack={mockOnNavigateBack}
        onOpenGapReport={mockOnOpenGapReport}
        initialResults={mockInitialResults}
        initialCandidates={mockCandidates}
      />
    );

    const buttons = screen.getAllByRole('button', { name: /^review\b|review evidence|view gap report/i });
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[0]); // Second click while processing

    await waitFor(() => {
      expect(jobsApi.fetchGapReportsForResult).toHaveBeenCalledTimes(1);
    });
  });

  it('displays user-friendly retry message on failure without crashing', async () => {
    vi.mocked(jobsApi.fetchGapReportsForResult).mockRejectedValue(new Error('Network error'));

    render(
      <CandidateComparisonView
        jobId="job-123"
        jobTitle="Senior Software Engineer"
        jobVersionId="ver-1"
        revision={1}
        requirementsCount={5}
        onNavigateBack={mockOnNavigateBack}
        onOpenGapReport={mockOnOpenGapReport}
        initialResults={mockInitialResults}
        initialCandidates={mockCandidates}
      />
    );

    const buttons = screen.getAllByRole('button', { name: /^review\b|review evidence|view gap report/i });
    fireEvent.click(buttons[0]);

    await waitFor(() => {
      expect(jobsApi.fetchGapReportsForResult).toHaveBeenCalledWith('res-alice-111');
    });
    expect(await screen.findByRole('button', { name: /try again/i })).toBeInTheDocument();
    expect(screen.getAllByText(/Network error/i).length).toBeGreaterThanOrEqual(1);
  });

  it('shows legacy rerun message when candidate has no resultId', () => {
    render(
      <CandidateComparisonView
        jobId="job-123"
        jobTitle="Senior Software Engineer"
        jobVersionId="ver-1"
        revision={1}
        requirementsCount={5}
        onNavigateBack={mockOnNavigateBack}
        onOpenGapReport={mockOnOpenGapReport}
        initialResults={mockInitialResults}
        initialCandidates={mockCandidates}
      />
    );

    expect(screen.getAllByText('Summary unavailable').length).toBeGreaterThan(0);
    expect(screen.getByText('Rerun comparison to view gap report.')).toBeInTheDocument();
  });
});
