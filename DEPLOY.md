# Deploying KMG Studio to Hetzner

**Target:** `studio.kmgtools.us` → `YOUR_SERVER_IP` (same server as Hemingway,
per your 7/17 confirmation — different subdomain, different port, same box)
**Stack:** Flask + Gunicorn + nginx + Let's Encrypt (identical pattern to Hemingway)

This mirrors Hemingway's `DEPLOY.md` exactly, adjusted for Studio's paths/port.

> This repo is public. The real server IP is never written here — it lives
> only in `.env` as `SERVER_IP` (gitignored). Everywhere below that says
> `YOUR_SERVER_IP`, substitute the value from your own `.env`.

---

## Step 1 — Push to GitHub

**1a. Create a new repo on GitHub**

Go to [github.com/new](https://github.com/new), name it `studio` (so it's
`github.com/KaiserMediaHub/studio`), set it to Public, don't add a README
or .gitignore — keep it empty.

**1b. Initialize git locally**

From the `studio` folder on your machine:

```bash
git init
git add .
git commit -m "KMG Studio — foundation skeleton"
```

**1c. Connect and push**

```bash
git remote add origin https://github.com/KaiserMediaHub/studio.git
git branch -M main
git push -u origin main
```

---

## Step 2 — SSH into the server

```bash
ssh root@YOUR_SERVER_IP
```

---

## Step 3 — Clone the repo

```bash
git clone https://github.com/KaiserMediaHub/studio.git /var/www/studio
cd /var/www/studio
```

---

## Step 4 — Set up Python environment

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install gunicorn
```

---

## Step 5 — Create the .env file on the server

```bash
nano /var/www/studio/.env
```

Paste this and fill in the values:

```
SECRET_KEY=some-long-random-string-here
APP_PASSWORD=your-studio-team-password-here
DB_PATH=/var/www/studio/data/studio.db
HEMINGWAY_BASE_URL=https://hemingway.kmgtools.us
HEMINGWAY_TEAM_PASSWORD=same-password-you-use-to-log-into-hemingway-directly
SERVER_IP=YOUR_SERVER_IP
```

Generate a good SECRET_KEY:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Create the data directory (matches Hemingway's convention — a `data/`
folder inside the app directory, not a separate mounted volume, since
Studio doesn't store large media itself):

```bash
mkdir -p /var/www/studio/data
```

---

## Step 6 — Test it manually

```bash
cd /var/www/studio
.venv/bin/python app.py
```

You should see it start on port 5000 (the `PORT` env var default — in
production gunicorn binds 3001 instead, per `studio.service`). Press
`Ctrl+C` to stop it.

---

## Step 7 — Create the systemd service

```bash
cp /var/www/studio/studio.service /etc/systemd/system/studio.service
systemctl daemon-reload
systemctl enable studio
systemctl start studio
systemctl status studio
```

Fix permissions so `www-data` can read/write:

```bash
chown -R www-data:www-data /var/www/studio
chmod 640 /var/www/studio/.env
systemctl restart studio
```

---

## Step 8 — Configure nginx

```bash
nano /etc/nginx/sites-available/studio
```

Paste this:

```nginx
server {
    listen 80;
    server_name studio.kmgtools.us;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }
}
```

Enable it:

```bash
ln -s /etc/nginx/sites-available/studio /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

---

## Step 9 — Point DNS in Cloudflare

1. Log into Cloudflare, select the `kmgtools.us` zone
2. DNS → Records → Add record
3. Type: A, Name: `studio`, IPv4: `YOUR_SERVER_IP` (from your `.env`), Proxy
   status: DNS only (grey cloud) for now — switch to Proxied after SSL works

---

## Step 10 — SSL with Let's Encrypt

```bash
certbot --nginx -d studio.kmgtools.us
```

Then switch Cloudflare's proxy status to Proxied (orange cloud).

---

## Updating the app later

```bash
ssh root@YOUR_SERVER_IP
cd /var/www/studio
git pull
.venv/bin/pip install -r requirements.txt   # only if requirements changed
systemctl restart studio
```

Exactly the same flow you already use for Hemingway — edit locally, push to
GitHub, pull + restart on the server.

---

## Troubleshooting

| Problem | Command |
|---|---|
| App not starting | `journalctl -u studio -n 50` |
| nginx errors | `nginx -t` then `journalctl -u nginx -n 20` |
| 502 Bad Gateway | Check `systemctl status studio` |
| Studio can't reach Hemingway | Check `HEMINGWAY_TEAM_PASSWORD` in `.env` matches Hemingway's real `TEAM_PASSWORD` |
