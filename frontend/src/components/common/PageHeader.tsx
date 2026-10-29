import React, { ReactNode } from "react";
import { Icon, IconName } from "../Icon";

export interface PageHeaderProps {
  title: string;
  subtitle?: string | ReactNode;
  backAction?: {
    label: string;
    onClick: () => void;
  };
  actions?: ReactNode;
  headingLevel?: "h1" | "h2";
  className?: string;
}

export function PageHeader({
  title,
  subtitle,
  backAction,
  actions,
  headingLevel = "h1",
  className = "",
}: PageHeaderProps) {
  const HeadingTag = headingLevel;

  return (
    <header className={`page-header ${className}`}>
      {backAction && (
        <button
          type="button"
          className="btn-back-link"
          onClick={backAction.onClick}
          aria-label={backAction.label}
        >
          <Icon name="arrow-left" size={15} />
          <span>{backAction.label}</span>
        </button>
      )}

      <div className="page-header-row">
        <div className="page-header-title-group">
          <HeadingTag className="page-header-title">{title}</HeadingTag>
          {subtitle && (
            <div className="page-header-subtitle">
              {typeof subtitle === "string" ? <p>{subtitle}</p> : subtitle}
            </div>
          )}
        </div>

        {actions && <div className="page-header-actions">{actions}</div>}
      </div>
    </header>
  );
}
