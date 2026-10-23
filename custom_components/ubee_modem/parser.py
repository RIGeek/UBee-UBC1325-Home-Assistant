"""Parse the Ubee cable modem status page.

Pure Python (stdlib only) so it can be unit tested without Home Assistant.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import re
from typing import Any

STATUS_MARKER = "Downstream Channel Status"

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DURATION_RE = re.compile(
    r"(?:(\d+)\s*days?)?\s*(?:(\d+)\s*hours?)?\s*(?:(\d+)\s*mins?)?\s*(?:(\d+)\s*secs?)?",
    re.I,
)
_SYSTIME_RE = re.compile(r"Current System Time:\s*</b>\s*([^<]+)", re.I)


class ParseError(Exception):
    """Raised when the page is not a recognisable status page."""


# --------------------------------------------------------------------------- #
# Generic HTML table extraction
# --------------------------------------------------------------------------- #
class _TableExtractor(HTMLParser):
    """Collect every <table> as a list of rows of cell text.

    Comments are ignored by HTMLParser by default, so the commented-out
    "not supported" rows on the Ubee page are skipped automatically.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._stack:
            if self._row:
                self._stack[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


# --------------------------------------------------------------------------- #
# Login form detection
# --------------------------------------------------------------------------- #
@dataclass
class LoginForm:
    """A login form discovered on a page."""

    action: str
    method: str
    fields: dict[str, str]
    username_field: str | None
    password_field: str | None


class _FormExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []
        self._form: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            self._form = {
                "action": a.get("action", ""),
                "method": (a.get("method") or "post").lower(),
                "inputs": [],
            }
        elif tag == "input":
            entry = {
                "name": a.get("name") or a.get("id") or "",
                "type": (a.get("type") or "text").lower(),
                "value": a.get("value", ""),
            }
            if self._form is None:
                # Inputs outside a <form>; keep them in a synthetic form.
                if not self.forms or self.forms[-1].get("synthetic") is not True:
                    self.forms.append(
                        {"action": "", "method": "post", "inputs": [], "synthetic": True}
                    )
                self.forms[-1]["inputs"].append(entry)
            else:
                self._form["inputs"].append(entry)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None


def find_login_form(html: str) -> LoginForm | None:
    """Return the first form on the page that contains a password field."""
    parser = _FormExtractor()
    parser.feed(html)
    for form in parser.forms:
        inputs = form["inputs"]
        pw = next((i for i in inputs if i["type"] == "password" and i["name"]), None)
        if pw is None:
            continue
        user = next(
            (
                i
                for i in inputs
                if i["type"] in ("text", "email", "") and i["name"]
                and re.search(r"user|login|name|account", i["name"], re.I)
            ),
            None,
        ) or next((i for i in inputs if i["type"] in ("text", "email") and i["name"]), None)
        fields = {
            i["name"]: i["value"]
            for i in inputs
            if i["name"] and i["type"] in ("hidden", "text", "email", "password", "submit")
        }
        return LoginForm(
            action=form["action"],
            method=form["method"],
            fields=fields,
            username_field=user["name"] if user else None,
            password_field=pw["name"],
        )
    return None


# --------------------------------------------------------------------------- #
# Value helpers
# --------------------------------------------------------------------------- #
def _num(text: str | None) -> float | None:
    if not text:
        return None
    m = _NUM_RE.search(text.replace(",", ""))
    return float(m.group()) if m else None


def _int(text: str | None) -> int | None:
    val = _num(text)
    return int(val) if val is not None else None


def _hz_to_mhz(text: str | None) -> float | None:
    val = _num(text)
    return round(val / 1_000_000, 3) if val is not None else None


def parse_duration(text: str | None) -> timedelta | None:
    """Parse '5 days 20 hours 49 mins 1 secs'."""
    if not text:
        return None
    m = _DURATION_RE.search(text)
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mi, seconds=s)


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class DownstreamChannel:
    index: int | None
    channel_id: int
    lock_status: str
    channel_type: str
    bonding_status: str
    frequency_mhz: float | None
    width_mhz: float | None
    snr_db: float | None
    power_dbmv: float | None
    modulation: str
    unerrored: int | None
    corrected: int | None
    uncorrectable: int | None

    @property
    def is_ofdm(self) -> bool:
        return "OFDM" in self.channel_type.upper()

    @property
    def locked(self) -> bool:
        return self.lock_status.lower() == "locked"


@dataclass
class UpstreamChannel:
    index: int | None
    channel_id: int
    lock_status: str
    channel_type: str
    bonding_status: str
    frequency_mhz: float | None
    width_mhz: float | None
    power_dbmv: float | None
    modulation: str

    @property
    def is_ofdm(self) -> bool:
        return "OFDM" in self.channel_type.upper()

    @property
    def locked(self) -> bool:
        return self.lock_status.lower() == "locked"


@dataclass
class StartupStep:
    status: str
    comment: str


@dataclass
class ModemStatus:
    prov_mode: str | None = None
    startup: dict[str, StartupStep] = field(default_factory=dict)
    downstream: list[DownstreamChannel] = field(default_factory=list)
    upstream: list[UpstreamChannel] = field(default_factory=list)
    ipv4: str | None = None
    ipv6: str | None = None
    ipv4_lease_duration: timedelta | None = None
    ipv4_lease_expires_in: timedelta | None = None
    system_time: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Page parsing
# --------------------------------------------------------------------------- #
def _header_index(header: list[str]) -> dict[str, int]:
    return {h.lower(): i for i, h in enumerate(header)}


def _col(row: list[str], hdr: dict[str, int], *names: str) -> str | None:
    for name in names:
        for key, idx in hdr.items():
            if name in key and idx < len(row):
                return row[idx]
    return None


def _find_header_row(rows: list[list[str]], needle: str) -> int | None:
    for i, row in enumerate(rows[:3]):
        if any(needle in c.lower() for c in row):
            return i
    return None


def parse_status_page(html: str) -> ModemStatus:
    """Parse cmconnectionstatus.php into a ModemStatus."""
    if STATUS_MARKER.lower() not in html.lower():
        raise ParseError("Page does not contain the downstream channel table")

    ex = _TableExtractor()
    ex.feed(html)
    status = ModemStatus()

    for rows in ex.tables:
        if not rows:
            continue
        title = " ".join(rows[0]).lower()

        # ---- CM IP Prov Mode (key/value table) ----
        if any(r and r[0].lower().startswith("cm ip prov mode") for r in rows):
            for r in rows:
                if r and r[0].lower().startswith("cm ip prov mode") and len(r) > 1:
                    status.prov_mode = r[1]
            continue

        # ---- Startup procedure ----
        if "startup procedure" in title:
            for r in rows[1:]:
                if len(r) >= 3 and r[0].lower() != "procedure":
                    status.startup[r[0]] = StartupStep(status=r[1], comment=r[2])
            continue

        # ---- Downstream ----
        if "downstream channel status" in title:
            h = _find_header_row(rows, "channel id")
            if h is None:
                continue
            hdr = _header_index(rows[h])
            for r in rows[h + 1 :]:
                cid = _int(_col(r, hdr, "channel id"))
                if cid is None:
                    continue
                status.downstream.append(
                    DownstreamChannel(
                        index=_int(_col(r, hdr, "channel index")),
                        channel_id=cid,
                        lock_status=_col(r, hdr, "lock") or "",
                        channel_type=_col(r, hdr, "channel type") or "",
                        bonding_status=_col(r, hdr, "bonding") or "",
                        frequency_mhz=_hz_to_mhz(_col(r, hdr, "center frequency", "frequency")),
                        width_mhz=_hz_to_mhz(_col(r, hdr, "width")),
                        snr_db=_num(_col(r, hdr, "snr", "mer")),
                        power_dbmv=_num(_col(r, hdr, "receive level", "power")),
                        modulation=_col(r, hdr, "modulation") or "",
                        unerrored=_int(_col(r, hdr, "unerrored")),
                        corrected=_int(_col(r, hdr, "corrected")),
                        uncorrectable=_int(_col(r, hdr, "uncorrectable")),
                    )
                )
            continue

        # ---- Upstream ----
        if "upstream channel status" in title:
            h = _find_header_row(rows, "channel id")
            if h is None:
                continue
            hdr = _header_index(rows[h])
            for r in rows[h + 1 :]:
                cid = _int(_col(r, hdr, "channel id"))
                if cid is None:
                    continue
                status.upstream.append(
                    UpstreamChannel(
                        index=_int(_col(r, hdr, "channel index")),
                        channel_id=cid,
                        lock_status=_col(r, hdr, "lock") or "",
                        channel_type=_col(r, hdr, "channel type") or "",
                        bonding_status=_col(r, hdr, "bonding") or "",
                        frequency_mhz=_hz_to_mhz(_col(r, hdr, "center frequency", "frequency")),
                        width_mhz=_hz_to_mhz(_col(r, hdr, "width")),
                        power_dbmv=_num(_col(r, hdr, "transmit level", "power")),
                        modulation=_col(r, hdr, "modulation") or "",
                    )
                )
            continue

        # ---- CM IP address / lease ----
        if "cm ip address" in title:
            for r in rows[1:]:
                if not r:
                    continue
                key, _, val = r[0].partition("=")
                key, val = key.strip().lower(), val.strip() or None
                if key == "ipv4":
                    status.ipv4 = val
                    status.ipv4_lease_duration = parse_duration(r[1] if len(r) > 1 else None)
                    status.ipv4_lease_expires_in = parse_duration(r[2] if len(r) > 2 else None)
                elif key == "ipv6":
                    status.ipv6 = val

    m = _SYSTIME_RE.search(html)
    if m:
        try:
            status.system_time = parsedate_to_datetime(m.group(1).strip())
        except (TypeError, ValueError):
            status.system_time = None

    if not status.downstream:
        raise ParseError("No downstream channels found")
    return status
