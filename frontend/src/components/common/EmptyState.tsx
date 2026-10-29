import React, { ReactNode } from "react";
import { Icon, IconName } from "../Icon";

export interface EmptyStateProps {
  icon?: IconName;
  title: string;
  description: string | ReactNode;
  primaryAction?: {
    label: string;
    onClick: () => void;
    icon?: IconName;
  };
  secondaryAction?: {
    label: string;
    onClick: () => void;
  };
  className?: string;
}

export function EmptyState({
  icon = "file-text",
  title,
  description,
  primaryAction,
  secondaryAction,
  className = "",
}: EmptyStateProps) {
  return (
    <div className={`empty-state-card ${className}`} role="region" aria-label={title}>
      <div className="empty-icon-circle" aria-hidden="true">
        <Icon name={icon} size={32} />
      </div>

      <h3 className="empty-title">{title}</h3>

      <div className="empty-description">
        {typeof description === "string" ? <p>{description}</p> : description}
      </div>

      {(primaryAction || secondaryAction) && (
        <div className="empty-state-actions" style={{ display: "flex", gap: "10px", marginTop: "16px" }}>
          {primaryAction && (
            <button
              type="button"
              className="btn btn-primary btn-md"
              onClick={primaryAction.onClick}
            >
              {primaryAction.icon && <Icon name={primaryAction.icon} size={15} />}
              <span>{primaryAction.label}</span>
            </button>
          )}

          {secondaryAction && (
            <button
              type="button"
              className="btn btn-secondary btn-md"
              onClick={secondaryAction.onClick}
            >
              <span>{secondaryAction.label}</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}
