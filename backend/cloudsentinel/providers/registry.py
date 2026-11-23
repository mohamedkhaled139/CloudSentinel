"""Registry of available check providers and integration adapters."""

from __future__ import annotations

from cloudsentinel.providers.agent import AgentProvider
from cloudsentinel.providers.base import IntegrationAdapter, Provider
from cloudsentinel.providers.http import HttpProvider
from cloudsentinel.providers.kubernetes import KubernetesAdapter, KubernetesProvider
from cloudsentinel.providers.prometheus import PrometheusAdapter, PrometheusProvider
from cloudsentinel.providers.synthetic import SyntheticProvider
from cloudsentinel.providers.tcp import TcpProvider


class Registry:
    def __init__(self) -> None:
        self.providers: dict[str, Provider] = {}
        self.adapters: dict[str, IntegrationAdapter] = {}

    def register_provider(self, provider: Provider) -> None:
        self.providers[provider.key] = provider

    def register_adapter(self, adapter: IntegrationAdapter) -> None:
        self.adapters[adapter.kind] = adapter

    def provider(self, key: str) -> Provider:
        try:
            return self.providers[key]
        except KeyError:
            raise KeyError(f"unknown monitor kind '{key}'") from None

    def adapter(self, kind: str) -> IntegrationAdapter:
        try:
            return self.adapters[kind]
        except KeyError:
            raise KeyError(f"unknown integration kind '{kind}'") from None


def default_registry() -> Registry:
    reg = Registry()
    reg.register_provider(HttpProvider())
    reg.register_provider(TcpProvider())
    reg.register_provider(PrometheusProvider())
    reg.register_provider(KubernetesProvider())
    reg.register_provider(AgentProvider())
    reg.register_provider(SyntheticProvider())
    reg.register_adapter(PrometheusAdapter())
    reg.register_adapter(KubernetesAdapter())
    return reg


registry = default_registry()
