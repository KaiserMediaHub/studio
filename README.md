# KMG Studio

Unified front door for Degas Clips + Hemingway, per `STUDIO_SYSTEM_DESIGN.md`
in the parent project folder. This is the **foundation skeleton** — build
step 1 of 13 (Section 9 of the design doc).

## What's here right now

- `app.py` — Flask app, placeholder shared-password login (same pattern as
  Degas/Hemingway today), a dashboard route that lists clients and their
  (currently empty) projects.
- `database.py` — SQLite schema: `clients`, `projects`, `posts`,
  `glossary_terms`. `clients` is seeded with the 4 known clients (Epiphany,
  Harris Projects, Ben G Kaiser, Innovation Portal) so there's real data to
  build against immediately.
- Deployment files (`setup.sh`, `studio.service`, `.env.example`) mirror
  Degas's proven Hetzner pattern exactly — gunicorn + systemd, port 5001 so
  it doesn't collide with Degas (5000) if they end up on the same box.

## What's NOT here yet (later build steps)

- Real unified auth (Degas/Hemingway accepting Studio's session) — step 2,
  blocked on getting Hemingway's actual source confirmed first.
- Degas `client_id` migration — step 3.
- Whisper base→small — step 4.
- Phase tracking reading Degas's real status — step 5.
- Caption Review flagging / glossary UI — step 6.
- Global style layer — steps 7-8, also blocked on the doc's content
  actually being written.
- Quick-post flow — step 9.
- Postiz integration + calendar — steps 10-11.

## Running locally

```bash
cd studio
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit SECRET_KEY / APP_PASSWORD
python app.py
```

Then open `http://localhost:5000`.

## Deploying to Hetzner

`setup.sh` assumes a git repo at `github.com/KaiserMediaHub/Studio.git` —
**that repo doesn't exist yet**. Before this can be deployed the way Degas
was, we need to agree on how code actually gets from here to the server
(push to a new GitHub repo, or another method) — flagged back to Ben,
not assumed.
