# Tour Finder Performance Report

Date: 2026-07-23

## Implemented

- MySQL 8 migration with all existing IDs and records preserved.
- Redis shared cache with in-memory fail-open fallback.
- 60-second public tour/filter cache and pipeline invalidation.
- Public tour payload separated from personalized interaction state.
- `/api/interactions` for current-user like state and live counters.
- `/api/views/batch` with max 50 IDs, MySQL `INSERT IGNORE`, and rate limiting.
- Like/comment/view rate limits shared through Redis.
- Database pagination (`limit=20`) and frontend infinite scroll.
- Per-channel 20-row cap executed with SQL `ROW_NUMBER()`.
- MySQL FULLTEXT search on title/country/city/summary.
- Indexed departure, return, price, source, country and order fields.
- GZip compression and response process-time header.
- Health endpoint for MySQL and Redis.
- Manual Redis background queue for scrape/pipeline jobs; no automatic schedule.
- Three-worker production Docker configuration.
- Optional S3/R2 uploader and `MEDIA_BASE_URL` CDN rewrite.

## Functional verification

- MySQL: healthy, 290 raw posts, 179 tours, 64 views, 4 likes, 5 comments at migration time.
- Redis: healthy and used by API cache/rate limit.
- Pagination: page 1 = 20, page 2 = 20, total active = 73.
- Batch views: 20 IDs accepted; duplicate batch remains idempotent.
- Search: Turkiya, Dubai and mandarintour queries returned HTTP 200.
- GZip: full list response previously compressed from about 51.5 KB to about 8.4 KB.

## Benchmark (local Windows + Docker MySQL/Redis, one ASGI process)

Before shared Redis cache:
- 100 concurrent reads: ~141 RPS, average ~654 ms, p95 ~675 ms.

After public Redis cache + 20-row pagination:
- 100 concurrent reads: ~682 RPS, average ~133 ms, p95 ~138 ms.
- Improvement: about 4.8x throughput.
- Cold filtered page: ~20 ms; warm cached page: ~3 ms.

These are local synthetic numbers, not a production SLA.

## Expected scale

- 10K daily active users spread through a day: supported by the current architecture.
- Large synchronized bursts: run the provided 3 API workers behind Nginx/Cloudflare and monitor p95/p99.
- 10K truly concurrent users still requires multiple API hosts/containers and a load balancer; one machine is not sufficient.

## Operations

Development infrastructure:

```powershell
docker compose -f docker-compose.mysql.yml up -d
```

Manual background jobs (pipeline remains non-automatic):

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\enqueue_job.py scrape
.\.venv\Scripts\python.exe scripts\enqueue_job.py pipeline
.\.venv\Scripts\python.exe -m app.worker
```

Production template:

```powershell
Copy-Item .env.mysql.example .env.mysql
Copy-Item backend\.env.prod.example backend\.env
docker compose -f docker-compose.prod.yml up -d --build
```

## External configuration still required

Cloudflare R2/S3 cannot be uploaded without account credentials. After setting `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, and `MEDIA_BASE_URL`, run:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\upload_media_s3.py
```

Change all development MySQL passwords before production deployment.