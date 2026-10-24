# Changelog — `fix-dropcopy-reconciler`

All notable engineering changes to `fix-dropcopy-reconciler` (`fix_reconciler`) are documented in this file.

## [0.3.0] — 2026-10-24
### Added
- Documented CLI diagnostic usage (`python -m fix_reconciler --self-test --json`) and deterministic invariant verification workflows in `README.md`.
- Added release history and semantic versioning audit trail in `CHANGELOG.md`.

## [0.2.1] — 2026-10-23
### Added
- Added automated CLI and runtime invariant regression suite in `tests/test_diagnostics_cli.py` covering seed determinism, negative-seed rejection, JSON output schema, and `--self-test` exit codes.

## [0.2.0] — 2026-10-21
### Added
- Added `src/fix_reconciler/diagnostics.py` (`run_diagnostic_suite`, `validate_runtime_invariants`) and module CLI runner `src/fix_reconciler/__main__.py`.
- Exported diagnostic helpers in `fix_reconciler.__all__`.

## [0.1.0] — 2026-10-19
### Added
- Initial architecture and core test suite: FIX 4.2/4.4 Drop-Copy trade execution reconciler, checksum validator, and T+1 CNS net settlement obligation engine.
