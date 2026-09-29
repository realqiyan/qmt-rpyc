"""Keep replies in-flight until RPyC has written them to the socket."""
import time

from rpyc.core.protocol import Connection


class ManagedConnection(Connection):
    def _dispatch_request(self, seq, raw_args):
        gate = getattr(type(self._local_root), '_request_gate', None)
        if gate is None:
            return super()._dispatch_request(seq, raw_args)
        with gate.reply():
            result = super()._dispatch_request(seq, raw_args)
            # RPyC may enqueue a reply behind another sender. Wait for the
            # queue AND current write before releasing the maintenance lease.
            while True:
                with self._sendlock:
                    if not self._send_queue:
                        break
                time.sleep(.001)
            return result
