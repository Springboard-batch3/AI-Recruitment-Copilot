import { describe, it, expect } from "vitest";
import fs from "fs";
import path from "path";

// Color-policy audit test: Prohibits application-owned red colors, rose/crimson/maroon,
// and red-family tokens across the frontend codebase.
describe("Color Policy Audit: Zero-Red UI Enforcement", () => {
  const FRONTEND_SRC_DIR = path.resolve(__dirname, "..");

  // Prohibited specific hex values discovered during inspection and general red hexes
  const PROHIBITED_HEX_LIST = [
    "#dc2626",
    "#fee2e2",
    "#fef2f2",
    "#fecaca",
    "#fca5a5",
    "#991b1b",
    "#b91c1c",
    "#ef4444",
    "#e11d48",
    "#be123c",
    "#881337",
    "#fffbfa",
  ];

  // Regex patterns for red-family keywords, framework classes, and red/rose shades
  const PROHIBITED_WORD_REGEX =
    /\b(rose|crimson|maroon|red-[1-9]00|text-red|bg-red|border-red|btn-danger)\b/i;

  // Scan all application-owned source and style files
  function getAllSourceFiles(dir: string): string[] {
    const results: string[] = [];
    const entries = fs.readdirSync(dir, { withFileTypes: true });

    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      // Exclude build artifacts, test files, and node_modules
      if (
        entry.name === "node_modules" ||
        entry.name === "dist" ||
        entry.name === ".git" ||
        entry.name === "__tests__"
      ) {
        continue;
      }

      if (entry.isDirectory()) {
        results.push(...getAllSourceFiles(fullPath));
      } else if (
        entry.name.endsWith(".tsx") ||
        entry.name.endsWith(".ts") ||
        entry.name.endsWith(".css") ||
        entry.name.endsWith(".html")
      ) {
        results.push(fullPath);
      }
    }
    return results;
  }

  const filesToAudit = getAllSourceFiles(FRONTEND_SRC_DIR);

  it("contains no prohibited red hex values across all application source files", () => {
    const violations: string[] = [];

    for (const filePath of filesToAudit) {
      const content = fs.readFileSync(filePath, "utf-8");
      const relativePath = path.relative(FRONTEND_SRC_DIR, filePath);
      const lines = content.split("\n");

      lines.forEach((line: string, index: number) => {
        const lineLower = line.toLowerCase();
        for (const hex of PROHIBITED_HEX_LIST) {
          if (lineLower.includes(hex)) {
            violations.push(`${relativePath}:${index + 1} contains prohibited hex '${hex}': ${line.trim()}`);
          }
        }
      });
    }

    expect(violations).toEqual([]);
  });

  it("contains no prohibited red/rose/crimson/maroon words or framework classes", () => {
    const violations: string[] = [];

    for (const filePath of filesToAudit) {
      const content = fs.readFileSync(filePath, "utf-8");
      const relativePath = path.relative(FRONTEND_SRC_DIR, filePath);
      const lines = content.split("\n");

      lines.forEach((line: string, index: number) => {
        if (PROHIBITED_WORD_REGEX.test(line)) {
          violations.push(`${relativePath}:${index + 1} matches prohibited pattern: ${line.trim()}`);
        }
      });
    }

    expect(violations).toEqual([]);
  });

  it("ensures centralized semantic status tokens are accessible non-red in index.css", () => {
    const cssPath = path.join(FRONTEND_SRC_DIR, "index.css");
    const cssContent = fs.readFileSync(cssPath, "utf-8");

    // Check status-danger is accessible non-red aubergine
    expect(cssContent).toContain("--status-danger: #63445b;");
    expect(cssContent).toContain("--status-danger-bg: #eadfe7;");
    expect(cssContent).toContain("--status-danger-border: #c9b5c3;");

    // Check gap tokens exist
    expect(cssContent).toContain("--gap-covered: #2f6b4f;");
    expect(cssContent).toContain("--gap-partial: #806022;");
    expect(cssContent).toContain("--gap-not-evidenced: #5b6863;");
  });
});
