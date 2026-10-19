# fix-dropcopy-reconciler

Post-trade **FIX 4.2 / FIX 4.4 ExecutionReport (`35=8`)** drop-copy clearing reconciler and **DTCC NSCC T+1 Continuous Net Settlement (CNS)** obligation calculator.

Built to verify street-side execution drop-copies from executing brokers, ECNs, and exchanges against internal Order Management System (OMS) fill ledgers in real time or end-of-day batch workflows.

---

## Architecture & Capabilities

1. **Wire-Level FIX 4.2 / 4.4 Parsing & Integrity Verification**
   - Parses SOH (`\x01`) and pipe (`|`) delimited `ExecutionReport (35=8)` frames.
   - Verifies standard Mod-256 `CheckSum (Tag 10)` framing.
   - Normalizes FIX 4.2 `ExecTransType (Tag 20)` (`0=New`, `1=Cancel`, `2=Correct`, `3=Status`) and FIX 4.4 `ExecType (Tag 150)` (`F=Trade`, `H=Trade Cancel`, `G=Trade Correct`, `I=Order Status`).

2. **Trade Bust & Restatement Lifecycle Engine**
   - Applies intraday trade busts (`20=1` / `150=H`) and price/quantity restatements (`20=2` / `150=G`) via `ExecRefID (Tag 19)` lineage tracking.
   - Enforces idempotency across `PossDupFlag (43=Y)` re-transmissions and filters non-fill order acknowledgments (`LastShares=0`).
   - Flags `STALE_BUST_REFERENCE` breaks when a cancel or correction references an unknown or already-busted execution ID.

3. **Two-Way OMS vs. Street Matching**
   - Matches active street executions against internal OMS records by primary `(ExecID)` and `(ExecRefID)` lineage, with secondary fallback on `ClOrdID (Tag 11)` when venues assign external execution IDs.
   - Classifies breaks into `PRICE_BREAK`, `QUANTITY_BREAK`, `SIDE_BREAK`, `SYMBOL_BREAK`, `ORPHAN_STREET_FILL`, and `MISSING_STREET_FILL`.

4. **DTCC NSCC T+1 Continuous Net Settlement (CNS) Engine**
   - Computes US equity T+1 settlement dates across weekends and NYSE holiday calendars.
   - Nets gross buy and sell executions per symbol into a single CNS share delivery/receive position and net cash settlement obligation, deducting **SEC Section 31** fees and **FINRA Trading Activity Fees (TAF)** (capped at `$8.30` per trade).

---

## Quick Start

```python
from datetime import date
from fix_reconciler import DropCopyReconciler, OmsExecution, Side

reconciler = DropCopyReconciler()

# 1. Ingest internal OMS execution ledger
reconciler.ingest_oms_fill(
    OmsExecution(
        cl_ord_id="CLO-20260316-01",
        exec_id="EX-1001",
        symbol="NVDA",
        side=Side.BUY,
        last_qty=500,
        last_px=135.25,
    )
)

# 2. Ingest street-side FIX 4.2 ExecutionReport (35=8) drop-copy
reconciler.ingest_drop_copy_raw(
    "8=FIX.4.2|35=8|20=0|150=2|39=2|11=CLO-20260316-01|17=EX-1001|"
    "55=NVDA|54=1|32=500|31=135.25|14=500|6=135.25|30=XNAS|10=082|"
)

# 3. Run reconciliation and T+1 CNS net settlement
summary = reconciler.reconcile(trade_date=date(2026, 3, 16))
assert summary.is_clean
cns = summary.cns_obligations["NVDA"]
print(f"Settles {cns.settlement_date}: net_shares={cns.net_share_position}, net_cash=${cns.total_net_settlement_cash:,.2f}")
```

---

## Running Tests

```bash
pytest -v
```

---

## Author

**Abdullah Azmi**  
- Website: [https://abdullahazmi.com](https://abdullahazmi.com)  
- GitHub: [@aazminu](https://github.com/aazminu)  
- Email: `m4u5t4@u.northwestern.edu`
