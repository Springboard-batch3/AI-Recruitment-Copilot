import React from "react";
import { Icon, IconName } from "../Icon";

export type BadgeVariant =
  | "indigo"
  | "teal"
  | "amber"
  | "neutral"
  | "draft"
  | "approved"
  | "stale"
  | "validated"
  | "reviewed";

export interface StatusBadgeProps {
  status: string;
  variant?: BadgeVariant;
  icon?: IconName;
  className?: string;
}

/**
 * Humanizes raw enum status keys into readable sentence-case / title-case strings.
 */
export function humanizeStatus(raw: string): string {
  if (!raw) return "";
  const mappings: Record<string, string> = {
    APPROVED: "Approved",
    VALIDATED: "Ready for Review",
    DRAFT: "Draft",
    STALE: "Stale",
    KEPT: "Reviewed",
    EDITED: "Edited",
    REVISED: "Regenerated",
    RECRUITER_AUTHORED: "Written by Recruiter",
    UNREVIEWED: "To Review",
    PENDING: "Pending",
    PASSED: "Passed",
    FAILED: "Failed",
    CONNECTED: "Connected",
    UNAVAILABLE: "Unavailable",
  };

  if (mappings[raw.toUpperCase()]) {
    return mappings[raw.toUpperCase()];
  }

  return raw
    .replace(/[_-]+/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function StatusBadge({
  status,
  variant,
  icon,
  className = "",
}: StatusBadgeProps) {
  const displayLabel = humanizeStatus(status);

  // Compute sensible default variant if not explicitly supplied
  const effectiveVariant: BadgeVariant =
    variant ||
    (status.toUpperCase() === "APPROVED" || status.toUpperCase() === "PASSED" || status.toUpperCase() === "KEPT"
      ? "approved"
      : status.toUpperCase() === "VALIDATED" || status.toUpperCase() === "EDITED"
      ? "validated"
      : status.toUpperCase() === "STALE" || status.toUpperCase() === "FAILED"
      ? "stale"
      : "neutral");

  const badgeClass =
    effectiveVariant === "approved" || effectiveVariant === "teal"
      ? "badge-status-approved"
      : effectiveVariant === "validated" || effectiveVariant === "indigo"
      ? "badge-status-validated"
      : effectiveVariant === "stale" || effectiveVariant === "amber"
      ? "badge-status-stale"
      : "badge-status-neutral";

  return (
    <span className={`badge ${badgeClass} ${className}`}>
      {icon && <Icon name={icon} size={12} className="badge-icon" />}
      <span>{displayLabel}</span>
    </span>
  );
}
