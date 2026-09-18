# Pool sizing for the ingest workers

Ran the ingest workers against staging with PgBouncer in transaction mode today.
With 32 workers and `default_pool_size = 20` the p99 write latency went from
40ms to 900ms the moment the nightly batch started — the workers were queueing
for a server connection, not for a lock.

Dropping to 12 workers with the same pool size held p99 under 60ms at the same
throughput. So the useful number is not "connections per worker" but
`pool_size >= peak concurrent transactions`, and our peak is set by the batch
job, not by the request traffic.

Two things I got wrong before:

- I assumed prepared statements were fine in transaction mode because the driver
  did not error. It silently re-prepares per transaction, which is where about
  15% of the latency went.
- I had been treating the connection limit as a database tuning knob. It is an
  application concurrency knob; the database just enforces it.

Next: measure whether raising the batch COPY size reduces transaction count
enough to let us go back to 32 workers.
