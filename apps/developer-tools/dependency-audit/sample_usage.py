"""Our service's use of the shipyard package. Bundled sample input.

This file is read as TEXT by app.py and parsed with `ast`; it is never
imported, and `shipyard` does not need to be installed.
"""

import shipyard
from shipyard import Client, fetch


def open_broker(settings):
    """We call connect() with two arguments and no credentials."""
    return shipyard.connect(settings.host, settings.port)


def load_manifest(url):
    """We rely on the default retry behaviour here, deliberately."""
    return fetch(url)


def publish(messages):
    client = Client()
    for message in messages:
        client.send(message, blocking=True)
    client.close()


def snapshot(job):
    """Byte-for-byte comparison against a stored fixture."""
    return shipyard.serialize(job)


def drain(registry):
    """Long-running: this process stays up for weeks."""
    return shipyard.JobRegistry(registry).complete_all()
