## What and why

<!-- One or two sentences. Quote the SRS § or decision (D…) / work package (WP…) this implements. -->

## Checklist

- **Packages touched:** <!-- e.g. kx-core-crm, apps/arifaa-pack -->
- **Contract changed (`kx/core/contracts.py`):** Y / N
- **Event payload version changed (`kx/core/event_payloads.py`):** Y / N <!-- if Y: old consumers still accept it? -->
- **Migration:** none / in which database (`crm`, `automation`, `agents`, `core`) and revision id
- **`docs/FEATURE_MAP.md` refreshed** (`uv run kx map --write docs/FEATURE_MAP.md`) when wiring changed: Y / N / not needed
- **Hand-off pack regenerated** (`make handoff`) when an endpoint changed: Y / N / not needed

## Tests run

<!-- Paste the summary lines, e.g. "make lint: ok", "pytest: 967 passed, 4 skipped". -->
