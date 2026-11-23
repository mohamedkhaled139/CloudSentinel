import { lazy, Suspense, type ComponentType } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { AuthProvider, useAuth } from "./lib/auth";
import { LoginPage } from "./pages/Login";

// Pages are code-split so the topology (React Flow) and chart libraries load on demand.
const page = <K extends string>(loader: () => Promise<Record<K, ComponentType>>, name: K) =>
  lazy(() => loader().then((m) => ({ default: m[name] })));
const OverviewPage = page(() => import("./pages/Overview"), "OverviewPage");
const ServicesPage = page(() => import("./pages/Services"), "ServicesPage");
const TopologyPage = page(() => import("./pages/Topology"), "TopologyPage");
const MonitoringPage = page(() => import("./pages/Monitoring"), "MonitoringPage");
const MonitorDetailPage = page(() => import("./pages/Monitoring"), "MonitorDetailPage");
const IncidentsPage = page(() => import("./pages/Incidents"), "IncidentsPage");
const IncidentDetailPage = page(() => import("./pages/Incidents"), "IncidentDetailPage");
const RisksPage = page(() => import("./pages/Risks"), "RisksPage");
const ReportsPage = page(() => import("./pages/Reports"), "ReportsPage");
const IntegrationsPage = page(() => import("./pages/Integrations"), "IntegrationsPage");
const SettingsPage = page(() => import("./pages/Settings"), "SettingsPage");

function defaultClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true, staleTime: 5000 } },
  });
}

export function AppRoutes() {
  const { user, loading } = useAuth();
  if (loading) return <Loading label="Connecting…" />;
  if (!user) return <LoginPage />;
  return (
    <Suspense fallback={<Loading />}>
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<OverviewPage />} />
        <Route path="services" element={<ServicesPage />} />
        <Route path="topology" element={<TopologyPage />} />
        <Route path="monitoring" element={<MonitoringPage />} />
        <Route path="monitoring/:id" element={<MonitorDetailPage />} />
        <Route path="incidents" element={<IncidentsPage />} />
        <Route path="incidents/:id" element={<IncidentDetailPage />} />
        <Route path="risks" element={<RisksPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
    </Suspense>
  );
}

export function App({ client = defaultClient() }: { client?: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
