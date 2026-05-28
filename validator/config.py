"""Validator + CLI shared configuration."""
import os

# Replace before deploy. The validator running with this hotkey runs the queue-management loop.
OWNER_HOTKEY = "5PLACEHOLDER_OWNER_HOTKEY_REPLACE_BEFORE_DEPLOY"

NETUID = 490
NETWORK = os.environ.get("NPA_NETWORK", "test")

API_URL = os.environ.get("NPA_API_URL", "https://api.neverplayalone.ai")

# Per-duel evaluation budget.
TASKS_PER_DUEL = int(os.environ.get("NPA_TASKS_PER_DUEL", "5"))
TRIALS_PER_TASK = int(os.environ.get("NPA_TRIALS_PER_TASK", "3"))

# Challenger must beat king's aggregate score by at least DETHRONE_DELTA to take the throne.
DETHRONE_DELTA = float(os.environ.get("NPA_DETHRONE_DELTA", "1.0"))

# On-chain score commitment payload version. Bump if the payload schema changes.
COMMIT_VERSION = 1

# Poll cadence for the main loop.
LOOP_POLL_SECONDS = int(os.environ.get("NPA_LOOP_POLL_SECONDS", "12"))

# Where cloned miner repos live (auto-pruned).
CLONE_ROOT = os.environ.get("NPA_CLONE_ROOT", "/tmp/npa_clones")
CLONE_TIMEOUT_SECONDS = 180
SINGLE_TASK_TIMEOUT_SECONDS = 240
