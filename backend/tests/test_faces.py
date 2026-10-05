import copy

import cv2
import httpx
import numpy as np
import pytest
from app.schemas.calibration import CalibrationDataset
from app.worker.api import create_worker
from app.worker.face_onnx import TEMPLATE, normalized_embedding, similarity_matrix
from app.worker.faces import FaceAnalyzer, FaceCandidate, TrackFaces, quality
from app.worker.runtime import Frame
from conftest import TEST_PASSWORD
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_analysis import replace_worker
from test_foundation import PAYLOAD
from test_worker import TestDetector, wait_for, write_video


def test_alignment_known_rotation_scale_translation_and_embedding_validation():
    angle = 0.35
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    points = TEMPLATE @ rotation.T * 2.3 + [100, 30]
    matrix, error = similarity_matrix(points)
    assert error < 1e-4
    assert np.allclose(points @ matrix[:, :2].T + matrix[:, 2], TEMPLATE, atol=1e-4)
    vector = normalized_embedding(np.arange(512))
    assert vector.dtype == np.float32 and np.isclose(np.linalg.norm(vector), 1)
    for bad in (np.zeros(512), np.full(512, np.nan), np.zeros(511)):
        with pytest.raises(ValueError):
            normalized_embedding(bad)
    with pytest.raises(ValueError):
        similarity_matrix(np.zeros((5, 2)))


def test_quality_hard_gates_reject_before_embedding(app_context):
    settings = app_context[2]
    image = np.random.default_rng(1).integers(60, 195, (240, 240, 3), dtype=np.uint8)
    face = {
        "bbox": np.array([10, 10, 220, 220]),
        "landmarks": TEMPLATE * 1.5 + [20, 20],
        "confidence": 0.95,
    }
    pose = {"yaw": 0, "pitch": 0, "roll": 0}
    accepted = quality(image, face, settings, pose)
    assert accepted.metadata["status"] == "accepted" and accepted.aligned.shape == (112, 112, 3)
    for pixels, angles, reason in (
        (np.full_like(image, 128), pose, "blurred"),
        (np.zeros_like(image), pose, "too_dark"),
        (np.full_like(image, 255), pose, "too_bright"),
        (image, pose | {"yaw": 70}, "pose_exceeded"),
        (image, None, "pose_unavailable"),
    ):
        candidate = quality(pixels, face, settings, angles)
        assert reason in candidate.metadata["reasons"] and candidate.aligned is None
    small = quality(image, face | {"bbox": np.array([10, 10, 60, 60])}, settings, pose)
    assert "face_too_small" in small.metadata["reasons"] and small.aligned is None


def test_person_roi_coordinates_ambiguity_and_tiny_face_skip_pose(app_context):
    class Models:
        info = {"status": "cpu", "actual_device": "cpu"}

        def __init__(self):
            self.detected = [
                {
                    "bbox": np.array([25, 5, 135, 145]),
                    "landmarks": TEMPLATE + [30, 15],
                    "confidence": 0.95,
                }
            ]
            self.pose_calls = 0

        def detect(self, image, side=320):
            assert side in {320, 640}
            if image.shape[:2] == (110, 176):
                offset = np.array([12, 30])
            else:
                assert image.shape == (200, 160, 3)
                offset = np.array([20, 40])
            delta = np.array([20, 40]) - offset
            return [
                face
                | {"bbox": face["bbox"] + np.tile(delta, 2), "landmarks": face["landmarks"] + delta}
                for face in self.detected
            ]

        def pose(self, image, bbox):
            self.pose_calls += 1
            assert np.array_equal(bbox, [45, 45, 155, 185])
            return {"yaw": 0, "pitch": 0, "roll": 0}

        def embed(self, aligned):
            pytest.fail("Inspect must not generate an embedding")

    models = Models()
    analyzer = FaceAnalyzer(app_context[2], models)
    image = np.random.default_rng(1).integers(60, 195, (240, 240, 3), dtype=np.uint8)
    face = analyzer.inspect(image, [20, 40, 180, 240])
    assert face.metadata["bbox"] == [45, 45, 155, 185]
    assert face.metadata["status"] == "accepted"
    models.detected.append(models.detected[0] | {"bbox": np.array([0, 5, 40, 70])})
    assert analyzer.inspect(image, [20, 40, 180, 240]).metadata["status"] == "ambiguous"
    assert models.pose_calls == 1
    models.detected = [
        {
            "bbox": np.array([25, 5, 60, 45]),
            "landmarks": TEMPLATE * 0.3 + [25, 5],
            "confidence": 0.95,
        }
    ]
    assert "face_too_small" in analyzer.inspect(image, [20, 40, 180, 240]).metadata["reasons"]
    assert models.pose_calls == 1
    models.detected = []
    assert analyzer.inspect(image, [20, 40, 180, 240]).metadata["status"] == "no_face"


class StubFaces:
    info = {"status": "cpu", "model_version": "unit-test"}

    def __init__(self):
        self.score = 0.8
        self.accept = True
        self.calls = self.embeddings = 0

    def inspect(self, image, bbox):
        self.calls += 1
        return FaceCandidate(
            {
                "status": "accepted" if self.accept else "rejected",
                "quality": self.score,
                "reasons": [],
            },
            np.full((112, 112, 3), 128, np.uint8) if self.accept else None,
        )

    def embed(self, image):
        self.embeddings += 1
        return np.ones(512, np.float32)


def sample_frame(now, session="a" * 32):
    return Frame(
        np.zeros((120, 160, 3), np.uint8), session, int(now * 10), "2026-10-02T00:00:00Z", now
    )


def people(count=1):
    return [
        {"track_id": identifier, "bbox": [0, 0, 160, 120], "confidence": 0.9}
        for identifier in range(1, count + 1)
    ]


def test_face_sampling_quality_gate_best_face_expiry_and_bounded_fairness(app_context):
    settings = app_context[2]
    cache, analyzer = TrackFaces(settings), StubFaces()
    analyzer.accept = False
    assert cache.process(analyzer, sample_frame(10), people(), {1})["quality_rejected"] == 1
    assert analyzer.embeddings == 0
    analyzer.accept = True
    assert cache.process(analyzer, sample_frame(10.2), people(), {1})["roi_attempts"] == 0
    assert cache.process(analyzer, sample_frame(10.6), people(), {1})["embeddings_created"] == 1
    assert cache.process(analyzer, sample_frame(11.2), people(), {1})["embeddings_created"] == 1
    analyzer.score = 0.9
    assert cache.process(analyzer, sample_frame(11.8), people(), {1})["embeddings_created"] == 1
    assert cache.thumbnail(1, "a" * 32, 11.9)[:2] == b"\xff\xd8"
    assert cache.thumbnail(1, "b" * 32, 11.9) is None
    assert cache.thumbnail(1, "a" * 32, 15) is None
    cache.process(analyzer, sample_frame(15), [], set())
    assert cache.size() == 0
    settings.face_tracks_per_camera, settings.face_rois_per_frame = 2, 1
    cache = TrackFaces(settings)
    first = people(3)
    cache.process(analyzer, sample_frame(20), first, {1, 2, 3})
    assert cache.size() == 2 and first[2]["face"]["status"] == "capacity"
    second = people(3)
    cache.process(analyzer, sample_frame(20.2), second, {1, 2, 3})
    assert second[1]["face"]["embedding_ready"]


@pytest.mark.parametrize("kind", ["faces", "people"])
def test_thumbnail_auth_camera_grants_revocation_and_disabled(app_context, admin_headers, kind):
    client = app_context[0]
    camera_id = client.post("/api/cameras", json=PAYLOAD, headers=admin_headers).json()["camera_id"]
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"\xff\xd8sample")

    replace_worker(client, handler)
    path = f"/api/cameras/{camera_id}/{kind}/1?stream_session_id={'a' * 32}"
    response = client.get(path)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert calls[-1].url.params["stream_session_id"] == "a" * 32
    assert client.get(f"/api/cameras/{camera_id}/{kind}/1").status_code == 422
    client.post("/api/auth/login", json={"username": "viewer", "password": TEST_PASSWORD})
    assert client.get(path).status_code == 403
    login = client.post("/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    client.put(f"/api/cameras/{camera_id}/access", json={"username": "viewer"}, headers=headers)
    client.post("/api/auth/login", json={"username": "viewer", "password": TEST_PASSWORD})
    assert client.get(path).status_code == 200
    login = client.post("/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    client.put(
        f"/api/cameras/{camera_id}/access",
        json={"username": "viewer", "can_view": False},
        headers=headers,
    )
    client.post("/api/auth/login", json={"username": "viewer", "password": TEST_PASSWORD})
    assert client.get(path).status_code == 403
    login = client.post("/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    # Mock stop returns the ordinary command response when disabling the camera.
    replace_worker(client, lambda request: httpx.Response(404))
    assert (
        client.put(
            f"/api/cameras/{camera_id}", json=PAYLOAD | {"enabled": False}, headers=headers
        ).status_code
        == 200
    )
    assert client.get(path).status_code == 403
    client.post("/api/auth/logout", headers=headers)
    assert client.get(path).status_code == 401


def test_worker_clears_face_buffers_on_stop_restart_loop_and_end(app_context):
    settings = app_context[2]
    video = settings.video_dir / "face-unit.mp4"
    write_video(video, seconds=1)
    with TestClient(
        create_worker(settings, TestDetector(), face_analyzer=StubFaces(), enable_gallery=False)
    ) as client:
        client.headers["X-Service-Token"] = settings.service_token.get_secret_value()
        payload = {"source": str(video), "source_type": "mp4", "loop": True}
        client.post("/internal/cameras/1/start", json=payload)
        state = wait_for(
            client,
            "/internal/cameras/1",
            lambda item: item.get("face_counts", {}).get("embeddings_created", 0) >= 1,
        )
        old = state["stream_session_id"]
        path = "/internal/cameras/1/faces/1"
        response = client.get(path, params={"stream_session_id": old})
        assert response.status_code == 200
        assert cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR).shape[
            :2
        ] == (112, 112)
        assert "embedding" not in state["result"]["tracks"][0]["face"]
        wait_for(
            client,
            "/internal/cameras/1",
            lambda item: item["stream_session_id"] != old and item["result"] is not None,
        )
        assert client.get(path, params={"stream_session_id": old}).status_code == 404
        client.post("/internal/cameras/1/stop")
        assert client.get(path, params={"stream_session_id": old}).status_code == 404
        assert client.get("/internal/cameras/1").json()["face_cache_tracks"] == 0
        client.post("/internal/cameras/1/start", json=payload | {"loop": False})
        ended = wait_for(client, "/internal/cameras/1", lambda item: item["state"] == "ended")
        assert ended["face_cache_tracks"] == 0
        assert (
            client.get(path, params={"stream_session_id": ended["stream_session_id"]}).status_code
            == 404
        )


def test_calibration_rejects_split_leakage_bad_intervals_and_false_pair_labels():
    fixture = {
        "purpose": "pipeline_smoke",
        "provenance": "manual",
        "sources": [
            {
                "source_id": "s",
                "source_group": "same-capture",
                "split": "smoke",
                "media_path": "data/samples/test.jpg",
                "sha256": "a" * 64,
                "width": 100,
                "height": 100,
            }
        ],
        "appearances": [],
        "references": [
            {
                "reference_id": "a",
                "source_id": "s",
                "subject_id": "person_a",
                "crop": [0, 0, 100, 100],
            },
            {
                "reference_id": "b",
                "source_id": "s",
                "subject_id": "person_b",
                "crop": [0, 0, 100, 100],
            },
        ],
        "trials": [{"first": "a", "second": "b", "same_subject": False}],
    }
    assert CalibrationDataset.model_validate(fixture)
    invalid = copy.deepcopy(fixture)
    invalid["sources"].append(fixture["sources"][0] | {"source_id": "other", "split": "evaluation"})
    with pytest.raises(ValidationError, match="leaked"):
        CalibrationDataset.model_validate(invalid)
    invalid = copy.deepcopy(fixture)
    invalid["trials"][0]["same_subject"] = True
    with pytest.raises(ValidationError, match="conflicts"):
        CalibrationDataset.model_validate(invalid)
    invalid = fixture | {
        "appearances": [
            {
                "source_id": "s",
                "subject_id": "person_a",
                "start_seconds": 0,
                "end_seconds": 5,
                "annotated_by": "manual",
            }
        ]
    }
    with pytest.raises(ValidationError, match="interval"):
        CalibrationDataset.model_validate(invalid)
