/**
 * Navigation Security and Internal Route Validation Utilities
 * Phase 2E.1: Robust Internal Route Fallback and Validation
 */

/**
 * Validates that a given destination URL is a safe, internal application route.
 * Rejects absolute external URLs, protocol-relative URLs, javascript URLs, data URLs,
 * and malicious encoded destinations.
 *
 * @param url The candidate destination string
 * @returns The sanitized internal route string, or null if invalid/malicious
 */
export function validateInternalReturnUrl(url: string | null | undefined): string | null {
  if (!url || typeof url !== "string") {
    return null;
  }

  const trimmed = url.trim();
  if (!trimmed) {
    return null;
  }

  // Reject dangerous schemes (javascript:, data:, vbscript:, file:, blob:)
  const lower = trimmed.toLowerCase();
  if (
    lower.startsWith("javascript:") ||
    lower.startsWith("data:") ||
    lower.startsWith("vbscript:") ||
    lower.startsWith("file:") ||
    lower.startsWith("blob:") ||
    lower.startsWith("about:")
  ) {
    return null;
  }

  // Reject any string containing explicit protocol pattern (e.g., http://, https://, ftp://)
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed)) {
    return null;
  }

  // Reject protocol-relative URLs (//) and backslash variations (/\, \/, \\)
  if (
    trimmed.startsWith("//") ||
    trimmed.startsWith("/\\") ||
    trimmed.startsWith("\\\\") ||
    trimmed.startsWith("\\/")
  ) {
    return null;
  }

  // Reject encoded protocol-relative or backslash injection (%2f%2f, %5c)
  if (
    trimmed.includes("%2f%2f") ||
    trimmed.includes("%2F%2F") ||
    trimmed.includes("%5c") ||
    trimmed.includes("%5C") ||
    trimmed.includes("%252f") ||
    trimmed.includes("%252F")
  ) {
    return null;
  }

  // Must start with an internal route indicator: '/' or '?' or '#'
  if (!trimmed.startsWith("/") && !trimmed.startsWith("?") && !trimmed.startsWith("#")) {
    return null;
  }

  // Parse check with a fixed internal origin
  try {
    const dummyOrigin = "http://localhost";
    const parsed = new URL(trimmed, dummyOrigin);
    // Ensure origin was not spoofed
    if (parsed.origin !== dummyOrigin) {
      return null;
    }
  } catch {
    return null;
  }

  return trimmed;
}

/**
 * Validates a return URL specifically for candidate portal authentication redirects.
 * Only permits safe internal candidate routes: /candidate/jobs, /candidate/applications,
 * /candidate/interviews, /candidate/profile, or /candidate (optionally with safe query params).
 */
export function validateCandidateReturnUrl(url: string | null | undefined): string | null {
  const sanitized = validateInternalReturnUrl(url);
  if (!sanitized) {
    return null;
  }

  // Extract the pathname from the sanitized relative URL
  try {
    const dummyOrigin = "http://localhost";
    const parsed = new URL(sanitized, dummyOrigin);
    const pathname = parsed.pathname.toLowerCase();

    // Check if it's a valid candidate route (not login/register to avoid infinite loops)
    const allowedPrefixes = [
      "/candidate/jobs",
      "/candidate/applications",
      "/candidate/interviews",
      "/candidate/profile",
      "/candidate/activate",
    ];

    if (pathname === "/candidate" || allowedPrefixes.some((prefix) => pathname === prefix || pathname.startsWith(prefix + "/"))) {
      return sanitized;
    }

    return null;
  } catch {
    return null;
  }
}

/**
 * Validates a return URL specifically for processed matching results or gap report context.
 * Confirms internal URL safety and returns the valid route or null.
 */
export function validateProcessedResultsReturnUrl(
  url: string | null | undefined,
  _jobId?: string,
  _runId?: string
): string | null {
  return validateInternalReturnUrl(url);
}

export interface ComparisonNavigationState {
  jobId: string;
  jobTitle?: string;
  jobVersionId: string;
  revision: number;
  requirementsCount?: number;
  runId?: string;
  searchQuery?: string;
  page?: number;
  sortField?: "rank" | "score" | "name" | "evidence";
  sortDirection?: "asc" | "desc";
  selectedProfileIds?: string[];
  filterConfidence?: string;
}
