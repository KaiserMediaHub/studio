import os

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify
)

from database import init_db, get_db
import hemingway_client

# ── Config ────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")

# Placeholder auth for this first skeleton: same shared-password session
# pattern already proven in Degas and Hemingway. This is NOT the final
# unified-auth story (STUDIO_SYSTEM_DESIGN.md Section 2/9, step 2) -- that
# requires Degas and Hemingway to accept Studio's session instead of their
# own login, which needs their real source confirmed first. This gets
# Studio itself running and testable while that's worked out.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "studio2026")


@app.before_request
def require_login():
    public = {"login", "static", "health"}
    if request.endpoint in public:
        return
    if not session.get("logged_in"):
        return redirect(url_for("login"))


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        error = "Incorrect password — try again."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Clients ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    # Corrected 7/17: no local clients table -- Studio calls Hemingway's
    # real /api/clients directly (hemingway_client.py) as the single
    # source of truth, rather than keeping a copy that can drift.
    try:
        clients = hemingway_client.get_clients()
    except hemingway_client.HemingwayError as e:
        return render_template("error.html", message=str(e)), 502

    active_client_id = request.args.get("client_id", type=int)
    active_client = None
    projects = []
    if clients:
        active_client = next(
            (c for c in clients if c["id"] == active_client_id),
            clients[0]
        )
        db = get_db()
        projects = db.execute(
            "SELECT * FROM projects WHERE client_id = ? ORDER BY created_at DESC",
            (active_client["id"],)
        ).fetchall()
        db.close()

    return render_template(
        "dashboard.html",
        clients=clients,
        active_client=active_client,
        projects=projects,
    )


@app.route("/api/clients")
def api_clients():
    try:
        return jsonify(hemingway_client.get_clients())
    except hemingway_client.HemingwayError as e:
        return jsonify({"error": str(e)}), 502


# ── Startup ───────────────────────────────────────────────────────────────────
_initialized = False


@app.before_request
def ensure_initialized():
    global _initialized
    if not _initialized:
        init_db()
        _initialized = True


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
