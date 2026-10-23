"""Exercise HTTP Basic and Digest auth against a real local aiohttp server.

The UBC1325CA prompts with a browser pop-up, i.e. HTTP authentication.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.ubee_modem.api import UbeeAuthError, UbeeModemClient

USER, PASSWORD, REALM = "user", "secret", "Ubee"
PATH = "/cgi-bin/cmconnectionstatus.php"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()  # noqa: S324 - matches modem digest


def _app(scheme: str, html: str) -> web.Application:
    nonces: set[str] = set()

    def challenge() -> web.Response:
        if scheme == "basic":
            hdr = f'Basic realm="{REALM}"'
        else:
            nonce = secrets.token_hex(8)
            nonces.add(nonce)
            hdr = f'Digest realm="{REALM}", qop="auth", nonce="{nonce}", algorithm=MD5'
        return web.Response(status=401, headers={"WWW-Authenticate": hdr}, text="401")

    async def handler(request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if scheme == "basic":
            if auth != "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode():
                return challenge()
            return web.Response(text=html, content_type="text/html")
        if not auth.startswith("Digest "):
            return challenge()
        p = dict(re.findall(r'(\w+)="?([^",]+)"?', auth[7:]))
        if p.get("nonce") not in nonces or p.get("username") != USER:
            return challenge()
        ha1 = _md5(f"{USER}:{REALM}:{PASSWORD}")
        ha2 = _md5(f"GET:{p['uri']}")
        want = _md5(f"{ha1}:{p['nonce']}:{p['nc']}:{p['cnonce']}:{p['qop']}:{ha2}")
        if p.get("response") != want:
            return challenge()
        return web.Response(text=html, content_type="text/html")

    app = web.Application()
    app.router.add_get(PATH, handler)
    return app


@pytest.mark.parametrize("scheme", ["basic", "digest"])
@pytest.mark.parametrize("password,ok", [(PASSWORD, True), ("wrong", False)])
async def test_http_auth(socket_enabled, status_html, scheme, password, ok):
    server = TestServer(_app(scheme, status_html), host="127.0.0.1")
    await server.start_server()
    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
            client = UbeeModemClient(s, f"127.0.0.1:{server.port}", PATH, USER, password)
            if ok:
                for _ in range(3):  # repeated polls keep working
                    status = await client.async_get_status()
                    assert len(status.downstream) == 26
                assert client.auth_method == scheme
            else:
                with pytest.raises(UbeeAuthError):
                    await client.async_get_status()
    finally:
        await server.close()


async def test_http_auth_without_credentials(socket_enabled, status_html):
    server = TestServer(_app("basic", status_html), host="127.0.0.1")
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as s:
            client = UbeeModemClient(s, f"127.0.0.1:{server.port}", PATH)
            with pytest.raises(UbeeAuthError):
                await client.async_get_status()
    finally:
        await server.close()
