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
            title             TEXT,                      -- Hemingway's per-section title (e.g.
                                                          -- "01 - clip_filename"), so Ben can tell
                                                          -- which video a post's copy came from
                                                          -- without opening the clip (Ben's ask,
                                                          -- 2026-08-26). NULL for quick posts.
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
        )
    """)
    # Safe migration for existing DBs created before hemingway_post_id/clip_id
    # existed -- same try/except pattern used for Degas's client_id column (task #6).
    for stmt in (
        "ALTER TABLE posts ADD COLUMN hemingway_post_id INTEGER",
        "ALTER TABLE posts ADD COLUMN clip_id INTEGER",
        "ALTER TABLE posts ADD COLUMN title TEXT",
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

    # Cloud KMG (Nextcloud) linkage (task #28) -- one Hemingway client maps
    # to one top-level Nextcloud folder name, e.g. 'Epiphany' -> /Epiphany.
    # Studio auto-creates /Epiphany/incoming and /Epiphany/captioned inside
    # it (ensure_folder()) the first time this is saved -- team members drop
    # raw footage in /incoming, Studio pushes finished exports to
    # /captioned. Same "Studio owns linkage the other service doesn't know
    # about" pattern as client_postiz_groups above.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_nextcloud_folders (
            client_id      INTEGER PRIMARY KEY,   -- Hemingway's client id
            folder_name    TEXT NOT NULL,
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    # Transcript/video review flags (Ben's ask, 2026-08-27). Team members
    # reviewing a clip can flag "needs transcript review" and/or "needs
    # video review" -- purely visual QA markers, independent of the
    # confidence-based glossary flagging above. One row per (project, clip);
    # both columns default to 0 (unflagged/normal). "Approved" isn't a
    # separate column -- it's an action that zeroes both columns back out
    # (see clip_review_flags_approve() in app.py), so there's nothing to
    # get out of sync between an "approved" flag and the two review flags.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clip_review_flags (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id          INTEGER NOT NULL,
            clip_id             INTEGER NOT NULL,   -- Degas's own clip id
            review_transcript   INTEGER NOT NULL DEFAULT 0,
            review_video        INTEGER NOT NULL DEFAULT 0,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, clip_id)
        )
    """)

    # Access codes (Ben's ask, 2026-09-03): replaces the single shared
    # APP_PASSWORD with a table of labeled, independently-revocable
    # passwords -- "not necessarily user-based," just distinct secrets Ben
    # can hand out per person/role and pull individually. is_admin marks
    # the one code that can see/manage this table; everyone else just logs
    # in and uses Studio normally. revoked_at is checked on EVERY request
    # (see require_login() in app.py), not just at login -- revoking a code
    # kicks out any session using it immediately, not on next login attempt.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS access_codes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            label         TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            revoked_at    TIMESTAMP,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Seed exactly once, from the existing APP_PASSWORD env var, so this
    # migration doesn't lock Ben out on first deploy -- his current password
    # keeps working, now as the admin code labeled "Admin (original)".
    existing = conn.execute("SELECT COUNT(*) AS n FROM access_codes").fetchone()["n"]
    if existing == 0:
        from werkzeug.security import generate_password_hash
        seed_password = os.environ.get("APP_PASSWORD", "studio2026")
        conn.execute(
            "INSERT INTO access_codes (label, password_hash, is_admin) VALUES (?, ?, 1)",
            ("Admin (original)", generate_password_hash(seed_password))
        )

    conn.commit()
    conn.close()
