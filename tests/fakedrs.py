"""A fake DRS for tests (observer in the combined tests). Returns responses yai-drs never returns.

- `noaccessid`: an access method with only access_url and no access_id (INT-AM-BOTH)
- `headers`: access_url carries headers (INT-HEADERS-REJECT, C-03)
- `redirect`: /objects redirects elsewhere (part of NEG-PASSPORT-SEND-UNTRUSTED)
- `pauth`: claims PassportAuth, but passport_auth_issuers lacks the issuer of alice's Visa
  (part of NEG-PASSPORT-SEND-UNTRUSTED; WES must not send the Passport)
Records each request received (and whether its body had a Passport).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PUBLIC = [{"type": "None"}]
OTHER_ISSUER = "https://nobody.example/visa/"  # does not overlap the issuer of alice's Visa


class FakeDrs:
    def __init__(self, blob_url: str, size: int):
        self.blob_url = blob_url  # content comes from the real yai-drs public delivery endpoint
        self.size = size
        self.seen: list[tuple[str, str, bytes]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def _send(self, status: int, body: Any = None, headers: dict | None = None) -> None:
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _any(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                fake.seen.append((self.command, self.path, body))
                oid = self.path.rstrip("/").split("/objects/")[-1].split("/")[0]
                if oid == "redirect":
                    self._send(302, {}, {"location": "http://127.0.0.1:9/elsewhere"})
                    return
                auth: dict[str, Any] = {"supported_types": ["None"], "passport_auth_issuers": []}
                if oid == "pauth":
                    auth = {"supported_types": ["PassportAuth"],
                            "passport_auth_issuers": [OTHER_ISSUER]}  # fmt: skip
                if self.command == "OPTIONS":
                    self._send(200, auth)
                    return
                url: dict[str, Any] = {"url": fake.blob_url}
                if oid == "headers":
                    url["headers"] = ["Authorization: Bearer CANARY-header"]
                self._send(200, {
                    "id": oid, "self_uri": f"drs://fake.yai.test/{oid}", "size": fake.size,
                    "created_time": "2026-10-17T00:00:00Z",
                    "checksums": [{"type": "sha-256", "checksum": "0" * 64}],
                    "access_methods": [{"type": "https", "access_url": url,
                                        "access_id": "a1", "authorizations": auth}],
                })  # fmt: skip

            do_GET = do_POST = do_OPTIONS = _any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.root = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def passports_received(self) -> int:
        return sum(1 for _, _, body in self.seen if b"passports" in body)

    def stop(self) -> None:
        self.server.shutdown()
