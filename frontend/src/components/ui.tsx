import { useEffect, useRef, type ReactNode } from "react";
import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  CircleSlash,
  FlaskConical,
  HelpCircle,
  Loader2,
  X,
  Zap,
} from "lucide-react";
import type { HealthStatus, IncidentStatus, Severity } from "../api/types";
import { humanize } from "../lib/format";

const STATUS_ICON: Record<string, ReactNode> = {
  up: <CheckCircle2 size={14} aria-hidden />,
  degraded: <AlertTriangle size={14} aria-hidden />,
  impacted: <Zap size={14} aria-hidden />,
  down: <AlertOctagon size={14} aria-hidden />,
  unknown: <HelpCircle size={14} aria-hidden />,
  unmonitored: <CircleDashed size={14} aria-hidden />,
  disabled: <CircleSlash size={14} aria-hidden />,
};

const STATUS_LABEL: Record<string, string> = {
  up: "Operational",
  degraded: "Degraded",
  impacted: "Impacted",
  down: "Down",
  unknown: "Unknown",
  unmonitored: "Unmonitored",
  disabled: "Disabled",
};

/** Status is always conveyed with icon + text, never color alone. */
export function StatusBadge({ status, label }: { status: HealthStatus | "disabled"; label?: string }) {
  return (
    <span className={`status ${status}`} data-testid="status">
      {STATUS_ICON[status] ?? STATUS_ICON.unknown}
      {label ?? STATUS_LABEL[status] ?? humanize(status)}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: Severity | string }) {
  return <span className={`badge sev-${severity}`}>{humanize(severity)}</span>;
}

export function IncidentStatusBadge({ status }: { status: IncidentStatus }) {
  return <span className={`badge st-${status}`}>{humanize(status)}</span>;
}

export function SyntheticBadge({ show = true }: { show?: boolean }) {
  if (!show) return null;
  return (
    <span className="badge synthetic" title="Synthetic demo data — not real monitoring data">
      <FlaskConical size={11} aria-hidden /> Synthetic
    </span>
  );
}

export function Card({
  title,
  actions,
  children,
  flush,
  className,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
}) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <div className="card-head">
          {typeof title === "string" ? <h2>{title}</h2> : <div style={{ flex: 1 }}>{title}</div>}
          {actions}
        </div>
      )}
      <div className={`card-body ${flush ? "flush" : ""}`}>{children}</div>
    </section>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      {icon}
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="empty" role="status">
      <Loader2 size={20} className="spin" aria-hidden />
      <p>{label}</p>
    </div>
  );
}

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="alert error" role="alert">
      {msg}
    </div>
  );
}

export function Modal({
  title,
  onClose,
  children,
  footer,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    ref.current?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="btn ghost icon-btn" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Kpi({
  label,
  value,
  sub,
  icon,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="card kpi">
      <div className="label">
        {icon}
        {label}
      </div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}
