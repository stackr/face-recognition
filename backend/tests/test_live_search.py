"""Camera capability metadata for the Live Search controls, using private fixtures."""

import pytest
from app.models import CameraPermission, User
from conftest import TEST_PASSWORD
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.mark.parametrize(
    "role,granted,operate,expected",
    [
        ("admin", False, False, (True, True)),
        ("viewer", False, False, (False, False)),
        ("viewer", True, True, (True, False)),
        ("operator", True, False, (True, False)),
        ("operator", True, True, (True, True)),
    ],
)
def test_camera_capabilities_follow_role_and_committed_grants(
    app_context, admin_headers, role, granted, operate, expected
):
    client, engine, _ = app_context
    response = client.post(
        "/api/cameras",
        json={"name": "Live Search private fixture", "source_type": "mp4"},
        headers=admin_headers,
    )
    camera_id = response.json()["camera_id"]
    with Session(engine) as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        if role == "operator":
            user = User(username="operator", role=role, password_hash=admin.password_hash)
            db.add(user)
            db.flush()
        else:
            user = db.scalar(select(User).where(User.username == role))
        if granted:
            db.add(CameraPermission(camera_id=camera_id, user_id=user.id, can_operate=operate))
        db.commit()
    login = client.post("/api/auth/login", json={"username": role, "password": TEST_PASSWORD})
    assert login.status_code == 200
    for value in (client.get("/api/cameras").json()[0], client.get(f"/api/cameras/{camera_id}").json()):
        assert (value["can_view"], value["can_operate"]) == expected
        assert "password_hash" not in value and "rtsp_url_encrypted" not in value
