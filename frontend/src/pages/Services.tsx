import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Pencil, Plus, Server, Trash2, X } from "lucide-react";
import { api } from "../api/client";
import type { Component, Dependency, Monitor } from "../api/types";
import { PageHeader } from "../components/Layout";
import { Card, EmptyState, ErrorAlert, Field, Loading, Modal, StatusBadge, SyntheticBadge } from "../components/ui";
import { useAuth } from "../lib/auth";
import { humanize, pct } from "../lib/format";
import { componentNameMap, useAction, useComponents, useMonitors } from "../lib/hooks";
import { useShell } from "../lib/shell";
import { useQuery } from "@tanstack/react-query";

const KINDS = ["service", "application", "server", "database", "network", "storage", "kubernetes", "cloud", "other"];
const CRIT = ["low", "medium", "high", "critical"];
const STATUS_RANK: Record<string, number> = { down: 0, impacted: 1, degraded: 2, unknown: 3, unmonitored: 4, up: 5 };

type Draft = Pick<Component, "name" | "kind" | "description" | "owner" | "environment" | "criticality"> & { tags: string };

function emptyDraft(): Draft {
  return { name: "", kind: "service", description: "", owner: "", environment: "", criticality: "medium", tags: "" };
}

export function ComponentModal({ initial, onClose }: { initial?: Component; onClose: () => void }) {
  const [d, setD] = useState<Draft>(
    initial ? { ...initial, tags: initial.tags.join(", ") } : emptyDraft(),
  );
  const save = useAction(
    async (draft: Draft) => {
      const body = { ...draft, tags: draft.tags.split(",").map((t) => t.trim()).filter(Boolean) };
      return initial ? api.patch(`/components/${initial.id}`, body) : api.post("/components", body);
    },
    ["components", "overview", "topology"],
    onClose,
  );
  const set = (k: keyof Draft, v: string) => setD({ ...d, [k]: v });
  return (
    <Modal
      title={initial ? `Edit ${initial.name}` : "Add service or component"}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!d.name.trim() || save.isPending} onClick={() => save.mutate(d)}>
            {initial ? "Save changes" : "Create"}
          </button>
        </>
      }
    >
      <div className="form-grid">
        <Field label="Name *"><input value={d.name} onChange={(e) => set("name", e.target.value)} placeholder="orders-api" /></Field>
        <Field label="Kind">
          <select value={d.kind} onChange={(e) => set("kind", e.target.value)}>
            {KINDS.map((k) => <option key={k} value={k}>{humanize(k)}</option>)}
          </select>
        </Field>
        <Field label="Criticality" hint="Drives incident severity and risk prioritization">
          <select value={d.criticality} onChange={(e) => set("criticality", e.target.value)}>
            {CRIT.map((k) => <option key={k} value={k}>{humanize(k)}</option>)}
          </select>
        </Field>
        <Field label="Owner / team"><input value={d.owner} onChange={(e) => set("owner", e.target.value)} /></Field>
        <Field label="Environment"><input value={d.environment} onChange={(e) => set("environment", e.target.value)} placeholder="prod" /></Field>
        <Field label="Tags" hint="Comma separated"><input value={d.tags} onChange={(e) => set("tags", e.target.value)} /></Field>
        <label className="field full">
          <span>Description</span>
          <textarea value={d.description} onChange={(e) => set("description", e.target.value)} />
        </label>
      </div>
      <ErrorAlert error={save.error} />
    </Modal>
  );
}

function DetailPanel({ comp, all, onClose }: { comp: Component; all: Map<number, Component>; onClose: () => void }) {
  const { can } = useAuth();
  const [editing, setEditing] = useState(false);
  const [target, setTarget] = useState("");
  const monitors = useMonitors();
  const deps = useQuery({ queryKey: ["topology"], queryFn: () => api.get<{ edges: Dependency[] }>("/topology") });
  const impact = useQuery({
    queryKey: ["impact", comp.id],
    queryFn: () => api.get<{ upstream: number[]; downstream: number[] }>(`/components/${comp.id}/impact`),
  });
  const addDep = useAction((t: number) => api.post("/dependencies", { source_id: comp.id, target_id: t }),
    ["components", "topology", "impact"], () => setTarget(""));
  const delDep = useAction((id: number) => api.del(`/dependencies/${id}`), ["components", "topology", "impact"]);
  const remove = useAction(() => api.del(`/components/${comp.id}`), ["components", "overview", "topology"], onClose);
  const mine: Monitor[] = (monitors.data ?? []).filter((m) => m.component_id === comp.id);
  const edges = (deps.data?.edges ?? []).filter((e) => e.source_id === comp.id);
  const candidates = [...all.values()].filter((c) => c.id !== comp.id && !comp.depends_on.includes(c.id));

  return (
    <Card
      title={<div className="row"><h2>{comp.name}</h2><SyntheticBadge show={comp.is_synthetic} /></div>}
      actions={
        <div className="row">
          {can("operator") && <button className="btn sm" onClick={() => setEditing(true)}><Pencil size={13} /> Edit</button>}
          <button className="btn ghost icon-btn" onClick={onClose} aria-label="Close details"><X size={16} /></button>
        </div>
      }
    >
      <div className="stack" style={{ gap: 16 }}>
        <dl className="detail-list">
          <dt>Status</dt><dd>{comp.health && <StatusBadge status={comp.health.effective_status} />}</dd>
          {comp.health?.effective_status === "impacted" && (
            <><dt>Impacted by</dt><dd>{comp.health.impacted_by.map((id) => all.get(id)?.name).join(", ")}</dd></>
          )}
          <dt>Kind</dt><dd>{humanize(comp.kind)}</dd>
          <dt>Criticality</dt><dd>{humanize(comp.criticality)}</dd>
          <dt>Owner</dt><dd>{comp.owner || "—"}</dd>
          <dt>Availability 24h</dt><dd className="num">{pct(comp.availability_24h, 3)}</dd>
          <dt>Open incidents</dt><dd>{comp.open_incidents ? <Link to={`/incidents?component_id=${comp.id}`}>{comp.open_incidents}</Link> : 0}</dd>
          {comp.description && <><dt>Description</dt><dd>{comp.description}</dd></>}
        </dl>

        <div>
          <h3>Monitors</h3>
          {mine.length === 0 ? (
            <p className="muted small">No monitors. <Link to={`/monitoring?new=1&component_id=${comp.id}`}>Add one</Link> to track this component directly.</p>
          ) : (
            <ul className="stack" style={{ listStyle: "none", padding: 0, margin: "8px 0 0" }}>
              {mine.map((m) => (
                <li key={m.id} className="row between">
                  <Link to={`/monitoring/${m.id}`}>{m.name}</Link>
                  <StatusBadge status={m.enabled ? m.last_status : "disabled"} />
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <h3>Depends on</h3>
          <ul className="stack" style={{ listStyle: "none", padding: 0, margin: "8px 0" }}>
            {edges.length === 0 && <li className="muted small">No declared dependencies.</li>}
            {edges.map((e) => (
              <li key={e.id} className="row between">
                <span>{all.get(e.target_id)?.name}</span>
                <span className="row">
                  {all.get(e.target_id)?.health && <StatusBadge status={all.get(e.target_id)!.health!.effective_status} />}
                  {can("operator") && (
                    <button className="btn ghost sm" aria-label="Remove dependency" onClick={() => delDep.mutate(e.id)}><Trash2 size={13} /></button>
                  )}
                </span>
              </li>
            ))}
          </ul>
          {can("operator") && candidates.length > 0 && (
            <div className="row">
              <select value={target} onChange={(e) => setTarget(e.target.value)} style={{ flex: 1 }} aria-label="Add dependency">
                <option value="">Add dependency…</option>
                {candidates.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <button className="btn sm" disabled={!target || addDep.isPending} onClick={() => addDep.mutate(Number(target))}>Add</button>
            </div>
          )}
          <ErrorAlert error={addDep.error} />
        </div>

        <div>
          <h3>Blast radius</h3>
          <p className="small" style={{ margin: "6px 0 0" }}>
            {impact.data?.downstream.length
              ? <>If {comp.name} fails, <strong>{impact.data.downstream.length}</strong> component(s) may be impacted: {impact.data.downstream.map((id) => all.get(id)?.name).join(", ")}.</>
              : <span className="muted">Nothing depends on this component.</span>}
          </p>
          <Link className="small" to={`/topology?focus=${comp.id}`}>Show in topology →</Link>
        </div>

        {can("operator") && (
          <div>
            <button className="btn danger sm" onClick={() => confirm(`Delete ${comp.name}? Its dependencies are removed; monitors are kept but unlinked.`) && remove.mutate(undefined)}>
              <Trash2 size={13} /> Delete component
            </button>
          </div>
        )}
      </div>
      {editing && <ComponentModal initial={comp} onClose={() => setEditing(false)} />}
    </Card>
  );
}

export function ServicesPage() {
  const { setOpen } = useShell();
  const { can } = useAuth();
  const { data, isLoading, error } = useComponents();
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(false);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const selectedId = Number(params.get("id")) || null;
  const byId = useMemo(() => componentNameMap(data), [data]);

  const rows = useMemo(() => {
    const term = q.toLowerCase();
    return (data ?? [])
      .filter((c) => !term || [c.name, c.owner, c.kind, ...c.tags].some((x) => x.toLowerCase().includes(term)))
      .filter((c) => !status || c.health?.effective_status === status)
      .sort((a, b) =>
        (STATUS_RANK[a.health?.effective_status ?? "up"] - STATUS_RANK[b.health?.effective_status ?? "up"]) ||
        a.name.localeCompare(b.name));
  }, [data, q, status]);
  const selected = selectedId ? byId.get(selectedId) : undefined;

  return (
    <>
      <PageHeader
        title="Services"
        subtitle="Inventory of services and infrastructure components with live health"
        setOpen={setOpen}
        actions={can("operator") && <button className="btn primary" onClick={() => setCreating(true)}><Plus size={15} /> Add component</button>}
      />
      <div className="content">
        <div className="filters">
          <input placeholder="Search name, owner, tag…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search components" />
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
            <option value="">All statuses</option>
            {["down", "impacted", "degraded", "up", "unknown", "unmonitored"].map((s) => <option key={s} value={s}>{humanize(s)}</option>)}
          </select>
          <span className="muted small">{rows.length} of {data?.length ?? 0}</span>
        </div>
        <ErrorAlert error={error} />
        <div className={selected ? "grid side" : ""}>
          <Card flush>
            {isLoading ? <Loading /> : rows.length === 0 ? (
              <EmptyState icon={<Server size={30} />} title={data?.length ? "No matching components" : "No services yet"}
                action={can("operator") && !data?.length && <button className="btn primary" onClick={() => setCreating(true)}>Add your first component</button>}>
                {data?.length ? "Adjust the filters." : "Components represent services, servers, databases, networks or clusters. Declare dependencies between them to see blast radius and correlated incidents."}
              </EmptyState>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Name</th><th>Status</th><th className="hide-sm">Kind</th><th className="hide-sm">Criticality</th><th className="hide-sm">Owner</th><th className="right">Avail. 24h</th><th className="right hide-sm">Deps</th><th className="right">Incidents</th></tr>
                  </thead>
                  <tbody>
                    {rows.map((c) => (
                      <tr key={c.id} className={`clickable ${c.id === selectedId ? "selected" : ""}`}
                        onClick={() => setParams(c.id === selectedId ? {} : { id: String(c.id) })}>
                        <td style={{ minWidth: 200 }}>
                          <div className="row" style={{ flexWrap: "nowrap" }}><strong className="nowrap">{c.name}</strong> <SyntheticBadge show={c.is_synthetic} /></div>
                          {(c.environment || c.tags.length > 0) && <div className="muted small">{[c.environment, ...c.tags.filter((t) => t !== "demo")].filter(Boolean).join(" · ")}</div>}
                        </td>
                        <td>{c.health && <StatusBadge status={c.health.effective_status} />}</td>
                        <td className="hide-sm">{humanize(c.kind)}</td>
                        <td className="hide-sm"><span className={`badge crit-${c.criticality}`}>{humanize(c.criticality)}</span></td>
                        <td className="hide-sm muted">{c.owner || "—"}</td>
                        <td className="right num">{pct(c.availability_24h)}</td>
                        <td className="right num hide-sm nowrap" title={`${c.depends_on.length} dependencies, ${c.dependents.length} dependents`}>{c.depends_on.length}↓ {c.dependents.length}↑</td>
                        <td className="right num">{c.open_incidents || ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          {selected && <DetailPanel key={selected.id} comp={selected} all={byId} onClose={() => setParams({})} />}
        </div>
      </div>
      {creating && <ComponentModal onClose={() => setCreating(false)} />}
    </>
  );
}
