"""Config flow, auth paths, entities and rate logic."""

from __future__ import annotations

import re

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.ubee_modem.const import (
    CONF_STATUS_PATH,
    CONF_USE_SSL,
    DEFAULT_STATUS_PATH,
    DOMAIN,
    EVENT_COUNTERS_RESET,
)

URL = "http://192.168.100.1/cgi-bin/cmconnectionstatus.php"
LOGIN_PAGE = """<html><body><form action="/goform/login" method="post">
<input type="hidden" name="token" value="t1">
<input type="text" name="loginUsername"><input type="password" name="loginPassword">
</form></body></html>"""

DATA = {
    CONF_HOST: "192.168.100.1",
    CONF_USERNAME: "user",
    CONF_PASSWORD: "secret",
    CONF_STATUS_PATH: DEFAULT_STATUS_PATH,
    CONF_USE_SSL: False,
}


def _bump(html: str, corr_add: int, unc_add: int) -> str:
    """Raise channel 20's corrected/uncorrectable counters."""
    return html.replace("<td>2784722</td>", f"<td>{2784722 + corr_add}</td>").replace(
        "<td>2046679</td>", f"<td>{2046679 + unc_add}</td>"
    )


async def _setup(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_config_flow_no_auth(hass: HomeAssistant, aioclient_mock, status_html):
    aioclient_mock.get(URL, text=status_html)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DATA, CONF_USERNAME: "", CONF_PASSWORD: ""}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.ubee_cable_modem_reachable").state == "on"
    assert hass.states.get("binary_sensor.ubee_cable_modem_operational").state == "on"
    assert hass.states.get("sensor.ubee_cable_modem_downstream_channels_locked").state == "26"
    assert hass.states.get("sensor.ubee_cable_modem_upstream_channels_locked").state == "4"
    snr_min = hass.states.get("sensor.ubee_cable_modem_downstream_snr_minimum")
    assert snr_min.state == "35.4" and snr_min.attributes["channel_id"] == 14
    assert hass.states.get("sensor.ubee_cable_modem_ds_ch_20_snr").state == "36.2"
    assert hass.states.get("sensor.ubee_cable_modem_ds_ofdm_ch_160_power").state == "-3.5"
    assert hass.states.get("sensor.ubee_cable_modem_us_ofdma_ch_4_transmit_power").state == "36.2"
    assert hass.states.get("sensor.ubee_cable_modem_connectivity_state").state == "Operational"
    # Rate needs two polls.
    assert hass.states.get("sensor.ubee_cable_modem_uncorrectable_codeword_rate").state == "unknown"


async def test_config_flow_cannot_connect(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.get(URL, exc=TimeoutError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_basic_auth(hass: HomeAssistant, aioclient_mock, status_html):
    calls = {"n": 0}

    async def side(method, url, data):
        calls["n"] += 1
        if calls["n"] == 1:
            return AiohttpClientMockResponse(method, url, status=401, text="no")
        return AiohttpClientMockResponse(method, url, text=status_html)

    aioclient_mock.get(URL, side_effect=side)
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    await _setup(hass, entry)
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert entry.runtime_data.client.auth_method == "basic"
    # Second request carried basic auth.
    assert aioclient_mock.mock_calls[-1][0] == "GET"


async def test_form_login(hass: HomeAssistant, aioclient_mock, status_html):
    state = {"logged_in": False}

    async def get_side(method, url, data):
        if state["logged_in"]:
            return AiohttpClientMockResponse(method, url, text=status_html)
        return AiohttpClientMockResponse(method, url, text=LOGIN_PAGE)

    async def post_side(method, url, data):
        if data.get("loginPassword") == "secret" and data.get("token") == "t1":
            state["logged_in"] = True
        return AiohttpClientMockResponse(method, url, text="ok")

    aioclient_mock.get(URL, side_effect=get_side)
    aioclient_mock.post("http://192.168.100.1/goform/login", side_effect=post_side)
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    await _setup(hass, entry)
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert entry.runtime_data.client.auth_method == "form"
    post = [c for c in aioclient_mock.mock_calls if c[0] == "POST"][0]
    assert post[2]["loginUsername"] == "user"


async def test_form_login_rejected_triggers_reauth(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.get(URL, text=LOGIN_PAGE)
    aioclient_mock.post("http://192.168.100.1/goform/login", text="bad")
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_rates_unreachable_and_reset(hass: HomeAssistant, aioclient_mock, status_html):
    pages = [status_html, _bump(status_html, 600, 120)]
    mode = {"fail": False}

    async def side(method, url, data):
        if mode["fail"]:
            raise TimeoutError
        return AiohttpClientMockResponse(method, url, text=pages[0])

    aioclient_mock.get(URL, side_effect=side)
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    await _setup(hass, entry)
    resets = async_capture_events(hass, EVENT_COUNTERS_RESET)
    coord = entry.runtime_data

    # Second poll with increased counters.
    pages[0] = pages[1]
    await coord.async_refresh()
    await hass.async_block_till_done()
    rate = hass.states.get("sensor.ubee_cable_modem_uncorrectable_codeword_rate")
    assert float(rate.state) > 0
    assert rate.attributes["worst_channels"] == {"ch_20": 120}

    # Modem drops off the LAN.
    mode["fail"] = True
    await coord.async_refresh()
    await hass.async_block_till_done()
    reach = hass.states.get("binary_sensor.ubee_cable_modem_reachable")
    assert reach.state == "off"
    assert reach.attributes["unreachable_count"] == 1
    assert hass.states.get("sensor.ubee_cable_modem_ds_ch_20_snr").state == "unavailable"

    # Comes back with counters reset (reboot).
    mode["fail"] = False
    pages[0] = re.sub(r"<td>(\d{7})</td>", "<td>5</td>", status_html)
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.ubee_cable_modem_reachable").state == "on"
    assert len(resets) == 1


async def test_unload(hass: HomeAssistant, aioclient_mock, status_html):
    aioclient_mock.get(URL, text=status_html)
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    await _setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED


async def test_diagnostics(hass: HomeAssistant, aioclient_mock, status_html):
    from custom_components.ubee_modem.diagnostics import async_get_config_entry_diagnostics

    aioclient_mock.get(URL, text=status_html)
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    await _setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"][CONF_PASSWORD] == "**REDACTED**"
    assert diag["status"]["ipv4"] == "**REDACTED**"
    assert len(diag["status"]["downstream"]) == 26
