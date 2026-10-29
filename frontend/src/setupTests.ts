import "@testing-library/jest-dom";
import { vi } from "vitest";

// Mock fetch for jsdom test environment
globalThis.fetch = vi.fn().mockImplementation(() =>
  Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve([]),
  })
);
