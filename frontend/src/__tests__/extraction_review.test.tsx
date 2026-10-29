import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ExtractionReview, ExtractionResultData } from "../components/ExtractionReview";

const mockExtraction: ExtractionResultData = {
  artifactId: "art-1",
  extractionId: "ext-1",
  extractorVersion: "extractor-v1.3",
  reviewRequired: true,
  blockCount: 2,
  blocks: [
    {
      blockId: "b-1",
      sequenceIndex: 1,
      pageNumber: 1,
      structuralLocation: "body_paragraph_0",
      blockType: "PARAGRAPH",
      extractionMethod: "PDF_DIRECT",
      rawText: "Nisha Rao — Senior UI Engineer",
      normalizedText: "Nisha Rao - Senior UI Engineer",
      confidence: 0.95,
      reviewRequired: false,
    },
    {
      blockId: "b-2",
      sequenceIndex: 2,
      pageNumber: 1,
      structuralLocation: "body_paragraph_1",
      blockType: "HEADING",
      extractionMethod: "PDF_DIRECT",
      rawText: "EXPERIENCE",
      normalizedText: "EXPERIENCE",
      confidence: 0.9,
      reviewRequired: true,
    },
  ],
};

describe("ExtractionReview Component", () => {
  it("renders raw extraction blocks and filters by search text", () => {
    render(
      <ExtractionReview
        extraction={mockExtraction}
        onNavigateBack={vi.fn()}
      />
    );

    expect(screen.getByText("Raw Extraction & Normalization")).toBeInTheDocument();
    expect(screen.getByText("2 Total Blocks")).toBeInTheDocument();
    expect(screen.getByText("Nisha Rao — Senior UI Engineer")).toBeInTheDocument();

    const searchInput = screen.getByPlaceholderText(/Search block contents/i);
    fireEvent.change(searchInput, { target: { value: "EXPERIENCE" } });

    expect(screen.queryByText("Nisha Rao — Senior UI Engineer")).toBeNull();
    expect(screen.getAllByText("EXPERIENCE").length).toBeGreaterThanOrEqual(1);
  });

  it("filters by block type dropdown", () => {
    render(
      <ExtractionReview
        extraction={mockExtraction}
        onNavigateBack={vi.fn()}
      />
    );

    const typeSelect = screen.getByLabelText(/Filter by block type/i);
    fireEvent.change(typeSelect, { target: { value: "HEADING" } });

    expect(screen.queryByText("Nisha Rao — Senior UI Engineer")).toBeNull();
    expect(screen.getAllByText("EXPERIENCE").length).toBeGreaterThanOrEqual(1);
  });
});
