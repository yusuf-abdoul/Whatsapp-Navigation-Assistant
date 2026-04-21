# 1. Record architecture decisions

Date: 2026-04-21

## Status

Accepted

## Context

Any decision that affects more than one module should be recorded so future contributors understand why the code looks the way it does.

## Decision

We use lightweight ADRs (Architecture Decision Records). Each ADR is numbered, dated, and committed to `docs/decisions/`.

Template:

```
# N. Short title

Date: YYYY-MM-DD

## Status
Proposed | Accepted | Superseded by ADR-M

## Context
What forces are at play?

## Decision
What did we choose?

## Consequences
What do we accept by choosing this?
```

## Consequences

- Decisions leave a paper trail.
- Onboarding gets faster.
- Reversing a decision requires a new superseding ADR, which forces deliberate thought.
