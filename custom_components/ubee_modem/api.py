"""HTTP client for the Ubee cable modem web UI."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin

import aiohttp

from .const import REQUEST_TIMEOUT
from .parser import STATUS_MARKER, ModemStatus, ParseError, find_login_form, parse_status_page

_LOGGER = logging.getLogger(__name__)


class UbeeError(Exception):
    """Base error."""


class UbeeConnectionError(UbeeError):
    """Modem could not be reached."""


class UbeeAuthError(UbeeError):
    """Authentication failed or is required."""


class UbeeParseError(UbeeError):
    """Status page was reached but could not be parsed."""


class UbeeModemClient:
    """Fetch and parse the modem status page.

    Authentication is handled adaptively because Ubee firmware varies:
      1. Try the status page as-is (session cookie from a previous login is reused).
      2. On HTTP 401 (the browser pop-up login), retry with HTTP Basic or
         Digest auth, whichever the modem's WWW-Authenticate challenge asks for.
      3. If a login form comes back instead of the status page, submit it
         (preserving hidden fields such as CSRF tokens), then fetch again.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        status_path: str,
        username: str | None = None,
        password: str | None = None,
        use_ssl: bool = False,
    ) -> None:
        self._session = session
        self._base = f"{'https' if use_ssl else 'http'}://{host}"
        self._status_url = urljoin(self._base + "/", status_path.lstrip("/"))
        self._username = username or None
        self._password = password or None
        self._basic: aiohttp.BasicAuth | None = None
        self._digest: aiohttp.DigestAuthMiddleware | None = None
        self._last_challenge = ""
        self.last_html: str | None = None
        self.auth_method: str = "none"

    @property
    def status_url(self) -> str:
        return self._status_url

    async def _get(self, url: str) -> tuple[int, str, str]:
        kwargs: dict = {"allow_redirects": True, "ssl": False}
        if self._digest is not None:
            kwargs["middlewares"] = (self._digest,)
        elif self._basic is not None:
            kwargs["auth"] = self._basic
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(url, **kwargs) as resp:
                    text = await resp.text(errors="replace")
                    self._last_challenge = resp.headers.get("WWW-Authenticate", "")
                    return resp.status, text, str(resp.url)
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise UbeeConnectionError(f"Error contacting modem at {url}: {err}") from err

    def _enable_http_auth(self) -> None:
        """Pick Basic or Digest based on the modem's 401 challenge."""
        if self._last_challenge.strip().lower().startswith("digest"):
            # Handles the challenge/nonce exchange itself on every request.
            self._digest = aiohttp.DigestAuthMiddleware(self._username or "", self._password)
            self._basic = None
            self.auth_method = "digest"
        else:
            self._basic = aiohttp.BasicAuth(self._username or "", self._password)
            self._digest = None
            self.auth_method = "basic"

    async def _post(self, url: str, data: dict[str, str]) -> tuple[int, str]:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.post(
                    url, data=data, allow_redirects=True, ssl=False
                ) as resp:
                    return resp.status, await resp.text(errors="replace")
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise UbeeConnectionError(f"Error posting login to {url}: {err}") from err

    @staticmethod
    def _is_status_page(text: str) -> bool:
        return STATUS_MARKER.lower() in text.lower()

    async def _form_login(self, page_html: str, page_url: str) -> None:
        form = find_login_form(page_html)
        if form is None:
            raise UbeeAuthError(
                "Modem returned a page that is neither the status page nor a "
                "recognisable login form"
            )
        if not self._password:
            raise UbeeAuthError("Modem requires a login but no password is configured")

        data = dict(form.fields)
        if form.username_field:
            data[form.username_field] = self._username or ""
        if form.password_field:
            data[form.password_field] = self._password
        action = urljoin(page_url, form.action) if form.action else page_url
        _LOGGER.debug(
            "Submitting login form to %s (fields: %s)",
            action,
            [k for k in data if k != form.password_field],
        )
        if form.method == "get":
            # Rare, but some firmwares use GET forms.
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    async with self._session.get(action, params=data, ssl=False) as resp:
                        await resp.read()
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                raise UbeeConnectionError(str(err)) from err
        else:
            await self._post(action, data)
        self.auth_method = "form"

    async def async_get_status(self) -> ModemStatus:
        """Return the parsed status page, logging in if required."""
        status, text, final_url = await self._get(self._status_url)

        if status == 401:
            if not self._password:
                raise UbeeAuthError("Modem returned 401 and no credentials are configured")
            self._enable_http_auth()
            status, text, final_url = await self._get(self._status_url)
            if status == 401:
                raise UbeeAuthError(f"Modem rejected HTTP {self.auth_method} credentials")

        if not self._is_status_page(text):
            # Probably redirected to (or served) a login page.
            await self._form_login(text, final_url)
            status, text, final_url = await self._get(self._status_url)
            if not self._is_status_page(text):
                if find_login_form(text) is not None:
                    raise UbeeAuthError("Login was not accepted by the modem")
                self.last_html = text
                raise UbeeParseError(
                    f"Unexpected response from {self._status_url} (HTTP {status})"
                )

        self.last_html = text
        try:
            return parse_status_page(text)
        except ParseError as err:
            raise UbeeParseError(str(err)) from err
