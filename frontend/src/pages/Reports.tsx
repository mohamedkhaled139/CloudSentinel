import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BarChart3, Download, FileText } from "lucide-react";
import { api, qs } from "../api/client";
import type { ReportData } from "../api/types";
import { PageHeader } from "../components/Layout";
import { Card, EmptyState, ErrorAlert, Field, IncidentStatusBadge, Kpi, Loading, SeverityBadge, SyntheticBadge } from "../components/ui";
import { dateTime, duration, ms, pct } from "../lib/format";
import { useShell } from "../lib/shell";

const PRESETS = [
  { d: 1, l: "24 hours" },
  { d: 7, l: "7 days" },
  { d: 30, l: "30 days" },
  { d: 90, l: "90 days" },
];

function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function ReportsPage() {
  const { setOpen } = useShell();
  const [preset, setPreset] = useState<number | null>(7);
  const [custom, setCustom] = useState(() => ({
    start: toLocalInput(new Date(Date.now() - 7 * 864e5)),
    end: toLocalInput(new Date()),
  }));
  const range = useMemo(() => {
    if (preset) {
      const end = new Date();
      // Round to the minute so the query key is stable between renders.
      end.setSeconds(0, 0);
      return { start: new Date(end.getTime() - preset * 864e5).toISOString(), end: end.toISOString() };
    }
    return { start: new Date(custom.start).toISOString(), end: new Date(custom.end).toISOString() };
  }, [preset, custom]);
  const query = qs(range);
  const { data, isLoading, error } = useQuery({
    queryKey: ["report", range],
    queryFn: () => api.get<ReportData>(`/reports/summary${query}`),
  });

  return (
    <>
      <PageHeader title="Reports" subtitle="Availability, incident frequency and duration, and recurring problems" setOpen={setOpen}
        actions={
          <div className="row">
            <a className="btn" href={`/api/v1/reports/export.pdf${query}`} download><FileText size={14} /> PDF</a>
            <a className="btn" href={`/api/v1/reports/export.csv${qs({ ...range, section: "components" })}`} download><Download size={14} /> Services CSV</a>
            <a className="btn hide-sm" href={`/api/v1/reports/export.csv${qs({ ...range, section: "monitors" })}`} download><Download size={14} /> Monitors CSV</a>
            <a className="btn hide-sm" href={`/api/v1/reports/export.csv${qs({ ...range, section: "incidents" })}`} download><Download size={14} /> Incidents CSV</a>
          </div>
        } />
      <div className="content">
        <div className="filters">
          <div className="segmented" role="group" aria-label="Report period">
            {PRESETS.map((p) => <button key={p.d} aria-pressed={preset === p.d} onClick={() => setPreset(p.d)}>{p.l}</button>)}
            <button aria-pressed={preset === null} onClick={() => setPreset(null)}>Custom</button>
          </div>
          {preset === null && (
            <>
              <Field label="From"><input type="datetime-local" value={custom.start} onChange={(e) => setCustom({ ...custom, start: e.target.value })} /></Field>
              <Field label="To"><input type="datetime-local" value={custom.end} onChange={(e) => setCustom({ ...custom, end: e.target.value })} /></Field>
            </>
          )}
        </div>
        <ErrorAlert error={error} />
        {isLoading || !data ? <Loading /> : (
          <>
            {data.contains_synthetic_data && <div className="alert warn small"><SyntheticBadge /> This period includes synthetic demo data.</div>}
            <div className="grid cols-4">
              <Kpi label="Overall availability" value={pct(data.overall_availability_pct, 3)} sub={`${data.total_checks.toLocaleString()} checks`} />
              <Kpi label="Incidents" value={data.incident_count}
                sub={["critical", "major", "minor", "info"].filter((s) => data.incidents_by_severity[s]).map((s) => `${data.incidents_by_severity[s]} ${s}`).join(" · ") || "None"} />
              <Kpi label="Mean time to resolve" value={duration(data.mttr_minutes)} sub={`${data.resolved_incident_count} resolved`} />
              <Kpi label="Total incident time" value={duration(data.total_incident_minutes)} sub={`${data.open_risks} open risk findings`} />
            </div>
            <div className="grid cols-2">
              <Card title="Service availability" flush>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Service</th><th>Criticality</th><th className="right">Availability</th><th className="right">Incidents</th><th className="right">Downtime</th></tr></thead>
                    <tbody>
                      {[...data.components].sort((a, b) => (a.availability_pct ?? 101) - (b.availability_pct ?? 101)).map((c) => (
                        <tr key={c.component_id}>
                          <td><Link to={`/services?id=${c.component_id}`}>{c.component}</Link></td>
                          <td className="muted">{c.criticality}</td>
                          <td className="right num">{pct(c.availability_pct, 3)}</td>
                          <td className="right num">{c.incidents}</td>
                          <td className="right num">{duration(c.downtime_minutes)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
              <Card title="Top recurring problems" flush>
                {data.top_problems.length === 0 ? <EmptyState icon={<BarChart3 size={28} />} title="No recurring problems in this period" /> : (
                  <table>
                    <thead><tr><th>Monitor</th><th>Component</th><th className="right">Incidents</th><th className="right">Failed checks</th></tr></thead>
                    <tbody>
                      {data.top_problems.map((p) => (
                        <tr key={p.monitor_id}>
                          <td><Link to={`/monitoring/${p.monitor_id}`}>{p.monitor}</Link></td>
                          <td className="muted">{p.component ?? "—"}</td>
                          <td className="right num">{p.incidents}</td>
                          <td className="right num">{p.failed_checks}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Card>
            </div>
            <Card title={`Incidents (${data.incidents.length})`} flush>
              {data.incidents.length === 0 ? <EmptyState title="No incidents in this period" /> : (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Severity</th><th>Incident</th><th>Status</th><th>Started</th><th className="right">Duration in period</th></tr></thead>
                    <tbody>
                      {data.incidents.slice().reverse().map((i) => (
                        <tr key={i.id}>
                          <td><SeverityBadge severity={i.severity} /></td>
                          <td><Link to={`/incidents/${i.id}`}>#{i.id} {i.title}</Link> {i.correlated_to && <span className="muted small">(↳ #{i.correlated_to})</span>} <SyntheticBadge show={i.is_synthetic} /></td>
                          <td><IncidentStatusBadge status={i.status} /></td>
                          <td className="nowrap muted">{dateTime(i.started_at)}</td>
                          <td className="right num">{duration(i.duration_minutes)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
            <Card title="Monitor availability" flush>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Monitor</th><th>Type</th><th className="right">Checks</th><th className="right">Failed</th><th className="right">Availability</th><th className="right">Avg latency</th></tr></thead>
                  <tbody>
                    {data.monitors.map((m) => (
                      <tr key={m.monitor_id}>
                        <td><Link to={`/monitoring/${m.monitor_id}`}>{m.monitor_name}</Link></td>
                        <td><code>{m.kind}</code></td>
                        <td className="right num">{m.checks}</td>
                        <td className="right num">{m.failed}</td>
                        <td className="right num">{pct(m.availability_pct, 3)}</td>
                        <td className="right num">{ms(m.avg_latency_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
            {data.notes.map((n) => <p key={n} className="muted small" style={{ margin: 0 }}>{n}</p>)}
          </>
        )}
      </div>
    </>
  );
}
