import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { EvidenceDrawer, EvidenceSpan } from "../components/EvidenceDrawer";

const mockEvidenceMap = new Map<string, EvidenceSpan>([
  [
    "ev-1",
    {
      evidenceId: "ev-1",
      resumeArtifactId: "art-1",
      pageNumber: 1,
      blockNumber: 3,
      characterStart: 10,
      characterEnd: 35,
      originalText: "Staff UI Engineer at CloudTech",
      extractionMethod: "RULE_BASED",
    },
  ],
  [
    "ev-2",
    {
      evidenceId: "ev-2",
      resumeArtifactId: "art-1",
      pageNumber: 1,
      blockNumber: 4,
      characterStart: 40,
      characterEnd: 65,
      originalText: "Jan 2021 — Present",
      extractionMethod: "RULE_BASED",
    },
  ],
]);

describe("EvidenceDrawer Component", () => {
  it("renders evidence drawer when open and displays snippet and char offsets", () => {
    const onClose = vi.fn();
    render(
      <EvidenceDrawer
        isOpen={true}
        onClose={onClose}
        fieldLabel="Position Title"
        fieldValue="Staff UI Engineer"
        evidenceIds={["ev-1", "ev-2"]}
        evidenceMap={mockEvidenceMap}
      />
    );

    expect(screen.getByText("Evidence Inspector")).toBeInTheDocument();
    expect(screen.getByText("Position Title")).toBeInTheDocument();
    expect(screen.getByText("Staff UI Engineer")).toBeInTheDocument();
    expect(screen.getByText("Staff UI Engineer at CloudTech")).toBeInTheDocument();
    expect(screen.getByText("[10 : 35]")).toBeInTheDocument();
    expect(screen.getByText("Page 1")).toBeInTheDocument();
    expect(screen.getByText("Block #3")).toBeInTheDocument();
  });

  it("navigates through multiple evidence spans", () => {
    render(
      <EvidenceDrawer
        isOpen={true}
        onClose={vi.fn()}
        fieldLabel="Position Title"
        fieldValue="Staff UI Engineer"
        evidenceIds={["ev-1", "ev-2"]}
        evidenceMap={mockEvidenceMap}
      />
    );

    expect(screen.getByText("Span 1 of 2")).toBeInTheDocument();
    const nextBtn = screen.getByText(/Next Span/i);
    fireEvent.click(nextBtn);

    expect(screen.getByText("Span 2 of 2")).toBeInTheDocument();
    expect(screen.getByText("Jan 2021 — Present")).toBeInTheDocument();
  });

  it("toggles technical audit details on demand", () => {
    render(
      <EvidenceDrawer
        isOpen={true}
        onClose={vi.fn()}
        fieldLabel="Position Title"
        fieldValue="Staff UI Engineer"
        evidenceIds={["ev-1"]}
        evidenceMap={mockEvidenceMap}
      />
    );

    expect(screen.queryByText("Evidence ID:")).toBeNull();
    const toggleBtn = screen.getByText(/Technical Audit Details/i);
    fireEvent.click(toggleBtn);

    expect(screen.getByText("Evidence ID:")).toBeInTheDocument();
    expect(screen.getByText("ev-1")).toBeInTheDocument();
    expect(screen.getByText("RULE_BASED")).toBeInTheDocument();
  });

  it("dismisses on Escape key press", () => {
    const onClose = vi.fn();
    render(
      <EvidenceDrawer
        isOpen={true}
        onClose={onClose}
        fieldLabel="Position Title"
        fieldValue="Staff UI Engineer"
        evidenceIds={["ev-1"]}
        evidenceMap={mockEvidenceMap}
      />
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
