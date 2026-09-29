"""Explicit server-side adapter selection; importing this module needs no SDK."""
from dataclasses import dataclass
from importlib import import_module
from typing import Mapping
from types import MappingProxyType

DEFAULT_ADAPTER = "xtquant_2.0.6.1"


@dataclass(frozen=True)
class Adapter:
    name: str
    sdk_version: str
    package: str
    requires_native_sdk: bool = True

    def connection_type(self):
        return import_module(self.package + ".connection").ConnectionManager

    def create_providers(self, connection=None, workers=8, environment=None):
        factory = import_module(self.package + ".factory")
        return factory.create_providers(connection, workers, environment)

    def storage_strategies(self, providers):
        factory = import_module(self.package + ".factory")
        return factory.storage_strategies(providers)

    def create_debug(self, connection):
        return import_module(self.package + ".debug").DebugGateway(connection)

    def discover(self):
        if not self.requires_native_sdk:
            raise ValueError('SDK discovery is unavailable for the BigQMT strategy bridge')
        return import_module(self.package + ".discovery").build_api_surface()


ADAPTERS: Mapping[str, Adapter] = MappingProxyType({
    DEFAULT_ADAPTER: Adapter(DEFAULT_ADAPTER, "2.0.6.1",
                             "qmt_rpyc.adapters.xtquant_2_0_6_1"),
    "bigqmt": Adapter("bigqmt", "strategy-bridge-v4", "qmt_rpyc.adapters.bigqmt", False),
})


def select_adapter(name=DEFAULT_ADAPTER):
    if name not in ADAPTERS:
        raise ValueError("Unknown QMT_RPYC_ADAPTER {!r}; available: {}".format(
            name, ", ".join(ADAPTERS)))
    return ADAPTERS[name]
