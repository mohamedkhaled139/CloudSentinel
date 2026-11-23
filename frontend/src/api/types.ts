export type Role = "admin" | "operator" | "viewer";
export type CheckStatus = "up" | "degraded" | "down" | "unknown";
export type HealthStatus = CheckStatus | "impacted" | "unmonitored";
export type IncidentStatus = "detected" | "investigating" | "resolved";
export type Severity = "info" | "minor" | "major" | "critical";

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface ComponentHealth {
  own_status: HealthStatus;
  effective_status: HealthStatus;
  impacted_by: number[];
  monitor_count: number;
}

export interface Component {
  id: number;
  name: string;
  kind: string;
  description: string;
  owner: string;
  environment: string;
  tags: string[];
  criticality: "low" | "medium" | "high" | "critical";
  is_synthetic: boolean;
  created_at: string;
  health: ComponentHealth | null;
  depends_on: number[];
  dependents: number[];
  open_incidents: number;
  availability_24h: number | null;
}

export interface Dependency {
  id: number;
  source_id: number;
  target_id: number;
  description: string;
}

export interface Monitor {
  id: number;
  name: string;
  kind: string;
  component_id: number | null;
  integration_id: number | null;
  config: Record<string, unknown>;
  enabled: boolean;
  interval_seconds: number;
  timeout_seconds: number;
  failure_threshold: number;
  recovery_threshold: number;
  degraded_latency_ms: number | null;
  open_incident_on_degraded: boolean;
  last_status: CheckStatus;
  last_message: string;
  last_checked_at: string | null;
  consecutive_failures: number;
  consecutive_successes: number;
  is_synthetic: boolean;
  created_at: string;
}

export interface CheckResult {
  id: number;
  monitor_id: number;
  checked_at: string;
  status: CheckStatus;
  latency_ms: number | null;
  message: string;
  details: Record<string, unknown>;
  is_synthetic: boolean;
}

export interface SeriesPoint {
  t: string;
  availability: number | null;
  latency_ms?: number | null;
  status?: CheckStatus | null;
  checks: number;
}

export interface MonitorStats {
  window_hours: number;
  checks: number;
  availability_pct: number | null;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  series: SeriesPoint[];
}

export interface IncidentEvent {
  id: number;
  at: string;
  kind: string;
  message: string;
  actor: string;
  data: Record<string, unknown>;
}

export interface Incident {
  id: number;
  title: string;
  status: IncidentStatus;
  severity: Severity;
  component_id: number | null;
  monitor_id: number | null;
  parent_id: number | null;
  summary: string;
  started_at: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
  resolution_notes: string;
  is_synthetic: boolean;
  component_name: string | null;
  monitor_name: string | null;
  child_count: number;
}

export interface IncidentDetail extends Incident {
  events: IncidentEvent[];
  children: Incident[];
  parent: Incident | null;
  evidence_results: CheckResult[];
  affected_components: number[];
}

export interface Risk {
  id: number;
  fingerprint: string;
  category: string;
  severity: Severity;
  title: string;
  description: string;
  recommendation: string;
  evidence: Record<string, unknown>;
  component_id: number | null;
  monitor_id: number | null;
  status: "open" | "dismissed" | "resolved";
  first_seen_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  dismissed_by: string | null;
  is_synthetic: boolean;
}

export interface JsonSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  const?: unknown;
  format?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  anyOf?: JsonSchema[];
  additionalProperties?: boolean | JsonSchema;
  minimum?: number;
  maximum?: number;
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
}

export interface ProviderInfo {
  key: string;
  label: string;
  description: string;
  integration_kind: string | null;
  synthetic: boolean;
  config_schema: JsonSchema;
}

export interface AdapterInfo {
  kind: string;
  label: string;
  description: string;
  config_schema: JsonSchema;
  secret_schema: JsonSchema;
}

export interface Integration {
  id: number;
  name: string;
  kind: string;
  config: Record<string, unknown>;
  secret_fields_set: string[];
  enabled: boolean;
  status: "ok" | "error" | "unknown";
  last_checked_at: string | null;
  last_error: string | null;
  created_at: string;
  monitor_count: number;
}

export interface Agent {
  id: number;
  name: string;
  token_prefix: string;
  hostname: string | null;
  agent_version: string | null;
  last_seen_at: string | null;
  created_at: string;
}

export interface Overview {
  generated_at: string;
  demo_mode: boolean;
  contains_synthetic_data: boolean;
  components: Record<string, number>;
  monitors: Record<string, number>;
  availability_24h: number | null;
  availability_7d: number | null;
  open_incidents: Incident[];
  open_risks: Record<string, number>;
  top_risks: Risk[];
  recent_results: { checked_at: string; status: CheckStatus; message: string; monitor: string; monitor_id: number }[];
  availability_trend: SeriesPoint[];
}

export interface ReportData {
  start: string;
  end: string;
  generated_at: string;
  overall_availability_pct: number | null;
  total_checks: number;
  incident_count: number;
  resolved_incident_count: number;
  mttr_minutes: number | null;
  total_incident_minutes: number;
  incidents_by_severity: Record<string, number>;
  monitors: {
    monitor_id: number;
    monitor_name: string;
    kind: string;
    component: string | null;
    checks: number;
    failed: number;
    availability_pct: number | null;
    avg_latency_ms: number | null;
    is_synthetic: boolean;
  }[];
  components: {
    component_id: number;
    component: string;
    criticality: string;
    availability_pct: number | null;
    incidents: number;
    downtime_minutes: number;
  }[];
  top_problems: { monitor_id: number; monitor: string; component: string | null; incidents: number; failed_checks: number }[];
  incidents: {
    id: number;
    title: string;
    severity: Severity;
    status: IncidentStatus;
    component: string | null;
    started_at: string;
    resolved_at: string | null;
    duration_minutes: number;
    correlated_to: number | null;
    is_synthetic: boolean;
  }[];
  open_risks: number;
  contains_synthetic_data: boolean;
  notes: string[];
}

export interface AppSettings {
  retention_check_results_days: number;
  retention_agent_reports_days: number;
  retention_resolved_incidents_days: number;
  retention_resolved_risks_days: number;
  auto_resolve_incidents: boolean;
  risk_lookback_days: number;
  recurring_failure_min_episodes: number;
  latency_regression_ratio: number;
  latency_min_samples: number;
  restart_min_count: number;
  stale_interval_multiplier: number;
  availability_target_pct: number;
  tls_expiry_risk_days: number;
}

export interface SystemInfo {
  version: string;
  environment: string;
  database: string;
  demo_mode: boolean;
  scheduler_enabled: boolean;
  scheduler_healthy: boolean | null;
  allow_private_targets: boolean;
}

export interface Page<T> {
  total: number;
  items: T[];
}
