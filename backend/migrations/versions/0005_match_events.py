"""Candidate events, session-scoped tracks and commit-ordered change journal."""

import sqlalchemy as sa
from alembic import op

revision = "0005_match_events"
down_revision = "0004_person_faces"
branch_labels = None
depends_on = None


def upgrade():
    state = op.create_table(
        "event_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_event_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.bulk_insert(state, [{"id": 1, "last_event_id": 0, "revision": 0}])
    op.create_table(
        "tracks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "camera_id",
            sa.Integer(),
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stream_session_id", sa.String(32), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("camera_id", "stream_session_id", "track_id", name="uq_track_session"),
    )
    op.create_index("ix_tracks_last_seen_at", "tracks", ["last_seen_at"])
    op.create_table(
        "match_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("persons.id", ondelete="SET NULL")),
        sa.Column("camera_id", sa.Integer(), sa.ForeignKey("cameras.id", ondelete="SET NULL")),
        sa.Column("track_pk", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="SET NULL")),
        sa.Column("stream_session_id", sa.String(32), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("face_similarity", sa.Float(), nullable=False),
        sa.Column("face_quality", sa.Float(), nullable=False),
        sa.Column("face_image_path", sa.String(64)),
        sa.Column("frame_image_path", sa.String(64)),
        sa.Column("video_clip_path", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("change_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("image_expires_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "camera_id", "stream_session_id", "track_id", "person_id", name="uq_event_candidate"
        ),
    )
    for field in ("person_id", "camera_id", "image_expires_at", "expires_at"):
        op.create_index("ix_match_events_" + field, "match_events", [field])
    op.create_table(
        "event_changes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("match_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_event_changes_event_id", "event_changes", ["event_id"])


def downgrade():
    for name in ("event_changes", "match_events", "tracks", "event_state"):
        op.drop_table(name)
