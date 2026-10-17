"""Add content_text and pages_json to documents table for persistent text retention.

Revision ID: 20261004_0004
Revises: 20261004_0003
Create Date: 2026-10-17 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20261004_0004"
down_revision = "20261004_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("content_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("pages_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "pages_json")
    op.drop_column("documents", "content_text")
