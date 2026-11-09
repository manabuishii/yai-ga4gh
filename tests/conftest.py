from __future__ import annotations

import pytest

from tests.servers import Server


@pytest.fixture
def serve(tmp_path):
    started: list[Server] = []

    def _serve(service: str, config: dict | None = None, name: str | None = None) -> Server:
        server = Server(service, config or {}, tmp_path / (name or service)).start()
        started.append(server)
        return server

    yield _serve
    for s in started:
        s.stop()
