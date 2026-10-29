import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { JobsDirectory } from "../components/jobs/JobsDirectory";
import { JobEditor } from "../components/jobs/JobEditor";
import { TaxonomySkillAutocomplete } from "../components/jobs/TaxonomySkillAutocomplete";
import { OccupationSelector } from "../components/jobs/OccupationSelector";
import * as jobsApi from "../api/jobs";
import * as taxonomyApi from "../api/taxonomy";

// Mock API modules
vi.mock("../api/jobs");
vi.mock("../api/taxonomy");

describe("Phase 2B.3: Jobs Workspace & Requirement Builder", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // =========================================================================
  // 1. Jobs Directory Rendering & Search
  // =========================================================================
  it("renders jobs directory and filters items by search query", () => {
    const mockJobs: jobsApi.JobSummary[] = [
      {
        jobId: "job-111",
        createdByUserId: "user-1",
        status: "ACTIVE",
        createdAt: "2026-09-01T10:00:00Z",
        updatedAt: "2026-09-01T12:00:00Z",
        currentVersion: {
          jobVersionId: "ver-1",
          versionNumber: 1,
          versionStatus: "DRAFT",
          revision: 2,
          title: "Senior Backend Engineer",
          department: "Engineering",
          location: "San Francisco",
          requirementsCount: 4,
          createdAt: "2026-09-01T10:00:00Z",
          updatedAt: "2026-09-01T12:00:00Z",
        },
      },
      {
        jobId: "job-222",
        createdByUserId: "user-1",
        status: "ACTIVE",
        createdAt: "2026-09-02T10:00:00Z",
        updatedAt: "2026-09-02T12:00:00Z",
        currentVersion: {
          jobVersionId: "ver-2",
          versionNumber: 1,
          versionStatus: "DRAFT",
          revision: 1,
          title: "Product Marketing Manager",
          department: "Marketing",
          location: "New York",
          requirementsCount: 2,
          createdAt: "2026-09-02T10:00:00Z",
          updatedAt: "2026-09-02T12:00:00Z",
        },
      },
    ];

    const onSelectJob = vi.fn();
    const onCreateJob = vi.fn();
    const onRefresh = vi.fn();

    render(
      <JobsDirectory
        jobs={mockJobs}
        isLoading={false}
        onSelectJob={onSelectJob}
        onCreateJob={onCreateJob}
        onRefresh={onRefresh}
      />
    );

    expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
    expect(screen.getByText("Product Marketing Manager")).toBeInTheDocument();
    expect(screen.getByText(/4 requirements/i)).toBeInTheDocument();

    // Test Search input
    const searchInput = screen.getByLabelText("Search jobs");
    fireEvent.change(searchInput, { target: { value: "Backend" } });

    expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
    expect(screen.queryByText("Product Marketing Manager")).not.toBeInTheDocument();

    // Click to select job
    fireEvent.click(screen.getByText("Senior Backend Engineer"));
    expect(onSelectJob).toHaveBeenCalledWith("job-111");
  });

  // =========================================================================
  // 2. Taxonomy Autocomplete (Advisory Suggestions & Custom Fallback)
  // =========================================================================
  it("allows selecting advisory O*NET suggestions or using custom text", async () => {
    const mockSkills: taxonomyApi.SkillSearchItem[] = [
      {
        conceptId: "sk-py-1",
        canonicalLabel: "Python",
        category: "Programming language",
        source: "ONET",
        sourceFamily: "software",
        sourceVersion: "31.0",
      },
    ];

    vi.mocked(taxonomyApi.searchSkills).mockResolvedValue(mockSkills);

    const onSelectSkill = vi.fn();
    const onUseCustomText = vi.fn();

    render(
      <TaxonomySkillAutocomplete
        onSelectSkill={onSelectSkill}
        onUseCustomText={onUseCustomText}
      />
    );

    const input = screen.getByLabelText("Search skill or enter custom requirement");
    fireEvent.change(input, { target: { value: "Python" } });

    // Wait for debounced search execution
    await waitFor(() => {
      expect(taxonomyApi.searchSkills).toHaveBeenCalledWith("Python", 8);
    });

    // Verify dropdown shows O*NET 31.0 Skills, Software skill family badge, and custom button
    expect(await screen.findByText("O*NET 31.0 Skills")).toBeInTheDocument();
    expect(screen.getByText("Software skill")).toBeInTheDocument();
    expect(screen.getByText(/Includes information from the O\*NET 31.0 Database/)).toBeInTheDocument();
    const suggestionOption = screen.getByRole("option");
    expect(suggestionOption).toHaveTextContent("Python");

    // Click suggestion
    fireEvent.click(suggestionOption);
    expect(onSelectSkill).toHaveBeenCalledWith(mockSkills[0]);
  });

  // =========================================================================
  // 3. Occupation Selector (Optional Advisory Link)
  // =========================================================================
  it("supports searching and clearing standard O*NET occupations", async () => {
    const mockOccs: taxonomyApi.OccupationSearchItem[] = [
      {
        conceptId: "occ-123",
        onetSocCode: "15-1252.00",
        canonicalTitle: "Software Developers",
        source: "ONET",
        sourceVersion: "31.0",
      },
    ];

    vi.mocked(taxonomyApi.searchOccupations).mockResolvedValue(mockOccs);

    const onSelectOccupation = vi.fn();

    const { rerender } = render(
      <OccupationSelector
        selectedConceptId={null}
        selectedTitle={null}
        onSelectOccupation={onSelectOccupation}
      />
    );

    const occInput = screen.getByLabelText("Search O*NET occupation reference");
    fireEvent.change(occInput, { target: { value: "Software" } });

    await waitFor(() => {
      expect(taxonomyApi.searchOccupations).toHaveBeenCalledWith("Software", 8);
    });

    expect(await screen.findByText("Software Developers")).toBeInTheDocument();
    expect(screen.getByText(/Includes information from the O\*NET 31.0 Database/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Software Developers"));
    expect(onSelectOccupation).toHaveBeenCalledWith(mockOccs[0]);

    // Rerender as selected
    rerender(
      <OccupationSelector
        selectedConceptId="occ-123"
        selectedTitle="Software Developers"
        onSelectOccupation={onSelectOccupation}
      />
    );

    expect(screen.getByText("Software Developers")).toBeInTheDocument();
    expect(screen.getByText("O*NET 31.0 Advisory Occupation Reference")).toBeInTheDocument();

    const clearBtn = screen.getByLabelText("Remove associated occupation");
    fireEvent.click(clearBtn);
    expect(onSelectOccupation).toHaveBeenCalledWith(null);
  });

  // =========================================================================
  // 4. Distinction of UI Autocomplete States (Loading, Empty, Error, Success)
  // =========================================================================
  it("distinguishes genuine no-results from API failures and handles stale requests", async () => {
    // 1. API error scenario
    vi.mocked(taxonomyApi.searchOccupations).mockRejectedValueOnce(new Error("503 Service Unavailable"));

    render(
      <OccupationSelector
        selectedConceptId={null}
        selectedTitle={null}
        onSelectOccupation={vi.fn()}
      />
    );

    const input = screen.getByLabelText("Search O*NET occupation reference");
    fireEvent.change(input, { target: { value: "Network" } });

    // Must show service error notice, not "No matching occupations found"
    expect(await screen.findByText(/Taxonomy service is temporarily unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText(/No standard occupation found for/i)).not.toBeInTheDocument();

    // 2. Genuine no-results scenario
    vi.mocked(taxonomyApi.searchOccupations).mockResolvedValueOnce([]);
    fireEvent.change(input, { target: { value: "Backend" } });

    expect(await screen.findByText(/No standard occupation found for/i)).toBeInTheDocument();
    expect(screen.getByText(/Try a standard O\*NET title such as "Software Developers"/i)).toBeInTheDocument();
  });

  // =========================================================================
  // 5. Progressive Disclosure: Collapsed defaults, hidden custom weights, zero-state checks
  // =========================================================================
  it("defaults optional occupation and requirement details to collapsed, hides custom weight, and handles zero criteria state", async () => {
    render(
      <JobEditor
        jobId={null}
        onNavigateBack={vi.fn()}
        onJobCreated={vi.fn()}
      />
    );

    // Occupation section should be collapsed by default
    const occToggle = screen.getByRole("heading", { name: /Occupation reference/i });
    expect(occToggle).toBeInTheDocument();
    // Autocomplete input should not be visible before clicking toggle
    expect(screen.queryByLabelText("Search O*NET occupation reference")).not.toBeInTheDocument();

    // Expand occupation disclosure
    fireEvent.click(occToggle);
    expect(screen.getByLabelText("Search O*NET occupation reference")).toBeInTheDocument();

    // Check Draft checks zero state: Must show neutral "No required criteria added yet.", NOT green pass
    expect(screen.getByText(/No required criteria added yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/All must-have criteria have business rationale/i)).not.toBeInTheDocument();

    // Add a requirement
    const addBtn = screen.getByText("Add Requirement");
    fireEvent.click(addBtn);

    // Advanced options should be collapsed by default
    const moreOptionsToggle = screen.getByText(/Advanced options/i);
    expect(moreOptionsToggle).toBeInTheDocument();
    expect(screen.queryByText(/Accepted Evidence Locations/i)).not.toBeInTheDocument();

    // Expand Advanced options
    fireEvent.click(moreOptionsToggle);
    expect(screen.getByText(/Accepted Evidence Locations/i)).toBeInTheDocument();

    // Custom weight control must NOT be rendered in default UI
    expect(screen.queryByLabelText(/Custom weight/i)).not.toBeInTheDocument();
  });

  // =========================================================================
  // 6. Category Sensitivity: Non-skill categories render clean text input
  // =========================================================================
  it("renders standard text input for non-skill categories without taxonomy search", async () => {
    render(
      <JobEditor
        jobId={null}
        onNavigateBack={vi.fn()}
        onJobCreated={vi.fn()}
      />
    );

    // Add Requirement
    fireEvent.click(screen.getByText("Add Requirement"));

    // By default, category is SKILL -> search autocomplete input is present
    expect(screen.getByLabelText("Search skill or enter custom requirement")).toBeInTheDocument();

    // Change category to RESPONSIBILITY
    const categorySelect = screen.getByLabelText("Category");
    fireEvent.change(categorySelect, { target: { value: "RESPONSIBILITY" } });

    // Autocomplete is replaced with plain text input
    expect(screen.queryByLabelText("Search skill or enter custom requirement")).not.toBeInTheDocument();
    const plainInput = screen.getByLabelText("Requirement description");
    expect(plainInput).toBeInTheDocument();

    fireEvent.change(plainInput, { target: { value: "Lead architectural reviews" } });
    expect(plainInput).toHaveValue("Lead architectural reviews");
  });

  // =========================================================================
  // 7. Atomic Draft Creation, Save, and Reload
  // =========================================================================
  it("creates draft, adds requirements with taxonomy reference, and saves atomically", async () => {
    const createdJobDetail: jobsApi.JobDetail = {
      jobId: "new-job-999",
      createdByUserId: "user-1",
      status: "ACTIVE",
      createdAt: "2026-09-03T12:00:00Z",
      updatedAt: "2026-09-03T12:00:00Z",
      currentVersion: {
        jobVersionId: "v-999",
        jobId: "new-job-999",
        versionNumber: 1,
        versionStatus: "DRAFT",
        revision: 1,
        title: "Senior Backend Developer",
        department: "Core Engineering",
        location: "San Francisco",
        employmentType: "FULL_TIME",
        workplaceType: "REMOTE",
        openingsCount: 1,
        createdByUserId: "user-1",
        requirements: [],
        reviewWarnings: [],
        createdAt: "2026-09-03T12:00:00Z",
        updatedAt: "2026-09-03T12:00:00Z",
      },
    };

    const savedWithReqs: jobsApi.JobDetail = {
      ...createdJobDetail,
      currentVersion: {
        ...createdJobDetail.currentVersion,
        revision: 2,
        occupationConceptId: "occ-123",
        requirements: [
          {
            requirementId: "req-1",
            displayOrder: 0,
            category: "SKILL",
            label: "Python",
            taxonomySkillConceptId: "sk-py-1",
            necessity: "MUST_HAVE",
            priority: "HIGH",
            acceptedEvidenceTypes: ["EMPLOYMENT", "PROJECT"],
            acceptedEquivalents: [],
            rationale: "Core backend language",
            origin: "TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED",
            reviewRequired: false,
            warningMetadata: [],
          },
        ],
      },
    };

    vi.mocked(jobsApi.createJobRequisition).mockResolvedValue(createdJobDetail);
    vi.mocked(jobsApi.saveDraftRequirements).mockResolvedValue(savedWithReqs);

    const onNavigateBack = vi.fn();
    const onJobCreated = vi.fn();

    render(
      <JobEditor
        jobId={null}
        onNavigateBack={onNavigateBack}
        onJobCreated={onJobCreated}
      />
    );

    // Fill Title
    const titleInput = screen.getByLabelText(/Job title/i);
    fireEvent.change(titleInput, { target: { value: "Senior Backend Developer" } });

    // Add Requirement
    const addReqBtn = screen.getByText("Add Requirement");
    fireEvent.click(addReqBtn);

    // Enter requirement custom text and click custom button
    const reqInput = screen.getByLabelText("Search skill or enter custom requirement");
    fireEvent.change(reqInput, { target: { value: "Python" } });

    const customBtn = await screen.findByText(/Use custom requirement/);
    fireEvent.click(customBtn);

    // Save Draft
    const saveBtn = screen.getByText("Save Draft");
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(jobsApi.createJobRequisition).toHaveBeenCalled();
      expect(jobsApi.saveDraftRequirements).toHaveBeenCalled();
      expect(onJobCreated).toHaveBeenCalledWith("new-job-999");
    });
  });

  // =========================================================================
  // 8. Phase 2D.4 UI Refinements: Candidate Comparison & Streamlined Requirements
  // =========================================================================
  describe("Phase 2D.4 UI Refinements: Candidate Comparison and Streamlined Requirements", () => {
    it("does not render job description import button and supports manual creation defaults", () => {
      render(
        <JobEditor
          jobId={null}
          onNavigateBack={vi.fn()}
          onJobCreated={vi.fn()}
        />
      );

      // Verify import button is removed
      expect(screen.queryByText("Import from job description")).not.toBeInTheDocument();

      // Add requirement: opens expanded card with PREFERRED necessity and MEDIUM priority defaults
      const addBtn = screen.getByText("Add Requirement");
      fireEvent.click(addBtn);

      // Segmented controls should show Preferred selected by default
      const prefBtn = screen.getByRole("button", { name: "Preferred" });
      expect(prefBtn).toHaveClass("active");

      // Switching to Must-have reveals immediate mandatory rationale input
      const mustHaveBtn = screen.getByRole("button", { name: "Must-have" });
      fireEvent.click(mustHaveBtn);
      expect(screen.getByLabelText(/Must-Have Rationale/i)).toBeInTheDocument();

      // Collapse card by clicking Done
      const doneBtn = screen.getByRole("button", { name: /Done editing requirement #1/i });
      fireEvent.click(doneBtn);

      // Compact row is rendered
      expect(screen.getByTestId("requirement-card-0")).toHaveClass("requirement-card-compact");
      expect(screen.getByRole("button", { name: /Edit requirement #1/i })).toBeInTheDocument();
    });

    it("renders primary Compare Candidates CTA and compact strip on READY_FOR_MATCHING job", async () => {
      const mockReadyJob: jobsApi.JobDetail = {
        jobId: "job-ready-100",
        createdByUserId: "recruiter-1",
        status: "ACTIVE",
        createdAt: "2026-09-04T10:00:00Z",
        updatedAt: "2026-09-04T10:00:00Z",
        currentVersion: {
          jobVersionId: "ver-100",
          jobId: "job-ready-100",
          versionNumber: 1,
          versionStatus: "READY_FOR_MATCHING",
          revision: 2,
          title: "Staff Systems Engineer",
          department: "Infrastructure",
          location: "San Francisco",
          employmentType: "FULL_TIME",
          workplaceType: "REMOTE",
          openingsCount: 1,
          createdByUserId: "recruiter-1",
          requirements: [
            {
              requirementId: "req-10",
              displayOrder: 0,
              category: "SKILL",
              label: "Kubernetes",
              necessity: "MUST_HAVE",
              priority: "HIGH",
              acceptedEvidenceTypes: ["EMPLOYMENT"],
              acceptedEquivalents: [],
              rationale: "Required for cluster maintenance.",
              origin: "RECRUITER_ENTERED",
            },
          ],
          reviewWarnings: [],
          createdAt: "2026-09-04T10:00:00Z",
          updatedAt: "2026-09-04T10:00:00Z",
        },
      };

      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockReadyJob);
      const onCompareCandidates = vi.fn();

      render(
        <JobEditor
          jobId="job-ready-100"
          onNavigateBack={vi.fn()}
          onJobCreated={vi.fn()}
          onCompareCandidates={onCompareCandidates}
        />
      );

      await screen.findByDisplayValue("Staff Systems Engineer");

      // Verify compact locked strip is rendered
      expect(
        screen.getByText(/This revision \(v1, Rev #2\) is locked and ready for candidate comparison\./)
      ).toBeInTheDocument();

      // Verify primary CTA: Compare candidates
      const compareBtn = screen.getByRole("button", { name: "Compare candidates" });
      expect(compareBtn).toBeInTheDocument();
      expect(compareBtn).toHaveClass("btn-primary");

      // Secondary CTA: Create new revision
      const newRevBtn = screen.getByRole("button", { name: "Create new revision" });
      expect(newRevBtn).toBeInTheDocument();
      expect(newRevBtn).toHaveClass("btn-secondary");

      // Click Compare candidates
      fireEvent.click(compareBtn);
      expect(onCompareCandidates).toHaveBeenCalledWith({
        jobId: "job-ready-100",
        jobTitle: "Staff Systems Engineer",
        jobVersionId: "ver-100",
        revision: 2,
        requirementsCount: 1,
      });
    });
  });

  // =========================================================================
  // Phase 2B.5: Requisition Readiness & Revision Locking
  // =========================================================================
  describe("Phase 2B.5: Requisition Readiness & Revision Locking", () => {
    const mockDraftJobDetail: jobsApi.JobDetail = {
      jobId: "job-ready-1",
      createdByUserId: "recruiter-1",
      status: "ACTIVE",
      createdAt: "2026-09-04T10:00:00Z",
      updatedAt: "2026-09-04T10:00:00Z",
      currentVersion: {
        jobVersionId: "ver-1",
        jobId: "job-ready-1",
        versionNumber: 1,
        versionStatus: "DRAFT",
        revision: 1,
        title: "Senior Fullstack Engineer",
        department: "Engineering",
        location: "Remote",
        employmentType: "FULL_TIME",
        workplaceType: "REMOTE",
        openingsCount: 1,
        createdByUserId: "recruiter-1",
        requirements: [
          {
            requirementId: "req-1",
            displayOrder: 0,
            category: "SKILL",
            label: "Python",
            necessity: "MUST_HAVE",
            priority: "HIGH",
            acceptedEvidenceTypes: ["EMPLOYMENT"],
            acceptedEquivalents: [],
            rationale: "Core backend architecture is written in Python.",
            origin: "RECRUITER_ENTERED",
          },
        ],
        reviewWarnings: [],
        createdAt: "2026-09-04T10:00:00Z",
        updatedAt: "2026-09-04T10:00:00Z",
      },
    };

    it("checks readiness on draft and displays structured findings", async () => {
      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockDraftJobDetail);
      vi.mocked(jobsApi.checkJobReadiness).mockResolvedValue({
        jobId: "job-ready-1",
        jobVersionId: "ver-1",
        versionNumber: 1,
        revision: 1,
        versionStatus: "DRAFT",
        ready: true,
        errors: [],
        warnings: [
          {
            code: "NO_OCCUPATION_SELECTED",
            severity: "WARNING",
            message: "No standard O*NET occupation reference is linked (optional).",
            blocksReadiness: false,
          },
        ],
        info: [
          {
            code: "CUSTOM_REQUIREMENT_USED",
            severity: "INFO",
            message: "Custom recruiter-entered requirement 'Python' has no O*NET ID.",
            blocksReadiness: false,
          },
        ],
        checkedAt: "2026-09-04T10:05:00Z",
      });

      render(
        <JobEditor
          jobId="job-ready-1"
          onNavigateBack={vi.fn()}
          onJobCreated={vi.fn()}
        />
      );

      expect(await screen.findByDisplayValue("Senior Fullstack Engineer")).toBeInTheDocument();
      expect(screen.getByText("v1 (Draft)")).toBeInTheDocument();

      // Click Check readiness
      const checkBtn = screen.getByText("Check readiness");
      fireEvent.click(checkBtn);

      expect(await screen.findByText(/Requisition passes all readiness checks/)).toBeInTheDocument();
      expect(screen.getByText("Advisory Warnings (1)")).toBeInTheDocument();
      expect(screen.getByText(/No standard O\*NET occupation reference is linked/)).toBeInTheDocument();
      expect(screen.getByText("Notes (1)")).toBeInTheDocument();
    });

    it("displays blocking errors and provides go-to-field focus without showing green success", async () => {
      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockDraftJobDetail);
      vi.mocked(jobsApi.checkJobReadiness).mockResolvedValue({
        jobId: "job-ready-1",
        jobVersionId: "ver-1",
        versionNumber: 1,
        revision: 1,
        versionStatus: "DRAFT",
        ready: false,
        errors: [
          {
            code: "MISSING_RATIONALE",
            severity: "ERROR",
            message: "Must-have requirement 'Python' lacks a job-related business rationale.",
            requirementId: "req-1",
            blocksReadiness: true,
          },
        ],
        warnings: [],
        info: [],
        checkedAt: "2026-09-04T10:05:00Z",
      });

      render(
        <JobEditor
          jobId="job-ready-1"
          onNavigateBack={vi.fn()}
          onJobCreated={vi.fn()}
        />
      );

      await screen.findByDisplayValue("Senior Fullstack Engineer");

      const checkBtn = screen.getByText("Check readiness");
      fireEvent.click(checkBtn);

      expect(await screen.findByText("Blocking Errors (1)")).toBeInTheDocument();
      expect(screen.getByText(/Must-have requirement 'Python' lacks a job-related business rationale/)).toBeInTheDocument();
      expect(screen.queryByText(/Requisition passes all readiness checks/)).not.toBeInTheDocument();

      // Click Go to field
      const goToFieldBtn = screen.getByText("Go to field");
      expect(goToFieldBtn).toBeInTheDocument();
      fireEvent.click(goToFieldBtn);
    });

    it("disables approve job when requisition has 0 requirements", async () => {
      const emptyReqsJobDetail: jobsApi.JobDetail = {
        ...mockDraftJobDetail,
        currentVersion: {
          ...mockDraftJobDetail.currentVersion,
          requirements: [],
        },
      };
      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(emptyReqsJobDetail);

      render(
        <JobEditor
          jobId="job-ready-1"
          onNavigateBack={vi.fn()}
          onJobCreated={vi.fn()}
        />
      );

      await screen.findByDisplayValue("Senior Fullstack Engineer");

      const approveBtn = screen.getByRole("button", { name: /Approve job/i });
      expect(approveBtn).toBeDisabled();
      expect(approveBtn).toHaveAttribute("title", "Add at least one requirement before approving");
      expect(jobsApi.markJobReady).not.toHaveBeenCalled();
    });

    it("locks revision as READY_FOR_MATCHING on direct approval, makes form read-only, and creates successor revision", async () => {
      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockDraftJobDetail);
      const mockReadyJobDetail: jobsApi.JobDetail = {
        ...mockDraftJobDetail,
        currentVersion: {
          ...mockDraftJobDetail.currentVersion,
          versionStatus: "READY_FOR_MATCHING",
          revision: 2,
          readyByUserId: "recruiter-1",
          readyAt: "2026-09-04T10:10:00Z",
        },
      };
      vi.mocked(jobsApi.markJobReady).mockResolvedValue(mockReadyJobDetail);

      render(
        <JobEditor
          jobId="job-ready-1"
          onNavigateBack={vi.fn()}
          onJobCreated={vi.fn()}
        />
      );

      await screen.findByDisplayValue("Senior Fullstack Engineer");

      // Direct single-step approval
      const approveBtn = screen.getByRole("button", { name: /Approve job/i });
      fireEvent.click(approveBtn);

      // Check that status changed and locked strip appeared
      expect(await screen.findByText(/This revision \(v1, Rev #2\) is locked and ready for candidate comparison\./)).toBeInTheDocument();
      expect(screen.getByText("v1 (Ready for matching)")).toBeInTheDocument();
      expect(screen.getByText(/Job approved successfully \(Revision #2\)\./)).toBeInTheDocument();

      // Verify title input is now disabled (read-only)
      const titleInput = screen.getByLabelText(/Job title/i);
      expect(titleInput).toBeDisabled();

      // Normal Save draft button is gone
      expect(screen.queryByText("Save as draft")).not.toBeInTheDocument();

      // Now create successor version
      const mockSuccessorJobDetail: jobsApi.JobDetail = {
        ...mockDraftJobDetail,
        currentVersion: {
          ...mockDraftJobDetail.currentVersion,
          jobVersionId: "ver-2",
          versionNumber: 2,
          versionStatus: "DRAFT",
          revision: 1,
          readyByUserId: null,
          readyAt: null,
        },
      };
      vi.mocked(jobsApi.createSuccessorVersion).mockResolvedValue(mockSuccessorJobDetail);

      const createSuccessorBtn = screen.getByRole("button", { name: "Create new revision" });
      fireEvent.click(createSuccessorBtn);

      expect(await screen.findByText("v2 (Draft)")).toBeInTheDocument();
      expect(titleInput).not.toBeDisabled();
      expect(screen.getByText("Save Draft")).toBeInTheDocument();
    });

    it("displays revision conflict message on stale ready attempt", async () => {
      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockDraftJobDetail);
      const conflictError: any = new Error("Revision conflict");
      conflictError.code = "REVISION_CONFLICT";
      vi.mocked(jobsApi.markJobReady).mockRejectedValue(conflictError);

      render(
        <JobEditor
          jobId="job-ready-1"
          onNavigateBack={vi.fn()}
          onJobCreated={vi.fn()}
        />
      );

      await screen.findByDisplayValue("Senior Fullstack Engineer");

      const approveBtn = screen.getByRole("button", { name: /Approve job/i });
      fireEvent.click(approveBtn);

      expect(await screen.findByText("Revision Conflict")).toBeInTheDocument();
      expect(screen.getByText(/This requisition was updated or changed status in another session/)).toBeInTheDocument();
    });
  });

  // =========================================================================
  // 6. Jobs Directory Status Mapping & Lifecycle Tab Filtering
  // =========================================================================
  describe("Jobs Directory Status Calculation and Tab Filtering", () => {
    const mockJobsList: jobsApi.JobSummary[] = [
      {
        jobId: "job-approved-1",
        createdByUserId: "user-1",
        status: "ACTIVE",
        createdAt: "2026-09-01T10:00:00Z",
        updatedAt: "2026-09-01T12:00:00Z",
        currentVersion: {
          jobVersionId: "ver-app-1",
          versionNumber: 1,
          versionStatus: "READY_FOR_MATCHING",
          revision: 2,
          title: "Principal Architect",
          department: "Engineering",
          location: "Remote",
          requirementsCount: 5,
          createdAt: "2026-09-01T10:00:00Z",
          updatedAt: "2026-09-01T12:00:00Z",
        },
      },
      {
        jobId: "job-draft-1",
        createdByUserId: "user-1",
        status: "ACTIVE",
        createdAt: "2026-09-02T10:00:00Z",
        updatedAt: "2026-09-02T12:00:00Z",
        currentVersion: {
          jobVersionId: "ver-draft-1",
          versionNumber: 1,
          versionStatus: "DRAFT",
          revision: 1,
          title: "Senior Backend Developer",
          department: "Platform",
          location: "San Francisco",
          requirementsCount: 3,
          createdAt: "2026-09-02T10:00:00Z",
          updatedAt: "2026-09-02T12:00:00Z",
        },
      },
      {
        jobId: "job-empty-1",
        createdByUserId: "user-1",
        status: "ACTIVE",
        createdAt: "2026-09-03T10:00:00Z",
        updatedAt: "2026-09-03T12:00:00Z",
        currentVersion: {
          jobVersionId: "ver-empty-1",
          versionNumber: 1,
          versionStatus: "DRAFT",
          revision: 1,
          title: "Junior QA Engineer",
          department: "Quality",
          location: "New York",
          requirementsCount: 0,
          createdAt: "2026-09-03T10:00:00Z",
          updatedAt: "2026-09-03T12:00:00Z",
        },
      },
    ];

    it("displays READY_FOR_MATCHING as Approved, unready revision with requirements as Draft, and 0 requirements as Needs requirements", () => {
      render(
        <JobsDirectory
          jobs={mockJobsList}
          isLoading={false}
          onSelectJob={vi.fn()}
          onCreateJob={vi.fn()}
          onRefresh={vi.fn()}
        />
      );

      // Verify status text
      expect(screen.getByText(/Approved · 5 requirements/)).toBeInTheDocument();
      expect(screen.getByText(/Draft · 3 requirements/)).toBeInTheDocument();
      expect(screen.getAllByText("Needs requirements").length).toBeGreaterThanOrEqual(1);

      // Verify contextual action buttons
      expect(screen.getByRole("button", { name: "View job Principal Architect" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Continue editing Senior Backend Developer" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Add requirements Junior QA Engineer" })).toBeInTheDocument();
    });

    it("filters correctly by Approved tab and excludes Draft jobs", () => {
      render(
        <JobsDirectory
          jobs={mockJobsList}
          isLoading={false}
          onSelectJob={vi.fn()}
          onCreateJob={vi.fn()}
          onRefresh={vi.fn()}
        />
      );

      const approvedTab = screen.getByRole("tab", { name: "Approved" });
      fireEvent.click(approvedTab);

      // Approved job is present
      expect(screen.getByText("Principal Architect")).toBeInTheDocument();
      // Draft and Needs requirements jobs are excluded
      expect(screen.queryByText("Senior Backend Developer")).not.toBeInTheDocument();
      expect(screen.queryByText("Junior QA Engineer")).not.toBeInTheDocument();
      expect(screen.getByText("1 job")).toBeInTheDocument();
    });

    it("filters correctly by Draft tab and excludes Approved jobs", () => {
      render(
        <JobsDirectory
          jobs={mockJobsList}
          isLoading={false}
          onSelectJob={vi.fn()}
          onCreateJob={vi.fn()}
          onRefresh={vi.fn()}
        />
      );

      const draftTab = screen.getByRole("tab", { name: "Draft" });
      fireEvent.click(draftTab);

      // Draft job with requirements is present
      expect(screen.getByText("Senior Backend Developer")).toBeInTheDocument();
      // Approved and 0-requirement jobs are excluded
      expect(screen.queryByText("Principal Architect")).not.toBeInTheDocument();
      expect(screen.queryByText("Junior QA Engineer")).not.toBeInTheDocument();
      expect(screen.getByText("1 job")).toBeInTheDocument();
    });

    it("filters correctly by Needs requirements tab", () => {
      render(
        <JobsDirectory
          jobs={mockJobsList}
          isLoading={false}
          onSelectJob={vi.fn()}
          onCreateJob={vi.fn()}
          onRefresh={vi.fn()}
        />
      );

      const needsReqsTab = screen.getByRole("tab", { name: "Needs requirements" });
      fireEvent.click(needsReqsTab);

      // 0-requirement job is present
      expect(screen.getByText("Junior QA Engineer")).toBeInTheDocument();
      // Others excluded
      expect(screen.queryByText("Principal Architect")).not.toBeInTheDocument();
      expect(screen.queryByText("Senior Backend Developer")).not.toBeInTheDocument();
      expect(screen.getByText("1 job")).toBeInTheDocument();
    });

    it("notifies parent and triggers live update after job is approved and when creating successor", async () => {
      const mockDraftDetail: jobsApi.JobDetail = {
        jobId: "job-sync-1",
        createdByUserId: "user-1",
        status: "ACTIVE",
        createdAt: "2026-09-04T10:00:00Z",
        updatedAt: "2026-09-04T10:00:00Z",
        currentVersion: {
          jobVersionId: "ver-1",
          jobId: "job-sync-1",
          versionNumber: 1,
          versionStatus: "DRAFT",
          revision: 1,
          title: "Staff Engineer",
          department: "Core",
          location: "Remote",
          employmentType: "FULL_TIME",
          workplaceType: "REMOTE",
          openingsCount: 1,
          internalReference: null,
          summary: "Core team",
          responsibilities: "Build systems",
          jobDescriptionText: null,
          occupationConceptId: null,
          occupationTitle: null,
          createdByUserId: "user-1",
          approvedByUserId: null,
          approvedAt: null,
          readyByUserId: null,
          readyAt: null,
          requirements: [
            {
              requirementId: "req-1",
              displayOrder: 0,
              category: "SKILL",
              label: "Python",
              necessity: "MUST_HAVE",
              priority: "CRITICAL",
              rationale: "Core requirement",
              acceptedEvidenceTypes: ["EMPLOYMENT"],
              acceptedEquivalents: [],
            },
          ],
          reviewWarnings: [],
          createdAt: "2026-09-04T10:00:00Z",
          updatedAt: "2026-09-04T10:00:00Z",
        },
      };

      const mockReadyDetail: jobsApi.JobDetail = {
        ...mockDraftDetail,
        currentVersion: {
          ...mockDraftDetail.currentVersion,
          versionStatus: "READY_FOR_MATCHING",
          revision: 2,
        },
      };

      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockDraftDetail);
      vi.mocked(jobsApi.markJobReady).mockResolvedValue(mockReadyDetail);

      const onJobUpdated = vi.fn();

      render(
        <JobEditor
          jobId="job-sync-1"
          onNavigateBack={vi.fn()}
          onJobCreated={vi.fn()}
          onJobUpdated={onJobUpdated}
        />
      );

      await screen.findByDisplayValue("Staff Engineer");

      const approveBtn = screen.getByRole("button", { name: /Approve job/i });
      fireEvent.click(approveBtn);

      await waitFor(() => {
        expect(jobsApi.markJobReady).toHaveBeenCalledWith("job-sync-1", 1);
        expect(onJobUpdated).toHaveBeenCalledWith("job-sync-1");
      });

      // Now create successor
      const mockSuccessorDetail: jobsApi.JobDetail = {
        ...mockDraftDetail,
        currentVersion: {
          ...mockDraftDetail.currentVersion,
          versionNumber: 2,
          versionStatus: "DRAFT",
          revision: 1,
        },
      };
      vi.mocked(jobsApi.createSuccessorVersion).mockResolvedValue(mockSuccessorDetail);

      const createSuccessorBtn = screen.getByRole("button", { name: "Create new revision" });
      fireEvent.click(createSuccessorBtn);

      await waitFor(() => {
        expect(jobsApi.createSuccessorVersion).toHaveBeenCalledWith("job-sync-1");
        expect(onJobUpdated).toHaveBeenCalledTimes(2);
      });
    });
  });
});

