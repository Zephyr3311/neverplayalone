"""API service configuration."""
import os

# Replace before deploy. Validators with this hotkey gain queue-management privileges.
OWNER_HOTKEY = "5PLACEHOLDER_OWNER_HOTKEY_REPLACE_BEFORE_DEPLOY"

NETUID = 490
DB_PATH = os.environ.get("NPA_DB_PATH", "npa_api.db")

# Reject signed requests older than this many seconds (replay window).
SIGNATURE_MAX_AGE = 60
