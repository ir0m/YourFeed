"""Fictional people whose posts are written by the model and shown on the timeline beside the summaries.

Revision ID: 0007_personas
Revises: 0006_llm_fallbacks
"""

from alembic import op

revision = "0007_personas"
down_revision = "0006_llm_fallbacks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE personas (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 40),
      profile text NOT NULL DEFAULT '' CHECK (char_length(profile) <= 1000),
      topics text NOT NULL DEFAULT '' CHECK (char_length(topics) <= 1000),
      tone text NOT NULL DEFAULT '' CHECK (char_length(tone) <= 1000),
      min_chars integer NOT NULL DEFAULT 40,
      max_chars integer NOT NULL DEFAULT 140,
      position integer NOT NULL DEFAULT 0,
      weight integer NOT NULL DEFAULT 10 CHECK (weight BETWEEN 0 AND 100),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CHECK (min_chars BETWEEN 10 AND 500 AND max_chars BETWEEN 10 AND 500 AND min_chars <= max_chars)
    );
    CREATE UNIQUE INDEX personas_name_key ON personas (lower(name));

    -- A post is either a summary of an article of a genre, or a persona's own post.
    ALTER TABLE timeline_entries
      ALTER COLUMN item_id DROP NOT NULL,
      ALTER COLUMN genre_id DROP NOT NULL,
      ADD COLUMN persona_id uuid REFERENCES personas(id) ON DELETE CASCADE,
      ADD CONSTRAINT timeline_entries_source CHECK (
        (item_id IS NOT NULL AND genre_id IS NOT NULL AND persona_id IS NULL)
        OR (item_id IS NULL AND genre_id IS NULL AND persona_id IS NOT NULL));
    CREATE INDEX timeline_entries_persona ON timeline_entries (persona_id, added_at DESC)
      WHERE persona_id IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("""
    DELETE FROM timeline_entries WHERE persona_id IS NOT NULL;
    ALTER TABLE timeline_entries
      DROP CONSTRAINT timeline_entries_source,
      DROP COLUMN persona_id,
      ALTER COLUMN item_id SET NOT NULL,
      ALTER COLUMN genre_id SET NOT NULL;
    DROP TABLE personas;
    """)
