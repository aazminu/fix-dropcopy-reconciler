"""FIX 4.2/4.4 Drop-Copy Reconciler and T+1 CNS Net Settlement Engine."""

from .reconciler import (
    BreakType,
    CnsSettlementObligation,
    DropCopyExecution,
    DropCopyReconciler,
    ExecTransType,
    OmsExecution,
    ReconciliationBreak,
    ReconciliationSummary,
    Side,
    compute_fix_checksum,
    next_business_day,
    parse_fix_message,
    validate_fix_checksum,
)

__all__ = [
    "BreakType",
    "CnsSettlementObligation",
    "DropCopyExecution",
    "DropCopyReconciler",
    "ExecTransType",
    "OmsExecution",
    "ReconciliationBreak",
    "ReconciliationSummary",
    "Side",
    "compute_fix_checksum",
    "next_business_day",
    "parse_fix_message",
    "validate_fix_checksum",
]

__version__ = "0.2.0"

from .diagnostics import run_diagnostic_suite, validate_runtime_invariants
__all__ = list(__all__) + ["run_diagnostic_suite", "validate_runtime_invariants"]
