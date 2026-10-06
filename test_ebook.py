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
r = c.post("/ebooks/content-playbook/unlock", data={"email": "not-an-email"})
check("invalid email rejected", count() == 0 and "error=" in r.headers["Location"])

c = fresh()
r = c.post("/ebooks/content-playbook/unlock", data={"email": "bot@x.com", "website": "http://spam"})
check("honeypot: nothing stored", count() == 0)

c = fresh()
r = c.post("/ebooks/content-playbook/unlock", data={"email": "  Jane@Example.COM "})
check("valid email stored, lowercased/trimmed", count() == 1)
r = c.get("/ebooks/content-playbook/download")
check("download works after unlock", r.status_code == 200 and r.data == b"%PDF-fake", r.status_code)
check("download is an attachment", "attachment" in r.headers.get("Content-Disposition", ""))
r = c.get("/ebooks/content-playbook")
check("unlocked page shows download button", b'class="btn"' in r.data)

c2 = fresh()
c2.post("/ebooks/content-playbook/unlock", data={"email": "jane@example.com"})
db = database.get_db()
row = db.execute("SELECT * FROM ebook_leads WHERE email = 'jane@example.com'").fetchone()
db.close()
check("repeat email: still one row, count incremented", count() == 1 and row["download_count"] == 2, dict(row))

c = fresh()
check("unlock does NOT grant access to the rest of Studio",
      (c.post("/ebooks/content-playbook/unlock", data={"email": "a@b.co"}), c.get("/"))[1].status_code == 302)

c = fresh()
check("leads page blocked when logged out", c.get("/settings/ebook-leads").status_code == 302)
with c.session_transaction() as s:
    s["logged_in"] = True
    s["is_admin"] = True
c.post("/ebooks/content-playbook/unlock", data={"email": "<script>x</script>@evil.com"})
r = c.get("/settings/ebook-leads")
check("admin sees leads, HTML escaped", r.status_code == 200 and b"<script>x" not in r.data and b"jane@example.com" in r.data)
r = c.get("/settings/ebook-leads?format=csv")
check("CSV export works", r.status_code == 200 and b"jane@example.com" in r.data)

# nginx hand-off: Flask only gates, nginx streams the file
os.environ["EBOOK_XACCEL_URI"] = "/_ebook_file/ebook.pdf"
c = fresh()
check("xaccel: locked visitor gets no hand-off header",
      "X-Accel-Redirect" not in c.get("/ebooks/content-playbook/download").headers)
c.post("/ebooks/content-playbook/unlock", data={"email": "x@y.co"})
r = c.get("/ebooks/content-playbook/download")
check("xaccel: unlocked visitor gets X-Accel-Redirect, empty body",
      r.headers.get("X-Accel-Redirect") == "/_ebook_file/ebook.pdf" and r.data == b"")
check("xaccel: attachment + pdf headers set",
      "attachment" in r.headers.get("Content-Disposition", "") and r.headers["Content-Type"] == "application/pdf")
del os.environ["EBOOK_XACCEL_URI"]

check("old /ebook URL is gone", fresh().get("/ebook").status_code == 302)

os.environ["EBOOK_PATH"] = "/nonexistent.pdf"
c = fresh()
c.post("/ebooks/content-playbook/unlock", data={"email": "q@r.co"})
check("missing PDF gives 503, not a crash", c.get("/ebooks/content-playbook/download").status_code == 503)

print(f"\n{results['pass']} passed, {results['fail']} failed")
sys.exit(1 if results["fail"] else 0)
