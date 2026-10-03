import json
import threading
import time
import uuid
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from app.core.face_test_data import private_directory, write_metadata
from app.worker.api import create_worker
from app.worker.face_onnx import TEMPLATE, FaceModels
from app.worker.face_tests import FaceTestManager
from app.worker.video_faces import ExtractedFace, FaceGroups, VideoTestError, detect_all
from conftest import TEST_PASSWORD
from fastapi.testclient import TestClient
from test_analysis import replace_worker
from test_worker import TestDetector, wait_for, write_video


def embedding(index):
    vector = np.zeros(512, dtype=np.float32)
    vector[index] = 1
    return vector


def extracted(index, quality=10, box=(10, 10, 50, 50), content=b"jpeg"):
    return ExtractedFace(
        np.array(box, dtype=np.float64),
        content,
        None if index is None else embedding(index),
        quality,
    )


def test_grouping_repeats_keeps_best_and_separates_simultaneous_faces():
    groups = FaceGroups(0.7, 10)
    assert groups.add_frame([extracted(0), extracted(1)], 1, 0) == [(1, b"jpeg"), (2, b"jpeg")]
    assert groups.add_frame([extracted(0, 5), extracted(1, 20, content=b"best")], 2, 0.1) == [
        (2, b"best")
    ]
    groups.add_frame([extracted(0)], 10, 1.0)
    assert len(groups.groups) == 2
    assert groups.metadata()[0]["occurrences"] == 3
    assert groups.metadata()[0]["best_frame"] == 1
    assert groups.metadata()[1]["best_frame"] == 2
    # Two simultaneous detections cannot be the same person in one frame.
    groups.add_frame([extracted(0), extracted(0)], 11, 1.1)
    assert len(groups.groups) == 3
    assert all("embedding" not in row for row in groups.metadata())


def test_unaligned_faces_are_displayed_and_position_grouped_with_explicit_limit():
    groups = FaceGroups(0.7, 1)
    groups.add_frame([extracted(None)], 1, 0)
    groups.add_frame([extracted(None)], 2, 0.1)
    assert groups.metadata()[0]["occurrences"] == 2
    assert groups.metadata()[0]["embedding_ready"] is False
    with pytest.raises(VideoTestError, match="group_limit_exceeded"):
        groups.add_frame([extracted(None, box=(80, 80, 100, 100))], 3, 0.2)


def test_whole_frame_detector_has_no_live_ten_face_cap():
    class Models:
        def detect(self, image, *, side, max_faces):
            assert side == 640 and max_faces is None
            return [
                {
                    "bbox": np.array([i * 40, 10, i * 40 + 30, 50]),
                    "landmarks": TEMPLATE * 0.3 + [i * 40, 10],
                    "confidence": 0.9,
                }
                for i in range(12)
            ]

    image = np.zeros((120, 600, 3), dtype=np.uint8)
    assert len(detect_all(Models(), image, 100, threading.Event())) == 12
    with pytest.raises(VideoTestError, match="face_limit_exceeded"):
        detect_all(Models(), image, 10, threading.Event())
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(VideoTestError, match="cancelled"):
        detect_all(Models(), image, 100, cancel)


def test_overlapping_tiles_cover_edges_and_deduplicate_same_face():
    image = np.zeros((1080, 1920, 3), dtype=np.uint16)
    image[:, :, 0] = np.arange(1920)
    image[:, :, 1] = np.arange(1080)[:, None]
    calls = []

    class Models:
        def detect(self, roi, *, side, max_faces):
            x, y = roi[0, 0, :2].astype(int)
            calls.append((x, y, roi.shape[1], roi.shape[0]))
            # Both faces lie inside several overlapping detector regions.
            result = []
            for left, top in [(900, 400), (1810, 1000)]:
                if (
                    x <= left
                    and left + 50 <= x + roi.shape[1]
                    and y <= top
                    and top + 60 <= y + roi.shape[0]
                ):
                    result.append(
                        {
                            "bbox": np.array([left - x, top - y, left + 50 - x, top + 60 - y]),
                            "landmarks": TEMPLATE * 0.4 + [left - x, top - y],
                            "confidence": 0.9,
                        }
                    )
            return result

    assert len(detect_all(Models(), image, 100, threading.Event())) == 2
    assert len(calls) > 1
    assert any(x + w == 1920 and y + h == 1080 for x, y, w, h in calls)
    assert len(detect_all(Models(), image, 100, threading.Event(), min_face_size=50)) == 2
    assert detect_all(Models(), image, 100, threading.Event(), min_face_size=51) == []


def test_per_call_confidence_filters_onnx_scores_without_changing_live_default():
    class Models(FaceModels):
        def __init__(self):
            self.settings = SimpleNamespace(face_detection_threshold=0.5)

        def run(self, name, image, side, mean, std):
            assert name == "det_10g.onnx"
            counts = [2 * (side // stride) ** 2 for stride in (8, 16, 32)]
            scores = [np.zeros(count, np.float32) for count in counts]
            scores[0][(10 * (side // 8) + 10) * 2] = 0.6
            scores[0][(25 * (side // 8) + 25) * 2] = 0.9
            return (
                scores
                + [np.ones((count, 4), np.float32) for count in counts]
                + [np.zeros((count, 5, 2), np.float32) for count in counts]
            )

    models = Models()
    image = np.zeros((320, 320, 3), np.uint8)
    assert len(models.detect(image)) == 2
    assert len(models.detect(image, score_threshold=0.8)) == 1
    assert models.detect(image, score_threshold=0.95) == []
    assert models.settings.face_detection_threshold == 0.5
    assert len(models.detect(image)) == 2


def test_minimum_size_uses_shorter_original_frame_side_after_clipping():
    class Models:
        def detect(self, image, *, side, max_faces, score_threshold):
            assert score_threshold == 0.8
            return [
                {"bbox": np.array(box), "landmarks": TEMPLATE, "confidence": 0.9}
                for box in [
                    (10, 10, 34, 70),
                    (50, 10, 90, 41),
                    (100, 10, 140, 42),
                    (280, 10, 340, 70),
                ]
            ]

    image = np.zeros((120, 300, 3), np.uint8)
    faces = detect_all(
        Models(), image, 100, threading.Event(), detection_threshold=0.8, min_face_size=32
    )
    assert len(faces) == 1 and np.array_equal(faces[0]["bbox"], [100, 10, 140, 42])


class EmptyFaces:
    info = {"actual_device": "cpu", "model_version": "test"}

    def __init__(self):
        self.models = self
        self.calls = 0
        self.thresholds = []

    def detect(self, image, **kwargs):
        self.calls += 1
        self.thresholds.append(kwargs.get("score_threshold"))
        return []


def submit_immediate(analyzer, task):
    future = Future()
    try:
        future.set_result(task(analyzer))
    except Exception as exc:
        future.set_exception(exc)
    return future


def await_job(manager, job_id, owner=1):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = manager.get(job_id, owner)
        if result["state"] not in {"queued", "running"} and manager.active is None:
            return result
        time.sleep(0.01)
    pytest.fail("Video test did not finish")


def upload_file(manager, content):
    job_id = str(uuid.uuid4())
    (manager.incoming / f"{job_id}.video").write_bytes(content)
    return job_id


@pytest.mark.parametrize("options", [{}, {"detection_threshold": 0.85, "min_face_size": 48}])
def test_job_analyzes_every_frame_without_sampling_and_preserves_empty_result(app_context, options):
    settings = app_context[2]
    analyzer = EmptyFaces()
    runtime = SimpleNamespace(
        settings=settings,
        face_analyzer=analyzer,
        submit_face_task=lambda task: submit_immediate(analyzer, task),
    )
    video = settings.video_dir / "all-frames.mp4"
    write_video(video, seconds=1)
    manager = FaceTestManager(settings, runtime)
    try:
        job_id = upload_file(manager, video.read_bytes())
        original_threshold = settings.face_detection_threshold
        manager.start(job_id, 1, "blank.mp4", **options)
        result = await_job(manager, job_id)
        assert result["state"] == "completed"
        assert result["processed_frames"] == result["total_frames"] == analyzer.calls == 20
        assert result["progress_percent"] == 100 and result["groups"] == []
        expected_threshold = options.get("detection_threshold", original_threshold)
        assert result["detection_threshold"] == expected_threshold
        assert result["min_face_size"] == options.get("min_face_size", 8)
        assert analyzer.thresholds == [expected_threshold] * 20
        assert settings.face_detection_threshold == original_threshold
        saved = json.loads((manager.root / job_id / "job.json").read_text())
        assert saved["detection_threshold"] == expected_threshold
        assert saved["min_face_size"] == result["min_face_size"]
        assert not (manager.root / job_id / "source.video").exists()
        assert "owner_id" not in result
        with pytest.raises(KeyError):
            manager.get(job_id, 2)
        assert manager.list(2) == []
        assert manager.delete_all(2) == {"deleted_jobs": 0}
        assert manager.delete_all(1) == {"deleted_jobs": 1}
        assert not (manager.root / job_id).exists()
    finally:
        manager.close()


def test_invalid_video_fails_without_claiming_full_analysis(app_context):
    settings = app_context[2]
    runtime = SimpleNamespace(settings=settings, face_analyzer=EmptyFaces())
    manager = FaceTestManager(settings, runtime)
    try:
        job_id = upload_file(manager, b"invalid private video")
        manager.start(job_id, 1, "invalid.mp4")
        result = await_job(manager, job_id)
        assert result["state"] == "failed" and result["error_code"] == "invalid_video"
        assert result["processed_frames"] == 0
        assert not (manager.root / job_id / "source.video").exists()
    finally:
        manager.close()


def test_restart_marks_interrupted_job_and_retention_deletes_owned_data(app_context):
    settings = app_context[2]
    job_id, expired_id = str(uuid.uuid4()), str(uuid.uuid4())
    root = private_directory(settings.face_test_dir)
    for key, state, expiry in [
        (job_id, "running", datetime.now(UTC) + timedelta(hours=1)),
        (expired_id, "completed", datetime.now(UTC) - timedelta(hours=1)),
    ]:
        directory = private_directory(root / key)
        write_metadata(
            directory / "job.json",
            {
                "job_id": key,
                "owner_id": 1,
                "state": state,
                "expires_at": expiry.isoformat(),
                "created_at": datetime.now(UTC).isoformat(),
                "groups": [],
            },
        )
        (directory / "source.video").write_bytes(b"video")
    manager = FaceTestManager(settings, SimpleNamespace())
    try:
        assert manager.get(job_id, 1)["error_code"] == "worker_restarted"
        assert not (root / job_id / "source.video").exists()
        assert not (root / expired_id).exists()
    finally:
        manager.close()


def test_delete_cancels_in_progress_job_and_does_not_recreate_files(app_context):
    settings = app_context[2]
    analyzer = EmptyFaces()
    entered = threading.Event()

    def slow_submit(task):
        future = Future()
        entered.set()
        # A queued future can be cancelled without starting a GPU call.
        return future

    runtime = SimpleNamespace(
        settings=settings, face_analyzer=analyzer, submit_face_task=slow_submit
    )
    video = settings.video_dir / "cancel.mp4"
    write_video(video, seconds=1)
    manager = FaceTestManager(settings, runtime)
    try:
        job_id = upload_file(manager, video.read_bytes())
        manager.start(job_id, 1, "cancel.mp4")
        assert entered.wait(3)
        another = upload_file(manager, video.read_bytes())
        with pytest.raises(OverflowError):
            manager.start(another, 2, "another.mp4")
        assert manager.delete_all(1) == {"deleted_jobs": 1}
        assert manager.active is None and manager.list(1) == []
        assert not (manager.root / job_id).exists()
    finally:
        manager.close()


def test_authenticated_api_upload_limits_csrf_and_owner_boundary(app_context, admin_headers):
    client, _, settings = app_context
    calls = []
    job_id = str(uuid.uuid4())

    def handler(request):
        calls.append(request)
        if request.method == "GET" and request.url.path.endswith("/face-tests"):
            return httpx.Response(200, json={"items": [], "can_start": True})
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["owner_id"] == 1 and payload["filename"] == "test.mp4"
            assert payload["detection_threshold"] == 0.83 and payload["min_face_size"] == 48
            path = settings.face_test_dir / ".incoming" / f"{payload['job_id']}.video"
            assert path.read_bytes() == b"video"
            assert path.stat().st_mode & 0o777 == 0o600
            return httpx.Response(200, json={"job_id": payload["job_id"], "state": "queued"})
        if request.method == "DELETE":
            return httpx.Response(200, json={"deleted_jobs": 1})
        return httpx.Response(404)

    replace_worker(client, handler)
    assert (
        client.post(
            "/api/face-tests", content=b"video", headers={"Content-Type": "video/mp4"}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/face-tests", content=b"video", headers=admin_headers).status_code == 415
    )
    response = client.post(
        "/api/face-tests?filename=test.mp4&detection_threshold=0.83&min_face_size=48",
        content=b"video",
        headers={**admin_headers, "Content-Type": "video/mp4"},
    )
    assert response.status_code == 202 and response.json()["state"] == "queued"
    assert client.get(f"/api/face-tests/{job_id}").status_code == 404
    settings.video_upload_max_mb = 1
    assert (
        client.post(
            "/api/face-tests",
            content=b"x" * (2**20 + 1),
            headers={**admin_headers, "Content-Type": "video/mp4"},
        ).status_code
        == 413
    )
    client.post("/api/auth/login", json={"username": "viewer", "password": TEST_PASSWORD})
    assert client.get(f"/api/face-tests/{job_id}/groups/1/image").status_code == 404
    assert str(calls[-1].url.params["owner_id"]) == "2"
    client.cookies.clear()
    assert client.get("/api/face-tests").status_code == 401
    assert client.get(f"/api/face-tests/{job_id}/groups/1/image").status_code == 401


@pytest.mark.parametrize(
    "query",
    [
        "detection_threshold=0.09",
        "detection_threshold=1",
        "detection_threshold=nan",
        "detection_threshold=inf",
        "min_face_size=7",
        "min_face_size=513",
        "min_face_size=32.5",
        "min_face_size=",
    ],
)
def test_invalid_detection_settings_rejected_before_storing_upload(
    app_context, admin_headers, query
):
    client, _, settings = app_context
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    replace_worker(client, handler)
    response = client.post(
        f"/api/face-tests?{query}",
        content=b"video",
        headers={**admin_headers, "Content-Type": "video/mp4"},
    )
    assert response.status_code == 422
    assert calls == []
    assert not list(settings.face_test_dir.rglob("*.video"))


def test_worker_all_frame_tasks_use_shared_scheduler_and_require_service_auth(app_context):
    settings = app_context[2]
    analyzer = EmptyFaces()
    with TestClient(
        create_worker(settings, TestDetector(), face_analyzer=analyzer, enable_gallery=False)
    ) as client:
        assert client.get("/internal/face-tests?owner_id=1").status_code == 401
        client.headers["X-Service-Token"] = settings.service_token.get_secret_value()
        assert client.get("/internal/face-tests?owner_id=1").json()["defaults"] == {
            "detection_threshold": settings.face_detection_threshold,
            "min_face_size": 8,
        }
        video = settings.video_dir / "scheduled.mp4"
        write_video(video, seconds=1)
        manager = client.app.state.runtime.face_tests
        job_id = upload_file(manager, video.read_bytes())
        assert (
            client.post(
                "/internal/face-tests",
                json={
                    "job_id": job_id,
                    "owner_id": 1,
                    "filename": "scheduled.mp4",
                    "min_face_size": 7,
                },
            ).status_code
            == 422
        )
        assert (manager.incoming / f"{job_id}.video").exists()
        assert manager.list(1) == []
        assert (
            client.post(
                "/internal/face-tests",
                json={"job_id": job_id, "owner_id": 1, "filename": "scheduled.mp4"},
            ).status_code
            == 200
        )
        result = wait_for(
            client,
            f"/internal/face-tests/{job_id}?owner_id=1",
            lambda value: value["state"] == "completed",
        )
        assert result["processed_frames"] == analyzer.calls == 20
        assert result["detection_threshold"] == settings.face_detection_threshold
        assert result["min_face_size"] == 8
        assert client.get(f"/internal/face-tests/{job_id}?owner_id=2").status_code == 404
        assert (
            client.get(f"/internal/face-tests/{job_id}/groups/1/image?owner_id=2").status_code
            == 404
        )
        assert client.delete("/internal/face-tests?owner_id=1").json() == {"deleted_jobs": 1}


def test_upload_rejection_cleans_temporary_file_and_accept_failure_rolls_back(
    app_context, admin_headers, monkeypatch
):
    client, _, settings = app_context
    replace_worker(
        client,
        lambda request: (
            httpx.Response(200, json={"items": [], "can_start": True})
            if request.method == "GET"
            else httpx.Response(429)
        ),
    )
    response = client.post(
        "/api/face-tests", content=b"video", headers={**admin_headers, "Content-Type": "video/mp4"}
    )
    assert response.status_code == 429
    assert not list((settings.face_test_dir / ".incoming").glob("*.video"))
    manager = FaceTestManager(
        settings, SimpleNamespace(settings=settings, face_analyzer=EmptyFaces())
    )
    try:
        job_id = upload_file(manager, b"video")

        def failed_write(*args):
            raise OSError("test storage failure")

        monkeypatch.setattr("app.worker.face_tests.write_metadata", failed_write)
        with pytest.raises(OSError):
            manager.start(job_id, 1, "test.mp4")
        assert manager.jobs == {} and manager.active is None
        assert not (manager.root / job_id).exists()
    finally:
        manager.close()


def test_video_test_does_not_starve_live_camera_or_other_gpu_commands(app_context):
    from test_faces import StubFaces

    settings = app_context[2]

    class CombinedFaces(StubFaces):
        def __init__(self):
            super().__init__()
            self.models = self
            self.video_calls = 0

        def detect(self, image, **kwargs):
            self.video_calls += 1
            time.sleep(0.03)
            return []

    analyzer = CombinedFaces()
    video = settings.video_dir / "concurrent.mp4"
    write_video(video, seconds=5)
    test_video = settings.video_dir / "short-test.mp4"
    write_video(test_video, seconds=1)
    with TestClient(
        create_worker(settings, TestDetector(), face_analyzer=analyzer, enable_gallery=False)
    ) as client:
        client.headers["X-Service-Token"] = settings.service_token.get_secret_value()
        assert (
            client.post(
                "/internal/cameras/1/start",
                json={"source": str(video), "source_type": "mp4", "loop": True},
            ).status_code
            == 200
        )
        before = wait_for(client, "/internal/cameras/1", lambda row: row["processed_frames"] >= 2)
        runtime = client.app.state.runtime
        job_id = upload_file(runtime.face_tests, test_video.read_bytes())
        runtime.face_tests.start(job_id, 1, "concurrent.mp4")
        thread_name = runtime.submit_face_task(
            lambda analyzer: threading.current_thread().name
        ).result(timeout=3)
        assert thread_name == "shared-gpu"
        result = wait_for(
            client,
            f"/internal/face-tests/{job_id}?owner_id=1",
            lambda row: row["state"] == "completed",
        )
        after = client.get("/internal/cameras/1").json()
        assert result["processed_frames"] == analyzer.video_calls == 20
        assert after["processed_frames"] > before["processed_frames"]
        assert (
            after["stream_session_id"] == before["stream_session_id"]
            and after["state"] == "running"
        )


def test_idle_gpu_scheduler_releases_last_frame_and_returned_features(app_context):
    import gc
    import weakref

    settings = app_context[2]
    with TestClient(
        create_worker(settings, TestDetector(), face_analyzer=EmptyFaces(), enable_gallery=False)
    ) as client:
        image = np.zeros((120, 160, 3), dtype=np.uint8)
        image_reference = weakref.ref(image)
        future = client.app.state.runtime.submit_face_task(
            lambda analyzer, image=image: np.zeros(512, dtype=np.float32)
        )
        result = future.result(timeout=3)
        vector_reference = weakref.ref(result)
        del image, result, future
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            gc.collect()
            if image_reference() is None and vector_reference() is None:
                break
            time.sleep(0.01)
        assert image_reference() is None and vector_reference() is None
