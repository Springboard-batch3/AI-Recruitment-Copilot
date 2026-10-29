import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { CandidateProfileReview, CandidateProfileData } from "../components/CandidateProfileReview";

const mockProfile: CandidateProfileData = {
  artifactId: "91710c77-2ec6-4ebd-bee3-495b29ebabbd",
  profileId: "810813c1-7391-4da7-8543-eeb7880b993d",
  profileVersion: 2,
  schemaVersion: "1.0",
  parserPipelineVersion: "pipeline-v1.3[section-detect-v1.2+identity-extract-v1.1+skill-summary-extract-v1.3]",
  provenance: "HUMAN_CORRECTED",
  parentProfileId: "11111111-2222-3333-4444-555555555555",
  parentProfileVersion: 1,
  createdAt: "2026-08-30T12:00:00Z",
  reviewRequired: false,
  warnings: [
    {
      code: "EMPLOYMENT_ORGANIZATION_MISSING",
      category: "EXTRACTION_WARNING",
      message: "Organization was not detected in one employment record.",
      fieldName: "organization",
    },
  ],
  identity: {
    resumeArtifactId: "91710c77-2ec6-4ebd-bee3-495b29ebabbd",
    fullName: {
      fieldName: "fullName",
      value: "Nisha Rao",
      normalizedValue: "Nisha Rao",
      confidence: 0.95,
      confidenceBand: "HIGH",
      evidenceIds: ["ev-1"],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    emails: [
      {
        fieldName: "email",
        value: "nisha.rao@example.com",
        normalizedValue: "nisha.rao@example.com",
        confidence: 0.98,
        confidenceBand: "HIGH",
        evidenceIds: ["ev-2"],
        extractionMethod: "RULE_BASED",
        reviewStatus: "CONFIRMED",
      },
    ],
    phoneNumbers: [
      {
        fieldName: "phone",
        value: "+1 (206) 555-0199",
        normalizedValue: "+12065550199",
        confidence: 0.9,
        confidenceBand: "HIGH",
        evidenceIds: ["ev-3"],
        extractionMethod: "RULE_BASED",
        reviewStatus: "CONFIRMED",
      },
    ],
    location: {
      fieldName: "location",
      value: "Seattle, WA",
      normalizedValue: "Seattle, WA",
      confidence: 0.85,
      confidenceBand: "HIGH",
      evidenceIds: ["ev-4"],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    linkedin: {
      fieldName: "linkedin",
      value: "https://linkedin.com/in/nisharao",
      normalizedValue: "https://linkedin.com/in/nisharao",
      confidence: 0.95,
      confidenceBand: "HIGH",
      evidenceIds: [],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    github: {
      fieldName: "github",
      value: null,
      normalizedValue: null,
      confidence: null,
      confidenceBand: "LOW",
      evidenceIds: [],
      extractionMethod: null,
      reviewStatus: "UNCONFIRMED",
    },
    portfolio: {
      fieldName: "portfolio",
      value: null,
      normalizedValue: null,
      confidence: null,
      confidenceBand: "LOW",
      evidenceIds: [],
      extractionMethod: null,
      reviewStatus: "UNCONFIRMED",
    },
  },
  professionalProfile: {
    headline: {
      fieldName: "headline",
      value: "Senior Frontend Engineer",
      normalizedValue: "Senior Frontend Engineer",
      confidence: 0.9,
      confidenceBand: "HIGH",
      evidenceIds: ["ev-5"],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    summary: {
      fieldName: "summary",
      value: "Experienced software engineer passionate about modern React architecture and accessible UX.",
      normalizedValue: "Experienced software engineer passionate about modern React architecture and accessible UX.",
      confidence: 0.88,
      confidenceBand: "HIGH",
      evidenceIds: ["ev-6"],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    skills: [
      {
        fieldName: "skill",
        value: "React",
        normalizedValue: "React",
        confidence: 0.99,
        confidenceBand: "HIGH",
        evidenceIds: ["ev-7"],
        extractionMethod: "CANONICAL_MATCH",
        reviewStatus: "CONFIRMED",
      },
      {
        fieldName: "skill",
        value: "TypeScript",
        normalizedValue: "TypeScript",
        confidence: 0.99,
        confidenceBand: "HIGH",
        evidenceIds: ["ev-8"],
        extractionMethod: "CANONICAL_MATCH",
        reviewStatus: "CONFIRMED",
      },
    ],
    employment: [
      {
        jobTitle: {
          fieldName: "jobTitle",
          value: "Staff UI Engineer",
          normalizedValue: "Staff UI Engineer",
          confidence: 0.92,
          confidenceBand: "HIGH",
          evidenceIds: ["ev-9"],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        organization: {
          fieldName: "organization",
          value: "CloudTech Systems",
          normalizedValue: "CloudTech Systems",
          confidence: 0.9,
          confidenceBand: "HIGH",
          evidenceIds: ["ev-10"],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        startDate: {
          fieldName: "startDate",
          value: "Jan 2021",
          normalizedValue: "2021-01-01",
          confidence: 0.95,
          confidenceBand: "HIGH",
          evidenceIds: ["ev-11"],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        endDate: {
          fieldName: "endDate",
          value: "Present",
          normalizedValue: null,
          confidence: 0.95,
          confidenceBand: "HIGH",
          evidenceIds: [],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        descriptions: [
          {
            fieldName: "description",
            value: "Architected enterprise design system used by 50+ engineers.",
            normalizedValue: "Architected enterprise design system used by 50+ engineers.",
            confidence: 0.85,
            confidenceBand: "HIGH",
            evidenceIds: ["ev-12"],
            extractionMethod: "RULE_BASED",
            reviewStatus: "CONFIRMED",
          },
        ],
      },
    ],
    education: [
      {
        degree: {
          fieldName: "degree",
          value: "Bachelor of Science",
          normalizedValue: "BS",
          confidence: 0.95,
          confidenceBand: "HIGH",
          evidenceIds: ["ev-13"],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        fieldOfStudy: {
          fieldName: "fieldOfStudy",
          value: "Computer Science",
          normalizedValue: "Computer Science",
          confidence: 0.9,
          confidenceBand: "HIGH",
          evidenceIds: ["ev-14"],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        institution: {
          fieldName: "institution",
          value: "University of Washington",
          normalizedValue: "University of Washington",
          confidence: 0.95,
          confidenceBand: "HIGH",
          evidenceIds: ["ev-15"],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        startDate: {
          fieldName: "startDate",
          value: "2015",
          normalizedValue: "2015-09-01",
          confidence: 0.9,
          confidenceBand: "HIGH",
          evidenceIds: [],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        endDate: {
          fieldName: "endDate",
          value: "2019",
          normalizedValue: "2019-06-01",
          confidence: 0.9,
          confidenceBand: "HIGH",
          evidenceIds: [],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        grade: {
          fieldName: "grade",
          value: "3.85 GPA",
          normalizedValue: "3.85",
          confidence: 0.85,
          confidenceBand: "HIGH",
          evidenceIds: [],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
      },
    ],
    projects: [
      {
        name: {
          fieldName: "name",
          value: "A11y Inspector",
          normalizedValue: "A11y Inspector",
          confidence: 0.9,
          confidenceBand: "HIGH",
          evidenceIds: [],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        description: {
          fieldName: "description",
          value: "Open-source browser extension for automated WCAG 2.2 auditing.",
          normalizedValue: "Open-source browser extension for automated WCAG 2.2 auditing.",
          confidence: 0.85,
          confidenceBand: "HIGH",
          evidenceIds: [],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        technologies: [
          {
            fieldName: "technology",
            value: "React",
            normalizedValue: "React",
            confidence: 0.9,
            confidenceBand: "HIGH",
            evidenceIds: [],
            extractionMethod: "RULE_BASED",
            reviewStatus: "CONFIRMED",
          },
        ],
        projectUrl: {
          fieldName: "projectUrl",
          value: "https://github.com/example/a11y-inspector",
          normalizedValue: "https://github.com/example/a11y-inspector",
          confidence: 0.9,
          confidenceBand: "HIGH",
          evidenceIds: [],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
      },
    ],
    certifications: [
      {
        name: {
          fieldName: "name",
          value: "AWS Certified Solutions Architect",
          normalizedValue: "AWS Certified Solutions Architect",
          confidence: 0.95,
          confidenceBand: "HIGH",
          evidenceIds: [],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        issuer: {
          fieldName: "issuer",
          value: "Amazon Web Services",
          normalizedValue: "Amazon Web Services",
          confidence: 0.9,
          confidenceBand: "HIGH",
          evidenceIds: [],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        issuedDate: {
          fieldName: "issuedDate",
          value: "2023",
          normalizedValue: "2023-01-01",
          confidence: 0.85,
          confidenceBand: "HIGH",
          evidenceIds: [],
          extractionMethod: "RULE_BASED",
          reviewStatus: "CONFIRMED",
        },
        credentialUrl: {
          fieldName: "credentialUrl",
          value: null,
          normalizedValue: null,
          confidence: null,
          confidenceBand: "LOW",
          evidenceIds: [],
          extractionMethod: null,
          reviewStatus: "UNCONFIRMED",
        },
      },
    ],
    languages: [
      {
        fieldName: "language",
        value: "English",
        normalizedValue: "NATIVE_OR_BILINGUAL",
        confidence: 0.95,
        confidenceBand: "HIGH",
        evidenceIds: [],
        extractionMethod: "RULE_BASED",
        reviewStatus: "CONFIRMED",
      },
      {
        fieldName: "language",
        value: "Spanish",
        normalizedValue: "PROFESSIONAL_WORKING",
        confidence: 0.85,
        confidenceBand: "HIGH",
        evidenceIds: [],
        extractionMethod: "RULE_BASED",
        reviewStatus: "CONFIRMED",
      },
    ],
    awards: [],
    publications: [],
  },
  evidence: [
    {
      evidenceId: "ev-1",
      resumeArtifactId: "91710c77-2ec6-4ebd-bee3-495b29ebabbd",
      pageNumber: 1,
      blockNumber: 1,
      characterStart: 0,
      characterEnd: 9,
      originalText: "Nisha Rao",
      extractionMethod: "RULE_BASED",
    },
  ],
};

describe("CandidateProfileReview Component", () => {
  it("does NOT display raw developer pipeline or schema strings in the recruiter UI", () => {
    render(
      <CandidateProfileReview
        profile={mockProfile}
        onNavigateBack={vi.fn()}
      />
    );

    // Verify developer strings are absent
    expect(screen.queryByText(/pipeline-v1\.3/i)).toBeNull();
    expect(screen.queryByText(/section-detect/i)).toBeNull();
    expect(screen.queryByText(/Derived from/i)).toBeNull();
    expect(screen.queryByText(/Schema: v1\.0/i)).toBeNull();
  });

  it("renders candidate header with avatar initials, name, headline, and provenance tag", () => {
    render(
      <CandidateProfileReview
        profile={mockProfile}
        onNavigateBack={vi.fn()}
      />
    );

    expect(screen.getByText("Nisha Rao")).toBeInTheDocument();
    expect(screen.getByText("NR")).toBeInTheDocument();
    expect(screen.getByText("Senior Frontend Engineer")).toBeInTheDocument();
    expect(screen.getByText("Seattle, WA")).toBeInTheDocument();
    expect(screen.getByText("Human Corrected")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
  });

  it("omits missing optional contact links gracefully without displaying 'Not Detected'", () => {
    render(
      <CandidateProfileReview
        profile={mockProfile}
        onNavigateBack={vi.fn()}
      />
    );

    // Present contact items
    expect(screen.getByText("nisha.rao@example.com")).toBeInTheDocument();
    expect(screen.getByText("+1 (206) 555-0199")).toBeInTheDocument();
    expect(screen.getByText("LinkedIn")).toBeInTheDocument();

    // Absent fields should not have "Not Detected" placeholder text
    expect(screen.queryByText(/^Not Detected$/i)).toBeNull();
    expect(screen.queryByText(/^Not Provided$/i)).toBeNull();
  });

  it("displays Candidate Overview snapshot metrics", () => {
    render(
      <CandidateProfileReview
        profile={mockProfile}
        onNavigateBack={vi.fn()}
      />
    );

    expect(screen.getByText("Candidate Snapshot")).toBeInTheDocument();
    expect(screen.getByText("Documented Positions")).toBeInTheDocument();
    expect(screen.getByText("Education Records")).toBeInTheDocument();
    expect(screen.getByText("Skills Composition")).toBeInTheDocument();
  });

  it("renders employment timeline with organization and normalized dates", () => {
    render(
      <CandidateProfileReview
        profile={mockProfile}
        onNavigateBack={vi.fn()}
      />
    );

    expect(screen.getByText("Staff UI Engineer")).toBeInTheDocument();
    expect(screen.getByText("CloudTech Systems")).toBeInTheDocument();
    expect(screen.getByText("Jan 2021 — Present")).toBeInTheDocument();
    expect(screen.getByText("Architected enterprise design system used by 50+ engineers.")).toBeInTheDocument();
  });

  it("converts raw language proficiency enums into human-readable labels", () => {
    render(
      <CandidateProfileReview
        profile={mockProfile}
        onNavigateBack={vi.fn()}
      />
    );

    expect(screen.getByText("Native or bilingual proficiency")).toBeInTheDocument();
    expect(screen.getByText("Professional working proficiency")).toBeInTheDocument();
    expect(screen.queryByText("PROFESSIONAL_WORKING")).toBeNull();
    expect(screen.queryByText("NATIVE_OR_BILINGUAL")).toBeNull();
  });

  it("renders human-readable review items in drawer", () => {
    render(
      <CandidateProfileReview
        profile={mockProfile}
        onNavigateBack={vi.fn()}
      />
    );

    const reviewBtn = screen.getByRole("button", { name: /profile review items/i });
    expect(reviewBtn).toBeInTheDocument();
    fireEvent.click(reviewBtn);

    expect(screen.getByText("Profile Review Items")).toBeInTheDocument();
    expect(screen.getByText("Organization missing from employment record")).toBeInTheDocument();
    expect(screen.getByText(/Organization was not detected in one employment record/i)).toBeInTheDocument();
  });
});
