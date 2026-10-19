"""Post-trade FIX 4.2/4.4 ExecutionReport (35=8) drop-copy clearing reconciler
and DTCC NSCC T+1 Continuous Net Settlement (CNS) engine.

Handles:
- Raw FIX 4.2 and FIX 4.4 tag-value parsing (SOH ``\\x01`` or pipe ``|`` delimited)
  with Mod-256 ``CheckSum (10)`` verification.
- Street-side Drop-Copy ``ExecutionReport (35=8)`` lifecycle state transitions:
  - ``ExecTransType=0`` (New) / ``ExecType=F`` (Trade)
  - ``ExecTransType=1`` (Cancel / Bust) / ``ExecType=H`` (Trade Cancel) via ``ExecRefID (19)``
  - ``ExecTransType=2`` (Correct / Restate) / ``ExecType=G`` (Trade Correct) via ``ExecRefID (19)``
  - ``ExecTransType=3`` (Status) and duplicate ``PossDupFlag (43=Y)`` idempotency.
- Two-way matching against internal OMS fill ledgers by ``(ClOrdID, ExecID)`` and
  fallback ``ClOrdID`` matching, isolating price, quantity, side, symbol, missing
  street, and orphan venue breaks.
- T+1 Continuous Net Settlement (CNS) calculation per symbol with US equity
  regulatory fee accounting (SEC Section 31 fees and FINRA TAF).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Dict, Iterable, List, Optional, Set, Tuple


class Side(str, Enum):
    """FIX Side (Tag 54) values."""

    BUY = "1"
    SELL = "2"
    SELL_SHORT = "5"
    SELL_SHORT_EXEMPT = "6"

    @property
    def is_buy(self) -> bool:
        return self == Side.BUY

    @property
    def is_sell(self) -> bool:
        return self in (Side.SELL, Side.SELL_SHORT, Side.SELL_SHORT_EXEMPT)


class ExecTransType(str, Enum):
    """FIX 4.2 ExecTransType (Tag 20) / normalized FIX 4.4 trade lifecycle action."""

    NEW = "0"
    CANCEL = "1"
    CORRECT = "2"
    STATUS = "3"


class BreakType(str, Enum):
    """Taxonomy of post-trade OMS vs. Street Drop-Copy reconciliation breaks."""

    ORPHAN_STREET_FILL = "ORPHAN_STREET_FILL"
    MISSING_STREET_FILL = "MISSING_STREET_FILL"
    PRICE_BREAK = "PRICE_BREAK"
    QUANTITY_BREAK = "QUANTITY_BREAK"
    SIDE_BREAK = "SIDE_BREAK"
    SYMBOL_BREAK = "SYMBOL_BREAK"
    STALE_BUST_REFERENCE = "STALE_BUST_REFERENCE"


@dataclass(frozen=True)
class OmsExecution:
    """Internal Order Management System (OMS) fill record."""

    cl_ord_id: str
    exec_id: str
    symbol: str
    side: Side
    last_qty: int
    last_px: float
    account: str = "PROP-MAIN"
    transact_time: str = ""

    @property
    def notional(self) -> float:
        return round(self.last_qty * self.last_px, 6)


@dataclass(frozen=True)
class DropCopyExecution:
    """Normalized street-side FIX 4.2 / 4.4 ExecutionReport (35=8) drop-copy message."""

    exec_id: str
    cl_ord_id: str
    symbol: str
    side: Side
    last_qty: int
    last_px: float
    exec_trans_type: ExecTransType = ExecTransType.NEW
    exec_ref_id: Optional[str] = None
    orig_cl_ord_id: Optional[str] = None
    exec_type: str = "2"
    ord_status: str = "2"
    cum_qty: int = 0
    avg_px: float = 0.0
    last_mkt: str = "XNAS"
    transact_time: str = ""
    poss_dup: bool = False
    begin_string: str = "FIX.4.2"

    @property
    def notional(self) -> float:
        return round(self.last_qty * self.last_px, 6)


@dataclass(frozen=True)
class ReconciliationBreak:
    """Structured break emitted when OMS and active Street Drop-Copy state diverge."""

    break_type: BreakType
    cl_ord_id: str
    exec_id: str
    symbol: str
    oms_qty: Optional[int]
    street_qty: Optional[int]
    oms_px: Optional[float]
    street_px: Optional[float]
    notional_diff: float
    description: str


@dataclass(frozen=True)
class CnsSettlementObligation:
    """DTCC NSCC T+1 Continuous Net Settlement (CNS) projection for a single security."""

    symbol: str
    trade_date: date
    settlement_date: date
    gross_buy_qty: int
    gross_sell_qty: int
    net_share_position: int
    gross_buy_notional: float
    gross_sell_notional: float
    net_cash_obligation: float
    sec_fee: float
    taf_fee: float
    total_net_settlement_cash: float
    trade_count: int


@dataclass
class ReconciliationSummary:
    """Complete output of a post-trade reconciliation and T+1 CNS run."""

    matched_exec_ids: List[str] = field(default_factory=list)
    active_street_trades: Dict[str, DropCopyExecution] = field(default_factory=dict)
    busted_exec_ids: Set[str] = field(default_factory=set)
    corrected_exec_ids: Dict[str, str] = field(default_factory=dict)
    breaks: List[ReconciliationBreak] = field(default_factory=list)
    cns_obligations: Dict[str, CnsSettlementObligation] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return len(self.breaks) == 0

    @property
    def match_rate(self) -> float:
        total = len(self.matched_exec_ids) + len(self.breaks)
        if total == 0:
            return 1.0
        return len(self.matched_exec_ids) / total


# Standard US equity market holidays (fixed + observed dates for 2024-2027)
_US_MARKET_HOLIDAYS: Set[date] = {
    date(2025, 1, 1),
    date(2025, 1, 20),
    date(2025, 2, 17),
    date(2025, 4, 18),
    date(2025, 5, 26),
    date(2025, 6, 19),
    date(2025, 7, 4),
    date(2025, 9, 1),
    date(2025, 11, 27),
    date(2025, 12, 25),
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
}


def next_business_day(
    trade_date: date,
    settlement_days: int = 1,
    holidays: Optional[Set[date]] = None,
) -> date:
    """Compute T+N settlement date skipping weekends and US equity market holidays."""
    if settlement_days < 0:
        raise ValueError("settlement_days must be non-negative")
    holiday_calendar = _US_MARKET_HOLIDAYS if holidays is None else holidays
    current = trade_date
    remaining = settlement_days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5 and current not in holiday_calendar:
            remaining -= 1
    return current


def compute_fix_checksum(body_with_delimiters: str) -> str:
    """Compute standard 3-digit zero-padded Mod-256 FIX CheckSum (Tag 10)."""
    byte_sum = sum(body_with_delimiters.encode("ascii")) % 256
    return f"{byte_sum:03d}"


def validate_fix_checksum(raw_fix: str, delimiter: Optional[str] = None) -> bool:
    """Validate Tag 10 CheckSum on a raw FIX string.

    Supports SOH (``\\x01``) or pipe (``|``) delimited strings. When pipe-delimited,
    each pipe is normalized to ``\\x01`` (ASCII 1) prior to computing the Mod-256 sum,
    matching standard FIX wire framing.
    """
    sep = delimiter or ("\x01" if "\x01" in raw_fix else "|")
    marker = f"{sep}10="
    idx = raw_fix.rfind(marker)
    if idx == -1:
        return False
    prefix = raw_fix[: idx + len(sep)]
    tail = raw_fix[idx + len(marker) :]
    expected_str = tail.split(sep, 1)[0].strip()
    if len(expected_str) != 3 or not expected_str.isdigit():
        return False
    wire_prefix = prefix.replace(sep, "\x01") if sep != "\x01" else prefix
    return compute_fix_checksum(wire_prefix) == expected_str


def parse_fix_message(raw_fix: str, delimiter: Optional[str] = None) -> Dict[int, str]:
    """Parse a raw FIX 4.2 / 4.4 string into a dictionary keyed by integer tag number."""
    if not raw_fix or not raw_fix.strip():
        raise ValueError("Empty FIX message")
    sep = delimiter or ("\x01" if "\x01" in raw_fix else "|")
    tags: Dict[int, str] = {}
    for token in raw_fix.strip().split(sep):
        if not token:
            continue
        if "=" not in token:
            raise ValueError(f"Malformed FIX tag=value token: {token!r}")
        k_str, val = token.split("=", 1)
        try:
            tag_num = int(k_str)
        except ValueError as exc:
            raise ValueError(f"Non-integer FIX tag {k_str!r}") from exc
        tags[tag_num] = val
    return tags


def _normalize_exec_trans_type(tags: Dict[int, str]) -> ExecTransType:
    """Resolve lifecycle transaction type across FIX 4.2 (Tag 20) and FIX 4.4 (Tag 150)."""
    if 20 in tags:
        return ExecTransType(tags[20])
    exec_type = tags.get(150, "2")
    if exec_type == "H":
        return ExecTransType.CANCEL
    if exec_type == "G":
        return ExecTransType.CORRECT
    if exec_type == "I":
        return ExecTransType.STATUS
    return ExecTransType.NEW


def drop_copy_from_fix_tags(tags: Dict[int, str]) -> DropCopyExecution:
    """Construct a :class:`DropCopyExecution` from parsed FIX tags."""
    msg_type = tags.get(35)
    if msg_type != "8":
        raise ValueError(f"Expected ExecutionReport (35=8), got 35={msg_type!r}")

    required = (17, 11, 55, 54)
    missing = [t for t in required if t not in tags]
    if missing:
        raise ValueError(f"Missing required ExecutionReport FIX tags: {missing}")

    trans_type = _normalize_exec_trans_type(tags)
    last_qty = int(float(tags.get(32, "0")))
    last_px = float(tags.get(31, "0.0"))

    return DropCopyExecution(
        exec_id=tags[17],
        cl_ord_id=tags[11],
        symbol=tags[55],
        side=Side(tags[54]),
        last_qty=last_qty,
        last_px=last_px,
        exec_trans_type=trans_type,
        exec_ref_id=tags.get(19),
        orig_cl_ord_id=tags.get(41),
        exec_type=tags.get(150, "2"),
        ord_status=tags.get(39, "2"),
        cum_qty=int(float(tags.get(14, str(last_qty)))),
        avg_px=float(tags.get(6, str(last_px))),
        last_mkt=tags.get(30, "XNAS"),
        transact_time=tags.get(60, ""),
        poss_dup=tags.get(43, "N") == "Y",
        begin_string=tags.get(8, "FIX.4.2"),
    )


class DropCopyReconciler:
    """Stateful post-trade reconciler comparing internal OMS fills with street drop-copies."""

    def __init__(self) -> None:
        self._oms_by_exec_id: Dict[str, OmsExecution] = {}
        self._oms_by_cl_ord_id: Dict[str, List[OmsExecution]] = {}
        self._active_street: Dict[str, DropCopyExecution] = {}
        self._all_seen_street_ids: Set[str] = set()
        self._busted_exec_ids: Set[str] = set()
        self._corrected_exec_ids: Dict[str, str] = {}
        self._lifecycle_breaks: List[ReconciliationBreak] = []

    def ingest_oms_fill(self, fill: OmsExecution) -> None:
        """Register an internal OMS execution fill."""
        if fill.last_qty <= 0 or fill.last_px <= 0:
            raise ValueError("OMS fill quantity and price must be strictly positive")
        self._oms_by_exec_id[fill.exec_id] = fill
        self._oms_by_cl_ord_id.setdefault(fill.cl_ord_id, []).append(fill)

    def ingest_oms_fills(self, fills: Iterable[OmsExecution]) -> None:
        for fill in fills:
            self.ingest_oms_fill(fill)

    def ingest_drop_copy_raw(self, raw_fix: str, delimiter: Optional[str] = None) -> DropCopyExecution:
        """Parse and ingest a raw FIX 4.2/4.4 ExecutionReport (35=8) message."""
        tags = parse_fix_message(raw_fix, delimiter=delimiter)
        report = drop_copy_from_fix_tags(tags)
        self.ingest_drop_copy(report)
        return report

    def ingest_drop_copy(self, report: DropCopyExecution) -> None:
        """Process a street-side ExecutionReport applying New/Cancel/Correct semantics."""
        if report.exec_trans_type == ExecTransType.STATUS:
            return

        # Idempotent handling of PossDup re-transmissions or duplicate ExecIDs on NEW fills
        if report.exec_trans_type == ExecTransType.NEW:
            if report.exec_id in self._all_seen_street_ids:
                return
            # Ignore non-fill order acks (e.g. LastShares == 0 on New order ack 150=0)
            if report.last_qty == 0:
                return
            self._all_seen_street_ids.add(report.exec_id)
            self._active_street[report.exec_id] = report
            return

        if report.exec_trans_type == ExecTransType.CANCEL:
            self._all_seen_street_ids.add(report.exec_id)
            ref_id = report.exec_ref_id
            if not ref_id or ref_id not in self._active_street:
                self._lifecycle_breaks.append(
                    ReconciliationBreak(
                        break_type=BreakType.STALE_BUST_REFERENCE,
                        cl_ord_id=report.cl_ord_id,
                        exec_id=report.exec_id,
                        symbol=report.symbol,
                        oms_qty=None,
                        street_qty=report.last_qty,
                        oms_px=None,
                        street_px=report.last_px,
                        notional_diff=0.0,
                        description=(
                            f"Bust ExecutionReport {report.exec_id} references unknown or "
                            f"already-inactive ExecRefID={ref_id!r}"
                        ),
                    )
                )
                return
            del self._active_street[ref_id]
            self._busted_exec_ids.add(ref_id)
            return

        if report.exec_trans_type == ExecTransType.CORRECT:
            self._all_seen_street_ids.add(report.exec_id)
            ref_id = report.exec_ref_id
            if not ref_id or ref_id not in self._active_street:
                self._lifecycle_breaks.append(
                    ReconciliationBreak(
                        break_type=BreakType.STALE_BUST_REFERENCE,
                        cl_ord_id=report.cl_ord_id,
                        exec_id=report.exec_id,
                        symbol=report.symbol,
                        oms_qty=None,
                        street_qty=report.last_qty,
                        oms_px=None,
                        street_px=report.last_px,
                        notional_diff=report.notional,
                        description=(
                            f"Trade Correct ExecutionReport {report.exec_id} references "
                            f"unknown or inactive ExecRefID={ref_id!r}"
                        ),
                    )
                )
                return
            del self._active_street[ref_id]
            self._corrected_exec_ids[ref_id] = report.exec_id
            self._active_street[report.exec_id] = report

    def reconcile(
        self,
        price_tolerance: float = 1e-4,
        trade_date: Optional[date] = None,
    ) -> ReconciliationSummary:
        """Reconcile active street fills against OMS executions and compute T+1 CNS."""
        breaks: List[ReconciliationBreak] = list(self._lifecycle_breaks)
        matched_exec_ids: List[str] = []
        matched_oms_exec_ids: Set[str] = set()
        matched_street_exec_ids: Set[str] = set()

        # Pass 1: Exact ExecID match (or ExecRefID lineage match after Trade Correct)
        reverse_corrections = {new_id: old_id for old_id, new_id in self._corrected_exec_ids.items()}

        for street_id, street_fill in self._active_street.items():
            candidate_oms = self._oms_by_exec_id.get(street_id)
            if candidate_oms is None and street_id in reverse_corrections:
                orig_id = reverse_corrections[street_id]
                candidate_oms = self._oms_by_exec_id.get(orig_id)

            if candidate_oms is not None:
                matched_oms_exec_ids.add(candidate_oms.exec_id)
                matched_street_exec_ids.add(street_id)
                pair_breaks = self._compare_pair(candidate_oms, street_fill, price_tolerance)
                if pair_breaks:
                    breaks.extend(pair_breaks)
                else:
                    matched_exec_ids.append(street_id)

        # Pass 2: Fallback ClOrdID matching for remaining unmatched fills
        for street_id, street_fill in self._active_street.items():
            if street_id in matched_street_exec_ids:
                continue
            oms_candidates = [
                f
                for f in self._oms_by_cl_ord_id.get(street_fill.cl_ord_id, [])
                if f.exec_id not in matched_oms_exec_ids
            ]
            if oms_candidates:
                candidate_oms = oms_candidates[0]
                matched_oms_exec_ids.add(candidate_oms.exec_id)
                matched_street_exec_ids.add(street_id)
                pair_breaks = self._compare_pair(candidate_oms, street_fill, price_tolerance)
                if pair_breaks:
                    breaks.extend(pair_breaks)
                else:
                    matched_exec_ids.append(street_id)

        # Pass 3: Unmatched Street fills -> ORPHAN_STREET_FILL
        for street_id, street_fill in self._active_street.items():
            if street_id not in matched_street_exec_ids:
                breaks.append(
                    ReconciliationBreak(
                        break_type=BreakType.ORPHAN_STREET_FILL,
                        cl_ord_id=street_fill.cl_ord_id,
                        exec_id=street_fill.exec_id,
                        symbol=street_fill.symbol,
                        oms_qty=None,
                        street_qty=street_fill.last_qty,
                        oms_px=None,
                        street_px=street_fill.last_px,
                        notional_diff=street_fill.notional,
                        description=(
                            f"Street fill ExecID={street_fill.exec_id} "
                            f"(ClOrdID={street_fill.cl_ord_id}) missing in internal OMS"
                        ),
                    )
                )

        # Pass 4: Unmatched OMS fills -> MISSING_STREET_FILL
        for oms_id, oms_fill in self._oms_by_exec_id.items():
            if oms_id not in matched_oms_exec_ids:
                breaks.append(
                    ReconciliationBreak(
                        break_type=BreakType.MISSING_STREET_FILL,
                        cl_ord_id=oms_fill.cl_ord_id,
                        exec_id=oms_fill.exec_id,
                        symbol=oms_fill.symbol,
                        oms_qty=oms_fill.last_qty,
                        street_qty=None,
                        oms_px=oms_fill.last_px,
                        street_px=None,
                        notional_diff=-oms_fill.notional,
                        description=(
                            f"OMS execution ExecID={oms_fill.exec_id} "
                            f"(ClOrdID={oms_fill.cl_ord_id}) unconfirmed or busted by street"
                        ),
                    )
                )

        effective_trade_date = trade_date or date(2026, 3, 16)
        cns_obligations = self.compute_cns_settlement(effective_trade_date)

        return ReconciliationSummary(
            matched_exec_ids=sorted(matched_exec_ids),
            active_street_trades=dict(self._active_street),
            busted_exec_ids=set(self._busted_exec_ids),
            corrected_exec_ids=dict(self._corrected_exec_ids),
            breaks=breaks,
            cns_obligations=cns_obligations,
        )

    @staticmethod
    def _compare_pair(
        oms: OmsExecution,
        street: DropCopyExecution,
        price_tolerance: float,
    ) -> List[ReconciliationBreak]:
        found: List[ReconciliationBreak] = []
        notional_diff = round(street.notional - oms.notional, 6)

        if oms.symbol != street.symbol:
            found.append(
                ReconciliationBreak(
                    break_type=BreakType.SYMBOL_BREAK,
                    cl_ord_id=oms.cl_ord_id,
                    exec_id=street.exec_id,
                    symbol=street.symbol,
                    oms_qty=oms.last_qty,
                    street_qty=street.last_qty,
                    oms_px=oms.last_px,
                    street_px=street.last_px,
                    notional_diff=notional_diff,
                    description=f"Symbol mismatch: OMS={oms.symbol} vs Street={street.symbol}",
                )
            )

        if oms.side != street.side:
            found.append(
                ReconciliationBreak(
                    break_type=BreakType.SIDE_BREAK,
                    cl_ord_id=oms.cl_ord_id,
                    exec_id=street.exec_id,
                    symbol=street.symbol,
                    oms_qty=oms.last_qty,
                    street_qty=street.last_qty,
                    oms_px=oms.last_px,
                    street_px=street.last_px,
                    notional_diff=notional_diff,
                    description=f"Side mismatch: OMS={oms.side.value} vs Street={street.side.value}",
                )
            )

        if oms.last_qty != street.last_qty:
            found.append(
                ReconciliationBreak(
                    break_type=BreakType.QUANTITY_BREAK,
                    cl_ord_id=oms.cl_ord_id,
                    exec_id=street.exec_id,
                    symbol=street.symbol,
                    oms_qty=oms.last_qty,
                    street_qty=street.last_qty,
                    oms_px=oms.last_px,
                    street_px=street.last_px,
                    notional_diff=notional_diff,
                    description=f"Quantity break: OMS={oms.last_qty} vs Street={street.last_qty}",
                )
            )

        if abs(oms.last_px - street.last_px) > price_tolerance:
            found.append(
                ReconciliationBreak(
                    break_type=BreakType.PRICE_BREAK,
                    cl_ord_id=oms.cl_ord_id,
                    exec_id=street.exec_id,
                    symbol=street.symbol,
                    oms_qty=oms.last_qty,
                    street_qty=street.last_qty,
                    oms_px=oms.last_px,
                    street_px=street.last_px,
                    notional_diff=notional_diff,
                    description=(
                        f"Price break exceeds tolerance ({price_tolerance}): "
                        f"OMS={oms.last_px:.4f} vs Street={street.last_px:.4f}"
                    ),
                )
            )

        return found

    def compute_cns_settlement(
        self,
        trade_date: date,
        sec_fee_rate: float = 27.80e-6,
        taf_rate_per_share: float = 0.000166,
        taf_max_per_trade: float = 8.30,
    ) -> Dict[str, CnsSettlementObligation]:
        """Compute DTCC NSCC T+1 Continuous Net Settlement (CNS) obligations per symbol.

        Conventions:
        - ``net_share_position`` > 0: net share receive from CNS; < 0: net share delivery to CNS.
        - ``net_cash_obligation`` = ``gross_sell_notional - gross_buy_notional``
          (positive means net cash credit receivable before regulatory fees; negative means debit payable).
        - Regulatory fees (SEC Section 31 fee on sell principal and FINRA TAF on sell shares,
          capped at ``taf_max_per_trade`` per execution) reduce net cash proceeds.
        """
        settlement_date = next_business_day(trade_date, settlement_days=1)
        by_symbol: Dict[str, List[DropCopyExecution]] = {}
        for fill in self._active_street.values():
            by_symbol.setdefault(fill.symbol, []).append(fill)

        obligations: Dict[str, CnsSettlementObligation] = {}
        for symbol, fills in sorted(by_symbol.items()):
            gross_buy_qty = 0
            gross_sell_qty = 0
            gross_buy_notional = 0.0
            gross_sell_notional = 0.0
            sec_fee_total = 0.0
            taf_fee_total = 0.0

            for fill in fills:
                if fill.side.is_buy:
                    gross_buy_qty += fill.last_qty
                    gross_buy_notional += fill.notional
                elif fill.side.is_sell:
                    gross_sell_qty += fill.last_qty
                    gross_sell_notional += fill.notional
                    sec_fee_total += fill.notional * sec_fee_rate
                    taf_fee_total += min(fill.last_qty * taf_rate_per_share, taf_max_per_trade)

            net_shares = gross_buy_qty - gross_sell_qty
            net_cash = round(gross_sell_notional - gross_buy_notional, 2)
            sec_fee_rounded = round(sec_fee_total, 2)
            taf_fee_rounded = round(taf_fee_total, 2)
            total_net_cash = round(net_cash - sec_fee_rounded - taf_fee_rounded, 2)

            obligations[symbol] = CnsSettlementObligation(
                symbol=symbol,
                trade_date=trade_date,
                settlement_date=settlement_date,
                gross_buy_qty=gross_buy_qty,
                gross_sell_qty=gross_sell_qty,
                net_share_position=net_shares,
                gross_buy_notional=round(gross_buy_notional, 2),
                gross_sell_notional=round(gross_sell_notional, 2),
                net_cash_obligation=net_cash,
                sec_fee=sec_fee_rounded,
                taf_fee=taf_fee_rounded,
                total_net_settlement_cash=total_net_cash,
                trade_count=len(fills),
            )

        return obligations
