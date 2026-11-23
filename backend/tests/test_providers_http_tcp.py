import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from cloudsentinel.models import CheckStatus
from cloudsentinel.providers.base import CheckContext
from cloudsentinel.providers.http import HttpConfig, HttpProvider
from cloudsentinel.providers.tcp import TcpConfig, TcpProvider


def ctx(cfg, timeout=2.0, degraded=None, allow_private=True):
    return CheckContext(
        monitor_id=1,
        config=cfg,
        timeout_seconds=timeout,
        degraded_latency_ms=degraded,
        allow_private_targets=allow_private,
    )


def run(coro):
    return asyncio.run(coro)


def mock_provider(handler):
    return HttpProvider(transport=httpx.MockTransport(handler))


def test_http_up():
    p = mock_provider(lambda req: httpx.Response(200, text="ok"))
    out = run(p.check(ctx(HttpConfig(url="http://svc.test/health"))))
    assert out.status == CheckStatus.up
    assert out.details["status_code"] == 200
    assert out.latency_ms is not None


def test_http_unexpected_status():
    p = mock_provider(lambda req: httpx.Response(503))
    out = run(p.check(ctx(HttpConfig(url="http://svc.test/"))))
    assert out.status == CheckStatus.down
    assert "503" in out.message


def test_http_expected_codes_and_body():
    p = mock_provider(lambda req: httpx.Response(404, text="nope"))
    out = run(p.check(ctx(HttpConfig(url="http://svc.test/", expected_status_codes=[404]))))
    assert out.status == CheckStatus.up
    p = mock_provider(lambda req: httpx.Response(200, text="status: degraded"))
    out = run(p.check(ctx(HttpConfig(url="http://svc.test/", body_contains="status: ok"))))
    assert out.status == CheckStatus.down


def test_http_connect_error_and_timeout():
    def boom(req):
        raise httpx.ConnectError("refused")

    assert run(mock_provider(boom).check(ctx(HttpConfig(url="http://svc.test/")))).status == CheckStatus.down

    def slow(req):
        raise httpx.ReadTimeout("slow")

    out = run(mock_provider(slow).check(ctx(HttpConfig(url="http://svc.test/"))))
    assert out.status == CheckStatus.down and "Timed out" in out.message


def test_http_latency_threshold_marks_degraded():
    p = mock_provider(lambda req: httpx.Response(200))
    out = run(p.check(ctx(HttpConfig(url="http://svc.test/"), degraded=0.000001)))
    assert out.status == CheckStatus.degraded


def test_http_config_validation():
    with pytest.raises(ValueError):
        HttpConfig(url="ftp://x")
    with pytest.raises(ValueError):
        HttpConfig(url="http://x", expected_status_codes=[700])


def test_http_blocks_private_targets_when_disabled():
    p = mock_provider(lambda req: httpx.Response(200))
    out = run(p.check(ctx(HttpConfig(url="http://127.0.0.1/"), allow_private=False)))
    assert out.status == CheckStatus.unknown


def test_http_against_real_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"healthy")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        out = run(HttpProvider().check(ctx(HttpConfig(url=url, body_contains="healthy"))))
        assert out.status == CheckStatus.up
    finally:
        server.shutdown()


async def _tcp_server(banner: bytes | None):
    async def handle(reader, writer):
        if banner:
            writer.write(banner)
            await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


def test_tcp_up_and_banner():
    async def scenario():
        server = await _tcp_server(b"SSH-2.0-OpenSSH_9.6\r\n")
        port = server.sockets[0].getsockname()[1]
        async with server:
            ok = await TcpProvider().check(
                ctx(TcpConfig(host="127.0.0.1", port=port, expect_banner="SSH-2.0"))
            )
            bad = await TcpProvider().check(ctx(TcpConfig(host="127.0.0.1", port=port, expect_banner="SMTP")))
        return ok, bad

    ok, bad = run(scenario())
    assert ok.status == CheckStatus.up and "SSH" in ok.details["banner"]
    assert bad.status == CheckStatus.down


def test_tcp_refused():
    async def scenario():
        server = await _tcp_server(None)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        return await TcpProvider().check(ctx(TcpConfig(host="127.0.0.1", port=port)))

    out = run(scenario())
    assert out.status == CheckStatus.down
    assert "Connection failed" in out.message


def test_tcp_timeout():
    # 10.255.255.1 is a non-routable address: connection attempts hang until the timeout.
    out = run(TcpProvider().check(ctx(TcpConfig(host="10.255.255.1", port=81), timeout=0.3)))
    assert out.status == CheckStatus.down
