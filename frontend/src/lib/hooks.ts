import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type {
  Agent,
  AdapterInfo,
  Component,
  Integration,
  Monitor,
  Overview,
  ProviderInfo,
  SystemInfo,
} from "../api/types";

export const useOverview = () =>
  useQuery({ queryKey: ["overview"], queryFn: () => api.get<Overview>("/overview"), refetchInterval: 15000 });

export const useComponents = () =>
  useQuery({ queryKey: ["components"], queryFn: () => api.get<Component[]>("/components"), refetchInterval: 20000 });

export const useMonitors = () =>
  useQuery({ queryKey: ["monitors"], queryFn: () => api.get<Monitor[]>("/monitors"), refetchInterval: 15000 });

export const useProviders = () =>
  useQuery({
    queryKey: ["providers"],
    queryFn: () => api.get<{ providers: ProviderInfo[]; integrations: AdapterInfo[] }>("/providers"),
    staleTime: 5 * 60_000,
  });

export const useIntegrations = () =>
  useQuery({ queryKey: ["integrations"], queryFn: () => api.get<Integration[]>("/integrations") });

export const useAgents = () => useQuery({ queryKey: ["agents"], queryFn: () => api.get<Agent[]>("/agents") });

export const useSystemInfo = () =>
  useQuery({ queryKey: ["system"], queryFn: () => api.get<SystemInfo>("/system/info"), staleTime: 60_000 });

/** Mutation that invalidates the given query keys on success. */
export function useAction<TVars, TResult = unknown>(
  fn: (vars: TVars) => Promise<TResult>,
  invalidate: string[] = [],
  onSuccess?: (r: TResult) => void,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      invalidate.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      onSuccess?.(r);
    },
  });
}

export function componentNameMap(components: Component[] | undefined): Map<number, Component> {
  return new Map((components ?? []).map((c) => [c.id, c]));
}
