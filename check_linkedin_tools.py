"""
One-off check (2026-08-24): does Postiz's public API expose a tool for
LinkedIn mention/tag lookup (searching people or company pages to @mention in
a post)? The docs only document Instagram's audioSearch tool as an example --
this actually asks Postiz what LinkedIn's integration supports, rather than
guessing from documentation.

Run on the server, from the studio directory (needs its .env for POSTIZ_API_KEY):
    cd /var/www/studio
    source .env 2>/dev/null || export $(grep -v '^#' .env | xargs)
    python3 check_linkedin_tools.py
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DB_PATH", "/tmp/_unused.db")

import postiz_client

try:
    integrations = postiz_client.list_integrations()
except postiz_client.PostizError as e:
    print(f"Couldn't reach Postiz: {e}")
    sys.exit(1)

linkedin_channels = [i for i in integrations if "linkedin" in i.get("identifier", "")]

if not linkedin_channels:
    print("No LinkedIn channels found connected to this Postiz account.")
    sys.exit(0)

for ch in linkedin_channels:
    print(f"\n=== {ch['name']} ({ch['identifier']}, id={ch['id']}) ===")
    try:
        resp = postiz_client._request("GET", f"/integration-settings/{ch['id']}").json()
    except postiz_client.PostizError as e:
        print(f"  Couldn't fetch settings: {e}")
        continue
    output = resp.get("output", resp)
    tools = output.get("tools", [])
    if not tools:
        print("  No provider tools exposed for this channel (no mention/tag lookup available via the API).")
    else:
        print(f"  {len(tools)} tool(s) available:")
        for t in tools:
            print(f"    - {t.get('methodName')}: {t.get('description')}")
    print(f"  Full settings schema: {json.dumps(output.get('settings'), indent=2)[:500]}")
