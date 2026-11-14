# Phase 9 Decision Notes

## Task 1

- Registered one instance of each built-in `CliDriver` in the instance-scoped default registry. `names()` remains sorted.
- Added assertions for the built-in names and each resolved concrete class.

## Task 2

- The live test uses `check(driver)` to query v2 capabilities and keeps the `live` marker. It was not run.
- Packaging now inspects the wheel archive and asserts there is no `scripts/` entry.

## Task 3

- Rewrote README for the v2 API, including exact entry-point signatures, public request/result fields, all event and error/outcome values, custom driver authoring, permissions, migration differences, and known limits.
- Updated the AGENTS layout and CONTRIBUTING module map to the v2 modules and fixture tooling.

## Task 4

- Added a README test that compiles every fenced Python example and checks imports from `coding_agent_cli` against `__all__`.
- The README test also enforces the migration heading and excludes removed/private API references.

## Task 5

- Preserved the AGENTS hard rules while updating its layout table and CONTRIBUTING module references.

## Verification

- `export VIRTUAL_ENV=; uv run ruff format --check` -> `106 files already formatted`.
- `export VIRTUAL_ENV=; uv run ruff check` -> `All checks passed!`.
- `export VIRTUAL_ENV=; uv run pyright` -> `0 errors, 0 warnings, 0 informations`.
- `export VIRTUAL_ENV=; timeout 300 uv run pytest -q` -> `371 passed, 1 skipped, 4 deselected in 26.38s`.
- Did not run live tests.
