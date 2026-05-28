"""Initialize the API sqlite database. Idempotent."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.store import init_db
from api.config import DB_PATH


if __name__ == "__main__":
    init_db()
    print(f"initialized {DB_PATH}")
