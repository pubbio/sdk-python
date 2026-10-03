# Pubb Python server SDK

Publish events and authorize private/presence subscriptions from Python 3.10+.
Uses the standard library; no runtime dependencies. This is a backend SDK.

## Install

```sh
pip install "git+https://github.com/pubbio/sdk-python.git@main"
```

The package is not yet published to PyPI. Pin a commit for reproducible builds.

## Publish

```python
import os
from pubb import PubbServer

pubb = PubbServer(
    os.environ["PUBB_APP_ID"], os.environ["PUBB_APP_KEY"],
    os.environ["PUBB_APP_SECRET"], api_host="https://api.pubb.io", timeout=30,
)
result = pubb.trigger(["updates", "notifications"], "message", {"text": "Hello"},
                      socket_id="123.456", idempotency_key="notification-42")
print(result.publication_id, result.duplicate)
```

`socket_id` and `idempotency_key` are optional. HTTP 202 means accepted for
processing, not delivered. There are no automatic retries; preserve the same
idempotency key when retrying the same event. `PubbAPIError` includes `status` and
`retry_after`; `PubbProtocolError` indicates malformed acceptance data. Network
errors propagate from urllib. Redirects are rejected to protect credentials.
Never put the app secret in a browser, mobile client, or repository.

## Authorize subscriptions

In your own authenticated backend route, verify that the current user may join
the requested channel, then return one of these dictionaries as JSON:

```python
pubb.authorize_channel("123.456", "private-room")
pubb.authorize_presence_channel("123.456", "presence-room", "user-1", {"name": "Ada"})
```

Derive user identity from your session; do not trust client-supplied user IDs.
The returned `channel_data` must be forwarded unchanged because its exact bytes
are signed. Authorization helpers only sign data; they do not check permissions.
This SDK implements Pubb's HTTP API and shared HMAC authorization format. It is
not a drop-in replacement for the Pusher HTTP SDK.

## Development

```sh
python -m pip install -e . build
python -m unittest discover -s tests -v
python -m build
```

Tests use a local HTTP server and fixed cross-language signing vectors. MIT.
