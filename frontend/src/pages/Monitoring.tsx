import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Activity, Pause, Pencil, Play, Plus, RefreshCw, Trash2 } from "lucide-react";
import { api, qs } from "../api/client";
import type { CheckResult, Monitor, MonitorStats, Page } from "../api/types";
import { AvailabilityChart, LatencyChart, StatusStrip } from "../components/charts";
import { PageHeader } from "../components/Layout";
import { cleanValues, defaultsFor, SchemaForm } from "../components/SchemaForm";
import { Card, EmptyState, ErrorAlert, Field, Kpi, Loading, Modal, StatusBadge, SyntheticBadge } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime, ms, pct, timeAgo } from "../lib/format";
import { componentNameMap, useAction, useComponents, useIntegrations, useMonitors, useProviders } from "../lib/hooks";
import { useShell } from "../lib/shell";

interface Draft {
  name: string;
  kind: string;
  component_id: number | null;
  integration_id: number | null;
  config: Record<string, unknown>;
  interval_seconds: number;
  timeout_seconds: number;
  failure_threshold: number;
  recovery_threshold: number;
  degraded_latency_ms: number | null;
  open_incident_on_degraded: boolean;
  enabled: boolean;
}

export function MonitorModal({ initial, onClose, presetComponent }: { initial?: Monitor; onClose: () => void; presetComponent?: number }) {
  const providers = useProviders();
  const integrations = useIntegrations();
  const components = useComponents();
  const [d, setD] = useState<Draft>(() =>
    initial
      ? { ...initial }
      : {
          name: "", kind: "http", component_id: presetComponent ?? null, integration_id: null, config: {},
          interval_seconds: 60, timeout_seconds: 10, failure_threshold: 3, recovery_threshold: 2,
          degraded_latency_ms: null, open_incident_on_degraded: false, enabled: true,
        },
  );
  const [test, setTest] = useState<{ status: string; message: string; latency_ms: number | null } | null>(null);
  const provider = providers.data?.providers.find((p) => p.key === d.kind);
  const usable = (integrations.data ?? []).filter((i) => i.kind === provider?.integration_kind);

  useEffect(() => {
    if (!initial && provider) setD((cur) => ({ ...cur, config: { ...defaultsFor(provider.config_schema), ...cur.config } }));
  }, [provider, initial]);

  const payload = () => ({ ...d, config: cleanValues(d.config) });
  const save = useAction(
    () => (initial ? api.patch<Monitor>(`/monitors/${initial.id}`, payload()) : api.post<Monitor>("/monitors", payload())),
    ["monitors", "overview", "components"],
    onClose,
  );
  const runTest = useAction(() => api.post<typeof test>("/monitors/test", payload()), [], (r) => setTest(r));
  const num = (v: string) => (v === "" ? null : Number(v));

  return (
    <Modal
      wide
      title={initial ? `Edit monitor: ${initial.name}` : "New monitor"}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={() => runTest.mutate(undefined)} disabled={runTest.isPending}>
            <Play size={14} /> {runTest.isPending ? "Testing…" : "Test now"}
          </button>
          <span style={{ flex: 1 }} />
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!d.name.trim() || save.isPending} onClick={() => save.mutate(undefined)}>
            {initial ? "Save" : "Create monitor"}
          </button>
        </>
      }
    >
      <div className="form-grid">
        <Field label="Name *"><input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="Checkout API health" /></Field>
        <Field label="Type" hint={provider?.description}>
          <select value={d.kind} disabled={!!initial} onChange={(e) => setD({ ...d, kind: e.target.value, config: {}, integration_id: null })}>
            {(providers.data?.providers ?? []).map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
        </Field>
        <Field label="Component" hint="The service or infrastructure this monitor represents">
          <select value={d.component_id ?? ""} onChange={(e) => setD({ ...d, component_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">— none —</option>
            {(components.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </Field>
        {provider?.integration_kind && (
          <Field label="Integration *" hint={usable.length ? undefined : `Configure a ${provider.integration_kind} integration first.`}>
            <select value={d.integration_id ?? ""} onChange={(e) => setD({ ...d, integration_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">Select…</option>
              {usable.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
            </select>
          </Field>
        )}
      </div>
      {provider && (
        <fieldset>
          <legend>{provider.label} settings</legend>
          <SchemaForm key={d.kind} schema={provider.config_schema} values={d.config} onChange={(config) => setD({ ...d, config })} />
        </fieldset>
      )}
      <fieldset>
        <legend>Schedule & incident rules</legend>
        <div className="form-grid">
          <Field label="Interval (seconds)"><input type="number" min={10} value={d.interval_seconds} onChange={(e) => setD({ ...d, interval_seconds: Number(e.target.value) })} /></Field>
          <Field label="Timeout (seconds)"><input type="number" min={1} step="any" value={d.timeout_seconds} onChange={(e) => setD({ ...d, timeout_seconds: Number(e.target.value) })} /></Field>
          <Field label="Open incident after N failures" hint="Consecutive failing checks"><input type="number" min={1} value={d.failure_threshold} onChange={(e) => setD({ ...d, failure_threshold: Number(e.target.value) })} /></Field>
          <Field label="Recover after N successes"><input type="number" min={1} value={d.recovery_threshold} onChange={(e) => setD({ ...d, recovery_threshold: Number(e.target.value) })} /></Field>
          <Field label="Degraded above latency (ms)" hint="Optional"><input type="number" value={d.degraded_latency_ms ?? ""} onChange={(e) => setD({ ...d, degraded_latency_ms: num(e.target.value) })} /></Field>
          <div className="stack" style={{ justifyContent: "center" }}>
            <label className="check"><input type="checkbox" checked={d.open_incident_on_degraded} onChange={(e) => setD({ ...d, open_incident_on_degraded: e.target.checked })} /> Open incidents for degraded results</label>
            <label className="check"><input type="checkbox" checked={d.enabled} onChange={(e) => setD({ ...d, enabled: e.target.checked })} /> Enabled</label>
          </div>
        </div>
      </fieldset>
      {test && (
        <div className={`alert ${test.status === "up" ? "ok" : test.status === "down" ? "error" : "warn"}`} role="status">
          <StatusBadge status={test.status as never} /> {test.message} {test.latency_ms !== null && <span className="num">· {ms(test.latency_ms)}</span>}
        </div>
      )}
      <ErrorAlert error={save.error ?? runTest.error} />
    </Modal>
  );
}

export function MonitoringPage() {
  const { setOpen } = useShell();
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const { data, isLoading, error } = useMonitors();
  const components = useComponents();
  const byComp = useMemo(() => componentNameMap(components.data), [components.data]);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const creating = params.get("new") === "1";

  const rows = (data ?? []).filter((m) =>
    (!q || m.name.toLowerCase().includes(q.toLowerCase()) || byComp.get(m.component_id ?? -1)?.name.toLowerCase().includes(q.toLowerCase())) &&
    (!status || (status === "disabled" ? !m.enabled : m.enabled && m.last_status === status)) &&
    (!kind || m.kind === kind));
  const kinds = [...new Set((data ?? []).map((m) => m.kind))];

  return (
    <>
      <PageHeader title="Monitoring" subtitle="Monitoring targets, configuration and check history" setOpen={setOpen}
        actions={can("operator") && <button className="btn primary" onClick={() => setParams({ new: "1" })}><Plus size={15} /> New monitor</button>} />
      <div className="content">
        <div className="filters">
          <input placeholder="Search monitors or components…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search monitors" />
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
            <option value="">All statuses</option>
            {["down", "degraded", "up", "unknown", "disabled"].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Filter by type">
            <option value="">All types</option>
            {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </div>
        <ErrorAlert error={error} />
        <Card flush>
          {isLoading ? <Loading /> : rows.length === 0 ? (
            <EmptyState icon={<Activity size={30} />} title={data?.length ? "No matching monitors" : "No monitors yet"}
              action={can("operator") && !data?.length && <button className="btn primary" onClick={() => setParams({ new: "1" })}>Create a monitor</button>}>
              {data?.length ? "Adjust the filters." : "Monitors check HTTP endpoints, TCP ports, Linux hosts (agent), Prometheus queries or Kubernetes workloads on a schedule."}
            </EmptyState>
          ) : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Monitor</th><th>Status</th><th className="hide-sm">Type</th><th className="hide-sm">Component</th><th className="hide-sm">Interval</th><th>Last check</th><th className="hide-sm">Message</th></tr></thead>
                <tbody>
                  {rows.map((m) => (
                    <tr key={m.id}>
                      <td><Link to={`/monitoring/${m.id}`}><strong>{m.name}</strong></Link> <SyntheticBadge show={m.is_synthetic} /></td>
                      <td><StatusBadge status={m.enabled ? m.last_status : "disabled"} /></td>
                      <td className="hide-sm"><code>{m.kind}</code></td>
                      <td className="hide-sm">{m.component_id ? <Link to={`/services?id=${m.component_id}`}>{byComp.get(m.component_id)?.name}</Link> : <span className="muted">—</span>}</td>
                      <td className="hide-sm num">{m.interval_seconds}s</td>
                      <td className="nowrap muted">{timeAgo(m.last_checked_at)}</td>
                      <td className="hide-sm muted small" style={{ maxWidth: 360 }}>{m.last_message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
      {creating && <MonitorModal presetComponent={Number(params.get("component_id")) || undefined} onClose={() => setParams({})} />}
    </>
  );
}

const WINDOWS = [{ h: 24, l: "24h" }, { h: 168, l: "7d" }, { h: 720, l: "30d" }];

export function MonitorDetailPage() {
  const { setOpen } = useShell();
  const { can } = useAuth();
  const { id } = useParams();
  const navigate = useNavigate();
  const mid = Number(id);
  const [hours, setHours] = useState(24);
  const [statusFilter, setStatusFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<CheckResult | null>(null);
  const components = useComponents();

  const monitor = useQuery({ queryKey: ["monitors", mid], queryFn: () => api.get<Monitor>(`/monitors/${mid}`), refetchInterval: 10000 });
  const stats = useQuery({ queryKey: ["monitors", mid, "stats", hours], queryFn: () => api.get<MonitorStats>(`/monitors/${mid}/stats?hours=${hours}`), refetchInterval: 30000 });
  const results = useQuery({
    queryKey: ["monitors", mid, "results", statusFilter, offset],
    queryFn: () => api.get<Page<CheckResult>>(`/monitors/${mid}/results${qs({ status: statusFilter, limit: 25, offset })}`),
    refetchInterval: 15000,
  });
  const run = useAction(() => api.post<CheckResult>(`/monitors/${mid}/run`), ["monitors", "overview"]);
  const toggle = useAction((enabled: boolean) => api.patch(`/monitors/${mid}`, { enabled }), ["monitors"]);
  const remove = useAction(() => api.del(`/monitors/${mid}`), ["monitors", "overview"], () => navigate("/monitoring"));

  const m = monitor.data;
  if (monitor.isLoading) return <><PageHeader title="Monitor" setOpen={setOpen} /><Loading /></>;
  if (!m) return <><PageHeader title="Monitor" setOpen={setOpen} /><div className="content"><ErrorAlert error={monitor.error} /></div></>;
  const comp = components.data?.find((c) => c.id === m.component_id);

  return (
    <>
      <PageHeader
        title={m.name}
        subtitle={<>{m.kind} monitor{comp && <> · <Link to={`/services?id=${comp.id}`}>{comp.name}</Link></>} · every {m.interval_seconds}s</>}
        setOpen={setOpen}
        actions={can("operator") && (
          <div className="row">
            <button className="btn" onClick={() => run.mutate(undefined)} disabled={run.isPending}><RefreshCw size={14} className={run.isPending ? "spin" : ""} /> Run now</button>
            <button className="btn" onClick={() => toggle.mutate(!m.enabled)}>{m.enabled ? <><Pause size={14} /> Pause</> : <><Play size={14} /> Resume</>}</button>
            <button className="btn" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</button>
            <button className="btn danger icon-btn" aria-label="Delete monitor" onClick={() => confirm(`Delete monitor "${m.name}" and its history?`) && remove.mutate(undefined)}><Trash2 size={14} /></button>
          </div>
        )}
      />
      <div className="content">
        <ErrorAlert error={run.error ?? toggle.error ?? remove.error} />
        {m.is_synthetic && <div className="alert warn small"><SyntheticBadge /> This monitor produces simulated demo results.</div>}
        <div className="grid cols-4">
          <Kpi label="Current status" value={<StatusBadge status={m.enabled ? m.last_status : "disabled"} />} sub={m.last_message || "—"} />
          <Kpi label={`Availability (${WINDOWS.find((w) => w.h === hours)?.l})`} value={pct(stats.data?.availability_pct, 3)} sub={`${stats.data?.checks ?? 0} check${stats.data?.checks === 1 ? "" : "s"}`} />
          <Kpi label="Avg / p95 response" value={ms(stats.data?.avg_latency_ms)} sub={`p95 ${ms(stats.data?.p95_latency_ms)}`} />
          <Kpi label="Last check" value={<span style={{ fontSize: 18 }}>{timeAgo(m.last_checked_at)}</span>}
            sub={m.consecutive_failures ? `${m.consecutive_failures} consecutive failures (incident at ${m.failure_threshold})` : `${m.consecutive_successes} consecutive passes`} />
        </div>

        <Card title="History" actions={
          <div className="segmented" role="group" aria-label="Time window">
            {WINDOWS.map((w) => <button key={w.h} aria-pressed={hours === w.h} onClick={() => setHours(w.h)}>{w.l}</button>)}
          </div>
        }>
          {stats.data && stats.data.checks > 0 ? (
            <div className="stack" style={{ gap: 18 }}>
              <StatusStrip series={stats.data.series} label="Status by time bucket" />
              <div className="grid cols-2">
                <div><h3>Availability</h3><AvailabilityChart data={stats.data.series} /></div>
                <div><h3>Response time</h3>
                  {stats.data.avg_latency_ms !== null
                    ? <LatencyChart data={stats.data.series} threshold={m.degraded_latency_ms} />
                    : <EmptyState title="No latency data">This check type does not measure target response time.</EmptyState>}
                </div>
              </div>
            </div>
          ) : <EmptyState icon={<Activity size={28} />} title="No results in this window">The first check runs shortly after the monitor is created. Use “Run now” to check immediately.</EmptyState>}
        </Card>

        <div className={selected ? "grid side" : ""}>
          <Card title="Check results" flush actions={
            <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setOffset(0); }} aria-label="Filter results" style={{ width: "auto" }}>
              <option value="">All results</option>
              {["up", "degraded", "down", "unknown"].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          }>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Time</th><th>Status</th><th className="right">Latency</th><th>Message</th></tr></thead>
                <tbody>
                  {(results.data?.items ?? []).map((r) => (
                    <tr key={r.id} className={`clickable ${selected?.id === r.id ? "selected" : ""}`} onClick={() => setSelected(r)}>
                      <td className="nowrap">{dateTime(r.checked_at)}</td>
                      <td><StatusBadge status={r.status} /></td>
                      <td className="right num">{ms(r.latency_ms)}</td>
                      <td className="muted small">{r.message}</td>
                    </tr>
                  ))}
                  {results.data?.items.length === 0 && <tr><td colSpan={4} className="muted">No results.</td></tr>}
                </tbody>
              </table>
            </div>
            {results.data && results.data.total > 25 && (
              <div className="row between" style={{ padding: 10 }}>
                <span className="muted small">{offset + 1}–{Math.min(offset + 25, results.data.total)} of {results.data.total}</span>
                <div className="row">
                  <button className="btn sm" disabled={offset === 0} onClick={() => setOffset(offset - 25)}>Newer</button>
                  <button className="btn sm" disabled={offset + 25 >= results.data.total} onClick={() => setOffset(offset + 25)}>Older</button>
                </div>
              </div>
            )}
          </Card>
          {selected && (
            <Card title="Result details" actions={<button className="btn ghost sm" onClick={() => setSelected(null)}>Close</button>}>
              <dl className="detail-list">
                <dt>Time</dt><dd>{dateTime(selected.checked_at)}</dd>
                <dt>Status</dt><dd><StatusBadge status={selected.status} /></dd>
                <dt>Message</dt><dd>{selected.message}</dd>
              </dl>
              <pre className="json" style={{ marginTop: 12 }}>{JSON.stringify(selected.details, null, 2)}</pre>
            </Card>
          )}
        </div>

        <Card title="Configuration">
          <pre className="json">{JSON.stringify(m.config, null, 2)}</pre>
          <p className="muted small" style={{ marginBottom: 0 }}>
            Timeout {m.timeout_seconds}s · incident after {m.failure_threshold} consecutive failures · recovers after {m.recovery_threshold} passes
            {m.degraded_latency_ms ? ` · degraded above ${m.degraded_latency_ms} ms` : ""}
          </p>
        </Card>
      </div>
      {editing && <MonitorModal initial={m} onClose={() => setEditing(false)} />}
    </>
  );
}
