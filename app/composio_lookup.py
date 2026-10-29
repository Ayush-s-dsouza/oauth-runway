"""Ask Composio what it already knows about a toolkit: is managed OAuth
available, what scopes does its default auth config request, and which
authorize/token URLs does it use. Optional -- skipped if COMPOSIO_API_KEY is
unset. Read-only (GET /api/v3/toolkits/{slug})."""

from __future__ import annotations

import os
import re

import httpx

API = "https://backend.composio.dev/api/v3/toolkits"


def _candidate_slugs(name: str) -> list[str]:
    base = name.strip().lower()
    base = re.sub(r"^https?://", "", base).split("/")[0]
    base = re.sub(r"^(www|app|api|developers?)\.", "", base)
    base = re.sub(r"\.(com|io|so|dev|ai|co|app|net|org)$", "", base)
    compact = re.sub(r"[^a-z0-9]", "", base)
    snake = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return list(dict.fromkeys([compact, snake]))


async def composio_toolkit(name_or_slug: str) -> dict:
    key = os.environ.get("COMPOSIO_API_KEY")
    if not key:
        return {"available": False, "reason": "COMPOSIO_API_KEY not configured on this server; skip Composio context."}
    async with httpx.AsyncClient(timeout=15, headers={"x-api-key": key}) as client:
        for slug in _candidate_slugs(name_or_slug):
            r = await client.get(f"{API}/{slug}")
            if r.status_code != 200:
                continue
            d = r.json()
            configs = []
            for a in d.get("auth_config_details") or []:
                fields = {f["name"]: f for f in (a.get("fields", {}).get("auth_config_creation", {}).get("optional") or [])}
                legacy = a.get("deprecated_auth_provider_details") or {}
                configs.append(
                    {
                        "mode": a.get("mode"),
                        "default_redirect_uri": (fields.get("oauth_redirect_uri") or {}).get("default"),
                        "default_scopes": [s for s in ((fields.get("scopes") or {}).get("default") or "").split(",") if s][:40],
                        "authorization_url": legacy.get("authorization_url"),
                        "token_url": legacy.get("token_url"),
                    }
                )
            meta = d.get("meta") or {}
            return {
                "available": True,
                "toolkit_slug": d.get("slug"),
                "name": d.get("name"),
                "app_url": meta.get("app_url"),
                "managed_auth_schemes": d.get("composio_managed_auth_schemes") or [],
                "managed_oauth_available": any("OAUTH" in s for s in d.get("composio_managed_auth_schemes") or []),
                "auth_configs": configs,
                "source_url": f"{API}/{d.get('slug')}",
            }
    return {"available": False, "reason": f"No Composio toolkit found for {name_or_slug!r} (tried {_candidate_slugs(name_or_slug)})."}
