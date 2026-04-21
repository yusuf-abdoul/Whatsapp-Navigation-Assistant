# QA Route Set

Manual ground-truth routes for weekly quality review.

## Adding a route

Create a YAML file named `<origin-slug>_to_<destination-slug>.yaml`:

```yaml
origin: "Berger Junction, Wuse, Abuja"
destination: "Jabi Lake Mall, Jabi, Abuja"
expected_distance_km_range: [5, 9]
expected_duration_min_range: [12, 25]
expected_landmarks:
  - "Wuse"
  - "Jabi"
notes: "Should not route through Central Business District at peak hours."
```

## Weekly review cadence

PM owns this. Run all QA routes against staging each Friday, record pass/fail, log regressions as GitHub issues tagged `qa-regression`.
