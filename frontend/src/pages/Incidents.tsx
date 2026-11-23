import { useState, type ReactElement } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  AlertOctagon,
  CheckCircle2,
  Eye,
  GitBranch,
  MessageSquare,
  Plus,
  RotateCcw,
  Siren,
} from "lucide-react";
import { api, qs } from "../api/client";
import type { Incident, IncidentDetail, Page } from "../api/types";
import { PageHeader } from "../components/Layout";
import {
  Card,
  EmptyState,
  ErrorAlert,
  Field,
  IncidentStatusBadge,
  Loading,
  Modal,
  SeverityBadge,
  StatusBadge,
  SyntheticBadge,
} from "../components/ui";
import { useAuth } from "../lib/auth";
import { between, dateTime, humanize, ms } from "../lib/format";
import { componentNameMap, useAction, useComponents } from "../lib/hooks";
import { useShell } from "../lib/shell";

const TABS = [
  { v: "open", l: "Open" },
  { v: "resolved", l: "Resolved" },
  { v: "", l: "All" },
];

function DeclareModal({ onClose }: { onClose: () => void }) {
  const components = useComponents();
  const [title, setTitle] = useState("");
  const [severity, setSeverity] = useState("major");
  const [componentId, setComponentId] = useState("");
  const [summary, setSummary] = useState("");
  const create = useAction(
    () => api.post<Incident>("/incidents", { title, severity, summary, component_id: componentId ? Number(componentId) : null }),
    ["incidents", "overview"],
    onClose,
  );
  return (
    <Modal title="Declare incident" onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={title.trim().length < 3 || create.isPending} onClick={() => create.mutate(undefined)}>Declare</button>
      </>
    }>
      <p className="muted small" style={{ margin: 0 }}>Use this for problems that monitors did not detect (user reports, vendor notices, planned-work overruns).</p>
      <Field label="Title *"><input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Elevated storage latency on SAN-A" /></Field>
      <div className="form-grid">
        <Field label="Severity">
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            {["critical", "major", "minor", "info"].map((s) => <option key={s} value={s}>{humanize(s)}</option>)}
          </select>
        </Field>
        <Field label="Affected component">
          <select value={componentId} onChange={(e) => setComponentId(e.target.value)}>
            <option value="">— none —</option>
            {(components.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </Field>
      </div>
      <Field label="Summary"><textarea value={summary} onChange={(e) => setSummary(e.target.value)} /></Field>
      <ErrorAlert error={create.error} />
    </Modal>
  );
}

export function IncidentsPage() {
  const { setOpen } = useShell();
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "open";
  const [severity, setSeverity] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [declaring, setDeclaring] = useState(false);
  const componentId = params.get("component_id");
  const { data, isLoading, error } = useQuery({
    queryKey: ["incidents", status, severity, q, offset, componentId],
    queryFn: () => api.get<Page<Incident>>(`/incidents${qs({ status, severity, q, limit: 50, offset, component_id: componentId })}`),
    refetchInterval: 15000,
  });

  return (
    <>
      <PageHeader title="Incidents" subtitle="Detected outages and degradations, their lifecycle and investigation" setOpen={setOpen}
        actions={can("operator") && <button className="btn primary" onClick={() => setDeclaring(true)}><Plus size={15} /> Declare incident</button>} />
      <div className="content">
        <div className="filters">
          <div className="segmented" role="group" aria-label="Incident status">
            {TABS.map((t) => (
              <button key={t.v} aria-pressed={status === t.v} onClick={() => { setParams(t.v === "open" ? {} : { status: t.v }); setOffset(0); }}>{t.l}</button>
            ))}
          </div>
          <select value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity">
            <option value="">Any severity</option>
            {["critical", "major", "minor", "info"].map((s) => <option key={s} value={s}>{humanize(s)}</option>)}
          </select>
          <input placeholder="Search title or summary…" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} aria-label="Search incidents" />
          {componentId && <button className="btn sm" onClick={() => setParams(status === "open" ? {} : { status })}>Clear component filter</button>}
        </div>
        <ErrorAlert error={error} />
        <Card flush>
          {isLoading ? <Loading /> : !data?.items.length ? (
            <EmptyState icon={status === "open" ? <CheckCircle2 size={30} /> : <Siren size={30} />}
              title={status === "open" ? "No open incidents" : "No incidents found"}>
              {status === "open" ? "Monitors open incidents automatically after their configured number of consecutive failures." : "Try a different filter."}
            </EmptyState>
          ) : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Severity</th><th>Incident</th><th>Status</th><th className="hide-sm">Component</th><th>Started</th><th className="right">Duration</th></tr></thead>
                <tbody>
                  {data.items.map((i) => (
                    <tr key={i.id}>
                      <td><SeverityBadge severity={i.severity} /></td>
                      <td>
                        <Link to={`/incidents/${i.id}`}><strong>#{i.id} {i.title}</strong></Link> <SyntheticBadge show={i.is_synthetic} />
                        <div className="muted small">
                          {i.parent_id ? <>↳ linked to <Link to={`/incidents/${i.parent_id}`}>#{i.parent_id}</Link></> : null}
                          {i.child_count ? <>{i.child_count} correlated incident(s)</> : null}
                        </div>
                      </td>
                      <td><IncidentStatusBadge status={i.status} /></td>
                      <td className="hide-sm">{i.component_name ?? "—"}</td>
                      <td className="nowrap muted">{dateTime(i.started_at)}</td>
                      <td className="right num">{between(i.started_at, i.resolved_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data && data.total > 50 && (
            <div className="row between" style={{ padding: 10 }}>
              <span className="muted small">{offset + 1}–{Math.min(offset + 50, data.total)} of {data.total}</span>
              <div className="row">
                <button className="btn sm" disabled={offset === 0} onClick={() => setOffset(offset - 50)}>Previous</button>
                <button className="btn sm" disabled={offset + 50 >= data.total} onClick={() => setOffset(offset + 50)}>Next</button>
              </div>
            </div>
          )}
        </Card>
      </div>
      {declaring && <DeclareModal onClose={() => setDeclaring(false)} />}
    </>
  );
}

const EVENT_ICON: Record<string, ReactElement> = {
  detected: <AlertOctagon size={11} />,
  check_failed: <AlertOctagon size={11} />,
  acknowledged: <Eye size={11} />,
  note: <MessageSquare size={11} />,
  correlated: <GitBranch size={11} />,
  resolved: <CheckCircle2 size={11} />,
  recovered: <CheckCircle2 size={11} />,
  check_recovering: <CheckCircle2 size={11} />,
  reopened: <RotateCcw size={11} />,
};

export function IncidentDetailPage() {
  const { setOpen } = useShell();
  const { can } = useAuth();
  const { id } = useParams();
  const iid = Number(id);
  const [note, setNote] = useState("");
  const [resolving, setResolving] = useState(false);
  const [resolution, setResolution] = useState("");
  const components = useComponents();
  const byComp = componentNameMap(components.data);
  const { data: inc, isLoading, error } = useQuery({
    queryKey: ["incidents", iid],
    queryFn: () => api.get<IncidentDetail>(`/incidents/${iid}`),
    refetchInterval: 15000,
  });
  const keys = ["incidents", "overview"];
  const ack = useAction(() => api.post(`/incidents/${iid}/acknowledge`, {}), keys);
  const addNote = useAction(() => api.post(`/incidents/${iid}/notes`, { note }), keys, () => setNote(""));
  const resolve = useAction(() => api.post(`/incidents/${iid}/resolve`, { note: resolution }), keys, () => setResolving(false));
  const reopen = useAction(() => api.post(`/incidents/${iid}/reopen`, {}), keys);

  if (isLoading) return <><PageHeader title="Incident" setOpen={setOpen} /><Loading /></>;
  if (!inc) return <><PageHeader title="Incident" setOpen={setOpen} /><div className="content"><ErrorAlert error={error} /></div></>;
  const open = inc.status !== "resolved";

  return (
    <>
      <PageHeader
        title={`#${inc.id} ${inc.title}`}
        subtitle={<>Started {dateTime(inc.started_at)} · {open ? `ongoing for ${between(inc.started_at, null)}` : `lasted ${between(inc.started_at, inc.resolved_at)}`}</>}
        setOpen={setOpen}
        actions={can("operator") && (
          <div className="row">
            {inc.status === "detected" && <button className="btn" onClick={() => ack.mutate(undefined)}><Eye size={14} /> Acknowledge</button>}
            {open && <button className="btn primary" onClick={() => setResolving(true)}><CheckCircle2 size={14} /> Resolve</button>}
            {!open && <button className="btn" onClick={() => reopen.mutate(undefined)}><RotateCcw size={14} /> Reopen</button>}
          </div>
        )}
      />
      <div className="content">
        <ErrorAlert error={ack.error ?? resolve.error ?? reopen.error ?? addNote.error} />
        <div className="row">
          <SeverityBadge severity={inc.severity} />
          <IncidentStatusBadge status={inc.status} />
          <SyntheticBadge show={inc.is_synthetic} />
          {inc.component_id && <span className="muted">Component: <Link to={`/services?id=${inc.component_id}`}>{inc.component_name}</Link></span>}
          {inc.monitor_id && <span className="muted">· Monitor: <Link to={`/monitoring/${inc.monitor_id}`}>{inc.monitor_name}</Link></span>}
        </div>

        <div className="grid side">
          <div className="stack" style={{ gap: 16 }}>
            {inc.summary && <Card title="Summary"><p style={{ margin: 0 }}>{inc.summary}</p></Card>}
            {inc.resolution_notes && <Card title="Resolution"><p style={{ margin: 0 }}>{inc.resolution_notes}</p>
              <p className="muted small" style={{ marginBottom: 0 }}>Resolved by {inc.resolved_by} · {dateTime(inc.resolved_at)}</p></Card>}
            <Card title="Timeline">
              <ol className="timeline">
                {inc.events.map((e) => (
                  <li key={e.id} className={e.kind}>
                    <span className="marker" aria-hidden>{EVENT_ICON[e.kind] ?? <MessageSquare size={11} />}</span>
                    <div className="meta">
                      <strong style={{ color: "var(--text)" }}>{humanize(e.kind)}</strong>
                      <span>{dateTime(e.at)}</span>
                      <span>by {e.actor}</span>
                    </div>
                    <div className="msg">
                      {e.message}
                      {typeof e.data.related_incident_id === "number" && <> <Link to={`/incidents/${e.data.related_incident_id}`}>View #{e.data.related_incident_id}</Link></>}
                    </div>
                  </li>
                ))}
              </ol>
              {can("operator") && (
                <div className="stack">
                  <label className="field">
                    <span>Add investigation note</span>
                    <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="What did you find? What did you change?" />
                  </label>
                  <div><button className="btn" disabled={!note.trim() || addNote.isPending} onClick={() => addNote.mutate(undefined)}><MessageSquare size={14} /> Add note</button></div>
                </div>
              )}
            </Card>
            <Card title="Evidence: check results around the incident" flush>
              {inc.evidence_results.length === 0 ? <EmptyState title="No monitor evidence">This incident was declared manually or its monitor history has been pruned by retention.</EmptyState> : (
                <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
                  <table>
                    <thead><tr><th>Time</th><th>Status</th><th className="right">Latency</th><th>Message</th></tr></thead>
                    <tbody>
                      {inc.evidence_results.map((r) => (
                        <tr key={r.id}>
                          <td className="nowrap">{dateTime(r.checked_at)}</td>
                          <td><StatusBadge status={r.status} /></td>
                          <td className="right num">{ms(r.latency_ms)}</td>
                          <td className="muted small">{r.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
          <div className="stack" style={{ gap: 16 }}>
            <Card title="Lifecycle">
              <dl className="detail-list">
                <dt>Detected</dt><dd>{dateTime(inc.started_at)}</dd>
                <dt>Acknowledged</dt><dd>{inc.acknowledged_at ? <>{dateTime(inc.acknowledged_at)}<div className="muted small">{inc.acknowledged_by}</div></> : "—"}</dd>
                <dt>Resolved</dt><dd>{inc.resolved_at ? dateTime(inc.resolved_at) : "—"}</dd>
                <dt>Time to ack</dt><dd className="num">{inc.acknowledged_at ? between(inc.started_at, inc.acknowledged_at) : "—"}</dd>
                <dt>Duration</dt><dd className="num">{between(inc.started_at, inc.resolved_at)}</dd>
              </dl>
            </Card>
            <Card title="Correlation">
              <p className="muted small" style={{ marginTop: 0 }}>Derived from declared dependencies. Correlation suggests a probable cause; it is not a confirmed root cause.</p>
              {inc.parent ? (
                <div className="alert warn small">
                  {inc.parent.component_id === inc.component_id ? "Grouped with" : "Probably caused by"}{" "}
                  <Link to={`/incidents/${inc.parent.id}`}>#{inc.parent.id} {inc.parent.title}</Link> ({inc.parent.status})
                </div>
              ) : null}
              {inc.children.length > 0 && (
                <>
                  <h3>Linked incidents ({inc.children.length})</h3>
                  <ul style={{ paddingLeft: 18, margin: "6px 0" }}>
                    {inc.children.map((c) => <li key={c.id}><Link to={`/incidents/${c.id}`}>#{c.id} {c.title}</Link> <IncidentStatusBadge status={c.status} /></li>)}
                  </ul>
                </>
              )}
              {!inc.parent && inc.children.length === 0 && <p className="small" style={{ marginBottom: 0 }}>No correlated incidents.</p>}
            </Card>
            <Card title="Potentially affected services">
              {inc.affected_components.length === 0 ? <p className="muted small" style={{ margin: 0 }}>No components depend on the affected component.</p> : (
                <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                  {inc.affected_components.map((cid) => {
                    const c = byComp.get(cid);
                    return (
                      <li key={cid} className="row between">
                        <Link to={`/services?id=${cid}`}>{c?.name ?? `#${cid}`}</Link>
                        {c?.health && <StatusBadge status={c.health.effective_status} />}
                      </li>
                    );
                  })}
                </ul>
              )}
              {inc.component_id && <p style={{ marginBottom: 0 }}><Link className="small" to={`/topology?focus=${inc.component_id}`}>View blast radius in topology →</Link></p>}
            </Card>
          </div>
        </div>
      </div>
      {resolving && (
        <Modal title="Resolve incident" onClose={() => setResolving(false)} footer={
          <>
            <button className="btn" onClick={() => setResolving(false)}>Cancel</button>
            <button className="btn primary" disabled={resolve.isPending} onClick={() => resolve.mutate(undefined)}>Resolve</button>
          </>
        }>
          <Field label="Resolution notes" hint="Root cause, fix applied, and follow-up actions. Included in reports.">
            <textarea value={resolution} onChange={(e) => setResolution(e.target.value)} rows={5} />
          </Field>
        </Modal>
      )}
    </>
  );
}
