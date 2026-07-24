"""
Client for Postiz's real public API (verified against docs.postiz.com 7/17 --
see STUDIO_SYSTEM_DESIGN.md Section 1/6, Path C).

Auth is a single organization-level API key (Settings > Developers > Public
API in Postiz's own UI) -- not per-client. Postiz's "customer groups" feature
is what maps its one account to KMG's 4 clients; Studio stores which group ID
belongs to which Hemingway client (see client_postiz_groups table).

Rate limit note: Postiz's docs disagree with themselves -- the API overview
page says 90/hr (100 on cloud) for the create-post endpoint specifically, but
every individual endpoint's own OpenAPI spec still states a flat 30/hr. This
client doesn't try to guess which is right; it surfaces 429s clearly rather
than pre-emptively throttling on an assumed number.
"""

import os
import requests

POSTIZ_BASE_URL = os.environ.get("POSTIZ_BASE_URL", "https://api.postiz.com/public/v1")
POSTIZ_API_KEY = os.environ.get("POSTIZ_API_KEY", "")


class PostizError(Exception):
    pass


def _request(method, path, **kwargs):
    if not POSTIZ_API_KEY:
        raise PostizError(
            "POSTIZ_API_KEY is not set -- get it from Postiz Settings > Developers > "
            "Public API and add it to .env."
        )
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = POSTIZ_API_KEY
    try:
        resp = requests.request(method, f"{POSTIZ_BASE_URL}{path}", headers=headers, timeout=15, **kwargs)
    except requests.exceptions.RequestException as e:
        raise PostizError(f"Couldn't reach Postiz at {POSTIZ_BASE_URL}{path}: {e}") from e

    if resp.status_code == 401:
        raise PostizError("Postiz rejected the API key (401) -- check POSTIZ_API_KEY in .env.")
    if resp.status_code == 429:
        raise PostizError("Postiz rate limit hit (429) -- wait before retrying. See module docstring on the rate limit discrepancy.")
    if resp.status_code >= 400:
        raise PostizError(f"Postiz API error ({resp.status_code}) on {path}: {resp.text[:300]}")
    return resp


def is_connected():
    """Verifies the API key is valid -- the first thing to check during setup."""
    return _request("GET", "/is-connected").json().get("connected", False)


def list_groups():
    """Returns Postiz's customer groups: [{id, name}, ...]. One of these
    should correspond to each KMG client (e.g. 'Epiphany')."""
    return _request("GET", "/groups").json()


def list_integrations(group_id=None):
    """Returns connected channels: [{id, name, identifier, disabled, profile,
    customer: {id, name}}, ...]. Pass group_id to filter to one client's
    channels only."""
    params = {"group": group_id} if group_id else {}
    return _request("GET", "/integrations", params=params).json()


def create_post(post_type, date_iso, posts, tags=None, short_link=False):
    """Creates/schedules a post. post_type is 'draft', 'schedule', or 'now'.
    posts is a list of {integration: {id}, value: [{content, image}], settings: {...}}
    dicts -- one per channel being posted to. Returns Postiz's response:
    [{postId, integration}, ...] -- one entry per channel, so a multi-channel
    post's IDs can all be stored.

    Settings needed per platform (from docs.postiz.com, confirmed 7/17):
    - LinkedIn / LinkedIn Page: {"__type": "linkedin"} or {"__type": "linkedin-page"} -- no other required fields
    - Facebook: {"__type": "facebook"} -- no other required fields
    - YouTube: {"__type": "youtube", "title": ..., "type": "public"|"private"|"unlisted"} --
      also requires an uploaded video file as the post's image/media, which
      Studio's quick-post flow doesn't produce yet (task #11 has no media
      upload path to Postiz) -- defer YouTube pushes until that exists.
    """
    body = {
        "type": post_type,
        "date": date_iso,
        "shortLink": short_link,
        "tags": tags or [],
        "posts": posts,
    }
    return _request("POST", "/posts", json=body).json()


def linkedin_post(integration_id, content, is_page=False, image=None):
    """Convenience builder for the common case: one LinkedIn channel, text only."""
    return {
        "integration": {"id": integration_id},
        "value": [{"content": content, "image": image or []}],
        "settings": {"__type": "linkedin-page" if is_page else "linkedin"},
    }


def facebook_post(integration_id, content, url=None, image=None):
    settings = {"__type": "facebook"}
    if url:
        settings["url"] = url
    return {
        "integration": {"id": integration_id},
        "value": [{"content": content, "image": image or []}],
        "settings": settings,
    }


def instagram_post(integration_id, content, image, identifier="instagram", post_type="post"):
    """Instagram (feed or standalone). Requires at least one image/video --
    Postiz/Instagram won't accept a text-only post here, unlike LinkedIn or
    Facebook. `identifier` is 'instagram' (Facebook-linked) or
    'instagram-standalone', per docs.postiz.com/public-api/providers/instagram."""
    return {
        "integration": {"id": integration_id},
        "value": [{"content": content, "image": image}],
        "settings": {
            "__type": identifier,
            "post_type": post_type,
            "is_trial_reel": False,
            "collaborators": [],
        },
    }


def youtube_post(integration_id, content, image, title, privacy="public"):
    """YouTube. Requires an uploaded video (image param, despite the name --
    Postiz's schema reuses `image` for all media types) and a title (2-100
    chars per docs.postiz.com/public-api/providers/youtube)."""
    return {
        "integration": {"id": integration_id},
        "value": [{"content": content, "image": image}],
        "settings": {
            "__type": "youtube",
            "title": title,
            "type": privacy,
            "selfDeclaredMadeForKids": "no",
        },
    }


# Platforms Studio can push to without any media attached -- the original,
# still-default set used by Quick Posts (task #11), which has no media
# upload UI. Kept separate from MEDIA_CAPABLE_IDENTIFIERS below so Quick
# Posts doesn't list channels it can't actually satisfy Postiz's
# requirements for.
SUPPORTED_SCHEDULE_IDENTIFIERS = {"linkedin", "linkedin-page", "facebook"}

# Platforms that Postiz/the underlying network requires real media for --
# posting without an image/video attached will fail (or be rejected outright
# for Instagram; YouTube has nothing to publish without a video file).
MEDIA_REQUIRED_IDENTIFIERS = {"instagram", "instagram-standalone", "youtube"}

# Full set the calendar's "Add Post" flow can offer, now that it has a media
# upload step (task #13 follow-up, 7/24 -- Ben's ask after seeing only 2 of
# his 4 connected channels selectable).
MEDIA_CAPABLE_IDENTIFIERS = SUPPORTED_SCHEDULE_IDENTIFIERS | MEDIA_REQUIRED_IDENTIFIERS


def build_post_item(integration_id, identifier, content, image=None, extra=None):
    """Picks the right settings builder for a channel based on its Postiz
    `identifier` (as returned by list_integrations()). Raises PostizError
    for anything Studio doesn't know how to push to yet, rather than
    silently sending a malformed settings object.

    `image` is a list of {id, path} dicts from upload_file() -- required for
    identifiers in MEDIA_REQUIRED_IDENTIFIERS. `extra` carries platform-
    specific fields Studio's caller collected (currently just YouTube's
    `title`)."""
    extra = extra or {}
    if identifier in MEDIA_REQUIRED_IDENTIFIERS and not image:
        raise PostizError(f"'{identifier}' requires an image or video attached -- none was provided.")

    if identifier == "linkedin":
        return linkedin_post(integration_id, content, is_page=False, image=image)
    if identifier == "linkedin-page":
        return linkedin_post(integration_id, content, is_page=True, image=image)
    if identifier == "facebook":
        return facebook_post(integration_id, content, image=image)
    if identifier in ("instagram", "instagram-standalone"):
        return instagram_post(integration_id, content, image, identifier=identifier)
    if identifier == "youtube":
        title = (extra.get("title") or "").strip()
        if not title:
            raise PostizError("YouTube requires a video title.")
        return youtube_post(integration_id, content, image, title)
    raise PostizError(
        f"Studio doesn't support scheduling to '{identifier}' yet -- only "
        f"{', '.join(sorted(MEDIA_CAPABLE_IDENTIFIERS))} are wired up."
    )


def upload_file(file_obj, filename, content_type):
    """Uploads a media file to Postiz, returning {id, name, path, ...}. The
    returned {id, path} pair is what gets passed into a post's `image` array
    (docs.postiz.com/public-api/uploads/upload-file -- accepts JPEG/PNG/GIF/
    WebP/AVIF/BMP/TIFF images or MP4 video; rejects everything else,
    including PDFs, based on actual file content not just extension)."""
    return _request(
        "POST", "/upload",
        files={"file": (filename, file_obj, content_type)},
    ).json()


def list_posts(start_date_iso, end_date_iso, customer=None):
    """Returns posts within a date range: {"posts": [{id, content,
    publishDate, releaseURL, state, integration: {...}}, ...]}. state is one
    of QUEUE, PUBLISHED, ERROR, DRAFT -- this is Postiz's own real status,
    which is what the calendar (task #13) should show rather than Studio
    guessing at it locally."""
    params = {"startDate": start_date_iso, "endDate": end_date_iso}
    if customer:
        params["customer"] = customer
    return _request("GET", "/posts", params=params).json()
