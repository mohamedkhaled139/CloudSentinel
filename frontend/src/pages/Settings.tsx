import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { api } from "../api/client";
import type { AppSettings, Role, User } from "../api/types";
import { PageHeader } from "../components/Layout";
import { Card, ErrorAlert, Field, Loading, Modal, StatusBadge } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime, humanize, timeAgo } from "../lib/format";
import { useAction, useSystemInfo } from "../lib/hooks";
import { useShell } from "../lib/shell";

const ROLE_HELP: Record<Role, string> = {
  admin: "Everything, including users, integrations and settings",
  operator: "Manage services, monitors, incidents and risks",
  viewer: "Read-only access",
};

function UserModal({ onClose }: { onClose: () => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const create = useAction(() => api.post("/users", { email, name, password, role }), ["users"], onClose);
  return (
    <Modal title="Add user" onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!email || password.length < 10 || create.isPending} onClick={() => create.mutate(undefined)}>Create user</button>
      </>
    }>
      <div className="form-grid">
        <Field label="Email *"><input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
        <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Initial password *" hint="At least 10 characters; ask the user to change it."><input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        <Field label="Role" hint={ROLE_HELP[role]}>
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {(["viewer", "operator", "admin"] as Role[]).map((r) => <option key={r} value={r}>{humanize(r)}</option>)}
          </select>
        </Field>
      </div>
      <ErrorAlert error={create.error} />
    </Modal>
  );
}

function Users() {
  const { user: me } = useAuth();
  const [adding, setAdding] = useState(false);
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users") });
  const update = useAction(({ id, body }: { id: number; body: Partial<User> }) => api.patch(`/users/${id}`, body), ["users"]);
  const remove = useAction((id: number) => api.del(`/users/${id}`), ["users"]);
  return (
    <Card title="Users & access" flush actions={<button className="btn sm primary" onClick={() => setAdding(true)}><Plus size={13} /> Add user</button>}>
      <ErrorAlert error={users.error ?? update.error ?? remove.error} />
      {users.isLoading ? <Loading /> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>User</th><th>Role</th><th>Status</th><th className="hide-sm">Last login</th><th /></tr></thead>
            <tbody>
              {(users.data ?? []).map((u) => (
                <tr key={u.id}>
                  <td><strong>{u.email}</strong>{u.name && <div className="muted small">{u.name}</div>}</td>
                  <td>
                    <select value={u.role} disabled={u.id === me?.id} aria-label={`Role for ${u.email}`} style={{ width: "auto" }}
                      onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value as Role } })}>
                      {(["viewer", "operator", "admin"] as Role[]).map((r) => <option key={r} value={r}>{humanize(r)}</option>)}
                    </select>
                  </td>
                  <td>
                    <label className="check">
                      <input type="checkbox" checked={u.is_active} disabled={u.id === me?.id} onChange={(e) => update.mutate({ id: u.id, body: { is_active: e.target.checked } })} />
                      {u.is_active ? "Active" : "Disabled"}
                    </label>
                  </td>
                  <td className="hide-sm muted">{timeAgo(u.last_login_at)}</td>
                  <td className="right">{u.id !== me?.id && <button className="btn sm danger" aria-label={`Delete ${u.email}`} onClick={() => confirm(`Delete ${u.email}?`) && remove.mutate(u.id)}><Trash2 size={13} /></button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding && <UserModal onClose={() => setAdding(false)} />}
    </Card>
  );
}

const SETTINGS_FIELDS: { key: keyof AppSettings; label: string; hint: string; step?: string; group: string }[] = [
  { key: "retention_check_results_days", label: "Check results (days)", hint: "Raw monitoring history", group: "Retention" },
  { key: "retention_agent_reports_days", label: "Agent reports (days)", hint: "Host metrics from agents", group: "Retention" },
  { key: "retention_resolved_incidents_days", label: "Resolved incidents (days)", hint: "Including timelines", group: "Retention" },
  { key: "retention_resolved_risks_days", label: "Resolved risk findings (days)", hint: "", group: "Retention" },
  { key: "risk_lookback_days", label: "Analysis window (days)", hint: "History considered by risk detection", group: "Risk analysis" },
  { key: "availability_target_pct", label: "Availability target (%)", hint: "Below this a finding is raised", step: "any", group: "Risk analysis" },
  { key: "recurring_failure_min_episodes", label: "Recurring failure episodes", hint: "Outage episodes in the window", group: "Risk analysis" },
  { key: "latency_regression_ratio", label: "Latency regression ratio", hint: "Last 24h median vs baseline", step: "any", group: "Risk analysis" },
  { key: "latency_min_samples", label: "Minimum samples", hint: "Before statistics are trusted", group: "Risk analysis" },
  { key: "restart_min_count", label: "Restart threshold", hint: "Restarts/reboots in the window", group: "Risk analysis" },
  { key: "stale_interval_multiplier", label: "Stale data multiplier", hint: "× monitor interval", step: "any", group: "Risk analysis" },
  { key: "tls_expiry_risk_days", label: "TLS expiry warning (days)", hint: "", group: "Risk analysis" },
];

function AppSettingsCard({ editable }: { editable: boolean }) {
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api.get<AppSettings>("/settings") });
  const [draft, setDraft] = useState<AppSettings | null>(null);
  useEffect(() => { if (q.data) setDraft(q.data); }, [q.data]);
  const save = useAction(() => api.put<AppSettings>("/settings", draft), ["settings"]);
  const prune = useAction(() => api.post<Record<string, number>>("/settings/retention/run"), []);
  if (!draft) return <Card title="Retention & analysis"><Loading /></Card>;
  const groups = [...new Set(SETTINGS_FIELDS.map((f) => f.group))];
  return (
    <Card title="Retention, incidents & risk analysis" actions={editable && (
      <div className="row">
        <button className="btn sm" onClick={() => prune.mutate(undefined)} disabled={prune.isPending}>Apply retention now</button>
        <button className="btn sm primary" onClick={() => save.mutate(undefined)} disabled={save.isPending}>Save settings</button>
      </div>
    )}>
      <div className="stack" style={{ gap: 16 }}>
        {save.isSuccess && <div className="alert ok small" role="status">Settings saved.</div>}
        {prune.data && <div className="alert ok small" role="status">Retention applied: {Object.entries(prune.data).map(([k, v]) => `${v} ${humanize(k).toLowerCase()}`).join(", ")} deleted.</div>}
        <ErrorAlert error={save.error ?? prune.error} />
        <label className="check">
          <input type="checkbox" disabled={!editable} checked={draft.auto_resolve_incidents} onChange={(e) => setDraft({ ...draft, auto_resolve_incidents: e.target.checked })} />
          Automatically resolve incidents when their monitor recovers
        </label>
        {groups.map((g) => (
          <fieldset key={g}>
            <legend>{g}</legend>
            <div className="form-grid">
              {SETTINGS_FIELDS.filter((f) => f.group === g).map((f) => (
                <Field key={f.key} label={f.label} hint={f.hint || undefined}>
                  <input type="number" step={f.step ?? "1"} disabled={!editable} value={String(draft[f.key])}
                    onChange={(e) => setDraft({ ...draft, [f.key]: Number(e.target.value) })} />
                </Field>
              ))}
            </div>
          </fieldset>
        ))}
      </div>
    </Card>
  );
}

function Account() {
  const { user } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const change = useAction(() => api.post("/auth/password", { current_password: current, new_password: next }), [], () => { setCurrent(""); setNext(""); });
  return (
    <Card title="My account">
      <dl className="detail-list" style={{ marginBottom: 14 }}>
        <dt>Email</dt><dd>{user?.email}</dd>
        <dt>Role</dt><dd>{humanize(user?.role ?? "")} — {user && ROLE_HELP[user.role]}</dd>
      </dl>
      <div className="form-grid">
        <Field label="Current password"><input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
        <Field label="New password" hint="At least 10 characters"><input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn" disabled={!current || next.length < 10 || change.isPending} onClick={() => change.mutate(undefined)}>Change password</button>
        {change.isSuccess && <span className="small" style={{ color: "var(--ok-text)" }}>Password changed.</span>}
      </div>
      <ErrorAlert error={change.error} />
    </Card>
  );
}

function Audit() {
  const q = useQuery({ queryKey: ["audit"], queryFn: () => api.get<{ id: number; at: string; actor: string; action: string; target: string }[]>("/audit?limit=100") });
  return (
    <Card title="Audit log (last 100 events)" flush>
      {q.isLoading ? <Loading /> : (
        <div className="table-wrap" style={{ maxHeight: 380, overflowY: "auto" }}>
          <table>
            <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th></tr></thead>
            <tbody>
              {(q.data ?? []).map((a) => (
                <tr key={a.id}><td className="nowrap muted">{dateTime(a.at)}</td><td>{a.actor}</td><td><code>{a.action}</code></td><td className="muted">{a.target}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function SettingsPage() {
  const { setOpen } = useShell();
  const { can } = useAuth();
  const system = useSystemInfo();
  const s = system.data;
  return (
    <>
      <PageHeader title="Settings" subtitle="Users, access control, retention and system configuration" setOpen={setOpen} />
      <div className="content">
        <div className="grid cols-2">
          <Account />
          <Card title="System">
            {!s ? <Loading /> : (
              <dl className="detail-list">
                <dt>Version</dt><dd className="mono">{s.version}</dd>
                <dt>Environment</dt><dd>{s.environment}</dd>
                <dt>Database</dt><dd>{s.database}</dd>
                <dt>Scheduler</dt><dd>{s.scheduler_enabled
                  ? <StatusBadge status={s.scheduler_healthy ? "up" : "down"} label={s.scheduler_healthy ? "Running" : "Stalled"} />
                  : "Disabled in this process (runs separately)"}</dd>
                <dt>Demo mode</dt><dd>{s.demo_mode ? "On — synthetic provider enabled" : "Off"}</dd>
                <dt>Private targets</dt><dd>{s.allow_private_targets ? "Allowed" : "Blocked"}</dd>
                <dt>API</dt><dd><a href="/api/docs" target="_blank" rel="noreferrer">OpenAPI documentation</a></dd>
              </dl>
            )}
            <p className="muted small" style={{ marginBottom: 0 }}>Process-level options (database, secret key, scheduler, CORS) are set with <code>CS_*</code> environment variables. See <code>docs/configuration.md</code>.</p>
          </Card>
        </div>
        {can("admin") && <Users />}
        <AppSettingsCard editable={can("admin")} />
        {can("admin") && <Audit />}
      </div>
    </>
  );
}
