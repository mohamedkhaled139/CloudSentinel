import { useState } from "react";
import { Copy, Plug, Plus, ServerCog, Trash2 } from "lucide-react";
import { api } from "../api/client";
import type { Agent, Integration } from "../api/types";
import { PageHeader } from "../components/Layout";
import { cleanValues, defaultsFor, SchemaForm } from "../components/SchemaForm";
import { Card, EmptyState, ErrorAlert, Field, Loading, Modal, StatusBadge } from "../components/ui";
import { useAuth } from "../lib/auth";
import { timeAgo } from "../lib/format";
import { useAction, useAgents, useIntegrations, useProviders } from "../lib/hooks";
import { useShell } from "../lib/shell";

function IntegrationModal({ initial, onClose }: { initial?: Integration; onClose: () => void }) {
  const providers = useProviders();
  const adapters = providers.data?.integrations ?? [];
  const [kind, setKind] = useState(initial?.kind ?? "prometheus");
  const adapter = adapters.find((a) => a.kind === kind);
  const [name, setName] = useState(initial?.name ?? "");
  const [config, setConfig] = useState<Record<string, unknown>>(initial?.config ?? {});
  const [secrets, setSecrets] = useState<Record<string, unknown>>({});
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const save = useAction(
    () => {
      const body = { name, config: cleanValues({ ...(adapter ? defaultsFor(adapter.config_schema) : {}), ...config }), secrets: cleanValues(secrets), enabled };
      return initial ? api.patch(`/integrations/${initial.id}`, body) : api.post("/integrations", { ...body, kind });
    },
    ["integrations"],
    onClose,
  );
  return (
    <Modal wide title={initial ? `Edit ${initial.name}` : "Add integration"} onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate(undefined)}>Save</button>
      </>
    }>
      <div className="form-grid">
        <Field label="Name *"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="prometheus-prod" /></Field>
        <Field label="Type" hint={adapter?.description}>
          <select value={kind} disabled={!!initial} onChange={(e) => { setKind(e.target.value); setConfig({}); setSecrets({}); }}>
            {adapters.map((a) => <option key={a.kind} value={a.kind}>{a.label}</option>)}
          </select>
        </Field>
      </div>
      {adapter && (
        <>
          <fieldset>
            <legend>Connection</legend>
            <SchemaForm key={`c-${kind}`} schema={adapter.config_schema} values={config} onChange={setConfig} idPrefix="cfg" />
          </fieldset>
          <fieldset>
            <legend>Credentials (encrypted at rest, never shown again)</legend>
            <SchemaForm key={`s-${kind}`} schema={adapter.secret_schema} values={secrets} onChange={setSecrets} secret setFields={initial?.secret_fields_set} idPrefix="sec" />
          </fieldset>
        </>
      )}
      <label className="check"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled</label>
      <ErrorAlert error={save.error} />
    </Modal>
  );
}

function AgentInstructions({ agent, onClose }: { agent: Agent & { token: string }; onClose: () => void }) {
  const url = window.location.origin;
  const cmd = `sudo CLOUDSENTINEL_URL=${url} CLOUDSENTINEL_TOKEN=${agent.token} \\\n  python3 cloudsentinel_agent.py --install`;
  return (
    <Modal title={`Agent "${agent.name}" registered`} onClose={onClose} footer={<button className="btn primary" onClick={onClose}>Done</button>}>
      <div className="alert warn small">Copy the token now. It is stored only as a hash and cannot be shown again.</div>
      <Field label="Agent token">
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <input readOnly value={agent.token} className="mono" onFocus={(e) => e.target.select()} />
          <button className="btn icon-btn" aria-label="Copy token" onClick={() => navigator.clipboard?.writeText(agent.token)}><Copy size={14} /></button>
        </div>
      </Field>
      <div>
        <h3>Install on a Linux host</h3>
        <p className="small muted">Copy <code>agent/cloudsentinel_agent.py</code> from the CloudSentinel repository to the host (Python 3.8+, no dependencies), then run:</p>
        <pre className="json">{cmd}</pre>
        <p className="small muted">Then create a monitor of type <strong>Linux host (agent)</strong> with agent id <code>{agent.id}</code>.</p>
      </div>
    </Modal>
  );
}

export function IntegrationsPage() {
  const { setOpen } = useShell();
  const { can } = useAuth();
  const integrations = useIntegrations();
  const agents = useAgents();
  const providers = useProviders();
  const [editing, setEditing] = useState<Integration | "new" | null>(null);
  const [agentName, setAgentName] = useState("");
  const [created, setCreated] = useState<(Agent & { token: string }) | null>(null);
  const [testResult, setTestResult] = useState<Record<number, { ok: boolean; message: string }>>({});
  const test = useAction(
    (id: number) => api.post<{ ok: boolean; message: string }>(`/integrations/${id}/test`).then((r) => ({ id, r })),
    ["integrations"],
    ({ id, r }) => setTestResult((cur) => ({ ...cur, [id]: r })),
  );
  const remove = useAction((id: number) => api.del(`/integrations/${id}`), ["integrations"]);
  const createAgent = useAction(() => api.post<Agent & { token: string }>("/agents", { name: agentName }), ["agents"], (a) => { setCreated(a); setAgentName(""); });
  const removeAgent = useAction((id: number) => api.del(`/agents/${id}`), ["agents"]);

  return (
    <>
      <PageHeader title="Integrations" subtitle="Data sources, Linux agents and the available check providers" setOpen={setOpen}
        actions={can("admin") && <button className="btn primary" onClick={() => setEditing("new")}><Plus size={15} /> Add integration</button>} />
      <div className="content">
        <ErrorAlert error={integrations.error ?? test.error ?? remove.error ?? createAgent.error} />
        <Card title="Data source integrations" flush>
          {integrations.isLoading ? <Loading /> : !integrations.data?.length ? (
            <EmptyState icon={<Plug size={30} />} title="No integrations configured"
              action={can("admin") && <button className="btn primary" onClick={() => setEditing("new")}>Connect Prometheus or Kubernetes</button>}>
              Integrations connect CloudSentinel to metrics sources and clusters. HTTP, TCP and agent checks need no integration.
            </EmptyState>
          ) : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Name</th><th>Type</th><th>Connection</th><th className="hide-sm">Last checked</th><th className="right">Monitors</th><th /></tr></thead>
                <tbody>
                  {integrations.data.map((i) => (
                    <tr key={i.id}>
                      <td><strong>{i.name}</strong>{!i.enabled && <span className="muted small"> (disabled)</span>}
                        <div className="muted small mono">{String(i.config.base_url ?? i.config.api_server_url ?? (i.config.in_cluster ? "in-cluster" : ""))}</div></td>
                      <td>{i.kind}</td>
                      <td>
                        <StatusBadge status={i.status === "ok" ? "up" : i.status === "error" ? "down" : "unknown"} label={i.status === "ok" ? "Connected" : i.status === "error" ? "Error" : "Not tested"} />
                        {(testResult[i.id]?.message || i.last_error) && <div className="small muted" style={{ maxWidth: 380 }}>{testResult[i.id]?.message ?? i.last_error}</div>}
                      </td>
                      <td className="hide-sm muted">{timeAgo(i.last_checked_at)}</td>
                      <td className="right num">{i.monitor_count}</td>
                      <td className="right nowrap">
                        {can("admin") && (
                          <div className="row" style={{ justifyContent: "flex-end" }}>
                            <button className="btn sm" onClick={() => test.mutate(i.id)} disabled={test.isPending}>Test</button>
                            <button className="btn sm" onClick={() => setEditing(i)}>Edit</button>
                            <button className="btn sm danger" aria-label={`Delete ${i.name}`} onClick={() => confirm(`Delete integration ${i.name}? Monitors using it will report unknown.`) && remove.mutate(i.id)}><Trash2 size={13} /></button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Linux host agents" flush actions={can("admin") && (
          <div className="row">
            <input placeholder="Agent name (e.g. web-01)" value={agentName} onChange={(e) => setAgentName(e.target.value)} style={{ width: 200 }} aria-label="New agent name" />
            <button className="btn sm" disabled={!agentName.trim()} onClick={() => createAgent.mutate(undefined)}><Plus size={13} /> Register</button>
          </div>
        )}>
          {agents.isLoading ? <Loading /> : !agents.data?.length ? (
            <EmptyState icon={<ServerCog size={30} />} title="No agents registered">
              The lightweight agent (a single dependency-free Python script) pushes load, memory, disk and boot-time data every 60 seconds.
            </EmptyState>
          ) : (
            <table>
              <thead><tr><th>Agent</th><th>ID</th><th>Hostname</th><th>Version</th><th>Last heartbeat</th><th /></tr></thead>
              <tbody>
                {agents.data.map((a) => {
                  const stale = !a.last_seen_at || Date.now() - new Date(a.last_seen_at).getTime() > 180_000;
                  return (
                    <tr key={a.id}>
                      <td><strong>{a.name}</strong> <span className="muted mono small">{a.token_prefix}…</span></td>
                      <td className="num">{a.id}</td>
                      <td>{a.hostname ?? "—"}</td>
                      <td className="muted">{a.agent_version ?? "—"}</td>
                      <td><StatusBadge status={stale ? (a.last_seen_at ? "down" : "unknown") : "up"} label={timeAgo(a.last_seen_at)} /></td>
                      <td className="right">{can("admin") && <button className="btn sm danger" aria-label={`Delete agent ${a.name}`} onClick={() => confirm(`Delete agent ${a.name}? Its token stops working.`) && removeAgent.mutate(a.id)}><Trash2 size={13} /></button>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Available check providers">
          <div className="grid cols-3">
            {(providers.data?.providers ?? []).map((p) => (
              <div key={p.key} className="stack" style={{ gap: 4 }}>
                <strong>{p.label} <code className="muted">{p.key}</code></strong>
                <span className="small muted">{p.description}</span>
                {p.integration_kind && <span className="small">Requires a <code>{p.integration_kind}</code> integration.</span>}
              </div>
            ))}
          </div>
          <p className="muted small" style={{ marginBottom: 0 }}>New providers can be added in Python; their configuration forms are generated automatically. See <code>docs/integrations.md</code>.</p>
        </Card>
      </div>
      {editing && <IntegrationModal initial={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
      {created && <AgentInstructions agent={created} onClose={() => setCreated(null)} />}
    </>
  );
}
