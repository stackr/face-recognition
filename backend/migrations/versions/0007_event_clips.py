"""Private event clips with durable asynchronous status."""

import sqlalchemy as sa
from alembic import op

revision = "0007_event_clips"
down_revision = "0006_recognition_controls"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "match_events",
        sa.Column("clip_state", sa.String(16), nullable=False, server_default="disabled"),
    )
    op.add_column("match_events", sa.Column("clip_error", sa.String(40)))
    op.add_column(
        "match_events",
        sa.Column("clip_details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("match_events", sa.Column("clip_expires_at", sa.DateTime()))
    op.create_index("ix_match_events_clip_expires_at", "match_events", ["clip_expires_at"])


def downgrade():
    op.drop_index("ix_match_events_clip_expires_at", table_name="match_events")
    for name in ("clip_expires_at", "clip_details", "clip_error", "clip_state"):
        op.drop_column("match_events", name)
