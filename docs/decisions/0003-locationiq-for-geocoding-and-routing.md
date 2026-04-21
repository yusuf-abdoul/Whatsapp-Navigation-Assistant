# 3. LocationIQ for geocoding, routing, and nearby search

Date: 2026-04-21

## Status

Accepted

## Context

PRD §7.2 calls for a cost-sensitive OSM-based stack. Options considered:

- **Self-host Nominatim + OSRM** — cheapest ongoing, but operationally heavy for a 1-person ops team and slow to set up.
- **LocationIQ** — managed Nominatim + OSRM, ~$0 at MVP volume, single API key.
- **Mapbox / Google Maps** — higher quality, significantly more expensive, not justified until usage data proves need.

## Decision

LocationIQ for all three capabilities (geocoding, routing, POI search). One vendor, one key, one client module.

## Consequences

- Fast setup, low cost, good-enough quality for Abuja's better-mapped zones.
- Vendor lock-in risk is low because we wrap calls in `app/resolver/` and `app/routing/` — swap is a module-local change.
- If Abuja POI data gaps hurt quality, we layer on the alias dictionary (`data/aliases/abuja.yaml`) before escalating to a paid provider.
