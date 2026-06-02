# Deploying WNA

We deploy to **Render** for the web service, **Neon** for Postgres, and
**Upstash** for Redis. All three have free tiers that cover the MVP scale.

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

### 3. Render (web service)

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

Open the Render service shell (Settings → **Shell**) and run:

```bash
python -m app.corridors.seed --city abuja
python -m app.corridors.seed --status
```

The three seed corridors should appear. Send "How do I get to banex" from
your phone — if you get the corridor reply, you're live.

## Day-2 operations

- **Roll out a code change**: push to `main` on GitHub. CI runs ruff/mypy/
  pytest. If green, Render auto-deploys (the `preDeployCommand` re-applies
  any new migrations).
- **Tune a setting without redeploying**: change the env var in the Render
  dashboard. Render restarts the service with the new value.
- **Inspect production data**: Neon dashboard has a query editor. Upstash
  dashboard has a Redis browser.
- **Roll back**: Render keeps the previous successful deploy. Click
  **Rollback** in the deploys tab.
- **Watch a deploy live**: the **Logs** tab streams stdout. structlog writes
  JSON lines, so you can grep for `event=corridor_seeded`, `event=route_success`,
  `event=rate_limited`, etc.

## Costs at MVP scale

- Render web service: **$7/month** (Starter, no sleep). Free tier sleeps
  after 15 min idle and adds 30s cold-start latency to the first message
  in a quiet hour — unacceptable for a chat bot, so don't use it.
- Neon: **free** (0.5 GB storage, 191 compute hours/month). Upgrade to Pro
  ($19/month) when we cross either limit.
- Upstash: **free** (10k commands/day). Plenty for hundreds of users.

Total at MVP: **$7/month**. Scaling to 1 GB Postgres adds $19. Replacing
the Twilio sandbox with a verified Meta number is then the only remaining
cost lever.

## Switching to Meta Direct (later)

1. Complete Meta Business verification (~1-2 weeks).
2. In Render dashboard, set `CHANNEL_PROVIDER=meta` and fill in the four
   `META_*` env vars.
3. In Meta's WhatsApp Manager, point the webhook at
   `https://wna-api.onrender.com/webhook/meta`. (Webhook is symmetric to
   Twilio's, our channel adapter handles the format difference.)
4. Restart the Render service to pick up the env change.
