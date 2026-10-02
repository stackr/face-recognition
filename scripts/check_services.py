"""Exercise the real MariaDB connection and an isolated, temporary Qdrant collection."""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import Settings
from app.db.session import make_engine
from qdrant_client import QdrantClient, models
from sqlalchemy import text


def main():
    settings = Settings()
    report = {}
    engine = make_engine(settings)
    with engine.connect() as connection:
        report["mariadb"] = {
            "version": connection.scalar(text("SELECT VERSION()")),
            "migration": connection.scalar(text("SELECT version_num FROM alembic_version")),
        }
    engine.dispose()
    client = QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key.get_secret_value() or None,
        timeout=5,
    )
    name = f"phase1_smoke_{uuid.uuid4().hex}"
    created = False
    try:
        client.create_collection(
            name, vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE)
        )
        created = True
        client.upsert(
            name,
            points=[
                models.PointStruct(
                    id=1,
                    vector=[1.0, 0.0, 0.0, 0.0],
                    payload={"person_id": 0, "face_id": 0, "model_version": "smoke-only"},
                )
            ],
            wait=True,
        )
        result = client.query_points(name, query=[1.0, 0.0, 0.0, 0.0], limit=1).points
        assert result and result[0].id == 1 and result[0].score > 0.99
        assert client.retrieve(name, ids=[1])[0].payload["model_version"] == "smoke-only"
        client.delete(name, points_selector=models.PointIdsList(points=[1]), wait=True)
        assert not client.retrieve(name, ids=[1])
        report["qdrant"] = {
            "status": "passed",
            "server_version": client.info().version,
            "operations": ["create", "upsert", "query", "retrieve", "delete"],
        }
    finally:
        if created:
            client.delete_collection(name)
        client.close()
    report["status"] = "passed"
    directory = Path(__file__).resolve().parents[1] / "data/reports"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "services.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
