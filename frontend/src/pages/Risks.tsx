import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, ShieldAlert, ShieldCheck } from "lucide-react";
import { api, qs } from "../api/client";
import type { Risk } from "../api/types";
import { PageHeader } from "../components/Layout";
import { Card, EmptyState, ErrorAlert, Loading, SeverityBadge, SyntheticBadge } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime, humanize, timeAgo } from "../lib/format";
import { componentNameMap, useAction, useComponents } from "../lib/hooks";
import { useShell } from "../lib/shell";

const CATEGORY_HELP: Record<string, string> = {
  recurring_failure: "Repeated outage episodes",
  latency_regression: "Response time growing vs baseline",
  availability_below_target: "Availability under the configured target",
  repeated_restarts: "Container restarts or host reboots",
  stale_monitoring_data: "Monitors not producing fresh data",
  inconclusive_monitor: "Checks unable to reach a verdict",
  monitoring_coverage_gap: "Important components without monitoring",
  integration_unreachable: "Data source connection failures",
  tls_certificate_expiry: "Certificates nearing expiry",
};

function Evidence({ evidence }: { evidence: Record<string, unknown> }) {
  const simple = Object.entries(evidence).filter(([, v]) => typeof v !== "object" || v === null);
  const complex = Object.entries(evidence).filter(([, v]) => typeof v === "object" && v !== null);
  return (
    <div>
      {simple.length > 0 && (
        <dl className="detail-list small">
          {simple.map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{humanize(k)}</dt>
              <dd className="num">{String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {complex.length > 0 && (
        <details style={{ marginTop: 6 }}>
          <summary className="small muted" style={{ cursor: "pointer" }}>Raw evidence</summary>
          <pre className="json">{JSON.stringify(Object.fromEntries(complex), null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

export function RisksPage() {
  const { setOpen } = useShell();
  const { can } = useAuth();
  const [params] = useSearchParams();
  const highlight = Number(params.get("id")) || null;
  const [status, setStatus] = useState("open");
  const [category, setCategory] = useState("");
  const components = useComponents();
  const byComp = componentNameMap(components.data);
  const { data, isLoading, error } = useQuery({
    queryKey: ["risks", status, category],
    queryFn: () => api.get<Risk[]>(`/risks${qs({ status, category })}`),
    refetchInterval: 30000,
  });
  const keys = ["risks", "overview"];
  const analyze = useAction(() => api.post<Record<string, number>>("/risks/analyze"), keys);
  const dismiss = useAction((id: number) => api.post(`/risks/${id}/dismiss`, {}), keys);
  const reopen = useAction((id: number) => api.post(`/risks/${id}/reopen`), keys);
  const counts = (data ?? []).reduce<Record<string, number>>((acc, r) => ({ ...acc, [r.category]: (acc[r.category] ?? 0) + 1 }), {});

  return (
    <>
      <PageHeader
        title="Risk intelligence"
        subtitle="Evidence-based findings that tend to precede outages. Findings are risks, not confirmed incidents."
        setOpen={setOpen}
        actions={can("operator") && (
          <button className="btn" onClick={() => analyze.mutate(undefined)} disabled={analyze.isPending}>
            <RefreshCw size={14} className={analyze.isPending ? "spin" : ""} /> Analyze now
          </button>
        )}
      />
      <div className="content">
        {analyze.data && (
          <div className="alert ok small" role="status">
            Analysis complete: {analyze.data.created} new, {analyze.data.updated} still present, {analyze.data.resolved} resolved.
          </div>
        )}
        <ErrorAlert error={error ?? analyze.error ?? dismiss.error} />
        <div className="filters">
          <div className="segmented" role="group" aria-label="Finding status">
            {["open", "dismissed", "resolved", "all"].map((s) => (
              <button key={s} aria-pressed={status === s} onClick={() => setStatus(s)}>{humanize(s)}</button>
            ))}
          </div>
          <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
            <option value="">All categories</option>
            {Object.keys(CATEGORY_HELP).map((c) => <option key={c} value={c}>{humanize(c)}</option>)}
          </select>
        </div>
        {Object.keys(counts).length > 1 && (
          <div className="row">
            {Object.entries(counts).map(([c, n]) => (
              <button key={c} className="btn sm" onClick={() => setCategory(c)} title={CATEGORY_HELP[c]}>{humanize(c)} <span className="num muted">{n}</span></button>
            ))}
          </div>
        )}
        <Card flush>
          {isLoading ? <Loading /> : !data?.length ? (
            <EmptyState icon={status === "open" ? <ShieldCheck size={30} /> : <ShieldAlert size={30} />} title={status === "open" ? "No open risks" : "Nothing here"}>
              {status === "open"
                ? "Risk analysis runs every few minutes over recorded check results, agent reports and integration status. It needs some history before trend-based findings (recurring failures, latency regression) can appear."
                : "No findings match this filter."}
            </EmptyState>
          ) : (
            data.map((r) => (
              <article key={r.id} className="risk-item" style={highlight === r.id ? { background: "var(--accent-soft)" } : undefined}>
                <div className="row between">
                  <div className="row">
                    <SeverityBadge severity={r.severity} />
                    <strong>{r.title}</strong>
                    <SyntheticBadge show={r.is_synthetic} />
                  </div>
                  <div className="row">
                    {can("operator") && r.status === "open" && <button className="btn sm" onClick={() => dismiss.mutate(r.id)}>Dismiss</button>}
                    {can("operator") && r.status === "dismissed" && <button className="btn sm" onClick={() => reopen.mutate(r.id)}>Reopen</button>}
                  </div>
                </div>
                <div className="muted small row">
                  <span>{humanize(r.category)}</span>
                  <span>· first seen {dateTime(r.first_seen_at)}</span>
                  <span>· last confirmed {timeAgo(r.last_seen_at)}</span>
                  {r.status !== "open" && <span>· {r.status}{r.dismissed_by ? ` by ${r.dismissed_by}` : ""}</span>}
                  {r.component_id && <Link to={`/services?id=${r.component_id}`}>{byComp.get(r.component_id)?.name ?? "component"}</Link>}
                  {r.monitor_id && <Link to={`/monitoring/${r.monitor_id}`}>monitor history</Link>}
                </div>
                <p>{r.description}</p>
                <div className="rec small"><strong>Recommendation:</strong> {r.recommendation}</div>
                <Evidence evidence={r.evidence} />
              </article>
            ))
          )}
        </Card>
      </div>
    </>
  );
}
