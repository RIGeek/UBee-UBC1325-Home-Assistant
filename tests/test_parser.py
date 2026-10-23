"""Parser tests (no Home Assistant needed)."""

from datetime import timedelta

import pytest

from custom_components.ubee_modem.parser import (
    ParseError,
    find_login_form,
    parse_duration,
    parse_status_page,
)


def test_parse_fixture(status_html):
    s = parse_status_page(status_html)
    assert s.prov_mode == "IPv4 Only"
    assert s.startup["Connectivity State"].comment == "Operational"
    assert s.startup["Boot State"].status == "OK"
    assert len(s.downstream) == 26
    assert len(s.upstream) == 4

    first = s.downstream[0]
    assert first.channel_id == 20
    assert first.frequency_mhz == 669.0
    assert first.snr_db == 36.2
    assert first.power_dbmv == -2.0
    assert first.corrected == 2784722
    assert first.uncorrectable == 2046679
    assert not first.is_ofdm

    ofdm = [c for c in s.downstream if c.is_ofdm]
    assert [c.channel_id for c in ofdm] == [159, 160]
    assert ofdm[0].width_mhz == 190.0
    assert ofdm[1].power_dbmv == -3.5

    us4 = s.upstream[3]
    assert us4.is_ofdm and us4.width_mhz is None and us4.power_dbmv == 36.2

    assert s.ipv4 == "10.0.0.64"
    assert s.ipv6 is None
    assert s.ipv4_lease_duration == timedelta(days=5, hours=20, minutes=49, seconds=1)
    assert s.ipv4_lease_expires_in == timedelta(days=3, hours=22, minutes=36, seconds=32)
    assert s.system_time.isoformat() == "2026-10-23T16:19:23+00:00"


def test_not_status_page():
    with pytest.raises(ParseError):
        parse_status_page("<html><body>Login</body></html>")


def test_duration():
    assert parse_duration("0 days 0 hours 0 mins 0 secs") == timedelta(0)
    assert parse_duration("") is None


def test_login_form_detection():
    html = """
    <form action="/goform/login" method="post">
      <input type="hidden" name="csrf" value="abc">
      <input type="text" name="loginUsername">
      <input type="password" name="loginPassword">
      <input type="submit" value="Login">
    </form>"""
    form = find_login_form(html)
    assert form.action == "/goform/login"
    assert form.username_field == "loginUsername"
    assert form.password_field == "loginPassword"
    assert form.fields["csrf"] == "abc"
    assert find_login_form("<p>no form</p>") is None
