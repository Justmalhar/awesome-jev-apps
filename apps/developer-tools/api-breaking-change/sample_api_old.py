"""shipyard 2.3 -- the public surface, before the release under review.

Bundled sample input for api_break.py. Not imported by anything.
"""


def connect(host, port=5432):
    """Open a connection to a shipyard broker. Part of the documented API."""
    return {"host": host, "port": port}


def fetch(url, retries=3, timeout=10.0):
    """Fetch a URL, retrying on transient failures. Documented and widely used."""
    return {"url": url, "retries": retries, "timeout": timeout}


def parse_legacy(payload):
    """Parse a pre-2.0 job payload. Documented as deprecated since 2.1."""
    return payload.split("|")


def serialize(obj, pretty=False):
    """Serialize a job payload to a string."""
    return str(obj)


def _cache_key(key):
    """Internal. Not exported, not documented, no compatibility promise."""
    return f"v1:{key}"


class Client:
    """The documented entry point for talking to a broker."""

    def send(self, message, blocking=True):
        """Send a message. `blocking` waits for the broker to acknowledge it."""
        return message

    def close(self, timeout=5.0):
        """Close the connection, waiting up to `timeout` seconds to drain."""
        return None

    def _reconnect(self, attempt):
        """Internal retry helper."""
        return attempt
