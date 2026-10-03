"""Persistent sampling controls and bounded recognition diagnostics."""

import sqlalchemy as sa
from alembic import op

revision = "0006_recognition_controls"
down_revision = "0005_match_events"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "function_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    from datetime import UTC, datetime

    op.bulk_insert(
        table,
        [
            {
                "id": 1,
                "revision": 0,
                "values": {},
                "updated_at": datetime.now(UTC).replace(tzinfo=None),
            }
        ],
    )
    op.create_table(
        "recognition_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "camera_id",
            sa.Integer(),
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stream_session_id", sa.String(32), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("frame_id", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("primary_reason", sa.String(40)),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("quality", sa.Float(), nullable=False),
        sa.Column("top_similarity", sa.Float()),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("settings_revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "camera_id",
            "stream_session_id",
            "track_id",
            "frame_id",
            name="uq_recognition_observation",
        ),
    )
    op.create_index("ix_recognition_camera_id_id", "recognition_logs", ["camera_id", "id"])
    op.create_index("ix_recognition_captured_at", "recognition_logs", ["captured_at"])
    op.create_index("ix_recognition_outcome_id", "recognition_logs", ["outcome", "id"])


def downgrade():
    op.drop_table("recognition_logs")
    op.drop_table("function_settings")
