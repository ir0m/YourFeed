"""Genre names and terms translated for each chosen language.

Revision ID: 0004_translations
Revises: 0003_locale
"""

from alembic import op

revision = "0004_translations"
down_revision = "0003_locale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE genres ADD COLUMN translations jsonb NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE genres DROP COLUMN translations")
