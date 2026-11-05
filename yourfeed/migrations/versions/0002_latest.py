"""Per-genre freshness and model-made search topics.

Revision ID: 0002_latest
Revises: 0001_initial
"""

from alembic import op

revision = "0002_latest"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE genres
      ADD COLUMN latest boolean NOT NULL DEFAULT true,
      ADD COLUMN topics text[] NOT NULL DEFAULT '{}',
      ADD COLUMN topic_index integer NOT NULL DEFAULT 0
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE genres DROP COLUMN topic_index, DROP COLUMN topics, DROP COLUMN latest")
