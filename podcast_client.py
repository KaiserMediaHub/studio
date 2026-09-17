"""
Claude content generation for the Podcast Page Generator tab (Ben's ask,
2026-09-17 -- merging the standalone podcast-page-generator tool into
Studio). This is separate from hemingway_client.py on purpose: it isn't
part of the voice/style/Tone Profile pipeline at all, just a one-shot
"given this transcript, suggest titles/quotes/topics" call, so it talks to
the Anthropic API directly rather than routing through Hemingway.

Ported from the standalone tool's app.py::generate_content_with_claude with
the same prompt and model -- not re-tuned here, just relocated.
"""

import json
import re

import anthropic

CLAUDE_MODEL = "claude-opus-4-8"


class PodcastContentError(Exception):
    pass


def generate_episode_content(transcript: str, cta_text: str) -> dict:
    """Returns {"title_suggestions": [...], "pull_quotes": [...],
    "description": "...", "key_topics": [...]} generated from the episode
    transcript. Raises PodcastContentError on any failure (missing API key,
    malformed response, etc.) so callers get one exception type to handle."""
    try:
        client = anthropic.Anthropic()
    except Exception as e:
        raise PodcastContentError(f"Couldn't initialize Anthropic client (check ANTHROPIC_API_KEY): {e}") from e

    prompt = f"""You are helping create a podcast episode page. Given the transcript below, produce:

1. **title_suggestions**: 3 compelling episode title options (plain strings, no numbering)
2. **pull_quotes**: 3-5 of the most interesting or insightful quotes from the transcript (verbatim, under 30 words each)
3. **description**: A 2-sentence SEO-friendly episode description
4. **key_topics**: 5-7 short topic tags (single words or 2-word phrases)

Transcript:
{transcript[:8000]}

Respond ONLY with valid JSON matching this shape:
{{
  "title_suggestions": ["...", "...", "..."],
  "pull_quotes": ["...", "..."],
  "description": "...",
  "key_topics": ["...", "..."]
}}"""

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        raise PodcastContentError(f"Claude API call failed: {e}") from e

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    try:
        content = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PodcastContentError(f"Claude returned non-JSON content: {e}") from e

    required = ("title_suggestions", "pull_quotes", "description", "key_topics")
    missing = [k for k in required if k not in content]
    if missing:
        raise PodcastContentError(f"Claude response missing expected fields: {missing}")

    return content
