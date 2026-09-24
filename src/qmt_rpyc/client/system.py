
from qmt_rpyc.contracts.common import EmptyRequest
from qmt_rpyc.contracts.system import Capabilities, Health

from .base import _API


class SystemAPI(_API):
    def get_health(self) -> Health:
        return self._call("system.get_health", EmptyRequest())

    def get_capabilities(self) -> Capabilities:
        return self._call("system.get_capabilities", EmptyRequest())
