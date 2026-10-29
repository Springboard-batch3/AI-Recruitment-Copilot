import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { RequirementGapReportView } from '../components/jobs/RequirementGapReportView';
import * as jobsApi from '../api/jobs';

vi.mock('../api/jobs', () => ({
  fetchGapReportDetail: vi.fn(),
  fetchCanonicalGapReportJson: vi.fn(),
}));

describe('RequirementGapReportView', () => {
  const mockOnBack = vi.fn();

  const mockReportDetail: any = {
    reportId: 'rep-test-456',
    jobId: 'job-123',
    jobVersionId: 'job-ver-1',
    revision: 1,
    matchRunId: 'run-123',
    matchResultId: 'res-alice-111',
    profileId: 'cand-alice-001',
    profileVersion: 1,
    evaluationDate: '2026-09-10T12:00:00Z',
    matcherAlgorithmVersion: '2.4.0',
    matchingRuleVersion: '2.4.0',
    scoringPolicyVersion: '2.4.0',
    matchInputSchemaVersion: '2.4.0',
    gapSchemaVersion: '1.0.0',
    gapPolicyVersion: '1.0.0',
    wordingTemplateVersion: '1.0.0',
    sourceFingerprint: 'src-fp-1',
    reportFingerprint: 'rep-fp-1',
    totalRequirements: 3,
    coveredCount: 1,
    partiallyCoveredCount: 1,
    notEvidencedCount: 1,
    unverifiedCount: 0,
    conflictingCount: 0,
    notApplicableCount: 0,
    mustHaveWarningCount: 1,
    compatibilityScore: 84.5,
    confidenceTier: 'HIGH',
    createdAt: '2026-09-10T12:00:00Z',
    items: [
      {
        itemId: 'item-1',
        requirementId: 'req-1',
        displayOrder: 1,
        category: 'Python Proficiency',
        necessity: 'MUST_HAVE',
        priority: 'HIGH',
        sourceMatchState: 'SATISFIED',
        sourceMatchValue: 1.0,
        gapState: 'COVERED',
        requiredValue: '5+ years',
        documentedValue: '6 years at Google',
        measurableShortfallValue: null,
        shortfallUnit: null,
        shortfallReasonCode: null,
        evidenceReferences: ['resume.pdf:page 2:Senior Python developer for 6 years'],
        reviewRequired: false,
        reviewActionCode: 'NONE',
        mustHaveWarningReference: null,
        recruiterWording: 'Candidate fully meets Python criteria with 6 years documented.',
        createdAt: '2026-09-10T12:00:00Z',
      },
      {
        itemId: 'item-2',
        requirementId: 'req-2',
        displayOrder: 2,
        category: 'AWS Cloud Architecture',
        necessity: 'MUST_HAVE',
        priority: 'CRITICAL',
        sourceMatchState: 'PARTIALLY_SATISFIED',
        sourceMatchValue: 0.5,
        gapState: 'PARTIALLY_COVERED',
        requiredValue: '3+ years AWS',
        documentedValue: '1 year AWS, 2 years GCP',
        measurableShortfallValue: 2.0,
        shortfallUnit: 'years',
        shortfallReasonCode: 'INSUFFICIENT_DURATION',
        evidenceReferences: ['resume.pdf:page 3:Worked with EC2 and S3 for 1 year'],
        reviewRequired: true,
        reviewActionCode: 'VERIFY_EVIDENCE',
        mustHaveWarningReference: 'WARN_AWS_EXP',
        recruiterWording: 'Candidate has 1 year AWS experience against 3+ years required.',
        createdAt: '2026-09-10T12:00:00Z',
      },
      {
        itemId: 'item-3',
        requirementId: 'req-3',
        displayOrder: 3,
        category: 'GraphQL API Design',
        necessity: 'PREFERRED',
        priority: 'MEDIUM',
        sourceMatchState: 'UNSATISFIED',
        sourceMatchValue: 0.0,
        gapState: 'NOT_EVIDENCED',
        requiredValue: 'Production experience',
        documentedValue: 'None cited',
        measurableShortfallValue: null,
        shortfallUnit: null,
        shortfallReasonCode: 'MISSING_SKILL',
        evidenceReferences: [],
        reviewRequired: true,
        reviewActionCode: 'REQUEST_CLARIFICATION',
        mustHaveWarningReference: null,
        recruiterWording: 'No documented GraphQL experience found in resume.',
        createdAt: '2026-09-10T12:00:00Z',
      },
    ],
  };

  beforeEach(() => {
    vi.resetAllMocks();
    window.print = vi.fn();
    window.URL.createObjectURL = vi.fn(() => 'blob:mock-url');
    window.URL.revokeObjectURL = vi.fn();
  });

  it('renders loading state initially and then displays the full report', async () => {
    vi.mocked(jobsApi.fetchGapReportDetail).mockResolvedValueOnce(mockReportDetail);

    render(
      <RequirementGapReportView
        reportId="rep-test-456"
        candidateName="Alice Smith"
        jobTitle="Staff Software Engineer"
        onNavigateBack={mockOnBack}
      />
    );

    expect(screen.getByText(/Loading requirement gap report/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText(/Alice Smith/i)).toBeInTheDocument();
      expect(screen.getByText(/Staff Software Engineer/i)).toBeInTheDocument();
      expect(screen.getByText('85%')).toBeInTheDocument();
      expect(screen.getAllByText('HIGH').length).toBeGreaterThanOrEqual(1);
    });

    // Check summary cards and table assessments with domain terminology
    expect(screen.getAllByText(/Evidence found/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Partial evidence found/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Not found in resume/i).length).toBeGreaterThanOrEqual(1);

    // Check gap items
    expect(screen.getByText('Python Proficiency')).toBeInTheDocument();
    expect(screen.getByText('AWS Cloud Architecture')).toBeInTheDocument();
    expect(screen.getByText('GraphQL API Design')).toBeInTheDocument();
  });

  it('filters requirements by state without changing summary counts', async () => {
    vi.mocked(jobsApi.fetchGapReportDetail).mockResolvedValueOnce(mockReportDetail);

    render(
      <RequirementGapReportView
        reportId="rep-test-456"
        candidateName="Alice Smith"
        jobTitle="Staff Software Engineer"
        onNavigateBack={mockOnBack}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Python Proficiency')).toBeInTheDocument();
    });

    // Filter to "Required Only"
    const necessitySelect = screen.getByLabelText(/Requirement type:/i);
    fireEvent.change(necessitySelect, { target: { value: 'MUST_HAVE' } });

    // Python (must-have) and AWS (must-have) should remain, GraphQL (preferred) should be filtered out
    expect(screen.getByText('Python Proficiency')).toBeInTheDocument();
    expect(screen.getByText('AWS Cloud Architecture')).toBeInTheDocument();
    expect(screen.queryByText('GraphQL API Design')).not.toBeInTheDocument();

    // Summary counts in the breakdown remain intact
    expect(screen.getAllByText(/Evidence found/i).length).toBeGreaterThanOrEqual(1);
  });

  it('expands and collapses evidence experts when clicking experts button', async () => {
    vi.mocked(jobsApi.fetchGapReportDetail).mockResolvedValueOnce(mockReportDetail);

    render(
      <RequirementGapReportView
        reportId="rep-test-456"
        candidateName="Alice Smith"
        jobTitle="Staff Software Engineer"
        onNavigateBack={mockOnBack}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Python Proficiency')).toBeInTheDocument();
    });

    const viewEvidenceBtns = screen.getAllByRole('button', { name: /View 1 evidence excerpt/i });
    fireEvent.click(viewEvidenceBtns[0]); // Click evidence for Python

    expect(screen.getByText(/Document experts & Citations/i)).toBeInTheDocument();
    expect(screen.getByText(/resume.pdf:page 2:Senior Python developer for 6 years/i)).toBeInTheDocument();

    // Toggle collapse
    fireEvent.click(viewEvidenceBtns[0]);

    await waitFor(() => {
      expect(screen.queryByText(/Document experts & Citations/i)).not.toBeInTheDocument();
    });
  });

  it('triggers canonical JSON download and print dialog', async () => {
    vi.mocked(jobsApi.fetchGapReportDetail).mockResolvedValueOnce(mockReportDetail);
    vi.mocked(jobsApi.fetchCanonicalGapReportJson).mockResolvedValueOnce({
      reportId: 'rep-test-456',
      canonical: true,
    } as any);

    render(
      <RequirementGapReportView
        reportId="rep-test-456"
        candidateName="Alice Smith"
        jobTitle="Staff Software Engineer"
        onNavigateBack={mockOnBack}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Download Canonical Report JSON/i })).toBeInTheDocument();
    });

    // Test Export JSON
    const exportBtn = screen.getByRole('button', { name: /Download Canonical Report JSON/i });
    fireEvent.click(exportBtn);

    await waitFor(() => {
      expect(jobsApi.fetchCanonicalGapReportJson).toHaveBeenCalledWith('rep-test-456');
    });

    // Test Print
    const printBtn = screen.getByRole('button', { name: /Print Report/i });
    fireEvent.click(printBtn);
    expect(window.print).toHaveBeenCalled();
  });

  it('navigates back when clicking Back to results', async () => {
    vi.mocked(jobsApi.fetchGapReportDetail).mockResolvedValueOnce(mockReportDetail);

    render(
      <RequirementGapReportView
        reportId="rep-test-456"
        candidateName="Alice Smith"
        jobTitle="Staff Software Engineer"
        onNavigateBack={mockOnBack}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Back to results/i })).toBeInTheDocument();
    });

    const backBtn = screen.getByRole('button', { name: /Back to results/i });
    fireEvent.click(backBtn);
    expect(mockOnBack).toHaveBeenCalledTimes(1);
  });
});
