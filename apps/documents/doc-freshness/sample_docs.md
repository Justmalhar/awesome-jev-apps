# Orbit API — Developer Guide (synthetic sample, v2.1 docs)

## Endpoints

- `POST /v1/jobs` creates a job and returns the job id in the `id` field.
- `GET /v1/jobs/{id}` returns a single job.
- `GET /v1/jobs` lists jobs for the calling workspace.
- `DELETE /v1/jobs/{id}` cancels a running job.
- `POST /v1/jobs/{id}/retry` re-queues a failed job.
- `GET /v1/usage` returns the current billing period's usage rollup.

## Authentication

- Every request must send `Authorization: Bearer <token>`.
- Tokens are created in the dashboard under Settings → API Keys.
- A token scoped to `jobs:read` may call any GET endpoint under `/v1/jobs`.

## Configuration

- The `ORBIT_API_URL` environment variable sets the API base URL.
- The `ORBIT_TIMEOUT_MS` environment variable controls the client timeout and defaults to 30000.
- The `retries` option defaults to 3.
- The `region` option is optional and defaults to the workspace's home region.
- The `webhook_secret` option is optional; when omitted, webhook signatures are not verified.

## Client library

- The Python client requires Python 3.9 or newer.
- Install it with `pip install orbit-client`.
- `orbit.Client()` reads credentials from the environment when no token is passed.
- The client exposes a synchronous API only.

## Webhooks

- Job completion fires a `job.completed` webhook.
- Webhook payloads are signed with HMAC-SHA1 and the signature is sent in the `X-Orbit-Signature` header.
- Failed webhook deliveries are retried with exponential backoff.

## Deprecated

- The `POST /v1/batch` endpoint accepts up to 100 jobs in a single call and remains supported for existing integrations.
- The `ORBIT_LEGACY_MODE` environment variable forces the v0 response shape.
