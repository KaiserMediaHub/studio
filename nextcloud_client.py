"""
Server-to-server client for Cloud KMG (self-hosted Nextcloud), task #28 --
Ben's ask 8/3: connect Studio to his new Nextcloud instance so remote team
members can drop raw footage in one place and pull it into a project without
downloading to a personal computer first, then push finished captioned clips
back out for the team to grab.

Same "unified front door" pattern as degas_client.py/hemingway_client.py/
postiz_client.py: Studio's backend holds one dedicated Nextcloud login (an
app password, generated specifically for Studio -- not Ben's personal admin
login, see the reference doc's own recommendation) and the browser never
talks to cloud.kmgtools.us directly.

Only WebDAV is used here (remote.php/dav/files/<username>/<path>) -- list,
upload, download, and create folders. Nextcloud's other API, the OCS Share
API, is for generating share links; nothing in this task needs a share link
since Studio pulls files in and pushes them out directly rather than working
through links a person would click.
"""

import os
from urllib.parse import urlparse, unquote
import xml.etree.ElementTree as ET

import requests
from requests.auth import HTTPBasicAuth

NEXTCLOUD_BASE_URL = os.environ.get("NEXTCLOUD_BASE_URL", "https://cloud.kmgtools.us")
NEXTCLOUD_USERNAME = os.environ.get("NEXTCLOUD_USERNAME", "")
NEXTCLOUD_APP_PASSWORD = os.environ.get("NEXTCLOUD_APP_PASSWORD", "")

DAV_NS = "{DAV:}"


class NextcloudError(Exception):
    pass


def _auth():
    if not NEXTCLOUD_USERNAME or not NEXTCLOUD_APP_PASSWORD:
        raise NextcloudError(
            "NEXTCLOUD_USERNAME / NEXTCLOUD_APP_PASSWORD are not set -- Studio can't log into "
            "Cloud KMG. Create a dedicated app password (Nextcloud Settings > Security > "
            "Devices & sessions) and add both to .env."
        )
    return HTTPBasicAuth(NEXTCLOUD_USERNAME, NEXTCLOUD_APP_PASSWORD)


def _dav_url(path):
    path = path.strip("/")
    return f"{NEXTCLOUD_BASE_URL}/remote.php/dav/files/{NEXTCLOUD_USERNAME}/{path}"


def _dav_path(path):
    """Path-only portion (no scheme/host) of the WebDAV URL for `path` --
    used to recognize/skip the 'self' entry PROPFIND always returns for the
    requested collection itself, alongside its children."""
    return urlparse(_dav_url(path)).path.rstrip("/")


def _request(method, path, **kwargs):
    try:
        resp = requests.request(method, _dav_url(path), auth=_auth(), timeout=30, **kwargs)
    except requests.exceptions.RequestException as e:
        raise NextcloudError(f"Couldn't reach Cloud KMG at {path}: {e}") from e
    if resp.status_code >= 400 and resp.status_code != 404:
        raise NextcloudError(f"Cloud KMG error ({resp.status_code}) on {path}: {resp.text[:200]}")
    return resp


def ensure_folder(path):
    """Creates a folder if it doesn't already exist -- and, since WebDAV's
    MKCOL isn't recursive, each parent segment along the way too. 405
    (already exists) and 409 (parent just got created by a concurrent
    request) both mean 'fine, it's there now', not real errors."""
    parts = [p for p in path.strip("/").split("/") if p]
    built = ""
    for part in parts:
        built = f"{built}/{part}" if built else part
        try:
            resp = requests.request("MKCOL", _dav_url(built), auth=_auth(), timeout=15)
        except requests.exceptions.RequestException as e:
            raise NextcloudError(f"Couldn't reach Cloud KMG creating folder '{built}': {e}") from e
        if resp.status_code not in (201, 405, 409):
            raise NextcloudError(f"Couldn't create Cloud KMG folder '{built}' ({resp.status_code}): {resp.text[:200]}")


def list_folder(path):
    """Returns [{name, size, last_modified}, ...] for files directly inside
    `path` (not recursive, and folders themselves are excluded -- Studio
    only ever needs a flat file list here). Returns [] if the folder
    doesn't exist yet rather than raising -- a brand new client's /incoming
    folder legitimately might not exist until ensure_folder() runs or the
    first file lands."""
    body = """<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:getcontentlength/>
    <d:resourcetype/>
    <d:getlastmodified/>
  </d:prop>
</d:propfind>"""
    resp = _request("PROPFIND", path, data=body, headers={"Depth": "1", "Content-Type": "application/xml"})
    if resp.status_code == 404:
        return []

    root = ET.fromstring(resp.content)
    self_path = _dav_path(path)
    items = []
    for response in root.findall(f"{DAV_NS}response"):
        href = (response.findtext(f"{DAV_NS}href") or "").rstrip("/")
        href_path = urlparse(href).path.rstrip("/") if href.startswith("http") else href.rstrip("/")
        if href_path == self_path:
            continue  # the collection itself, not a child

        propstat = response.find(f"{DAV_NS}propstat")
        props = propstat.find(f"{DAV_NS}prop") if propstat is not None else None
        is_dir = props is not None and props.find(f"{DAV_NS}resourcetype/{DAV_NS}collection") is not None
        if is_dir:
            continue

        name = unquote(href_path.rsplit("/", 1)[-1])
        size_text = props.findtext(f"{DAV_NS}getcontentlength") if props is not None else None
        items.append({
            "name": name,
            "size": int(size_text) if size_text else 0,
            "last_modified": props.findtext(f"{DAV_NS}getlastmodified") if props is not None else None,
        })
    return items


def download_file(path):
    """Streaming GET for a file's raw bytes -- used to pull a team member's
    raw footage out of /incoming into Degas. Returns the raw requests
    response so the caller can choose .content (small/simple) or
    .iter_content() depending on need."""
    return _request("GET", path, stream=True)


def upload_file(path, data, content_type="application/octet-stream"):
    """PUT-uploads bytes to Cloud KMG at the given path. The parent folder
    must already exist -- callers should ensure_folder() first if unsure
    (WebDAV PUT does not create missing parent directories)."""
    return _request("PUT", path, data=data, headers={"Content-Type": content_type})
