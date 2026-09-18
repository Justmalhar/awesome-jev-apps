# shipyard 3.0.0

## Breaking

- `connect()` now requires a `credentials` argument. Passing only `host` and `port` raises `TypeError`.
- The `parse_legacy()` helper, deprecated since 2.1, has been removed.
- `Client.send(blocking=...)` is now `Client.send(block=...)`. The old keyword raises.

## Changed

- The default for `fetch(retries=)` is now `0` instead of `3`. Calls that relied on the default no longer retry; pass `retries` explicitly to keep the old behaviour.
- `serialize()` now emits indented output by default (`pretty=True`). Consumers comparing serialized strings byte-for-byte will see differences.
- Connection pooling now defaults to a maximum of 20 connections per host, up from 10.

## Fixed

- Fixed a leak in `JobRegistry` where completed jobs were never evicted, causing unbounded memory growth in long-running processes.
- `Client.close()` no longer swallows the exception raised while draining the queue.
- Corrected the timezone handling in `Job.scheduled_at` for zones with a half-hour offset.

## Security

- Authorization headers are no longer written to the debug log. Rotate any token that appeared in logs produced by 2.x.

## Deprecated

- `Broker.enqueue_many()` is deprecated in favour of `Broker.enqueue_batch()` and will be removed in 4.0.

## Internal

- The wire protocol between broker and worker moved from pickle to msgpack. No public API change.
- Test suite migrated from nose to pytest.
