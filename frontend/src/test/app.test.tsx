import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient } from "@tanstack/react-query";
import { App } from "../App";

const user = { id: 1, email: "ops@example.com", name: "Ops", role: "operator", is_active: true, created_at: "", last_login_at: null };
const overview = {
  generated_at: new Date().toISOString(),
  demo_mode: false,
  contains_synthetic_data: false,
  components: { total: 2, up: 1, down: 1 },
  monitors: { total: 2, up: 1, down: 1 },
  availability_24h: 99.5,
  availability_7d: 99.9,
  open_incidents: [
    { id: 7, title: "db is unavailable", status: "detected", severity: "critical", component_id: 2, monitor_id: 1,
      parent_id: null, summary: "", started_at: new Date().toISOString(), acknowledged_at: null, acknowledged_by: null,
      resolved_at: null, resolved_by: null, resolution_notes: "", is_synthetic: false, component_name: "db",
      monitor_name: "db tcp", child_count: 1 },
  ],
  open_risks: { total: 0 },
  top_risks: [],
  recent_results: [],
  availability_trend: [],
};

function mockFetch(routes: Record<string, { status: number; body: unknown }>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const path = url.replace("/api/v1", "").split("?")[0];
    const key = `${init?.method ?? "GET"} ${path}`;
    const r = routes[key] ?? { status: 200, body: [] };
    return new Response(r.status === 204 ? null : JSON.stringify(r.body), { status: r.status });
  }));
  return calls;
}

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("shows first-run setup when no users exist", async () => {
    mockFetch({
      "GET /auth/me": { status: 401, body: { detail: "Not authenticated" } },
      "GET /auth/setup-status": { status: 200, body: { needs_setup: true } },
    });
    render(<App client={client()} />);
    expect(await screen.findByRole("button", { name: "Create administrator" })).toBeInTheDocument();
  });

  it("signs in and renders the overview with open incidents", async () => {
    const calls = mockFetch({
      "GET /auth/me": { status: 401, body: { detail: "Not authenticated" } },
      "GET /auth/setup-status": { status: 200, body: { needs_setup: false } },
      "POST /auth/login": { status: 200, body: { access_token: "x", user } },
      "GET /overview": { status: 200, body: overview },
      "GET /components": { status: 200, body: [] },
      "GET /system/info": { status: 200, body: { version: "0.1.0" } },
    });
    render(<App client={client()} />);
    await userEvent.type(await screen.findByLabelText("Email"), "ops@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("db is unavailable")).toBeInTheDocument();
    expect(screen.getByText("99.500%")).toBeInTheDocument();
    const login = calls.find((c) => c.url.endsWith("/auth/login"))!;
    expect((login.init?.headers as Record<string, string>)["X-CloudSentinel-Client"]).toBe("web");
    expect(login.init?.credentials).toBe("same-origin");
  });

  it("shows an error for invalid credentials", async () => {
    mockFetch({
      "GET /auth/me": { status: 401, body: { detail: "Not authenticated" } },
      "GET /auth/setup-status": { status: 200, body: { needs_setup: false } },
      "POST /auth/login": { status: 401, body: { detail: "Invalid email or password" } },
    });
    render(<App client={client()} />);
    await userEvent.type(await screen.findByLabelText("Email"), "x@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Invalid email or password"));
  });
});
