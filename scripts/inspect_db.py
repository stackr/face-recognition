"""Read-only connection/schema diagnostic with sanitized output."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import Settings
from app.db.session import make_engine
from sqlalchemy import text


def main():
    engine = make_engine(Settings())
    try:
        with engine.connect() as connection:
            version = connection.scalar(text("SELECT VERSION()"))
            tables = connection.scalars(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() ORDER BY table_name"
                )
            ).all()
        print(json.dumps({"connected": True, "version": version, "tables": tables}))
    except Exception as exc:
        original = getattr(exc, "orig", None)
        code = original.args[0] if original and original.args else None
        print(json.dumps({"connected": False, "error_type": type(exc).__name__, "code": code}))
        raise SystemExit(1) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
