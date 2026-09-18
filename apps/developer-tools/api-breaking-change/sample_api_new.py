"""shipyard 2.4 -- the public surface, after the release under review.

Bundled sample input for api_break.py. Not imported by anything.
"""


def connect(host, port, credentials):
    """Open a connection to a shipyard broker. Part of the documented API."""
    return {"host": host, "port": port, "credentials": credentials}


def fetch(url, retries=0, timeout=10.0):
    """Fetch a URL. Retries are now opt-in so that callers own their own policy."""
    return {"url": url, "retries": retries, "timeout": timeout}


def serialize(obj, pretty=True):
    """Serialize a job payload to a string."""
    return str(obj)


def _cache_key(key, namespace):
    """Internal. Not exported, not documented, no compatibility promise."""
    return f"v2:{namespace}:{key}"


class Client:
    """The documented entry point for talking to a broker."""

    def send(self, message, block=True):
        """Send a message. `block` waits for the broker to acknowledge it."""
        return message

    def close(self, timeout):
        """Close the connection, waiting up to `timeout` seconds to drain."""
        return None

    def _reconnect(self, attempt, backoff):
        """Internal retry helper."""
        return attempt
