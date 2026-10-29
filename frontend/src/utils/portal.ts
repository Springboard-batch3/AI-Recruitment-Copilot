/**
 * Portal Detection and Cross-Workspace Navigation Utilities
 * 
 * Supports isolated simultaneous Admin and Recruiter sessions by partitioning
 * host-only session cookies between admin.localhost and recruiter.localhost.
 * 
 * The portal context is used strictly for presentation and navigation;
 * backend authorization remains authoritative.
 */

export type PortalContext = "admin" | "recruiter" | "candidate" | "default";

/**
 * Determines the current portal context from the given hostname or window.location.hostname.
 */
export function getPortalContext(hostname?: string): PortalContext {
  let host = hostname;
  if (!host && typeof window !== "undefined" && window.location?.hostname) {
    host = window.location.hostname;
  }
  if (!host) {
    return "default";
  }

  const normHost = host.toLowerCase().trim();
  if (normHost === "admin.localhost" || normHost.startsWith("admin.")) {
    return "admin";
  }
  if (normHost === "recruiter.localhost" || normHost.startsWith("recruiter.")) {
    return "recruiter";
  }
  if (normHost === "candidate.localhost" || normHost.startsWith("candidate.")) {
    return "candidate";
  }
  return "default";
}

/**
 * Returns the origin URL for the target portal preserving protocol and port.
 */
export function getPortalOrigin(
  target: "admin" | "recruiter" | "candidate",
  win?: { location: { protocol?: string; port?: string; hostname?: string } }
): string {
  const activeWin = win || (typeof window !== "undefined" ? window : undefined);
  const protocol = activeWin?.location?.protocol || "http:";
  const port = activeWin?.location?.port ? `:${activeWin.location.port}` : "";
  const currentHost = (activeWin?.location?.hostname || "localhost").toLowerCase();

  // If local development (*.localhost or localhost or 127.0.0.1)
  if (currentHost.includes("localhost") || currentHost === "127.0.0.1") {
    return `${protocol}//${target}.localhost${port}`;
  }

  // If domain has custom production prefix e.g., portal.example.com -> target.example.com
  const parts = currentHost.split(".");
  if (parts.length > 2) {
    parts[0] = target;
    return `${protocol}//${parts.join(".")}${port}`;
  }

  return `${protocol}//${target}.${currentHost}${port}`;
}

/**
 * Constructs a safe cross-portal URL to open a specific route in the target workspace.
 */
export function getCrossPortalUrl(
  target: "admin" | "recruiter" | "candidate",
  pathAndQuery: string = "",
  win?: { location: { protocol?: string; port?: string; hostname?: string } }
): string {
  const origin = getPortalOrigin(target, win);
  const cleanPath = pathAndQuery.trim();
  if (!cleanPath) {
    return origin;
  }
  if (cleanPath.startsWith("/")) {
    return `${origin}${cleanPath}`;
  }
  if (cleanPath.startsWith("?")) {
    return `${origin}/${cleanPath}`;
  }
  return `${origin}/${cleanPath}`;
}

/**
 * Returns the quiet workspace label for display in headers and shells.
 */
export function getPortalLabel(portal: PortalContext): string {
  switch (portal) {
    case "admin":
      return "Admin workspace";
    case "recruiter":
      return "Recruiter workspace";
    case "candidate":
      return "Candidate portal";
    default:
      return "Smart Hiring Platform";
  }
}

/**
 * Returns the contextual login instruction subtitle based on portal context.
 */
export function getLoginPortalSubtitle(portal: PortalContext): string {
  switch (portal) {
    case "admin":
      return "Sign in to Admin workspace";
    case "recruiter":
      return "Sign in to Recruiter workspace";
    case "candidate":
      return "Sign in to your Candidate portal";
    default:
      return "Sign in to continue to the recruitment workspace.";
  }
}
