import React, { ReactNode } from "react";
import { Icon, IconName } from "../Icon";

export type NoticeVariant = "info" | "success" | "warning" | "error";

export interface InlineNoticeProps {
  variant?: NoticeVariant;
  title?: string;
  children: ReactNode;
  icon?: IconName;
  actions?: ReactNode;
  className?: string;
}

export function InlineNotice({
  variant = "info",
  title,
  children,
  icon,
  actions,
  className = "",
}: InlineNoticeProps) {
  const isAlert = variant === "error" || variant === "warning";
  const defaultIcon: IconName =
    variant === "error" || variant === "warning"
      ? "alert-triangle"
      : variant === "success"
      ? "check-circle"
      : "info";

  const effectiveIcon = icon || defaultIcon;

  return (
    <div
      className={`inline-notice inline-notice-${variant} ${className}`}
      role={isAlert ? "alert" : "status"}
      aria-live={isAlert ? "assertive" : "polite"}
    >
      <div className="inline-notice-icon-wrapper" aria-hidden="true">
        <Icon name={effectiveIcon} size={18} />
      </div>

      <div className="inline-notice-content">
        {title && <h4 className="inline-notice-title">{title}</h4>}
        <div className="inline-notice-message">{children}</div>
        {actions && <div className="inline-notice-actions">{actions}</div>}
      </div>
    </div>
  );
}
