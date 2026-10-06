import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "studio_test_data", "test_studio_ebook.db")
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

pdf = os.path.join(tempfile.gettempdir(), "studio_test_ebook.pdf")
with open(pdf, "wb") as f:
    f.write(b"%PDF-fake")
os.environ["EBOOK_PATH"] = pdf

import app as app_module
import database

database.init_db()
results = {"pass": 0, "fail": 0}


def check(name, cond, detail=""):
    if cond:
        results["pass"] += 1
        print(f"PASS  {name}")
    else:
        results["fail"] += 1
        print(f"FAIL  {name}  {detail}")


def fresh():
    return app_module.app.test_client()  # NOT logged in -- public visitor


N = {"first_name": "Jane", "last_name": "Doe"}


def count():
    db = database.get_db()
    n = db.execute("SELECT COUNT(*) AS n FROM ebook_leads").fetchone()["n"]
    db.close()
    return n


c = fresh()
r = c.get("/ebooks/content-playbook")
check("page loads without login", r.status_code == 200 and b"Unlock download" in r.data, r.status_code)

c = fresh()
r = c.get("/ebooks/content-playbook/download")
check("download blocked before unlocking (redirects)", r.status_code == 302, r.status_code)

c = fresh()
r = c.post("/ebooks/content-playbook/unlock", data={**N, "email": "not-an-email"})
check("invalid email rejected", count() == 0 and "error=" in r.headers["Location"])

c = fresh()
r = c.post("/ebooks/content-playbook/unlock", data={**N, "email": "bot@x.com", "website": "http://spam"})
check("honeypot: nothing stored", count() == 0)

c = fresh()
r = c.post("/ebooks/content-playbook/unlock", data={**N, "email": "  Jane@Example.COM "})
check("valid email stored, lowercased/trimmed", count() == 1)
r = c.get("/ebooks/content-playbook/download")
check("download works after unlock", r.status_code == 200 and r.data == b"%PDF-fake", r.status_code)
check("download is an attachment", "attachment" in r.headers.get("Content-Disposition", ""))
r = c.get("/ebooks/content-playbook")
check("unlocked page shows download button", b'class="btn"' in r.data)

c2 = fresh()
c2.post("/ebooks/content-playbook/unlock", data={**N, "email": "jane@example.com"})
db = database.get_db()
row = db.execute("SELECT * FROM ebook_leads WHERE email = 'jane@example.com'").fetchone()
db.close()
check("repeat email: still one row, count incremented", count() == 1 and row["download_count"] == 2, dict(row))

c = fresh()
check("unlock does NOT grant access to the rest of Studio",
      (c.post("/ebooks/content-playbook/unlock", data={**N, "email": "a@b.co"}), c.get("/"))[1].status_code == 302)

c = fresh()
check("leads page blocked when logged out", c.get("/settings/ebook-leads").status_code == 302)
with c.session_transaction() as s:
    s["logged_in"] = True
    s["is_admin"] = True
c.post("/ebooks/content-playbook/unlock", data={**N, "email": "<script>x</script>@evil.com"})
r = c.get("/settings/ebook-leads")
check("admin sees leads, HTML escaped", r.status_code == 200 and b"<script>x" not in r.data and b"jane@example.com" in r.data)
r = c.get("/settings/ebook-leads?format=csv")
check("CSV export works", r.status_code == 200 and b"jane@example.com" in r.data)

# --- names (Ben's ask 2026-10-06) ---
c = fresh()
before = count()
c.post("/ebooks/content-playbook/unlock", data={"email": "noname@x.com"})
check("missing names rejected", count() == before)
c.post("/ebooks/content-playbook/unlock", data={"first_name": "A", "last_name": "  ", "email": "noname@x.com"})
check("blank last name rejected", count() == before)
c.post("/ebooks/content-playbook/unlock", data={"first_name": "  Mary   Ann ", "last_name": "O'Neil", "email": "mary@x.com"})
db = database.get_db()
row = db.execute("SELECT * FROM ebook_leads WHERE email = 'mary@x.com'").fetchone()
db.close()
check("names stored, whitespace collapsed", row and row["first_name"] == "Mary Ann" and row["last_name"] == "O'Neil", dict(row) if row else None)
r = fresh().get("/ebooks/content-playbook")
check("form shows name fields", b'name="first_name"' in r.data and b'name="last_name"' in r.data)

admin = fresh()
with admin.session_transaction() as sess:
    sess["logged_in"] = True; sess["is_admin"] = True
page = admin.get("/settings/ebook-leads").data
check("admin table shows names", b"Mary Ann" in page and b"O&#39;Neil" in page)
csvd = admin.get("/settings/ebook-leads?format=csv").data
check("CSV has name columns", csvd.startswith(b"first_name,last_name,email") and b'"Mary Ann","O\'Neil","mary@x.com"' in csvd, csvd[:160])
# formula injection via name is defused
admin.post("/ebooks/content-playbook/unlock", data={"first_name": "=HYPERLINK(\"x\")", "last_name": "+cmd", "email": "inj@x.com"})
csvd = admin.get("/settings/ebook-leads?format=csv").data
check("CSV defuses formula in names", b'"\'=HYPERLINK' in csvd and b'"\'+cmd"' in csvd, csvd[-200:])

# repeat visitor keeps original name
c.post("/ebooks/content-playbook/unlock", data={"first_name": "Different", "last_name": "Person", "email": "mary@x.com"})
db = database.get_db()
row = db.execute("SELECT * FROM ebook_leads WHERE email = 'mary@x.com'").fetchone()
db.close()
check("repeat visitor: original name kept, count bumped", row["first_name"] == "Mary Ann" and row["download_count"] == 2, dict(row))

# legacy row (created before name columns) gets names filled on return
db = database.get_db()
db.execute("INSERT INTO ebook_leads (email) VALUES ('legacy@x.com')")
db.commit(); db.close()
c.post("/ebooks/content-playbook/unlock", data={"first_name": "Leo", "last_name": "Gacy", "email": "legacy@x.com"})
db = database.get_db()
row = db.execute("SELECT * FROM ebook_leads WHERE email = 'legacy@x.com'").fetchone()
db.close()
check("legacy NULL-name row filled in on return", row["first_name"] == "Leo" and row["last_name"] == "Gacy", dict(row))

# migration: old-schema table (no name columns) upgrades cleanly
import sqlite3
old = os.path.join(tempfile.gettempdir(), "studio_test_data", "old_schema.db")
if os.path.exists(old): os.remove(old)
conn = sqlite3.connect(old)
conn.execute("CREATE TABLE ebook_leads (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE, first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, download_count INTEGER NOT NULL DEFAULT 1)")
conn.execute("INSERT INTO ebook_leads (email) VALUES ('old@x.com')")
conn.commit(); conn.close()
real_path = os.environ["DB_PATH"]
os.environ["DB_PATH"] = old
import importlib
importlib.reload(database)
database.init_db()
conn = sqlite3.connect(old)
cols = [r[1] for r in conn.execute("PRAGMA table_info(ebook_leads)")]
kept = conn.execute("SELECT COUNT(*) FROM ebook_leads").fetchone()[0]
conn.close()
check("migration adds name columns, keeps existing rows", "first_name" in cols and "last_name" in cols and kept == 1, cols)
database.init_db()  # second run must not error
check("migration is re-runnable", True)
os.environ["DB_PATH"] = real_path
importlib.reload(database)

# nginx hand-off: Flask only gates, nginx streams the file
os.environ["EBOOK_XACCEL_URI"] = "/_ebook_file/ebook.pdf"
c = fresh()
check("xaccel: locked visitor gets no hand-off header",
      "X-Accel-Redirect" not in c.get("/ebooks/content-playbook/download").headers)
c.post("/ebooks/content-playbook/unlock", data={**N, "email": "x@y.co"})
r = c.get("/ebooks/content-playbook/download")
check("xaccel: unlocked visitor gets X-Accel-Redirect, empty body",
      r.headers.get("X-Accel-Redirect") == "/_ebook_file/ebook.pdf" and r.data == b"")
check("xaccel: attachment + pdf headers set",
      "attachment" in r.headers.get("Content-Disposition", "") and r.headers["Content-Type"] == "application/pdf")
del os.environ["EBOOK_XACCEL_URI"]

check("old /ebook URL is gone", fresh().get("/ebook").status_code == 302)

os.environ["EBOOK_PATH"] = "/nonexistent.pdf"
c = fresh()
c.post("/ebooks/content-playbook/unlock", data={**N, "email": "q@r.co"})
check("missing PDF gives 503, not a crash", c.get("/ebooks/content-playbook/download").status_code == 503)

print(f"\n{results['pass']} passed, {results['fail']} failed")
sys.exit(1 if results["fail"] else 0)
