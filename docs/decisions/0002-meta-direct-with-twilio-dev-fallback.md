# 2. Meta Cloud API for production, Twilio sandbox for dev

Date: 2026-04-21

## Status

Accepted

## Context

Meta Cloud API Direct is ≈10× cheaper per message than Twilio but requires business verification, a dedicated phone number, and template approval (1–2 week lead time). On an 8-week MVP with a 4-person team, blocking Sprint 1 on verification is unacceptable.

## Decision

- Production target: Meta Cloud API Direct.
- Development + fallback: Twilio WhatsApp sandbox.
- Both providers implement the `ChannelAdapter` interface in `app/channel/base.py`.
- `CHANNEL_PROVIDER` env var selects at runtime.
- Ops kicks off Meta verification on Day 1, running in parallel with Sprint 1 dev.

## Consequences

- Zero Sprint 1 blockage on verification.
- Slightly more code (two adapters instead of one), but the adapter interface is narrow.
- If Meta verification takes longer than expected, we ship beta on Twilio and migrate later without code changes outside `channel/`.
