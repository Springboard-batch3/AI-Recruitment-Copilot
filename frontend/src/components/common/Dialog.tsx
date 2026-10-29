import React, { useEffect, useRef, ReactNode } from "react";
import { Icon } from "../Icon";

export interface DialogProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  maxWidth?: string;
  triggerRef?: React.RefObject<HTMLElement | null>;
  className?: string;
}

export function Dialog({
  isOpen,
  onClose,
  title,
  children,
  footer,
  maxWidth = "600px",
  triggerRef,
  className = "",
}: DialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  const initialTriggerElement = useRef<HTMLElement | null>(null);

  // Capture the activeElement when dialog opens to restore focus when it closes
  useEffect(() => {
    if (isOpen) {
      initialTriggerElement.current = (triggerRef?.current || document.activeElement) as HTMLElement;
    }
  }, [isOpen, triggerRef]);

  // Focus trap and Escape key listener
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }

      if (e.key === "Tab" && dialogRef.current) {
        const focusableElements = dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );
        if (focusableElements.length === 0) return;

        const firstElement = focusableElements[0];
        const lastElement = focusableElements[focusableElements.length - 1];

        if (e.shiftKey) {
          if (document.activeElement === firstElement) {
            e.preventDefault();
            lastElement.focus();
          }
        } else {
          if (document.activeElement === lastElement) {
            e.preventDefault();
            firstElement.focus();
          }
        }
      }
    };

    document.addEventListener("keydown", handleKeyDown);

    // Initial focus into close button or first focusable control
    const timer = setTimeout(() => {
      if (closeBtnRef.current) {
        closeBtnRef.current.focus();
      }
    }, 20);

    // Prevent background scrolling
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      clearTimeout(timer);
      document.body.style.overflow = originalOverflow;

      // Restore focus to trigger element
      const elToFocus = triggerRef?.current || initialTriggerElement.current;
      if (elToFocus && typeof elToFocus.focus === "function") {
        setTimeout(() => elToFocus.focus(), 10);
      }
    };
  }, [isOpen, onClose, triggerRef]);

  if (!isOpen) return null;

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  const titleId = `dialog-title-${title.replace(/\s+/g, "-").toLowerCase()}`;

  return (
    <div
      className="dialog-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onClick={handleBackdropClick}
    >
      <div
        className={`dialog-container ${className}`}
        ref={dialogRef}
        style={{ maxWidth }}
      >
        <header className="dialog-header">
          <h2 id={titleId} className="dialog-title">
            {title}
          </h2>
          <button
            ref={closeBtnRef}
            type="button"
            className="dialog-close-btn"
            onClick={onClose}
            aria-label="Close dialog"
          >
            <Icon name="x" size={18} />
          </button>
        </header>

        <div className="dialog-body">{children}</div>

        {footer && <footer className="dialog-footer">{footer}</footer>}
      </div>
    </div>
  );
}
