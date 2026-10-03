from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class PubbAPIError(Exception):
    """The API returned an unsuccessful status. Response bodies are not exposed."""

    def __init__(self, status: int, retry_after: str | None = None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"Pubb API returned HTTP {status}")


class PubbProtocolError(Exception):
    """The API returned an invalid acceptance response."""


@dataclass(frozen=True)
class TriggerResponse:
    success: bool
    accepted: bool
    channels: list[str]
    publication_id: str
    duplicate: bool


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _string(value: str, field: str, limit: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip() or (limit and len(value) > limit):
        raise ValueError(f"{field} must be a nonempty string" + (f" of at most {limit} characters" if limit else ""))
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class PubbServer:
    """Synchronous server API; keep app_secret on a trusted backend only.

    Network errors propagate as urllib/OSError exceptions. Requests are never
    automatically retried: reuse an idempotency key when retrying a publication.
    """

    def __init__(self, app_id: str, app_key: str, app_secret: str, *,
                 api_host: str = "https://api.pubb.io", timeout: float = 30):
        self._app_id = _string(app_id, "app_id")
        self._app_key = _string(app_key, "app_key")
        self._app_secret = _string(app_secret, "app_secret")
        parts = urlsplit(api_host)
        if (parts.scheme not in ("http", "https") or not parts.hostname or
                parts.username or parts.password or parts.query or parts.fragment):
            raise ValueError("api_host must be an HTTP(S) URL without credentials, query or fragment")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        self._api_host = api_host.rstrip("/")
        self._timeout = timeout
        self._opener = build_opener(_NoRedirect())

    def trigger(self, channel: str | list[str], name: str, data: Any, *,
                socket_id: str | None = None, idempotency_key: str | None = None) -> TriggerResponse:
        channels = [channel] if isinstance(channel, str) else channel
        if not isinstance(channels, list) or not 1 <= len(channels) <= 100:
            raise ValueError("channel must be a string or a list of 1 to 100 channels")
        for item in channels:
            _string(item, "channel", 200)
        _string(name, "name", 200)
        if len(_json(data).encode("utf-8")) > 10240:
            raise ValueError("data must serialize to at most 10 KiB of JSON")
        body = {"channel": channel, "name": name, "data": data}
        if socket_id is not None:
            body["socket_id"] = _string(socket_id, "socket_id", 100)
        headers = {"Authorization": f"Bearer {self._app_secret}", "Content-Type": "application/json"}
        if idempotency_key is not None:
            if not isinstance(idempotency_key, str) or not re.fullmatch(r"[!-~]{1,128}", idempotency_key):
                raise ValueError("idempotency_key must contain 1 to 128 printable nonspace ASCII characters")
            headers["Idempotency-Key"] = idempotency_key
        request = Request(f"{self._api_host}/api/apps/{quote(self._app_id, safe='')}/events",
                          data=_json(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status != 202:
                    raise PubbAPIError(response.status, response.headers.get("Retry-After"))
                raw = response.read()
        except HTTPError as error:
            status, retry = error.code, error.headers.get("Retry-After")
            error.close()
            raise PubbAPIError(status, retry) from None
        try:
            result = json.loads(raw)
            if (not isinstance(result, dict) or result.get("success") is not True or
                    result.get("accepted") is not True or
                    not isinstance(result.get("channels"), list) or not result["channels"] or
                    any(not isinstance(c, str) or not c for c in result["channels"]) or
                    not isinstance(result.get("publicationId"), str) or not result["publicationId"] or
                    not isinstance(result.get("duplicate"), bool)):
                raise ValueError("invalid response")
            return TriggerResponse(True, True, result["channels"], result["publicationId"], result["duplicate"])
        except (ValueError, UnicodeError):
            raise PubbProtocolError("Pubb API returned an invalid acceptance response") from None

    def authorize_channel(self, socket_id: str, channel: str) -> dict[str, str]:
        _string(socket_id, "socket_id", 100)
        _string(channel, "channel", 200)
        if not channel.startswith("private-"):
            raise ValueError("authorize_channel requires a private- channel")
        return {"auth": self._sign(f"{socket_id}:{channel}")}

    def authorize_presence_channel(self, socket_id: str, channel: str, user_id: str,
                                   user_info: Any = None) -> dict[str, str]:
        _string(socket_id, "socket_id", 100)
        _string(channel, "channel", 200)
        _string(user_id, "user_id")
        if not channel.startswith("presence-"):
            raise ValueError("authorize_presence_channel requires a presence- channel")
        user = {"user_id": user_id}
        if user_info is not None:
            user["user_info"] = user_info
        channel_data = _json(user)
        return {"auth": self._sign(f"{socket_id}:{channel}:{channel_data}"), "channel_data": channel_data}

    def _sign(self, value: str) -> str:
        digest = hmac.new(self._app_secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{self._app_key}:{digest}"
