import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { CandidateProfileReview, CandidateProfileData } from "../components/CandidateProfileReview";
import { ReviewDrawer } from "../components/ReviewDrawer";

const mockProfileWith4Warnings: CandidateProfileData = {
  artifactId: "art-1",
  profileId: "prof-1",
  profileVersion: 1,
  schemaVersion: "1.0",
  parserPipelineVersion: "pipeline-v1.3",
  provenance: "PARSER_EXTRACTED",
  reviewRequired: true,
  createdAt: "2026-08-30T12:00:00Z",
  warnings: [
    {
      code: "UNRESOLVED_SKILL_CANDIDATE",
      category: "SKILL_NORMALIZATION",
      fieldName: "skills",
      message: "Skill could not be matched to the current catalogue",
    },
    {
      code: "PROJECT_NAME_MISSING",
      category: "PROJECTS",
      fieldName: "name",
      message: "A project is missing its title",
    },
    {
      code: "EMPLOYMENT_ORGANIZATION_MISSING",
      category: "EMPLOYMENT",
      fieldName: "organization",
      message: "Organization missing from employment record",
    },
    {
      code: "LANGUAGE_PROFICIENCY_UNRESOLVED",
      category: "LANGUAGES",
      fieldName: "proficiency",
      message: "Language proficiency level could not be determined",
    },
  ],
  identity: {
    resumeArtifactId: "art-1",
    fullName: {
      fieldName: "fullName",
      value: "Aarav Mehta",
      normalizedValue: "Aarav Mehta",
      confidence: 0.95,
      confidenceBand: "HIGH",
      evidenceIds: [],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    emails: [],
    phoneNumbers: [],
    location: {
      fieldName: "location",
      value: "Bengaluru",
      normalizedValue: "Bengaluru",
      confidence: 0.9,
      confidenceBand: "HIGH",
      evidenceIds: [],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    linkedin: { fieldName: "linkedin", value: null, normalizedValue: null, confidence: null, confidenceBand: "UNKNOWN", evidenceIds: [], extractionMethod: null, reviewStatus: "UNREVIEWED" },
    github: { fieldName: "github", value: null, normalizedValue: null, confidence: null, confidenceBand: "UNKNOWN", evidenceIds: [], extractionMethod: null, reviewStatus: "UNREVIEWED" },
    portfolio: { fieldName: "portfolio", value: null, normalizedValue: null, confidence: null, confidenceBand: "UNKNOWN", evidenceIds: [], extractionMethod: null, reviewStatus: "UNREVIEWED" },
  },
  professionalProfile: {
    headline: {
      fieldName: "headline",
      value: "Senior Backend Engineer",
      normalizedValue: "Senior Backend Engineer",
      confidence: 0.9,
      confidenceBand: "HIGH",
      evidenceIds: [],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    summary: {
      fieldName: "summary",
      value: "Experienced engineer.",
      normalizedValue: "Experienced engineer.",
      confidence: 0.9,
      confidenceBand: "HIGH",
      evidenceIds: [],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    skills: [],
    employment: [],
    education: [],
    projects: [],
    certifications: [],
    languages: [],
    awards: [],
    publications: [],
  },
  evidence: [],
};

describe("Review Items Placement and Drawer", () => {
  it("does not render a large always-visible warning panel across the top", () => {
    render(
      <CandidateProfileReview
        profile={mockProfileWith4Warnings}
        onNavigateBack={vi.fn()}
      />
    );

    // Large inline warnings breakdown card should NOT be in the document
    expect(screen.queryByText(/^Review Items \(4\)$/i)).toBeNull();
  });

  it("renders a compact Review Items button with count 4 and accessible label", () => {
    render(
      <CandidateProfileReview
        profile={mockProfileWith4Warnings}
        onNavigateBack={vi.fn()}
      />
    );

    const btn = screen.getByRole("button", { name: /Open 4 profile review items/i });
    expect(btn).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("hides Review Items action when profile has 0 warnings", () => {
    const profileNoWarn: CandidateProfileData = {
      ...mockProfileWith4Warnings,
      warnings: [],
      reviewRequired: false,
    };

    render(
      <CandidateProfileReview
        profile={profileNoWarn}
        onNavigateBack={vi.fn()}
      />
    );

    expect(screen.queryByRole("button", { name: /profile review items/i })).toBeNull();
    expect(screen.getByText("Reviewed")).toBeInTheDocument();
  });

  it("opens ReviewDrawer on click and converts internal codes into readable messages", () => {
    render(
      <CandidateProfileReview
        profile={mockProfileWith4Warnings}
        onNavigateBack={vi.fn()}
      />
    );

    const btn = screen.getByRole("button", { name: /Open 4 profile review items/i });
    fireEvent.click(btn);

    expect(screen.getByText("Profile Review Items")).toBeInTheDocument();
    expect(screen.getByText("Skill could not be matched to the current catalogue")).toBeInTheDocument();
    expect(screen.getByText("A project is missing its title")).toBeInTheDocument();
    expect(screen.getByText("Organization missing from employment record")).toBeInTheDocument();
    expect(screen.getByText("Language proficiency level could not be determined")).toBeInTheDocument();
  });

  it("closes ReviewDrawer when Escape key is pressed", () => {
    const onClose = vi.fn();
    render(
      <ReviewDrawer
        isOpen={true}
        onClose={onClose}
        warnings={mockProfileWith4Warnings.warnings}
        onNavigateToSection={vi.fn()}
      />
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("groups review items into user-facing sections", () => {
    render(
      <ReviewDrawer
        isOpen={true}
        onClose={vi.fn()}
        warnings={mockProfileWith4Warnings.warnings}
        onNavigateToSection={vi.fn()}
      />
    );

    expect(screen.getByText(/Skills & Summary/i)).toBeInTheDocument();
    expect(screen.getByText(/Projects/i)).toBeInTheDocument();
    expect(screen.getByText(/Employment Experience/i)).toBeInTheDocument();
    expect(screen.getByText(/Languages/i)).toBeInTheDocument();
  });

  it("calls onNavigateToSection when clicking View in Profile", () => {
    const onNavigate = vi.fn();
    const onClose = vi.fn();
    render(
      <ReviewDrawer
        isOpen={true}
        onClose={onClose}
        warnings={mockProfileWith4Warnings.warnings}
        onNavigateToSection={onNavigate}
      />
    );

    const viewBtns = screen.getAllByText(/View in Profile/i);
    fireEvent.click(viewBtns[0]);

    expect(onNavigate).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});
