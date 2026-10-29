"""Deterministic live probes the agent can run against a provider.

They never create anything and never authenticate. They answer "does this
actually exist and behave the way the docs say?" -- e.g. hitting a provider's
authorize endpoint with a deliberately invalid client_id shows the endpoint is
live and how strictly it validates client_id / redirect_uri.

Every request is SSRF-guarded: public hosts only, re-checked on every
redirect hop, with timeouts and a response size cap.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import re
import socket
from html import unescape
from urllib.parse import urlencode, urljoin, urlparse, urlunparse, parse_qsl

import httpx

TIMEOUT = 8.0
MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 6
UA = "OAuthRunway-probe/1.0 (+research bot; read-only)"
PROBE_CLIENT_ID = "oauth-runway-probe-invalid-client"
COMPOSIO_CALLBACK = "https://backend.composio.dev/api/v3/toolkits/auth/callback"


class ProbeBlocked(Exception):
    pass


def _host_is_public(host: str) -> bool:
    if not host or host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def check_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ProbeBlocked(f"scheme not allowed: {p.scheme!r}")
    if p.username or p.password:
        raise ProbeBlocked("credentials in URL not allowed")
    if p.port not in (None, 80, 443):
        raise ProbeBlocked(f"port not allowed: {p.port}")
    if not _host_is_public(p.hostname or ""):
        raise ProbeBlocked(f"host is not a public internet host: {p.hostname!r}")
    return url


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_TAG_RE = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.I | re.S)
_LOGIN_HINTS = re.compile(r"/(login|signin|sign_in|session|identifier|servicelogin|sso)(/|$)", re.I)


def _text_excerpt(html: str, n: int = 700) -> str:
    text = unescape(_TAG_RE.sub(" ", html))
    return re.sub(r"\s+", " ", text).strip()[:n]


async def _get(url: str) -> dict:
    """GET with manual redirect following so each hop is re-checked."""
    hops: list[dict] = []
    current = check_url(url)
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": UA}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            async with client.stream("GET", current) as resp:
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) >= MAX_BYTES:
                        break
                hops.append({"url": current, "status": resp.status_code})
                if resp.is_redirect and "location" in resp.headers:
                    current = check_url(urljoin(current, resp.headers["location"]))
                    continue
                return {
                    "status": resp.status_code,
                    "final_url": current,
                    "hops": hops,
                    "content_type": resp.headers.get("content-type", ""),
                    "body": body.decode(resp.encoding or "utf-8", errors="replace"),
                }
    return {"status": None, "final_url": current, "hops": hops, "content_type": "", "body": "", "error": "too many redirects"}


def _summarise(r: dict) -> dict:
    body = r.get("body", "")
    title = _TITLE_RE.search(body)
    final = r["final_url"]
    redirected_to_login = any(
        _LOGIN_HINTS.search(p) and "error" not in p for p in (urlparse(h["url"]).path or "" for h in r["hops"][1:])
    )
    has_password_field = bool(re.search(r'type=["\']?password', body, re.I))
    return {
        "status": r["status"],
        "final_url": final,
        "redirect_chain": [f'{h["status"]} {h["url"]}' for h in r["hops"]],
        "title": unescape(title.group(1)).strip()[:200] if title else None,
        "login_wall": redirected_to_login or has_password_field,
        "text_excerpt": _text_excerpt(body) if "html" in r.get("content_type", "") or "<" in body[:50] else body[:700],
        "error": r.get("error"),
    }


async def probe_url(url: str) -> dict:
    """Does this page exist, where does it redirect, is it behind a login?"""
    try:
        return {"probe": "url", "requested_url": url, **_summarise(await _get(url))}
    except ProbeBlocked as e:
        return {"probe": "url", "requested_url": url, "blocked": str(e)}
    except httpx.HTTPError as e:
        return {"probe": "url", "requested_url": url, "error": f"{type(e).__name__}: {e}"}


async def probe_oauth_authorize(authorize_url: str, scope: str | None = None, redirect_uri: str | None = None) -> dict:
    """Hit the provider's authorize endpoint with a deliberately invalid client_id
    and the Composio callback as redirect_uri. Nothing is created; the provider's
    error response shows the endpoint is live and how it validates requests."""
    p = urlparse(authorize_url)
    params = dict(parse_qsl(p.query))
    params.update(
        {
            "client_id": PROBE_CLIENT_ID,
            "redirect_uri": redirect_uri or COMPOSIO_CALLBACK,
            "response_type": "code",
            "state": "oauth-runway-probe",
        }
    )
    if scope:
        params["scope"] = scope
    url = urlunparse(p._replace(query=urlencode(params)))
    try:
        out = _summarise(await _get(url))
    except ProbeBlocked as e:
        return {"probe": "oauth_authorize", "requested_url": authorize_url, "blocked": str(e)}
    except httpx.HTTPError as e:
        return {"probe": "oauth_authorize", "requested_url": authorize_url, "error": f"{type(e).__name__}: {e}"}
    # Surface any OAuth error params that came back in a redirect.
    oauth_errors = {}
    for hop in out["redirect_chain"]:
        q = dict(parse_qsl(urlparse(hop.split(" ", 1)[1]).query))
        for k in ("error", "error_description", "authError", "err"):
            if k in q:
                oauth_errors[k] = q[k][:300]
                if k == "authError":  # Google: base64url protobuf; pull out the readable strings
                    try:
                        raw = base64.urlsafe_b64decode(q[k] + "=" * (-len(q[k]) % 4))
                        oauth_errors["authError_decoded"] = " | ".join(s.decode() for s in re.findall(rb"[\x20-\x7e]{6,}", raw))
                    except ValueError:
                        pass
    return {
        "probe": "oauth_authorize",
        "requested_url": authorize_url,
        "sent_client_id": PROBE_CLIENT_ID,
        "sent_redirect_uri": params["redirect_uri"],
        "oauth_error_params": oauth_errors,
        **out,
    }


async def probe_oidc_discovery(domain: str) -> dict:
    """Fetch /.well-known/openid-configuration (endpoints + scopes_supported). Accepts a domain or issuer URL."""
    base = domain if domain.startswith("http") else f"https://{domain}"
    url = base.rstrip("/") + "/.well-known/openid-configuration"
    try:
        r = await _get(url)
    except ProbeBlocked as e:
        return {"probe": "oidc_discovery", "requested_url": url, "blocked": str(e)}
    except httpx.HTTPError as e:
        return {"probe": "oidc_discovery", "requested_url": url, "error": f"{type(e).__name__}: {e}"}
    import json

    try:
        doc = json.loads(r["body"])
    except ValueError:
        return {"probe": "oidc_discovery", "requested_url": url, "status": r["status"], "found": False}
    keep = ("issuer", "authorization_endpoint", "token_endpoint", "revocation_endpoint", "code_challenge_methods_supported")
    return {
        "probe": "oidc_discovery",
        "requested_url": url,
        "status": r["status"],
        "found": True,
        **{k: doc.get(k) for k in keep},
        "scopes_supported": (doc.get("scopes_supported") or [])[:40],
    }


async def run_with_timeout(coro, seconds: float = 25.0) -> dict:
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        return {"error": "probe timed out"}
