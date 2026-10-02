import base64
import io
import uuid
from datetime import timedelta

import httpx
import numpy as np
import pytest
from app.core.face_data import MODEL_VERSION
from app.models import AuditLog, GalleryState, Person, PersonFace
from app.models.foundation import utc_now
from app.services.face_search import MemoryFaceSearch, Reference
from app.worker.gallery import ReferenceGallery
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session


def image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (112, 112), (127, 127, 127)).save(stream, format="JPEG")
    return stream.getvalue()


@pytest.fixture
def gallery_context(app_context, admin_headers):
    client, engine, settings = app_context
    vector = np.zeros(512, dtype=np.float32)
    vector[0] = 1
    gallery = ReferenceGallery(settings, engine, client.app.state.qdrant)
    control = {"offline": False, "rejected": None, "vector": vector, "last_revision": None}

    def transport(request):
        import json

        payload = (
            json.loads(request.content)
            if request.headers.get("content-type") == "application/json"
            else {}
        )
        if request.url.path == "/internal/gallery/reload":
            if control["offline"]:
                return httpx.Response(503)
            return httpx.Response(200, json=gallery.reload(payload["revision"]))
        if request.url.path == "/internal/references/analyze":
            if control["rejected"]:
                return httpx.Response(
                    422,
                    json={
                        "detail": {
                            "code": control["rejected"],
                            "quality": {"reasons": [control["rejected"]]},
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "quality": {"quality": 0.9},
                    "model_version": MODEL_VERSION,
                    "embedding": control["vector"].tolist(),
                    "aligned_jpeg": base64.b64encode(image_bytes()).decode(),
                },
            )
        if request.url.path == "/internal/references/search":
            return httpx.Response(
                200,
                json=gallery.search(
                    payload["embedding"], allowed_person_ids=payload["allowed_person_ids"]
                ),
            )
        return httpx.Response(404)

    client.app.state.worker.client.close()
    client.app.state.worker.client = httpx.Client(
        transport=httpx.MockTransport(transport), base_url="http://127.0.0.1:8001"
    )
    yield client, engine, settings, admin_headers, gallery, control
    gallery.close()


def create(client, headers, name="시험 인물"):
    result = client.post("/api/persons", headers=headers, json={"name": name})
    assert result.status_code == 201
    return result.json()["id"]


def upload(client, headers, person_id):
    return client.post(
        f"/api/persons/{person_id}/faces",
        headers={**headers, "Content-Type": "image/jpeg"},
        content=image_bytes(),
    )


def test_multiple_references_provider_equivalence(gallery_context):
    client, _, _, headers, gallery, control = gallery_context
    first = create(client, headers)
    second = create(client, headers, "다른 인물")
    for _ in range(3):
        assert upload(client, headers, first).status_code == 201
    control["vector"] = control["vector"].copy()
    control["vector"][0], control["vector"][1] = 0.8, 0.6
    assert upload(client, headers, second).status_code == 201
    query = np.eye(1, 512, dtype=np.float32)[0]
    memory = gallery.search(query, provider="memory")
    qdrant = gallery.search(query, provider="qdrant")
    assert [m["person_id"] for m in memory["matches"]] == [first, second]
    assert [m["person_id"] for m in qdrant["matches"]] == [first, second]
    np.testing.assert_allclose(
        [m["similarity"] for m in memory["matches"]],
        [m["similarity"] for m in qdrant["matches"]],
        atol=1e-6,
    )
    body = client.get(f"/api/persons/{first}").json()
    assert len(body["faces"]) == 3
    for face in body["faces"]:
        assert not {"embedding", "embedding_id", "embedding_encrypted", "image_path"} & face.keys()


def test_permissions_images_audit_and_search(gallery_context):
    from app.core.security import password_hasher
    from app.models import User

    from backend.tests.conftest import TEST_PASSWORD

    client, engine, _, headers, _, _ = gallery_context
    person_id = create(client, headers)
    face = upload(client, headers, person_id).json()
    path = f"/api/persons/{person_id}/faces/{face['id']}/image"
    with Session(engine) as db:
        db.add(
            User(
                username="operator",
                role="operator",
                password_hash=password_hasher.hash(TEST_PASSWORD),
            )
        )
        db.commit()
    viewer = client.post(
        "/api/auth/login", json={"username": "viewer", "password": TEST_PASSWORD}
    ).json()
    assert client.get("/api/persons").json() == []
    assert client.get(path).status_code == 403
    assert (
        client.post(
            "/api/persons/search", headers={"X-CSRF-Token": viewer["csrf_token"]}
        ).status_code
        == 403
    )
    operator = client.post(
        "/api/auth/login", json={"username": "operator", "password": TEST_PASSWORD}
    ).json()
    op_headers = {"X-CSRF-Token": operator["csrf_token"], "Content-Type": "image/jpeg"}
    assert (
        client.post("/api/persons/search", headers=op_headers, content=image_bytes()).json()[
            "matches"
        ]
        == []
    )
    auth = client.post(
        "/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD}
    ).json()
    headers = {"X-CSRF-Token": auth["csrf_token"]}
    assert (
        client.put(
            f"/api/persons/{person_id}/access",
            headers=headers,
            json={"username": "operator", "can_view": True},
        ).status_code
        == 200
    )
    operator = client.post(
        "/api/auth/login", json={"username": "operator", "password": TEST_PASSWORD}
    ).json()
    assert client.get(path).status_code == 200
    result = client.post(
        "/api/persons/search",
        headers={"X-CSRF-Token": operator["csrf_token"], "Content-Type": "image/jpeg"},
        content=image_bytes(),
    ).json()
    assert result["matches"][0]["person_id"] == person_id and result["matches"][0]["candidate"]
    assert "embedding" not in result and "aligned_jpeg" not in result
    client.post("/api/auth/logout", headers={"X-CSRF-Token": operator["csrf_token"]})
    assert client.get(path).status_code == 401
    with Session(engine) as db:
        actions = set(db.scalars(select(AuditLog.action)))
        assert {
            "person.create",
            "person.face.upload",
            "person.access.update",
            "person.image.read",
            "person.search",
        } <= actions


def test_sync_failure_disable_delete_and_retry(gallery_context, monkeypatch):
    client, engine, settings, headers, gallery, control = gallery_context
    person_id = create(client, headers)
    control["offline"] = True
    face = upload(client, headers, person_id)
    assert face.status_code == 202 and face.json()["state"] == "pending"
    assert gallery.search(control["vector"])["matches"] == []
    control["offline"] = False
    assert client.post("/api/persons/maintenance/retry", headers=headers).json()["pending"] == 0
    assert gallery.search(control["vector"])["matches"]
    real_delete = client.app.state.qdrant.delete

    def failed(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(client.app.state.qdrant, "delete", failed)
    control["offline"] = True
    assert (
        client.put(
            f"/api/persons/{person_id}",
            headers=headers,
            json={"name": "시험 인물", "enabled": False},
        ).status_code
        == 202
    )
    assert gallery.search(control["vector"])["matches"] == []
    response = client.delete(f"/api/persons/{person_id}", headers=headers)
    assert response.status_code == 202
    assert client.get(f"/api/persons/{person_id}").status_code == 404
    assert list(settings.reference_dir.glob("*.jpg"))
    control["offline"] = False
    assert client.post("/api/persons/maintenance/retry", headers=headers).json()["pending"] == 1
    monkeypatch.setattr(client.app.state.qdrant, "delete", real_delete)
    assert client.post("/api/persons/maintenance/retry", headers=headers).json()["pending"] == 0
    assert not list(settings.reference_dir.glob("*.jpg"))
    with Session(engine) as db:
        assert db.get(Person, person_id) is None
        assert not list(db.scalars(select(PersonFace)))
    assert client.app.state.qdrant.count(settings.face_collection).count == 0


def test_independent_retention_and_expired_cache(gallery_context):
    client, engine, settings, headers, gallery, control = gallery_context
    person_id = create(client, headers)
    face_id = upload(client, headers, person_id).json()["id"]
    with Session(engine) as db:
        face = db.get(PersonFace, face_id)
        face.image_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    assert client.get(f"/api/persons/{person_id}/faces/{face_id}/image").status_code == 404
    client.app.state.references.cleanup()
    assert gallery.search(control["vector"])["matches"]
    assert not list(settings.reference_dir.glob("*.jpg"))
    with Session(engine) as db:
        face = db.get(PersonFace, face_id)
        face.embedding_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    # Worker excludes expired embeddings even before maintenance changes the revision.
    assert gallery.search(control["vector"])["matches"] == []
    client.app.state.references.cleanup()
    with Session(engine) as db:
        assert db.get(PersonFace, face_id) is None
    assert client.app.state.qdrant.count(settings.face_collection).count == 0


@pytest.mark.parametrize("code", ["no_face", "multiple_faces", "quality_rejected", "invalid_image"])
def test_rejected_image_leaves_no_artifacts(gallery_context, code):
    client, engine, settings, headers, _, control = gallery_context
    person_id = create(client, headers)
    control["rejected"] = code
    response = upload(client, headers, person_id)
    assert response.status_code == 422 and response.json()["detail"]["code"] == code
    with Session(engine) as db:
        assert not list(db.scalars(select(PersonFace)))
    assert not list(settings.reference_dir.glob("*.jpg"))


def test_stale_camera_match_is_hidden(gallery_context):
    from app.api.persons import filter_matches
    from app.models import User

    client, engine, _, headers, _, _ = gallery_context
    person_id = create(client, headers)
    face_id = upload(client, headers, person_id).json()["id"]
    with Session(engine) as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        revision = db.get(GalleryState, 1).revision
        result = {
            "gallery_revision": revision,
            "matches": [{"person_id": person_id, "face_id": face_id}],
        }
        assert filter_matches(db, user, result)
    client.put(
        f"/api/persons/{person_id}", headers=headers, json={"name": "시험 인물", "enabled": False}
    )
    with Session(engine) as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        assert filter_matches(db, user, result) == []


def test_invalid_vectors_and_deterministic_ties():
    provider = MemoryFaceSearch()
    query = np.eye(1, 512, dtype=np.float32)[0]
    refs = [
        Reference(2, 1, "first", str(uuid.uuid4()), query),
        Reference(1, 1, "first", str(uuid.uuid4()), query),
    ]
    assert provider.search(query, refs)[0]["face_id"] == 1
    for vector in [np.zeros(512), np.ones(511), np.full(512, np.nan)]:
        with pytest.raises(ValueError):
            provider.search(vector, refs)


def test_commit_failure_removes_private_file(gallery_context, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    client, engine, settings, headers, _, control = gallery_context
    person_id = create(client, headers)
    analyzed = {
        "quality": {"quality": 0.9},
        "model_version": MODEL_VERSION,
        "embedding": control["vector"],
        "aligned_jpeg": base64.b64encode(image_bytes()).decode(),
    }
    original = Session.commit

    def failed_commit(db):
        raise SQLAlchemyError("injected commit failure")

    monkeypatch.setattr(Session, "commit", failed_commit)
    with pytest.raises(SQLAlchemyError):
        client.app.state.references.upload(person_id, analyzed, None)
    monkeypatch.setattr(Session, "commit", original)
    assert not list(settings.reference_dir.glob("*.jpg"))
    with Session(engine) as db:
        assert not list(db.scalars(select(PersonFace)))


def test_ack_failure_after_upsert_reverts_searchable_state(gallery_context, monkeypatch):
    from fastapi import HTTPException

    client, _, _, headers, gallery, control = gallery_context
    person_id = create(client, headers)
    worker = client.app.state.worker
    original = worker.request
    calls = 0

    def failed_ack(method, path, **kwargs):
        nonlocal calls
        if path == "/internal/gallery/reload":
            calls += 1
            if calls == 2:
                raise HTTPException(503, "Injected worker outage")
        return original(method, path, **kwargs)

    monkeypatch.setattr(worker, "request", failed_ack)
    response = upload(client, headers, person_id)
    assert response.status_code == 202 and response.json()["state"] == "pending"
    assert gallery.search(control["vector"])["matches"] == []
    monkeypatch.setattr(worker, "request", original)
    assert client.post("/api/persons/maintenance/retry", headers=headers).json()["pending"] == 0
    assert gallery.search(control["vector"])["matches"]


def test_embedding_retention_keeps_valid_image(gallery_context):
    client, engine, _, headers, gallery, control = gallery_context
    person_id = create(client, headers)
    face_id = upload(client, headers, person_id).json()["id"]
    with Session(engine) as db:
        face = db.get(PersonFace, face_id)
        face.embedding_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    client.app.state.references.cleanup()
    assert client.get(f"/api/persons/{person_id}/faces/{face_id}/image").status_code == 200
    assert gallery.search(control["vector"])["matches"] == []
    with Session(engine) as db:
        face = db.get(PersonFace, face_id)
        assert face.state == "expired" and face.embedding_encrypted is None and face.image_path


def test_unowned_collection_is_preserved(app_context):
    from app.services.face_search import ensure_collection
    from qdrant_client import models as qm

    client = app_context[0].app.state.qdrant
    client.create_collection(
        "existing", vectors_config=qm.VectorParams(size=512, distance=qm.Distance.COSINE)
    )
    with pytest.raises(ValueError):
        ensure_collection(client, "existing")
    assert client.collection_exists("existing")


def test_bounded_image_decoder_and_face_counts(app_context):
    from app.worker.faces import FaceAnalyzer
    from app.worker.reference_images import ReferenceRejected, analyze_reference

    class Models:
        info = {"actual_device": "cpu"}
        detected = []

        def detect(self, image):
            return self.detected

        def embed(self, image):
            pytest.fail("Rejected references must not be embedded")

    models = Models()
    analyzer = FaceAnalyzer(app_context[2], models)
    for content, code in [(b"not an image", "invalid_image"), (image_bytes(), "no_face")]:
        with pytest.raises(ReferenceRejected) as error:
            analyze_reference(analyzer, content)
        assert error.value.code == code
    models.detected = [{}, {}]
    with pytest.raises(ReferenceRejected) as error:
        analyze_reference(analyzer, image_bytes())
    assert error.value.code == "multiple_faces"
    stream = io.BytesIO()
    Image.new("RGB", (5000, 1)).save(stream, format="PNG")
    with pytest.raises(ReferenceRejected) as error:
        analyze_reference(analyzer, stream.getvalue())
    assert error.value.code == "image_dimensions_exceeded"


def test_system_status_does_not_expose_unchecked_candidates(gallery_context, monkeypatch):
    from app.api import system

    client = gallery_context[0]
    original = client.app.state.worker.request

    def worker_status(method, path, **kwargs):
        if path == "/internal/status":
            return httpx.Response(
                200, json={"status": "ok", "cameras": [{"result": {"tracks": []}}]}
            )
        return original(method, path, **kwargs)

    def failed_filter(*args):
        raise RuntimeError("SQL eligibility unavailable")

    monkeypatch.setattr(client.app.state.worker, "request", worker_status)
    monkeypatch.setattr(system, "filter_camera_status", failed_filter)
    assert client.get("/api/system/status").json()["worker"] == {"status": "unavailable"}
