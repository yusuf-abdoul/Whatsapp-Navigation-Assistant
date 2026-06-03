# Deploying WNA

Two host options for the FastAPI service:

- **Fly.io** — free tier, no card required at signup, [fly.toml](../fly.toml) is in the repo.
- **Render** — $7/mo Starter, card required up front, [render.yaml](../render.yaml) is in the repo.

Either one pairs with **Neon** (Postgres) and **Upstash** (Redis), both free at MVP scale. Total cost: **$0/mo on Fly**, **$7/mo on Render**.

This doc covers data setup once, then the host-specific steps for each.

## One-time setup

### 1. Neon (Postgres)

1. Sign in at [neon.tech](https://neon.tech) and create a new project.
2. Region: **Frankfurt** (closest to Nigeria; Render's region matches).
3. From the project dashboard, copy the **pooled connection string**.
4. Replace the `postgresql://` prefix with `postgresql+asyncpg://`. Example:
   ```
   postgresql+asyncpg://wna:xxx@ep-xyz-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require
   ```
   This is what goes in the `DATABASE_URL` env var on Render.

### 2. Upstash (Redis)

1. Sign in at [upstash.com](https://upstash.com) and create a Redis database.
2. Region: **EU-West-1 (Frankfurt)**.
3. Eviction: **noeviction** (we set short TTLs ourselves).
4. Copy the **Redis URL** from the dashboard. It starts with `rediss://`. This
   is what goes in the `REDIS_URL` env var on Render.

### 3a. Fly.io (recommended for first launch — $0/mo)

1. Install the CLI: `curl -L https://fly.io/install.sh | sh`
2. `fly auth signup` (or `fly auth login` if you already have an account).
3. From the repo root:
   ```bash
   fly launch --no-deploy --copy-config --name wna-api --region fra
   ```
   That creates the Fly app from the existing `fly.toml` without deploying yet.
4. Set the secrets — one command, no dashboard clicking:
   ```bash
   fly secrets set \
     DATABASE_URL="postgresql+asyncpg://...neon..." \
     REDIS_URL="rediss://...upstash..." \
     LOCATIONIQ_KEY="..." \
     SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
     TWILIO_ACCOUNT_SID="..." \
     TWILIO_AUTH_TOKEN="..." \
     TWILIO_WHATSAPP_FROM="whatsapp:+14155238886"
   ```
5. `fly deploy` — the `release_command` runs migrations against Neon first,
   then the web process starts.
6. Note the public URL: `https://wna-api.fly.dev`.

### 3b. Render (alternative — $7/mo, nicer UX)

1. Push this repo to GitHub.
2. Sign in at [render.com](https://render.com), click **New +** → **Blueprint**.
3. Connect the repo. Render reads `render.yaml` and proposes a web service
   named `wna-api`.
4. Click **Apply**. Render creates the service but the build will fail on
   the first deploy because no secrets are set yet. That's expected.
5. Open the service settings → **Environment** and fill in each `sync: false`
   variable:
   - `DATABASE_URL` — the Neon string from step 1
   - `REDIS_URL` — the Upstash string from step 2
   - `LOCATIONIQ_KEY` — your LocationIQ API key
   - `SESSION_SECRET` — generate with `python -c "import secrets;
     print(secrets.token_urlsafe(48))"`
   - Twilio sandbox credentials (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
     `TWILIO_WHATSAPP_FROM`). Use Twilio for the soft launch.
   - Leave the `META_*` vars empty until verification clears, then switch
     `CHANNEL_PROVIDER` to `meta`.
6. Click **Manual Deploy** → **Deploy latest commit**. The `preDeployCommand:
   alembic upgrade head` runs migrations against Neon, then the web process
   starts.
7. Note the public URL (something like `https://wna-api.onrender.com`).

### 4. Point Twilio at the deployed webhook

1. Twilio Console → **Messaging** → **Sandbox settings**.
2. "When a message comes in": `https://wna-api.onrender.com/webhook/twilio`.
3. Method: **HTTP POST**. Save.

### 5. Seed Abuja corridors

**On Fly:**
```bash
fly ssh console -C "python -m app.corridors.seed --city abuja"
fly ssh console -C "python -m app.corridors.seed --status"
```

**On Render:** open the service shell (Settings → **Shell**) and run:
```bash
python -m app.corridors.seed --city abuja
python -m app.corridors.seed --status
```

The three seed corridors should appear. Send "How do I get to banex" from
your phone — if you get the corridor reply, you're live.

## Day-2 operations

- **Roll out a code change**: push to `main` on GitHub. CI runs ruff/mypy/
  pytest. If green:
  - Render: auto-deploys on green push.
  - Fly: run `fly deploy` from your laptop (or wire it into CI later).

  Either way, the migration command runs first; if Alembic fails the deploy
  is rolled back automatically.
- **Tune a setting without redeploying**:
  - Render: change the env var in the dashboard, service restarts.
  - Fly: `fly secrets set FOO=bar` — re-rolls one machine at a time.
- **Inspect production data**: Neon dashboard has a query editor. Upstash
  dashboard has a Redis browser. Same regardless of host.
- **Roll back**:
  - Render: **Rollback** in the deploys tab.
  - Fly: `fly releases` then `fly deploy --image <previous-image-ref>`.
- **Watch logs live**:
  - Render: **Logs** tab streams stdout.
  - Fly: `fly logs`.

  structlog writes JSON lines either way, so you can grep for `event=route_success`,
  `event=rate_limited`, etc.

## Costs at MVP scale

| Service | Fly setup | Render setup |
|---|---|---|
| Web app | **$0** (free tier covers a 256MB shared VM) | **$7/mo** (Starter, no sleep) |
| Postgres (Neon free) | $0 | $0 |
| Redis (Upstash free) | $0 | $0 |
| **Total** | **$0/mo** | **$7/mo** |

Free tiers are sufficient for hundreds of users:

- **Neon free**: 0.5 GB storage, 191 compute hours/month. Upgrade to Pro
  ($19/mo) when we cross either limit.
- **Upstash free**: 10k commands/day. Each WhatsApp message is roughly 3-5
  Redis ops (session + abuse counters), so ~2-3k message turns per day.
- **Fly free**: 3 shared-cpu-1x VMs free. We need 1.

Replacing the Twilio sandbox with a verified Meta number is the only
remaining cost lever for messaging itself.

## Switching to Meta Direct (later)

1. Complete Meta Business verification (~1-2 weeks).
2. In Render dashboard, set `CHANNEL_PROVIDER=meta` and fill in the four
   `META_*` env vars.
3. In Meta's WhatsApp Manager, point the webhook at
   `https://wna-api.onrender.com/webhook/meta`. (Webhook is symmetric to
   Twilio's, our channel adapter handles the format difference.)
4. Restart the service to pick up the env change (`fly machines restart` or
   Render's Manual Deploy).
