"""Run the gateway locally: `python -m artie_gateway.devserver`.

Serves `POST /v1/linkedin` on http://127.0.0.1:3000 with the same handler Vercel
runs. Set `ART_GATEWAY_ALLOW_MEMORY_STORE=1` to use in-process counters instead
of a KV store. This is a development convenience, not a production server.
"""
from __future__ import annotations

import logging
import os
from http.server import ThreadingHTTPServer

from .adapter import GatewayHandler

ROUTES = ("/v1/linkedin", "/api/v1/linkedin")


class DevHandler(GatewayHandler):
    def _serve(self, method: str) -> None:
        if self.path.split("?")[0] not in ROUTES:
            self._send_not_found()
            return
        super()._serve(method)

    def _send_not_found(self) -> None:
        payload = b'{"error": {"code": "not_found", "message": "Use POST /v1/linkedin."}}'
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    port = int(os.environ.get("PORT", "3000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), DevHandler)
    print(f"artie-gateway on http://127.0.0.1:{port}/v1/linkedin")
    server.serve_forever()


if __name__ == "__main__":
    main()
