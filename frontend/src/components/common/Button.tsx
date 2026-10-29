import React, { ButtonHTMLAttributes, ReactNode } from "react";
import { Icon, IconName } from "../Icon";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "destructive";
export type ButtonSize = "xs" | "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: IconName;
  iconPosition?: "left" | "right";
  isLoading?: boolean;
  loadingText?: string;
  children?: ReactNode;
}

export function Button({
  variant = "secondary",
  size = "md",
  icon,
  iconPosition = "left",
  isLoading = false,
  loadingText,
  disabled,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  const variantClass =
    variant === "primary"
      ? "btn-primary"
      : variant === "quiet"
      ? "btn-quiet"
      : variant === "destructive"
      ? "btn-destructive"
      : "btn-secondary";

  const sizeClass =
    size === "xs"
      ? "btn-xs"
      : size === "sm"
      ? "btn-sm"
      : size === "lg"
      ? "btn-lg"
      : "btn-md";

  const isDisabled = disabled || isLoading;

  return (
    <button
      type="button"
      className={`btn ${variantClass} ${sizeClass} ${className}`}
      disabled={isDisabled}
      aria-busy={isLoading ? "true" : undefined}
      {...rest}
    >
      {isLoading ? (
        <>
          <span className="loading-spinner" aria-hidden="true" style={{ width: "14px", height: "14px" }} />
          <span>{loadingText || children || "Loading..."}</span>
        </>
      ) : (
        <>
          {icon && iconPosition === "left" && <Icon name={icon} size={size === "xs" ? 12 : size === "sm" ? 14 : 16} />}
          {children}
          {icon && iconPosition === "right" && <Icon name={icon} size={size === "xs" ? 12 : size === "sm" ? 14 : 16} />}
        </>
      )}
    </button>
  );
}
