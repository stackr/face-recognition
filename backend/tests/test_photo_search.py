import base64
import io
import json
import threading
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from app.core.security import password_hasher
from app.models import GalleryState, User
from app.worker.api import create_worker
from app.worker.reference_images import ReferenceRejected, find_faces_in_photo
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session
from test_persons import create, image_bytes, upload
from test_persons import gallery_context as gallery_context
from test_uploaded_faces import VideoModels
from test_worker import TestDetector


def photo_bytes(*, orientation=None):
    stream = io.BytesIO()
    photo = Image.new("RGB", (120, 160), (80, 140, 180))
    exif = Image.Exif()
    if orientation:
        exif[274] = orientation
    photo.save(stream, format="JPEG", exif=exif)
    return stream.getvalue()


def fake_gallery():
    calls = []

    def search(vector, snapshot, **kwargs):
        calls.append((vector, kwargs))
        return {"matches": [{"person_id": 7, "face_id": 3, "name": "Target", "similarity": 0.95}]}

    return SimpleNamespace(
        snapshot=lambda: ((9, ()), []), search_snapshot=search, close=lambda: None
    ), calls


def test_group_photo_faces_locations_orientation_and_private_embeddings(app_context):
    settings = app_context[2]
    settings.video_face_detection_threshold = 0.1
    gallery, calls = fake_gallery()
    analyzer = SimpleNamespace(models=VideoModels(), embed=lambda image: np.ones(512, np.float32))
    result = find_faces_in_photo(
        analyzer, photo_bytes(orientation=6), settings, threading.Event(), gallery, [7]
    )
    assert (result["image_width"], result["image_height"]) == (160, 120)
    assert len(result["faces"]) == len(calls) == 2
    assert [face["index"] for face in result["faces"]] == [1, 2]
    assert result["faces"][0]["bbox"] == [10, 10, 75, 80]
    assert result["faces"][1]["bbox"] == [85, 10, 150, 80]
    assert all(face["matches"][0]["person_id"] == 7 for face in result["faces"])
    assert all(call[1]["allowed_person_ids"] == [7] for call in calls)
    assert all(np.linalg.norm(call[0]) == pytest.approx(1) for call in calls)
    assert "embedding" not in json.dumps(result)
    preview = base64.b64decode(result["preview_data_url"].split(",", 1)[1])
    assert Image.open(io.BytesIO(preview)).size == (160, 120)


def test_portrait_fallback_recovers_faces_without_duplicate_results(app_context):
    settings = app_context[2]
    settings.video_face_detection_threshold = 0.1
    models = VideoModels()
    original_detect = models.detect
    models.detect = lambda image, **kwargs: (
        original_detect(image, **kwargs) if kwargs["side"] == 320 else []
    )
    analyzer = SimpleNamespace(models=models, embed=lambda image: np.ones(512, np.float32))
    gallery, calls = fake_gallery()
    result = find_faces_in_photo(
        analyzer, photo_bytes(orientation=6), settings, threading.Event(), gallery, [7]
    )
    assert len(result["faces"]) == len(calls) == 2
    assert all(face["quality"]["detector_input"] == 320 for face in result["faces"])


def test_no_face_invalid_photo_and_exceeded_face_limit(app_context):
    settings = app_context[2]
    gallery, calls = fake_gallery()
    analyzer = SimpleNamespace(models=VideoModels(), embed=lambda image: np.ones(512, np.float32))
    # Fake faces have confidence 0.2, below the default threshold.
    result = find_faces_in_photo(analyzer, photo_bytes(), settings, threading.Event(), gallery, [])
    assert result["faces"] == [] and not calls
    with pytest.raises(ReferenceRejected, match="invalid_image"):
        find_faces_in_photo(analyzer, b"invalid", settings, threading.Event(), gallery, [])
    settings.video_face_detection_threshold = 0.1
    settings.face_test_max_faces_per_frame = 1
    with pytest.raises(ReferenceRejected, match="face_limit_exceeded"):
        find_faces_in_photo(
            analyzer, photo_bytes(orientation=6), settings, threading.Event(), gallery, []
        )


def test_internal_photo_search_runs_detection_and_validates_auth(app_context):
    settings = app_context[2]
    settings.video_face_detection_threshold = 0.1
    gallery, calls = fake_gallery()
    analyzer = SimpleNamespace(
        models=VideoModels(), info={}, embed=lambda image: np.ones(512, np.float32)
    )
    with TestClient(
        create_worker(settings, TestDetector(), face_analyzer=analyzer, enable_gallery=False)
    ) as client:
        client.app.state.runtime.gallery = gallery
        path = "/internal/references/find-photo?allowed_person_ids=7"
        assert client.post(path, content=photo_bytes()).status_code == 401
        client.headers["X-Service-Token"] = settings.service_token.get_secret_value()
        response = client.post(path, content=photo_bytes(orientation=6))
        assert response.status_code == 200
        assert len(response.json()["faces"]) == len(calls) == 2
        rejected = client.post(path, content=b"invalid")
        assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "invalid_image"


@pytest.fixture
def photo_context(gallery_context):
    client, engine, settings, headers, gallery, control = gallery_context
    original = client.app.state.worker.client._transport
    control["photo_revision"] = None
    control["ids"] = None

    def transport(request):
        if request.url.path != "/internal/references/find-photo":
            return original.handle_request(request)
        ids = [int(value) for value in request.url.params.get_list("allowed_person_ids")]
        control["ids"] = ids
        searched = gallery.search(control["vector"], allowed_person_ids=ids)
        return httpx.Response(
            200,
            json={
                "gallery_revision": control["photo_revision"] or searched["gallery_revision"],
                "threshold": settings.face_match_threshold,
                "image_width": 112,
                "image_height": 112,
                "preview_data_url": "data:image/jpeg;base64,"
                + base64.b64encode(image_bytes()).decode(),
                "faces": [
                    {
                        "index": index,
                        "bbox": [0, 0, 50, 50],
                        "quality": {"status": "accepted", "reasons": []},
                        "matches": searched["matches"],
                    }
                    for index in (1, 2)
                ],
            },
        )

    client.app.state.worker.client = httpx.Client(
        transport=httpx.MockTransport(transport), base_url="http://127.0.0.1:8001"
    )
    yield client, engine, settings, headers, gallery, control
    client.app.state.worker.client.close()


def test_public_photo_search_target_all_and_stale_gallery(photo_context):
    client, engine, settings, headers, gallery, control = photo_context
    first = create(client, headers, "First")
    second = create(client, headers, "Second")
    assert upload(client, headers, first).status_code == 201
    assert upload(client, headers, second).status_code == 201
    headers = headers | {"Content-Type": "image/jpeg"}
    result = client.post(
        f"/api/persons/search-photo?person_id={first}", headers=headers, content=image_bytes()
    )
    assert result.status_code == 200
    assert control["ids"] == [first]
    assert len(result.json()["faces"]) == 2
    assert all(face["matches"][0]["candidate"] for face in result.json()["faces"])
    assert all(
        [match["person_id"] for match in face["matches"]] == [first]
        for face in result.json()["faces"]
    )
    assert "embedding" not in result.text and "gallery_revision" not in result.json()
    result = client.post("/api/persons/search-photo", headers=headers, content=image_bytes())
    assert set(control["ids"]) == {first, second}
    assert all(len(face["matches"]) == 2 for face in result.json()["faces"])
    with Session(engine) as db:
        control["photo_revision"] = db.get(GalleryState, 1).revision - 1
    result = client.post("/api/persons/search-photo", headers=headers, content=image_bytes())
    assert all(face["matches"] == [] for face in result.json()["faces"])
    assert (
        client.post(
            "/api/persons/search-photo",
            headers=headers | {"Content-Type": "text/plain"},
            content=b"bad",
        ).status_code
        == 415
    )
    assert (
        client.post(
            "/api/persons/search-photo?person_id=0", headers=headers, content=image_bytes()
        ).status_code
        == 422
    )


def test_public_photo_search_permissions_and_disabled_target(photo_context):
    client, engine, settings, headers, gallery, control = photo_context
    person_id = create(client, headers)
    assert upload(client, headers, person_id).status_code == 201
    assert (
        client.put(
            f"/api/persons/{person_id}",
            headers=headers,
            json={"name": "Disabled", "enabled": False},
        ).status_code
        == 200
    )
    result = client.post(
        f"/api/persons/search-photo?person_id={person_id}",
        headers=headers | {"Content-Type": "image/jpeg"},
        content=image_bytes(),
    )
    assert result.status_code == 200 and all(
        face["matches"] == [] for face in result.json()["faces"]
    )
    with Session(engine) as db:
        db.add(
            User(
                username="operator",
                role="operator",
                password_hash=password_hasher.hash("operator-password"),
            )
        )
        db.commit()
    login = client.post(
        "/api/auth/login", json={"username": "operator", "password": "operator-password"}
    ).json()
    op_headers = {"X-CSRF-Token": login["csrf_token"], "Content-Type": "image/jpeg"}
    assert (
        client.post(
            f"/api/persons/search-photo?person_id={person_id}",
            headers=op_headers,
            content=image_bytes(),
        ).status_code
        == 403
    )
    result = client.post("/api/persons/search-photo", headers=op_headers, content=image_bytes())
    assert result.status_code == 200 and control["ids"] == []
    assert all(face["matches"] == [] for face in result.json()["faces"])
    viewer = client.post(
        "/api/auth/login", json={"username": "viewer", "password": "test-password-long-enough"}
    ).json()
    assert (
        client.post(
            "/api/persons/search-photo",
            headers={"X-CSRF-Token": viewer["csrf_token"]},
            content=image_bytes(),
        ).status_code
        == 403
    )
