"""Native CUDA/reference lifecycle verification using public smoke samples only."""

import io
import json
import sys
import time
import uuid
from datetime import timedelta

import httpx
from api_session import ROOT, api_session, checked
from PIL import Image

sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.db.session import make_engine
from app.models import PersonFace
from app.models.foundation import utc_now
from app.services.worker_client import WorkerClient
from sqlalchemy.orm import Session


def main():
    settings = Settings()
    engine = make_engine(settings)
    worker = WorkerClient(settings)
    person_id = camera_id = None
    photos = [
        ROOT / "data/calibration/person_b_reference.jpg",
        ROOT / "data/calibration/person_b_variant.jpg",
    ]
    report = {"status": "passed", "purpose": "pipeline_smoke", "accuracy_calibrated": False}
    with api_session() as client:
        client.timeout = 45
        try:
            system = checked(client.get("/api/system/status"))
            report["checked_at"] = system["checked_at"]
            assert system["phase"] == 4
            face_info = system["worker"]["face_analysis"]
            assert face_info["status"] == "passed" and face_info["actual_device"] == "cuda"
            report["face_device"] = face_info["actual_device"]
            person_id = checked(
                client.post("/api/persons", json={"name": "PHASE4-CHECK-" + uuid.uuid4().hex[:8]})
            )["id"]
            faces = [
                checked(
                    client.post(
                        f"/api/persons/{person_id}/faces",
                        content=photo.read_bytes(),
                        headers={"Content-Type": "image/jpeg"},
                    )
                )
                for photo in photos
            ]
            assert all(face["state"] == "ready" for face in faces)
            report["registered_references"] = len(faces)
            path = f"/api/persons/{person_id}/faces/{faces[0]['id']}/image"
            with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False) as anonymous:
                assert anonymous.get(path).status_code == 401
            image = Image.open(io.BytesIO(checked_image(client, path)))
            assert image.size == (112, 112)
            report["aligned_image_size"] = list(image.size)
            for content, code in [
                (ROOT.joinpath("data/samples/zidane.jpg").read_bytes(), "multiple_faces"),
                (blank(), "no_face"),
                (b"invalid image", "invalid_image"),
            ]:
                rejected = client.post(
                    f"/api/persons/{person_id}/faces",
                    content=content,
                    headers={"Content-Type": "image/jpeg"},
                )
                assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == code
            analyzed = worker.request(
                "POST", "/internal/references/analyze", content=photos[0].read_bytes()
            ).json()
            scores = {}
            for provider in ("memory", "qdrant"):
                result = worker.request(
                    "POST",
                    "/internal/references/search",
                    json={
                        "embedding": analyzed["embedding"],
                        "allowed_person_ids": [person_id],
                        "provider": provider,
                    },
                ).json()
                assert (
                    len(result["matches"]) == 1 and result["matches"][0]["person_id"] == person_id
                )
                scores[provider] = result["matches"][0]["similarity"]
            assert abs(scores["memory"] - scores["qdrant"]) < 1e-5
            report["provider_scores"] = scores
            probe = checked(
                client.post(
                    "/api/persons/search",
                    content=photos[1].read_bytes(),
                    headers={"Content-Type": "image/jpeg"},
                )
            )
            assert any(
                item["person_id"] == person_id and item["candidate"] for item in probe["matches"]
            )
            camera_id = checked(
                client.post(
                    "/api/cameras",
                    json={"name": "PHASE4-CHECK-" + uuid.uuid4().hex[:8], "source_type": "mp4"},
                )
            )["camera_id"]
            checked(
                client.put(
                    f"/api/cameras/{camera_id}/video",
                    content=(ROOT / "data/videos/face-smoke.mp4").read_bytes(),
                    headers={"Content-Type": "video/mp4"},
                )
            )
            checked(
                client.post(
                    f"/api/cameras/{camera_id}/start", json={"source_type": "mp4", "loop": True}
                )
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                state = checked(client.get(f"/api/cameras/{camera_id}/status"))
                matches = [
                    match
                    for track in (state.get("result") or {}).get("tracks", [])
                    for match in track.get("face", {}).get("matches", [])
                    if match["person_id"] == person_id
                ]
                if matches:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("Live reference candidate unavailable")
            report["live_candidate_similarity"] = matches[0]["similarity"]
            processed_before = state["processed_frames"]
            concurrent_probe = checked(
                client.post(
                    "/api/persons/search",
                    content=photos[1].read_bytes(),
                    headers={"Content-Type": "image/jpeg"},
                )
            )
            assert any(
                item["person_id"] == person_id and item["candidate"]
                for item in concurrent_probe["matches"]
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = checked(client.get(f"/api/cameras/{camera_id}/status"))
                if state["processed_frames"] > processed_before:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("Camera stopped progressing during reference analysis")
            checked(
                client.put(
                    f"/api/persons/{person_id}", json={"name": "PHASE4-CHECK", "enabled": False}
                )
            )
            state = checked(client.get(f"/api/cameras/{camera_id}/status"))
            assert not any(
                match["person_id"] == person_id
                for track in (state.get("result") or {}).get("tracks", [])
                for match in track.get("face", {}).get("matches", [])
            )
            checked(
                client.put(
                    f"/api/persons/{person_id}", json={"name": "PHASE4-CHECK", "enabled": True}
                )
            )
            checked(client.post(f"/api/cameras/{camera_id}/stop"))
            with Session(engine) as db:
                for face in db.query(PersonFace).filter(PersonFace.person_id == person_id):
                    assert (
                        settings.reference_dir / face.image_path
                    ).stat().st_mode & 0o777 == 0o600
                    face.image_expires_at = utc_now() - timedelta(seconds=1)
                db.commit()
            assert client.get(path).status_code == 404
            checked(client.post("/api/persons/maintenance/retry"))
            assert all(
                not face["image_available"]
                for face in checked(client.get(f"/api/persons/{person_id}"))["faces"]
            )
            assert worker.request(
                "POST",
                "/internal/references/search",
                json={"embedding": analyzed["embedding"], "allowed_person_ids": [person_id]},
            ).json()["matches"]
            with Session(engine) as db:
                for face in db.query(PersonFace).filter(PersonFace.person_id == person_id):
                    face.embedding_expires_at = utc_now() - timedelta(seconds=1)
                db.commit()
            assert not worker.request(
                "POST",
                "/internal/references/search",
                json={"embedding": analyzed["embedding"], "allowed_person_ids": [person_id]},
            ).json()["matches"]
            checked(client.post("/api/persons/maintenance/retry"))
            assert not checked(client.get(f"/api/persons/{person_id}"))["faces"]
            report["checks"] = [
                "multiple_references",
                "authenticated_images",
                "no_face_rejected",
                "multiple_faces_rejected",
                "invalid_image_rejected",
                "memory_qdrant_equivalence",
                "live_candidate",
                "reference_analysis_during_live_processing",
                "disable_immediately_excluded",
                "independent_retention",
                "expired_cache_excluded",
                "physical_cleanup",
            ]
        finally:
            if camera_id:
                checked(client.post(f"/api/cameras/{camera_id}/stop"))
                checked(client.delete(f"/api/cameras/{camera_id}"))
            if person_id:
                checked(client.delete(f"/api/persons/{person_id}"))
            worker.client.close()
            engine.dispose()
    directory = ROOT / "data/reports"
    directory.mkdir(parents=True, exist_ok=True)
    directory.joinpath("phase4-native.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def checked_image(client, path):
    response = client.get(path)
    response.raise_for_status()
    return response.content


def blank():
    stream = io.BytesIO()
    Image.new("RGB", (640, 480), (128, 128, 128)).save(stream, format="JPEG")
    return stream.getvalue()


if __name__ == "__main__":
    main()
