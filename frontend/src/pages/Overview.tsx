import { Link } from "react-router-dom";
import { Activity, CheckCircle2, Server, ShieldAlert, Siren } from "lucide-react";
import { AvailabilityChart } from "../components/charts";
import { PageHeader } from "../components/Layout";
import {
  Card,
  EmptyState,
  ErrorAlert,
  IncidentStatusBadge,
  Kpi,
  Loading,
  SeverityBadge,
  StatusBadge,
  SyntheticBadge,
} from "../components/ui";
import { between, pct, timeAgo } from "../lib/format";
import { useComponents, useOverview } from "../lib/hooks";
import { useShell } from "../lib/shell";

const ORDER = ["down", "impacted", "degraded", "up", "unknown", "unmonitored"] as const;
const COLOR: Record<string, string> = {
  up: "var(--st-up)",
  degraded: "var(--st-degraded)",
  impacted: "var(--st-impacted)",
  down: "var(--st-down)",
  unknown: "var(--st-unknown)",
  unmonitored: "var(--surface-3)",
};

export function OverviewPage() {
  const { setOpen } = useShell();
  const { data, error, isLoading } = useOverview();
  const components = useComponents();

  if (isLoading) return <><PageHeader title="Overview" setOpen={setOpen} /><Loading /></>;
  if (error || !data) return <><PageHeader title="Overview" setOpen={setOpen} /><div className="content"><ErrorAlert error={error} /></div></>;

  const total = data.components.total ?? 0;
  const unhealthy = (data.components.down ?? 0) + (data.components.impacted ?? 0) + (data.components.degraded ?? 0);
  const attention = (components.data ?? [])
    .filter((c) => c.health && ["down", "impacted", "degraded"].includes(c.health.effective_status))
    .sort((a, b) => ORDER.indexOf(a.health!.effective_status as never) - ORDER.indexOf(b.health!.effective_status as never));

  if (total === 0 && (data.monitors.total ?? 0) === 0) {
    return (
      <>
        <PageHeader title="Overview" setOpen={setOpen} />
        <div className="content">
          <Card>
            <EmptyState icon={<Server size={32} />} title="Nothing is monitored yet"
              action={
                <div className="row">
                  <Link className="btn primary" to="/services">Add a service</Link>
                  <Link className="btn" to="/monitoring">Create a monitor</Link>
                </div>
              }>
              Start by describing your services and infrastructure components, then attach HTTP, TCP, agent,
              Prometheus or Kubernetes monitors. To explore with sample data, run the demo environment
              (<code>CS_DEMO_MODE=true cloudsentinel seed-demo</code>).
            </EmptyState>
          </Card>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle={`Updated ${timeAgo(data.generated_at)} · refreshes every 15s`}
        setOpen={setOpen}
      />
      <div className="content">
        <div className="grid cols-4">
          <Kpi
            icon={<Server size={14} />}
            label="Service health"
            value={<span>{total - unhealthy}<span className="muted" style={{ fontSize: 16 }}> / {total}</span></span>}
            sub={
              <>
                <div className="bar" aria-hidden>
                  {ORDER.map((s) =>
                    data.components[s] ? (
                      <span key={s} style={{ flex: data.components[s], background: COLOR[s] }} />
                    ) : null,
                  )}
                </div>
                <div style={{ marginTop: 6 }}>
                  {ORDER.filter((s) => data.components[s]).map((s) => `${data.components[s]} ${s}`).join(" · ")}
                </div>
              </>
            }
          />
          <Kpi icon={<CheckCircle2 size={14} />} label="Availability (24h)" value={pct(data.availability_24h, 3)}
            sub={`7 days: ${pct(data.availability_7d, 3)}`} />
          <Kpi icon={<Siren size={14} />} label="Open incidents" value={data.open_incidents.length}
            sub={<Link to="/incidents">View incidents →</Link>} />
          <Kpi icon={<ShieldAlert size={14} />} label="Open risks" value={data.open_risks.total ?? 0}
            sub={["critical", "major", "minor"].filter((s) => data.open_risks[s]).map((s) => `${data.open_risks[s]} ${s}`).join(" · ") || "No open findings"} />
        </div>

        <div className="grid side">
          <Card title="Availability · last 24 hours" actions={<span className="muted small">all monitors, hourly</span>}>
            <AvailabilityChart data={data.availability_trend} />
          </Card>
          <Card title="Needs attention" flush>
            {attention.length === 0 ? (
              <EmptyState icon={<CheckCircle2 size={28} />} title="All services operational">
                No component is down, degraded or impacted by a failing dependency.
              </EmptyState>
            ) : (
              <table>
                <tbody>
                  {attention.slice(0, 8).map((c) => (
                    <tr key={c.id}>
                      <td><Link to={`/services?id=${c.id}`}>{c.name}</Link> <SyntheticBadge show={c.is_synthetic} /></td>
                      <td className="right"><StatusBadge status={c.health!.effective_status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>

        <div className="grid cols-2">
          <Card title="Open incidents" flush actions={<Link className="small" to="/incidents">All incidents</Link>}>
            {data.open_incidents.length === 0 ? (
              <EmptyState icon={<Siren size={28} />} title="No open incidents">
                Incidents are opened automatically when a monitor fails its configured number of consecutive checks.
              </EmptyState>
            ) : (
              <table>
                <tbody>
                  {data.open_incidents.map((i) => (
                    <tr key={i.id}>
                      <td><SeverityBadge severity={i.severity} /></td>
                      <td>
                        <Link to={`/incidents/${i.id}`}>{i.title}</Link> <SyntheticBadge show={i.is_synthetic} />
                        {i.parent_id && <div className="muted small">Linked to #{i.parent_id}</div>}
                      </td>
                      <td><IncidentStatusBadge status={i.status} /></td>
                      <td className="right muted nowrap">{between(i.started_at, null)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
          <Card title="Top risks" flush actions={<Link className="small" to="/risks">Risk intelligence</Link>}>
            {data.top_risks.length === 0 ? (
              <EmptyState icon={<ShieldAlert size={28} />} title="No open risk findings">
                Risk analysis runs periodically over recorded data and reports patterns that tend to precede outages.
              </EmptyState>
            ) : (
              <table>
                <tbody>
                  {data.top_risks.map((r) => (
                    <tr key={r.id}>
                      <td><SeverityBadge severity={r.severity} /></td>
                      <td><Link to={`/risks?id=${r.id}`}>{r.title}</Link> <SyntheticBadge show={r.is_synthetic} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>

        <Card title="Recent failed or degraded checks" flush>
          {data.recent_results.length === 0 ? (
            <EmptyState icon={<Activity size={28} />} title="No failed checks recorded" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>When</th><th>Monitor</th><th>Status</th><th>Message</th></tr></thead>
                <tbody>
                  {data.recent_results.map((r, i) => (
                    <tr key={i}>
                      <td className="nowrap muted">{timeAgo(r.checked_at)}</td>
                      <td><Link to={`/monitoring/${r.monitor_id}`}>{r.monitor}</Link></td>
                      <td><StatusBadge status={r.status} /></td>
                      <td className="muted">{r.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
