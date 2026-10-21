"""Deterministic runtime diagnostics and invariant verification for `fix_reconciler`."""

from __future__ import annotations

import time
from typing import Any

from datetime import date
from .reconciler import compute_fix_checksum, validate_fix_checksum, next_business_day

def _domain_probe(seed: int) -> dict:
    body = "8=FIX.4.2\x019=65\x0135=8\x0149=EXEC\x0156=OMS\x0134=1\x01"
    cs = compute_fix_checksum(body)
    raw = f"{body}10={cs}\x01"
    valid = validate_fix_checksum(raw)
    nbd = next_business_day(date(2026, 10, 23))
    return {
        "checksum": cs,
        "checksum_valid": valid,
        "next_business_day": nbd.isoformat(),
        "invariants_ok": (len(cs) == 3 and valid is True and nbd.isoformat() == "2026-10-26"),
    }


def run_diagnostic_suite(seed: int = 42) -> dict[str, Any]:
    """Execute deterministic domain probe for `fix-dropcopy-reconciler` and return structured metrics."""
    if not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    t0 = time.perf_counter()
    metrics = _domain_probe(seed)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    return {
        "repository": "fix-dropcopy-reconciler",
        "package": "fix_reconciler",
        "seed": seed,
        "elapsed_ms": elapsed_ms,
        "metrics": metrics,
        "status": "ok" if metrics.get("invariants_ok", False) else "failed",
    }


def validate_runtime_invariants(seed: int = 42) -> dict[str, Any]:
    """Validate deterministic reproducibility and domain invariants for `fix_reconciler`."""
    run_a = run_diagnostic_suite(seed=seed)
    run_b = run_diagnostic_suite(seed=seed)
    reproducible = run_a["metrics"] == run_b["metrics"]
    passed = bool(run_a["metrics"].get("invariants_ok", False)) and reproducible
    return {
        "repository": "fix-dropcopy-reconciler",
        "package": "fix_reconciler",
        "seed": seed,
        "reproducible": reproducible,
        "passed": passed,
        "metrics": run_a["metrics"],
    }
