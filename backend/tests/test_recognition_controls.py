import importlib.util
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import Base, Camera, CameraPermission, FunctionSettings, RecognitionLog, User
from app.schemas.recognition import SamplingSettings
from app.services.recognition import RecognitionStore, load_sampling, observation
from app.worker.api import create_worker
from app.worker.detector import YoloPersonDetector
from app.worker.face_onnx import TEMPLATE, FaceModels
from app.worker.faces import FaceAnalyzer, TrackFaces
from app.worker.runtime import WorkerRuntime
from conftest import TEST_PASSWORD
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from test_analysis import replace_worker
from test_faces import StubFaces, people, sample_frame
from test_worker import TestDetector, boxes, write_video


def test_settings_auth_validation_conflict_pending_and_ack(app_context, admin_headers):
    client, engine, _ = app_context
    original = client.get("/api/function-settings").json()
    assert original["values"]["face_analysis_interval"] == 0.5
    assert original["values"]["face_match_threshold"] == 0.75
    assert original["values"]["detection_confidence"] == 0.1
    values = original["values"] | {
        "face_analysis_interval": 0.2,
        "detection_fps": 10,
        "face_match_threshold": 0.65,
        "detection_confidence": 0.4,
    }
    assert client.put("/api/function-settings", json=values | {"revision": 0}).status_code == 403
    for bad in (
        {"face_analysis_interval": 0.1},
        {"detection_fps": 16},
        {"face_rois_per_frame": 1.5},
        {"face_match_threshold": -1.01},
        {"face_match_threshold": 1.01},
        {"face_match_threshold": "NaN"},
        {"face_match_threshold": None},
        {"detection_confidence": -0.01},
        {"detection_confidence": 1.01},
        {"detection_confidence": "NaN"},
        {"detection_confidence": "Infinity"},
        {"detection_confidence": None},
        {"person_detection_enabled": "false"},
        {"video_face_detection_threshold": 0.09},
        {"video_face_detection_threshold": 1},
        {"video_face_min_size": 7},
        {"video_face_min_size": 513},
        {"video_face_min_size": 8.5},
        {"extra": 1},
    ):
        assert (
            client.put(
                "/api/function-settings", json=values | bad | {"revision": 0}, headers=admin_headers
            ).status_code
            == 422
        )
    saved = client.put(
        "/api/function-settings", json=values | {"revision": 0}, headers=admin_headers
    )
    assert (
        saved.status_code == 202 and saved.json()["revision"] == 1 and not saved.json()["applied"]
    )
    with Session(engine) as db:
        assert db.get(FunctionSettings, 1).values == values
    assert (
        client.put(
            "/api/function-settings", json=values | {"revision": 0}, headers=admin_headers
        ).status_code
        == 409
    )
    replace_worker(
        client, lambda request: httpx.Response(200, json={"revision": 1, "values": values})
    )
    assert client.get("/api/function-settings").json()["applied"]
    viewer_login = client.post(
        "/api/auth/login", json={"username": "viewer", "password": TEST_PASSWORD}
    )
    assert client.get("/api/function-settings").status_code == 200
    assert (
        client.put(
            "/api/function-settings",
            json=values | {"revision": 1},
            headers={"X-CSRF-Token": viewer_login.json()["csrf_token"]},
        ).status_code
        == 403
    )
    login = client.post("/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
    client.post("/api/auth/logout", headers={"X-CSRF-Token": login.json()["csrf_token"]})
    assert client.get("/api/function-settings").status_code == 401
    assert client.get("/api/recognition-logs").status_code == 401


def test_older_saved_sampling_controls_keep_values_and_use_environment_threshold(
    app_context, admin_headers
):
    client, engine, settings = app_context
    settings.face_match_threshold = 0.81
    settings.detection_confidence = 0.25
    legacy = {"detection_fps": 12, "face_analysis_interval": 0.3, "face_rois_per_frame": 7}
    with Session(engine) as db:
        db.add(FunctionSettings(id=1, revision=9, values=legacy))
        db.commit()
    value = client.get("/api/function-settings").json()
    assert value["revision"] == 9
    assert value["values"] == SamplingSettings.defaults(settings).model_dump() | legacy
    with Session(engine) as db:
        assert db.get(FunctionSettings, 1).values == legacy
        row = db.get(FunctionSettings, 1)
        row.values = legacy | {"face_match_threshold": 0.62}
        db.commit()
        assert load_sampling(db, settings)[1].face_match_threshold == 0.62
        assert load_sampling(db, settings)[1].detection_confidence == 0.25
        row.values = row.values | {"detection_confidence": 0.8}
        db.commit()
        assert load_sampling(db, settings)[1].detection_confidence == 0.8


def record(camera_id, frame_id, *, outcome="quality_rejected", reasons=None, when=None):
    return {
        "camera_id": camera_id,
        "frame_id": frame_id,
        "stream_session_id": "a" * 32,
        "track_id": 1,
        "captured_at": when or datetime.now(UTC).replace(tzinfo=None),
        "outcome": outcome,
        "primary_reason": (reasons or ["blurred"])[0],
        "reasons": reasons or ["blurred"],
        "quality": 0.4,
        "top_similarity": None,
        "metrics": {"blur_score": 20},
        "sample_count": 0,
        "settings_revision": 0,
    }


def test_logs_filter_summary_pagination_and_camera_grants(app_context, admin_headers):
    client, engine, settings = app_context
    ids = [
        client.post(
            "/api/cameras", json={"name": name, "source_type": "mp4"}, headers=admin_headers
        ).json()["camera_id"]
        for name in ("A", "B")
    ]
    store = RecognitionStore(settings, engine)
    store.save(
        [
            record(ids[0], 1),
            record(ids[0], 2, outcome="no_face", reasons=["no_face"]),
            record(ids[1], 1),
        ]
    )
    all_logs = client.get("/api/recognition-logs?limit=2").json()
    assert all_logs["summary"]["total"] == 3 and all_logs["summary"]["reasons"] == {
        "blurred": 2,
        "no_face": 1,
    }
    assert all_logs["has_more"] and len(all_logs["items"]) == 2
    older = client.get(f"/api/recognition-logs?limit=2&before_id={all_logs['next_cursor']}").json()
    assert len(older["items"]) == 1 and older["summary"]["total"] == 3
    assert {item["id"] for item in older["items"]}.isdisjoint(
        item["id"] for item in all_logs["items"]
    )
    filtered = client.get(f"/api/recognition-logs?camera_id={ids[0]}&reason=no_face").json()
    assert filtered["summary"]["total"] == 1 and filtered["items"][0]["outcome"] == "no_face"
    assert client.get("/api/recognition-logs?outcome=wrong").status_code == 422
    assert (
        client.get(
            "/api/recognition-logs?start=2026-10-03T02:00:00Z&end=2026-10-03T01:00:00Z"
        ).status_code
        == 422
    )
    with Session(engine) as db:
        viewer = db.scalar(select(User).where(User.username == "viewer"))
        viewer_id = viewer.id
        db.add(CameraPermission(camera_id=ids[0], user_id=viewer.id, can_operate=False))
        db.commit()
    client.post("/api/auth/login", json={"username": "viewer", "password": TEST_PASSWORD})
    visible = client.get("/api/recognition-logs").json()
    assert visible["summary"]["total"] == 2 and {
        item["camera_id"] for item in visible["items"]
    } == {ids[0]}
    assert client.get(f"/api/recognition-logs?camera_id={ids[1]}").status_code == 403
    with Session(engine) as db:
        db.delete(db.get(CameraPermission, (ids[0], viewer_id)))
        db.commit()
    assert client.get("/api/recognition-logs").json()["summary"]["total"] == 0


def test_log_retention_limit_idempotence_and_camera_delete(app_context, admin_headers):
    client, engine, settings = app_context
    camera_id = client.post(
        "/api/cameras", json={"name": "Bounded logs", "source_type": "mp4"}, headers=admin_headers
    ).json()["camera_id"]
    settings.recognition_log_max_records = 3
    store = RecognitionStore(settings, engine)
    store.save([record(camera_id, frame) for frame in range(5)])
    store.save(
        [
            record(camera_id, 4),
            record(camera_id, 6, when=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=8)),
        ]
    )
    with Session(engine) as db:
        assert list(db.scalars(select(RecognitionLog.frame_id).order_by(RecognitionLog.id))) == [
            2,
            3,
            4,
        ]
        db.delete(db.get(Camera, camera_id))
        db.commit()
        assert db.scalar(select(func.count()).select_from(RecognitionLog)) == 0


def test_observation_excludes_private_content_and_unsampled_frames():
    face = {
        "frame_id": 1,
        "status": "accepted",
        "quality": 0.8,
        "sample_count": 3,
        "embedding": ["private"],
        "jpeg": "private",
        "matches": [{"name": "private"}],
        "comparison": {"outcome": "below_threshold", "top_similarity": 0.6, "threshold": 0.75},
    }
    item = observation(
        1, "a" * 32, 1, "2026-10-03T01:00:00Z", {"track_id": 2, "face": face}, 4, "ready"
    )
    assert item["outcome"] == "below_threshold" and item["top_similarity"] == 0.6
    assert "private" not in str(item)
    rejected = observation(
        1,
        "a" * 32,
        1,
        "2026-10-03T01:00:00Z",
        {"track_id": 2, "face": face | {"status": "rejected", "reasons": ["blurred"]}},
        4,
        "ready",
    )
    assert rejected["outcome"] == "quality_rejected" and rejected["top_similarity"] is None
    assert (
        observation(
            1, "a" * 32, 2, "2026-10-03T01:00:00Z", {"track_id": 2, "face": face}, 4, "ready"
        )
        is None
    )
    assert (
        observation(
            1, "a" * 32, 1, "2026-10-03T01:00:00Z", {"track_id": 2, "face": face}, 4, "unavailable"
        )["outcome"]
        == "search_unavailable"
    )


def test_live_sampling_update_keeps_track_and_updates_tracker_clock(app_context):
    settings = app_context[2]

    class ConfigurableDetector(TestDetector):
        def configure_confidence(self, value):
            self.confidence = value

    detector = ConfigurableDetector()
    runtime = WorkerRuntime(settings, detector, StubFaces())
    assert detector.confidence == settings.detection_confidence
    tracker = SimpleNamespace(fps=5, tracker=SimpleNamespace(max_frames_lost=15))
    faces = object()
    run = SimpleNamespace(
        lock=threading.RLock(),
        tracker=tracker,
        faces=faces,
        next_due=100,
        cancel=threading.Event(),
        latest=None,
        state="running",
    )
    try:
        with runtime.lock:
            runtime.runs[1] = run
        values = SamplingSettings(
            detection_fps=10,
            face_analysis_interval=0.2,
            face_rois_per_frame=8,
            face_match_threshold=0.68,
            detection_confidence=0.4,
        )
        runtime.queue_sampling(2, values)
        runtime.queue_sampling(1, SamplingSettings.defaults(settings))
        deadline = time.monotonic() + 2
        while runtime.sampling_status()["revision"] != 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert runtime.sampling_status() == {"revision": 2, "values": values.model_dump()}
        assert run.faces is faces and run.tracker is tracker and tracker.fps == 10
        assert tracker.tracker.max_frames_lost == 30
        assert settings.face_match_threshold == 0.68
        assert settings.detection_confidence == detector.confidence == 0.4
    finally:
        with runtime.lock:
            runtime.runs.clear()
        runtime.close()


def test_detector_receives_persisted_cutoff_before_first_and_later_inference(app_context):
    settings = app_context[2]
    settings.detection_confidence = 0.35
    calls = []

    def predict(frames, **kwargs):
        calls.append(kwargs["conf"])
        return [SimpleNamespace(boxes=boxes(empty=kwargs["conf"] > 0.9)) for _ in frames]

    detector = YoloPersonDetector.__new__(YoloPersonDetector)
    detector.model = SimpleNamespace(predict=predict)
    detector.info = {"model": "unit-test", "actual_device": "cpu"}
    detector.confidence, detector.device, detector.fp16 = 0.1, "cpu", False
    runtime = WorkerRuntime(settings, detector)
    try:
        image = np.zeros((120, 160, 3), np.uint8)
        assert len(detector.detect(image)) == 1
        runtime.queue_sampling(
            1, SamplingSettings.defaults(settings).model_copy(update={"detection_confidence": 0.95})
        )
        deadline = time.monotonic() + 2
        while runtime.sampling_status()["revision"] != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(detector.detect_batch([image, image])[0]) == 0
        assert calls == [0.35, 0.95]
        assert detector.info["confidence_threshold"] == 0.95
        assert settings.track_low_threshold == 0.1 and settings.new_track_threshold == 0.6
        assert settings.model_copy(update={"detection_confidence": 0.95}).tracker_thresholds()
        with pytest.raises(ValueError):
            settings.model_copy(update={"track_low_threshold": 0.7}).tracker_thresholds()
    finally:
        runtime.close()


def test_running_camera_cutoff_changes_tracks_without_restarting_or_losing_tracker(app_context):
    settings = app_context[2]
    video = settings.video_dir / "confidence.mp4"
    write_video(video, seconds=5)

    class ConfigurableDetector(TestDetector):
        def configure_confidence(self, value):
            self.confidence = value

        def detect(self, image):
            return boxes(empty=self.confidence > 0.9)

    detector = ConfigurableDetector()
    with TestClient(
        create_worker(settings, detector, enable_faces=False, enable_gallery=False)
    ) as client:
        client.headers["X-Service-Token"] = settings.service_token.get_secret_value()
        assert (
            client.post(
                "/internal/cameras/1/start",
                json={"source": str(video), "source_type": "mp4", "loop": True},
            ).status_code
            == 200
        )
        from test_worker import wait_for

        before = wait_for(
            client, "/internal/cameras/1", lambda row: bool((row.get("result") or {}).get("tracks"))
        )
        runtime = client.app.state.runtime
        tracker = runtime.get(1).tracker
        values = SamplingSettings.defaults(settings).model_copy(
            update={"detection_confidence": 0.95}
        )
        runtime.queue_sampling(1, values)
        after = wait_for(
            client,
            "/internal/cameras/1",
            lambda row: (row.get("result") or {}).get("detection_confidence") == 0.95,
        )
        assert after["state"] == "running" and after["result"]["tracks"] == []
        assert after["stream_session_id"] == before["stream_session_id"]
        assert runtime.get(1).tracker is tracker
        runtime.queue_sampling(2, values.model_copy(update={"detection_confidence": 0.1}))
        restored = wait_for(
            client, "/internal/cameras/1", lambda row: bool((row.get("result") or {}).get("tracks"))
        )
        assert restored["result"]["detection_confidence"] == 0.1
        assert restored["stream_session_id"] == before["stream_session_id"]
        assert runtime.get(1).tracker is tracker


@pytest.mark.parametrize(
    "mode,expected_region,expected_side,passes",
    [("head", "head", 320, 1), ("retry", "person", 640, 3), ("neighbor", "person", 640, 3)],
)
def test_head_roi_retry_coordinates_and_neighbor_association(
    app_context, mode, expected_region, expected_side, passes
):
    class Models:
        info = {}

        def __init__(self):
            self.calls = []

        def detect(self, image, side=320):
            self.calls.append((image.shape[:2], side))
            if mode == "retry" and side != 640:
                return []
            origin = [40, 2] if len(self.calls) == 1 else [50, 20]
            bbox = (
                np.array([80, 40, 200, 180]) if mode != "neighbor" else np.array([251, 40, 270, 80])
            )
            return [
                {
                    "bbox": bbox - np.tile(origin, 2),
                    "landmarks": TEMPLATE * 1.2 + [80, 45] - origin,
                    "confidence": 0.98,
                }
            ]

        def pose(self, image, bbox):
            assert np.array_equal(bbox, [80, 40, 200, 180])
            return {"yaw": 0, "pitch": 0, "roll": 0}

    models = Models()
    pixels = np.random.default_rng(1).integers(60, 195, (400, 320, 3), dtype=np.uint8)
    candidate = FaceAnalyzer(app_context[2], models).inspect(pixels, [50, 20, 250, 380])
    assert candidate.metadata["detector_region"] == expected_region
    assert candidate.metadata["detector_input"] == expected_side and len(models.calls) == passes
    if mode == "neighbor":
        assert candidate.metadata["status"] == "no_face"
    else:
        assert candidate.metadata["status"] == "accepted" and candidate.metadata["bbox"] == [
            80,
            40,
            200,
            180,
        ]


@pytest.mark.parametrize("side", [320, 640])
def test_scrfd_dynamic_anchor_grid_restores_original_coordinates(app_context, side):
    models = FaceModels.__new__(FaceModels)
    models.settings = app_context[2]
    outputs = [None] * 9
    for index, stride in enumerate((8, 16, 32)):
        grid = side // stride
        count = grid * grid * 2
        outputs[index] = np.zeros((count, 1), dtype=np.float32)
        outputs[index + 3] = np.zeros((count, 4), dtype=np.float32)
        outputs[index + 6] = np.zeros((count, 10), dtype=np.float32)
        if index == 0:
            anchor = 2 * ((grid // 2) * grid + grid // 2)
            outputs[index][anchor] = 0.9
            outputs[index + 3][anchor] = 40 * side / 320 / stride
    models.run = lambda name, image, size, mean, std: outputs
    faces = models.detect(np.zeros((320, 320, 3), dtype=np.uint8), side=side)
    assert len(faces) == 1 and np.allclose(faces[0]["bbox"], [120, 120, 200, 200])


def test_multiframe_uses_lower_quality_faces_consensus_evidence_and_gallery_changes(app_context):
    class Analyzer(StubFaces):
        position = 0

        def embed(self, image):
            vector = np.zeros(512, np.float32)
            vector[:2] = [1, self.position]
            return vector

    class Gallery:
        revision = 1
        empty = False
        calls = 0

        def snapshot(self):
            return (self.revision, ()), []

        def search(self, vector, limit):
            self.calls += 1
            score = {0: 0.6, 0.1: 0.95, 0.2: 0.9}[round(float(vector[1] / vector[0]), 1)]
            return {
                "threshold": 0.75,
                "matches": []
                if self.empty
                else [{"person_id": 1, "face_id": 10, "name": "test", "similarity": score}],
            }

    cache, analyzer, gallery = TrackFaces(app_context[2]), Analyzer(), Gallery()
    for index, score in enumerate((0.95, 0.8, 0.85)):
        analyzer.position, analyzer.score = index / 10, score
        tracks = people()
        assert (
            cache.process(analyzer, sample_frame(10 + index * 0.6), tracks, {1})[
                "embeddings_created"
            ]
            == 1
        )
        cache.match(gallery, tracks)
        if index < 2:
            assert tracks[0]["face"]["matches"] == []
    match = tracks[0]["face"]["matches"][0]
    assert match["supporting_samples"] == 2 and np.isclose(
        match["similarity"], np.average([0.6, 0.95, 0.9], weights=[0.95, 0.8, 0.85])
    )
    assert cache.tracks[1]["best"]["frame_id"] == 100
    assert cache.tracks[1]["match_samples"][1]["frame_id"] == 112
    cache.match(gallery, tracks)
    assert gallery.calls == 3
    gallery.empty, gallery.revision = True, 2
    cache.match(gallery, tracks)
    assert (
        tracks[0]["face"]["matches"] == []
        and tracks[0]["face"]["comparison"]["outcome"] == "gallery_empty"
    )


def test_threshold_change_rejudges_cached_samples_without_reembedding_or_search(app_context):
    settings = app_context[2]

    class Gallery:
        calls = 0

        def snapshot(self):
            return (1, ()), []

        def search(self, vector, limit):
            self.calls += 1
            return {
                "threshold": 0.75,
                "matches": [{"person_id": 1, "face_id": 10, "name": "test", "similarity": 0.8}],
            }

    cache, analyzer, gallery = TrackFaces(settings), StubFaces(), Gallery()
    for now in (10, 10.6):
        tracks = people()
        cache.process(analyzer, sample_frame(now), tracks, {1})
        cache.match(gallery, tracks)
    assert tracks[0]["face"]["matches"][0]["supporting_samples"] == 2
    settings.face_match_threshold = 0.9
    cache.match(gallery, tracks)
    assert tracks[0]["face"]["matches"] == []
    assert tracks[0]["face"]["comparison"]["threshold"] == 0.9
    assert tracks[0]["face"]["comparison"]["outcome"] == "below_threshold"
    settings.face_match_threshold = 0.6
    cache.match(gallery, tracks)
    assert tracks[0]["face"]["matches"][0]["supporting_samples"] == 2
    assert tracks[0]["face"]["comparison"]["threshold"] == 0.6
    assert analyzer.embeddings == gallery.calls == 2


def test_negative_threshold_never_counts_an_absent_match_as_support(app_context):
    settings = app_context[2]
    settings.face_match_threshold = -1

    class Gallery:
        calls = 0

        def snapshot(self):
            return (1, ()), []

        def search(self, vector, limit):
            self.calls += 1
            return {
                "threshold": -1,
                "matches": [{"person_id": 1, "face_id": 10, "name": "test", "similarity": -0.5}]
                if self.calls == 1
                else [],
            }

    cache, analyzer, gallery = TrackFaces(settings), StubFaces(), Gallery()
    for now in (10, 10.6):
        analyzer.score = 0.8 if now == 10 else 0.95
        tracks = people()
        cache.process(analyzer, sample_frame(now), tracks, {1})
        cache.match(gallery, tracks)
    assert tracks[0]["face"]["matches"] == []
    assert tracks[0]["face"]["comparison"]["supporting_samples"] == 1
    assert cache.tracks[1]["match_samples"][1]["frame_id"] == 100


def test_multiframe_bound_expiry_session_and_identity_change(app_context):
    settings = app_context[2]
    settings.face_analysis_interval = 0.2
    cache, analyzer = TrackFaces(settings), StubFaces()
    for i in range(8):
        cache.process(analyzer, sample_frame(10 + i * 0.3), people(), {1})
    assert len(cache.tracks[1]["samples"]) == 5
    cache.tracks[1]["match_samples"] = {1: cache.tracks[1]["samples"][0]}
    analyzer.accept = False
    cache.process(analyzer, sample_frame(15.2), people(), {1})
    assert cache.tracks[1]["samples"] == [] and cache.tracks[1]["best"] is None
    assert cache.tracks[1]["match_samples"] == {}
    analyzer.accept = True
    cache.process(analyzer, sample_frame(16), people(), {1})
    cache.process(analyzer, sample_frame(16.3, session="b" * 32), people(), {1})
    assert len(cache.tracks[1]["samples"]) == 1 and cache.tracks[1]["stream_session_id"] == "b" * 32
    vector = np.zeros(512, np.float32)
    vector[0] = 1
    analyzer.embed = lambda image: vector
    counts = cache.process(analyzer, sample_frame(16.6, session="b" * 32), people(), {1})
    assert counts["sample_resets"] == 1 and len(cache.tracks[1]["samples"]) == 1


def test_recognition_migration_matches_metadata_and_seeds_defaults(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'recognition.sqlite'}")
    added = {"function_settings", "recognition_logs"}
    Base.metadata.create_all(
        engine, tables=[table for table in Base.metadata.sorted_tables if table.name not in added]
    )
    path = Path(__file__).parents[1] / "migrations/versions/0006_recognition_controls.py"
    spec = importlib.util.spec_from_file_location("recognition_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    with Session(engine) as db:
        assert (
            db.get(FunctionSettings, 1).values == {} and db.get(FunctionSettings, 1).revision == 0
        )
    engine.dispose()


def test_slow_sampling_keeps_enough_time_for_consensus(app_context):
    settings = app_context[2]
    settings.face_analysis_interval = 10
    cache, analyzer = TrackFaces(settings), StubFaces()
    for now in range(10, 21):
        cache.process(analyzer, sample_frame(now), people(), {1})
    assert len(cache.tracks[1]["samples"]) == 2
