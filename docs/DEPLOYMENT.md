# Deployment and Operations

## Run it for a demo on one machine

Use the steps in [TEST_CREDENTIALS.md](TEST_CREDENTIALS.md), with `npm run demo` for the
frontend. It serves the production build at http://localhost:3000.

## Run it in production

Needs Docker on a server, and HTTPS in front of it. `COOKIE_SECURE` is forced on, so
sign-in will not work over plain HTTP on a real domain.

1. Copy `.env.example` to `.env` and replace every value. Generate secrets with:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. Start the stack. Migrations run when the API starts.

   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```

3. Load the festival once:

   ```bash
   docker compose -f docker-compose.prod.yml exec api python -m scripts.seed
   ```

4. Point your HTTPS proxy at port 8080, or set `WEB_PORT` to change it. Forward
   WebSocket upgrades so live updates work.

The API refuses to start in production with a weak `JWT_SECRET`, the example database
password, or `COOKIE_SECURE=false`. API docs are switched off in production.

## Before going live

- Replace the shared `DEMO_PASSWORD` accounts. Create real users in Settings and disable
  the seeded ones, or have each person change their password.
- Replace simulated budgets, expenses, sponsors, vehicles, trips, shifts and incidents
  with real records.
- Add `ANTHROPIC_API_KEY` if you want free-form assistant questions.

## Backups

```bash
bash deploy/backup.sh
```

Writes a checked backup to `backups/` and keeps the latest 14. Set `KEEP` to change that.
Schedule it with cron for production, and copy the files to another machine. Pass the
container name as the first argument for the production stack.

### Test a backup without touching live data

```bash
bash deploy/restore.sh backups/<file>.dump festos_restore_test
```

### Recover the live database

Stop the API first, then:

```bash
CONFIRM_RESTORE=yes bash deploy/restore.sh backups/<file>.dump
```

Anything recorded after the backup is lost. Start the API again and check `/api/ready`.

## Monitoring

| Check | Meaning |
|---|---|
| `GET /api/health` | The API process is running |
| `GET /api/ready` | The API can reach the database. Returns 503 if not |

Each request logs its method, path, status, time and a request ID. Query strings are not
logged, because they can contain names. Requests over one second and all 5xx errors log
at WARNING. Users see a reference on a server error: search the logs for it to find the
stack trace. Send container logs to your log service for alerts.

## Limits

- Run one API process. Live updates and rate limits are held in memory, so several
  workers would not share them. Move both to Redis or PostgreSQL before scaling out.
- Camera check-in only works over HTTPS or on localhost, because browsers block the
  camera elsewhere. Over plain HTTP on a local network, type the pass code or use a USB
  or Bluetooth QR reader.
- The load test reached 33 requests a second with 20 concurrent users and no errors, on
  a single machine that was also running the test client. Run
  `python -m scripts.load_test` on your own hardware for real figures.
