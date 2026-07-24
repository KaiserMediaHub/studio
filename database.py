import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "studio.db"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()

    # No local `clients` table -- corrected 7/17. Hemingway's own
    # `clients` table (id, name, style_rules, created_at) is simple and
    # already canonical; Studio calls its /api/clients directly via
    # hemingway_client.py rather than keeping a local copy that could
    # drift out of sync. `client_id` values below are Hemingway's own
    # client IDs, not a local foreign key -- there's nothing local to
    # reference against, so no FOREIGN KEY constraint on client_id here.

    # Studio-owned project/phase tracking. Intake-through-Clipped phases are
    # read from Degas's own clips.status once linked (Section 4's "major
    # simplification" -- Studio doesn't duplicate that state, it reads it).
    # Drafting/Post Review/Scheduled/Published are genuinely new state Studio
    # owns, since they don't exist in Degas or Hemingway's data model today.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id          INTEGER NOT NULL,      -- Hemingway's client id
            name               TEXT NOT NULL,
            degas_project_id   INTEGER,                -- FK into Degas's own projects table
            phase              TEXT DEFAULT 'intake',
            hemingway_batch_id INTEGER,                -- set once "Write Posts" has generated
                                                          -- a real batch from this project's transcript
            archived_at        TIMESTAMP,               -- NULL = active; set = archived, hidden by default
            created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Safe migrations for existing DBs created before these columns existed --
    # same try/except pattern used elsewhere in this file.
    for stmt in (
        "ALTER TABLE projects ADD COLUMN hemingway_batch_id INTEGER",
        "ALTER TABLE projects ADD COLUMN archived_at TIMESTAMP",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass

    # Per-post scheduling status -- deliberately separate from project.phase
    # (Section 4: "phase tracking is two-tiered" -- a batch moves through
    # Intake..Post Review together, but each post gets scheduled/published
    # independently).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id         INTEGER NOT NULL,       -- Hemingway's client id
            project_id        INTEGER,                -- null for quick posts (Section 9)
            source            TEXT DEFAULT 'pipeline', -- 'pipeline' or 'quick'
            caption           TEXT,
            media_ref         TEXT,                    -- Drive file/folder reference
            status            TEXT DEFAULT 'draft',     -- draft -> scheduled -> published
            postiz_post_id    TEXT,
            scheduled_for     TIMESTAMP,
            hemingway_post_id INTEGER,                  -- Hemingway's own posts.id, so quick
                                                          -- posts can call /api/posts/<id>/rewrite
                                                          -- to regenerate (task #11)
            clip_id           INTEGER,                   -- Degas clip this post's copy was
                                                          -- generated from (project-sourced
                                                          -- posts only, NULL for quick posts) --
                                                          -- lets scheduling attach the matching
                                                          -- exported video for YouTube/Instagram
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
        )
    """)
    # Safe migration for existing DBs created before hemingway_post_id/clip_id
    # existed -- same try/except pattern used for Degas's client_id column (task #6).
    for stmt in (
        "ALTER TABLE posts ADD COLUMN hemingway_post_id INTEGER",
        "ALTER TABLE posts ADD COLUMN clip_id INTEGER",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass

    # Glossary system (Section 4) -- semi-automatic growth, client_id NULL
    # means a global entry. Status starts 'pending' (auto-detected candidate
    # from a Caption Review edit) and needs one click to 'confirmed'.
    # Postiz customer-group linkage (task #12) -- one Hemingway client maps to
    # one Postiz group (customer). This is genuinely new Studio-owned data:
    # Hemingway has no concept of Postiz, and Postiz's groups are identified
    # by its own opaque IDs, not Hemingway's client IDs.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_postiz_groups (
            client_id          INTEGER PRIMARY KEY,   -- Hemingway's client id
            postiz_group_id    TEXT NOT NULL,
            postiz_group_name  TEXT,
            updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS glossary_terms (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id         INTEGER,                -- NULL = global; else Hemingway's client id
            term              TEXT NOT NULL,
            category          TEXT DEFAULT 'other',    -- name / company / figure / other
            status            TEXT DEFAULT 'pending',  -- pending / confirmed
            occurrence_count  INTEGER DEFAULT 1,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
