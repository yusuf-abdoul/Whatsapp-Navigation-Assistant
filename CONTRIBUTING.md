# Contributing to WNA

Thanks for your interest — this project only works if commuters, developers,
and city-watchers pitch in. Every kind of contribution is welcome: a bug
report, a new corridor for your neighbourhood, a fix, or a feature.

## Ways to contribute

- **File a bug.** Open an issue with steps to reproduce. If you saw the bug
  on the live bot, paste the chat transcript.
- **Submit a corridor.** You don't have to write code — sign in to the web
  form and add routes for a neighbourhood we don't cover yet.
- **Fix a bug or ship a feature.** Look for issues tagged `good first issue`
  or `help wanted`. Comment on the issue before you start so we don't step
  on each other.
- **Improve docs.** Unclear README, missing setup step, out-of-date architecture
  note — PRs welcome.

## Local development

Prerequisites: Python 3.12, [uv](https://github.com/astral-sh/uv), Docker.

```bash
git clone https://github.com/<your-fork>/WNA.git
cd WNA
uv sync
cp .env.example .env                  # fill in secrets
docker compose up -d postgres redis
uv run alembic upgrade head
uv run python -m app.corridors.seed
uv run uvicorn app.main:app --reload
```

Open http://localhost:8000. Health: http://localhost:8000/health.

**Testing WhatsApp locally.** The bot needs a public URL. Use ngrok:

```bash
# terminal 1
uv run uvicorn app.main:app --reload
# terminal 2
ngrok http 8000
```

Point your Twilio sandbox webhook at `https://<ngrok-id>.ngrok.app/webhook/twilio`.
Join the sandbox from your phone and message it. Full walkthrough:
[docs/contributing.md](docs/contributing.md#testing-webhooks-locally).

## Tests, lint, types

Every PR must pass all three:

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run mypy app        # types
```

- Unit tests live in `tests/unit/` — pure logic, no network or Redis.
- Integration tests live in `tests/integration/` — real Postgres (via
  `docker compose up -d postgres`) + fake Redis. Tests use a separate
  `wna_test` database, so your dev data is never touched.

## Pull requests

- Branch from `main`: `feat/<short-name>`, `fix/<short-name>`, `docs/<...>`.
- **Small PRs merge fast.** Aim for one shipped slice per PR — a bug fix, one
  feature, one refactor. Split large work.
- **Conventional Commits** for the title:
  - `feat: add nearby-search flow`
  - `fix: handle empty geocode response`
  - `docs: clarify session TTL rationale`
- Link the issue you're addressing in the description.
- The maintainer will review and squash-merge to `main`.

## Coding style

- `ruff` handles lint + format. Don't bikeshed style — let the tool decide.
- Comments explain **why**, never **what**. If a comment restates the code,
  delete it.
- No half-finished implementations. Don't add error handling for scenarios
  that can't happen. Trust internal code and framework guarantees; validate
  at system boundaries only.
- Prefer editing existing files over creating new ones.
- Follow existing module patterns before introducing a new one.

## Design decisions

Any choice that affects more than one module should get a short ADR in
`docs/decisions/`. Copy the template from
`docs/decisions/0001-record-architecture-decisions.md`.

## Reporting security issues

**Do not open a public issue for security bugs.** Instead, email the
maintainer directly (see the GitHub profile). We'll acknowledge within 3
business days and coordinate a fix + disclosure.

## Code of Conduct

This project follows the [Contributor Covenant Code of
Conduct](CODE_OF_CONDUCT.md). By participating you agree to those terms.

## License

By submitting a contribution you agree to license it under the same
[Apache License 2.0](LICENSE) that covers the rest of the project. No CLA
required — the contribution grant in Apache-2.0 §5 is sufficient.

## Questions

Open a [discussion](https://github.com/<your-fork>/WNA/discussions) — or ask
in an issue if there isn't a discussion category that fits.
