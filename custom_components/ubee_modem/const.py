"""Constants for the Ubee Cable Modem integration."""

from __future__ import annotations

DOMAIN = "ubee_modem"
MANUFACTURER = "Ubee Interactive"
DEFAULT_MODEL = "UBC1325CA"

CONF_STATUS_PATH = "status_path"
CONF_USE_SSL = "use_ssl"
CONF_UNCORRECTABLE_THRESHOLD = "uncorrectable_threshold"

DEFAULT_HOST = "192.168.100.1"
DEFAULT_STATUS_PATH = "/cgi-bin/cmconnectionstatus.php"
DEFAULT_SCAN_INTERVAL = 60  # seconds
MIN_SCAN_INTERVAL = 10
DEFAULT_UNCORRECTABLE_THRESHOLD = 10.0  # uncorrectable codewords per minute

REQUEST_TIMEOUT = 20

EVENT_COUNTERS_RESET = f"{DOMAIN}_counters_reset"
EVENT_UNREACHABLE = f"{DOMAIN}_unreachable"
EVENT_RECOVERED = f"{DOMAIN}_recovered"
