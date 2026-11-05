"""Initial schema.

Revision ID: 0001_initial
Revises:
"""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE settings (
      id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
      interval_minutes integer NOT NULL DEFAULT 60
        CHECK (interval_minutes IN (15, 30, 60, 120, 180, 360, 720, 1440)),
      items_per_run integer NOT NULL DEFAULT 20 CHECK (items_per_run BETWEEN 1 AND 50),
      retention_hours integer NOT NULL DEFAULT 24 CHECK (retention_hours BETWEEN 1 AND 168),
      llm_provider text NOT NULL DEFAULT 'openai'
        CHECK (llm_provider IN ('openai', 'anthropic', 'claude_cli', 'codex_cli', 'gemini_cli')),
      llm_model text NOT NULL DEFAULT '',
      refresh_requested_at timestamptz,
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    INSERT INTO settings (id) VALUES (1);

    CREATE TABLE genres (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 40),
      position integer NOT NULL DEFAULT 0,
      weight integer NOT NULL DEFAULT 10 CHECK (weight BETWEEN 0 AND 100),
      sites text[] NOT NULL DEFAULT '{}',
      focus_terms text[] NOT NULL DEFAULT '{}',
      exclude_terms text[] NOT NULL DEFAULT '{}',
      discovery_percent integer NOT NULL DEFAULT 20 CHECK (discovery_percent BETWEEN 0 AND 100),
      min_chars integer NOT NULL DEFAULT 100,
      max_chars integer NOT NULL DEFAULT 160,
      stance smallint NOT NULL DEFAULT 1 CHECK (stance BETWEEN 0 AND 3),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CHECK (min_chars BETWEEN 40 AND 800 AND max_chars BETWEEN 40 AND 800 AND min_chars <= max_chars)
    );
    CREATE UNIQUE INDEX genres_name_key ON genres (lower(name));

    -- Search results. A URL is used once across all genres.
    CREATE TABLE items (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      genre_id uuid NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
      url text NOT NULL UNIQUE,
      title text NOT NULL,
      snippet text,
      site_name text,
      published_at timestamptz,
      run_id bigint,
      rank integer NOT NULL DEFAULT 0,
      found_at timestamptz NOT NULL DEFAULT now(),
      attempted_at timestamptz,
      skip_reason text,
      posted_at timestamptz
    );
    CREATE INDEX items_unposted ON items (genre_id, found_at DESC) WHERE posted_at IS NULL;

    CREATE TABLE summaries (
      item_id uuid PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
      title text NOT NULL,
      facts text NOT NULL,
      interpretation text NOT NULL DEFAULT '',
      provider text NOT NULL,
      model text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE timeline_entries (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      item_id uuid NOT NULL UNIQUE REFERENCES items(id) ON DELETE CASCADE,
      genre_id uuid NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
      title text NOT NULL,
      facts text NOT NULL,
      interpretation text NOT NULL DEFAULT '',
      added_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX timeline_entries_order ON timeline_entries (added_at DESC, id DESC);

    CREATE TABLE runs (
      id bigserial PRIMARY KEY,
      started_at timestamptz NOT NULL DEFAULT now(),
      finished_at timestamptz,
      status text NOT NULL DEFAULT 'running',
      target integer NOT NULL DEFAULT 0,
      posted integer NOT NULL DEFAULT 0,
      found integer NOT NULL DEFAULT 0,
      found_target integer NOT NULL DEFAULT 0,
      searches integer NOT NULL DEFAULT 0,
      search_error text,
      llm_calls integer NOT NULL DEFAULT 0,
      input_tokens integer NOT NULL DEFAULT 0,
      output_tokens integer NOT NULL DEFAULT 0,
      error text,
      detail jsonb NOT NULL DEFAULT '{}'
    );
    CREATE INDEX runs_started ON runs (started_at DESC);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE runs, timeline_entries, summaries, items, genres, settings;
    """)
