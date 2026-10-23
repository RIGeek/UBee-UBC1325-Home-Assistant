# Ubee Cable Modem for Home Assistant

A HACS custom integration that polls the signal status page of a Ubee DOCSIS 3.1 cable modem (built and tested against the **UBC1325CA**) and exposes it as Home Assistant sensors, so intermittent dropouts can be correlated with RF conditions.

It reads `http://192.168.100.1/cgi-bin/cmconnectionstatus.php` (configurable) and parses:

- Startup procedure (connectivity state, boot state, primary downstream, config file, security)
- Every downstream channel (SC-QAM and OFDM): frequency, width, SNR/MER, receive level, modulation, unerrored / corrected / uncorrectable codewords
- Every upstream channel (SC-QAM and OFDMA): frequency, width, transmit level, modulation
- CM IP address, DHCP lease duration / time remaining, modem system time

## Installation

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/RIGeek/UBee-UBC1325-Home-Assistant`, category **Integration**.
2. Install **Ubee Cable Modem**, restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *Ubee Cable Modem*.

Manual install: copy `custom_components/ubee_modem` into your `config/custom_components/`.

Home Assistant must be able to reach `192.168.100.1`. If HA sits behind your own router/firewall, that usually works as long as the firewall routes 192.168.100.0/24 out the WAN port (most do). If not, add a static route or an outbound NAT rule for it.

## Configuration

| Field | Default | Notes |
|---|---|---|
| Host | `192.168.100.1` | |
| Username / Password | blank | Leave blank if the status page loads without logging in |
| Status page path | `/cgi-bin/cmconnectionstatus.php` | |
| Use HTTPS | off | Certificate is not verified (modems use self-signed certs) |

**Options** (Configure button): polling interval (default 60 s, min 10 s) and the *Uncorrectable errors* alert threshold (default 10 codewords/min).

### Authentication

Ubee firmware varies, so the client adapts on each poll:

1. Fetch the status page directly (re-using any session cookie).
2. If the modem answers **HTTP 401** (the browser pop-up login the UBC1325CA uses), retry with HTTP **Basic** or **Digest** auth, whichever the modem's challenge asks for.
3. If it serves a **login form** instead of the status page, submit that form with your credentials (hidden fields such as CSRF tokens are preserved), then fetch the status page again.

The *Reachable* binary sensor's `auth_method` attribute shows which path worked (`none`, `basic`, `digest` or `form`).

To check from a terminal which scheme your modem uses, and that your credentials work:

```bash
curl -sI http://192.168.100.1/cgi-bin/cmconnectionstatus.php | grep -i www-authenticate
curl -s --anyauth -u 'USER:PASS' http://192.168.100.1/cgi-bin/cmconnectionstatus.php | grep -c "Downstream Channel Status"   # 1 = success
``` If the modem's login is JavaScript-driven (e.g. it hashes the password client-side), step 3 will fail with *invalid auth* — open the browser dev tools → Network tab, log in, and file an issue with the login request (redact the password) so it can be supported.

## Entities

Modem-wide (device **Ubee Cable Modem**):

| Entity | Notes |
|---|---|
| `binary_sensor.*_reachable` | Off whenever a poll fails. Never goes *unavailable*, so modem reboots / LAN-side drops are visible in history. Attributes: `unreachable_count`, `last_unreachable` |
| `binary_sensor.*_operational` | Connectivity State = OK / Operational and Boot State = OK |
| `binary_sensor.*_channel_lock_problem` | Any DS/US channel not locked |
| `binary_sensor.*_uncorrectable_errors` | Uncorrectable rate above the threshold |
| `sensor.*_uncorrectable_codeword_rate` | Codewords/min since last poll — **the key metric for intermittent problems**. Attribute `worst_channels` shows which channels took the hits |
| `sensor.*_corrected_codeword_rate` | Codewords/min since last poll |
| `sensor.*_corrected_codewords`, `*_uncorrectable_codewords` | Totals across all DS channels (`total_increasing`, so modem reboots are handled) |
| `sensor.*_downstream_snr_minimum` / `_average` | `channel_id` attribute on min |
| `sensor.*_downstream_power_minimum` / `_maximum` | dBmV |
| `sensor.*_upstream_transmit_power_minimum` / `_maximum` | dBmV |
| `sensor.*_downstream_channels_locked`, `*_upstream_channels_locked` | Counts, with SC-QAM/OFDM breakdown |
| `sensor.*_connectivity_state`, `*_boot_state` | |
| Diagnostic | Primary DS frequency, security, provisioning mode, DHCP lease obtained / expires, modem IPv4 + config file + system time (disabled by default) |

Per channel (created automatically as channels appear; keyed by **channel ID**, so they go *unavailable* if the CMTS moves you off a channel):

- `sensor.*_ds_ch_<id>_snr`, `sensor.*_ds_ch_<id>_power` — enabled
- `sensor.*_ds_ch_<id>_corrected`, `sensor.*_ds_ch_<id>_uncorrectable` — **disabled by default** (enable the ones you care about; 26 channels × 4 sensors adds up in the recorder). Attribute `since_last_poll`.
- `sensor.*_us_ch_<id>_transmit_power`
- OFDM channels are named `ds_ofdm_ch_<id>_*` / `us_ofdma_ch_<id>_*`.

### Events

| Event | When | Data |
|---|---|---|
| `ubee_modem_unreachable` | First failed poll after a good one | `reason` |
| `ubee_modem_recovered` | First good poll after failures | `down_seconds` |
| `ubee_modem_counters_reset` | Codeword totals went backwards (modem rebooted / re-registered) | previous/current totals |

## Example automations

Log every dropout with RF context:

```yaml
automation:
  - alias: "Modem: log dropout"
    triggers:
      - trigger: event
        event_type: ubee_modem_recovered
    actions:
      - action: logbook.log
        data:
          name: Cable modem
          message: >
            Back after {{ trigger.event.data.down_seconds | round(0) }} s.
            SNR min {{ states('sensor.ubee_cable_modem_downstream_snr_minimum') }} dB,
            US TX max {{ states('sensor.ubee_cable_modem_upstream_transmit_power_maximum') }} dBmV.

  - alias: "Modem: uncorrectable burst"
    triggers:
      - trigger: state
        entity_id: binary_sensor.ubee_cable_modem_uncorrectable_errors
        to: "on"
    actions:
      - action: notify.notify
        data:
          message: >
            Uncorrectables {{ states('sensor.ubee_cable_modem_uncorrectable_codeword_rate') }}/min
            (worst: {{ state_attr('sensor.ubee_cable_modem_uncorrectable_codeword_rate','worst_channels') }})
```

Pair it with the built-in **Ping** integration against an upstream target (e.g. your CMTS gateway or 1.1.1.1). *Ping down + modem reachable + operational* points at the plant/ISP; *modem unreachable* points at a modem reboot or the LAN side.

## Interpreting the numbers (rough DOCSIS 3.1 guidelines)

| Metric | Good | Marginal | Bad |
|---|---|---|---|
| DS receive level | −7 to +7 dBmV | ±7–10 | beyond ±10 / ±15 |
| DS SNR, QAM256 | ≥ 33 dB | 30–33 | < 30 |
| OFDM MER | ≥ 34 dB | 30–34 | < 30 |
| US transmit (ATDMA, 3+ channels) | 35–48 dBmV | 48–51 | > 51 |

Uncorrectables that rise **on every channel at once** while SNR looks fine usually mean impulse noise / ingress (loose or corroded connector, cracked drop, a bad splitter, or a neighbour's leak), which is exactly the kind of thing that shows up as bursts in the rate sensor.

## Development

```bash
pip install pytest-homeassistant-custom-component
pytest
```

Diagnostics (device page → ⋮ → Download diagnostics) include the parsed data and the raw status page HTML with IPs and credentials redacted — attach it when reporting a parsing problem with another Ubee model.
