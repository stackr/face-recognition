"""Explicit camera grants for non-admin analysis access."""

import sqlalchemy as sa
from alembic import op

revision = "0003_camera_permissions"
down_revision = "0002_video_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "camera_permissions",
        sa.Column(
            "camera_id",
            sa.Integer(),
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("can_operate", sa.Boolean(), nullable=False),
    )


def downgrade():
    op.drop_table("camera_permissions")
