"""Per-genre countries or regions and languages for search, and whether summaries are translated.

Revision ID: 0003_locale
Revises: 0002_latest
"""

from alembic import op

revision = "0003_locale"
down_revision = "0002_latest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE genres
      ADD COLUMN locales text[] NOT NULL DEFAULT '{ja-JP}' CHECK (cardinality(locales) >= 1),
      ADD COLUMN translate boolean NOT NULL DEFAULT true
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE genres DROP COLUMN translate, DROP COLUMN locales")
