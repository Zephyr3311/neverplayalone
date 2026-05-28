"""Dump current API state for debugging."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import store


if __name__ == "__main__":
    print("== king ==")
    print(json.dumps(store.get_king(), indent=2))
    print("\n== queue ==")
    print(json.dumps(store.list_queue(), indent=2))
    print("\n== latest duel ==")
    print(json.dumps(store.latest_duel(), indent=2))
    print("\n== recent duels ==")
    print(json.dumps(store.list_duels(limit=10, offset=0), indent=2))
