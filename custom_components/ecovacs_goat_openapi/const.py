"""Constants for the Ecovacs GOAT Open API integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "ecovacs_goat_openapi"

CONF_API_KEY = "api_key"
CONF_BASE_URL = "base_url"
CONF_NICKNAME = "nickname"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_BASE_URL = "https://open.ecovacs.com"
DEFAULT_SCAN_INTERVAL_SECONDS = 60
DEFAULT_SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS)

ENDPOINT_ROBOT_CTL = "robot/ctl"
ENDPOINT_ROBOT_DEVICE_LIST = "robot/deviceList"

API_TIMEOUT_SECONDS = 15

ATTR_CLEAN_STATE = "clean_state"
ATTR_CHARGE_STATE = "charge_state"
ATTR_STATION_STATE = "station_state"
ATTR_LAST_RESPONSE = "last_response"
ATTR_NICKNAME = "nickname"
ATTR_ERROR_REASON = "error_reason"
ATTR_ERROR_DETAILS = "error_details"

CLEAN_ACTION_START = "s"
CLEAN_ACTION_RESUME = "r"

# Kept for decoding only; v0.3.0 deliberately does not expose pause/dock/stop.
CLEAN_ACTION_PAUSE = "p"
CLEAN_ACTION_STOP = "h"
