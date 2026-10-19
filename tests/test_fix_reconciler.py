"""Comprehensive test suite for fix-dropcopy-reconciler."""

from datetime import date
import pytest

from fix_reconciler import (
    BreakType,
    DropCopyExecution,
    DropCopyReconciler,
    ExecTransType,
    OmsExecution,
    Side,
    compute_fix_checksum,
    next_business_day,
    parse_fix_message,
    validate_fix_checksum,
)


def _build_wire_fix(fields: list[tuple[int, str]], delimiter: str = "|") -> str:
    """Build a valid FIX message with computed Tag 10 CheckSum."""
    body = delimiter.join(f"{k}={v}" for k, v in fields) + delimiter
    wire_body = body.replace(delimiter, "\x01")
    checksum = compute_fix_checksum(wire_body)
    return f"{body}10={checksum}{delimiter}"


def test_parse_fix_message_pipe_and_soh():
    raw_pipe = _build_wire_fix(
        [(8, "FIX.4.2"), (35, "8"), (11, "ORD-101"), (17, "EX-1"), (55, "AAPL"), (54, "1"), (32, "100"), (31, "215.50")],
        delimiter="|",
    )
    tags_pipe = parse_fix_message(raw_pipe)
    assert tags_pipe[8] == "FIX.4.2"
    assert tags_pipe[35] == "8"
    assert tags_pipe[55] == "AAPL"
    assert validate_fix_checksum(raw_pipe) is True

    raw_soh = raw_pipe.replace("|", "\x01")
    tags_soh = parse_fix_message(raw_soh)
    assert tags_soh == tags_pipe
    assert validate_fix_checksum(raw_soh) is True


def test_validate_fix_checksum_detects_corruption():
    raw = _build_wire_fix(
        [(8, "FIX.4.2"), (35, "8"), (11, "ORD-1"), (17, "EX-1"), (55, "MSFT"), (54, "1"), (32, "50"), (31, "420.00")]
    )
    corrupted = raw.replace("31=420.00", "31=421.00")
    assert validate_fix_checksum(corrupted) is False
    assert validate_fix_checksum("8=FIX.4.2|35=8|55=MSFT|") is False


def test_parse_fix_message_rejects_malformed_tokens():
    with pytest.raises(ValueError, match="Empty FIX message"):
        parse_fix_message("   ")
    with pytest.raises(ValueError, match="Malformed FIX tag=value"):
        parse_fix_message("8=FIX.4.2|BADTOKEN|35=8|")
    with pytest.raises(ValueError, match="Non-integer FIX tag"):
        parse_fix_message("8=FIX.4.2|TAG=8|")


def test_clean_two_way_reconciliation():
    reconciler = DropCopyReconciler()
    reconciler.ingest_oms_fill(
        OmsExecution(cl_ord_id="CLO-1", exec_id="EX-100", symbol="NVDA", side=Side.BUY, last_qty=500, last_px=135.25)
    )
    reconciler.ingest_oms_fill(
        OmsExecution(cl_ord_id="CLO-2", exec_id="EX-101", symbol="NVDA", side=Side.SELL, last_qty=200, last_px=136.00)
    )

    msg1 = _build_wire_fix(
        [(8, "FIX.4.2"), (35, "8"), (20, "0"), (11, "CLO-1"), (17, "EX-100"), (55, "NVDA"), (54, "1"), (32, "500"), (31, "135.25")]
    )
    msg2 = _build_wire_fix(
        [(8, "FIX.4.2"), (35, "8"), (20, "0"), (11, "CLO-2"), (17, "EX-101"), (55, "NVDA"), (54, "2"), (32, "200"), (31, "136.00")]
    )
    reconciler.ingest_drop_copy_raw(msg1)
    reconciler.ingest_drop_copy_raw(msg2)

    summary = reconciler.reconcile(trade_date=date(2026, 3, 16))
    assert summary.is_clean is True
    assert summary.match_rate == 1.0
    assert summary.matched_exec_ids == ["EX-100", "EX-101"]


def test_exec_trans_type_cancel_busts_street_fill():
    reconciler = DropCopyReconciler()
    # OMS has the fill, but the venue busts it -> results in MISSING_STREET_FILL break
    reconciler.ingest_oms_fill(
        OmsExecution(cl_ord_id="CLO-10", exec_id="EX-500", symbol="SPY", side=Side.BUY, last_qty=1000, last_px=580.00)
    )
    new_msg = _build_wire_fix(
        [(8, "FIX.4.2"), (35, "8"), (20, "0"), (11, "CLO-10"), (17, "EX-500"), (55, "SPY"), (54, "1"), (32, "1000"), (31, "580.00")]
    )
    bust_msg = _build_wire_fix(
        [(8, "FIX.4.2"), (35, "8"), (20, "1"), (19, "EX-500"), (11, "CLO-10"), (17, "EX-501-BUST"), (55, "SPY"), (54, "1"), (32, "1000"), (31, "580.00")]
    )
    reconciler.ingest_drop_copy_raw(new_msg)
    reconciler.ingest_drop_copy_raw(bust_msg)

    summary = reconciler.reconcile()
    assert "EX-500" in summary.busted_exec_ids
    assert len(summary.active_street_trades) == 0
    assert len(summary.breaks) == 1
    assert summary.breaks[0].break_type == BreakType.MISSING_STREET_FILL


def test_fix44_exec_type_trade_correct_updates_price_and_matches_updated_oms():
    reconciler = DropCopyReconciler()
    # OMS still holds old price 150.00 while venue sends FIX 4.4 Trade Correct (150=G) to 149.50
    reconciler.ingest_oms_fill(
        OmsExecution(cl_ord_id="CLO-22", exec_id="EX-700", symbol="META", side=Side.BUY, last_qty=300, last_px=150.00)
    )
    new_msg = _build_wire_fix(
        [(8, "FIX.4.4"), (35, "8"), (150, "F"), (11, "CLO-22"), (17, "EX-700"), (55, "META"), (54, "1"), (32, "300"), (31, "150.00")]
    )
    correct_msg = _build_wire_fix(
        [(8, "FIX.4.4"), (35, "8"), (150, "G"), (19, "EX-700"), (11, "CLO-22"), (17, "EX-701"), (55, "META"), (54, "1"), (32, "300"), (31, "149.50")]
    )
    reconciler.ingest_drop_copy_raw(new_msg)
    reconciler.ingest_drop_copy_raw(correct_msg)

    summary = reconciler.reconcile()
    assert summary.corrected_exec_ids == {"EX-700": "EX-701"}
    assert len(summary.breaks) == 1
    brk = summary.breaks[0]
    assert brk.break_type == BreakType.PRICE_BREAK
    assert brk.oms_px == 150.00
    assert brk.street_px == 149.50
    assert brk.notional_diff == pytest.approx(-150.0)


def test_quantity_and_side_breaks_detected():
    reconciler = DropCopyReconciler()
    reconciler.ingest_oms_fill(
        OmsExecution(cl_ord_id="CLO-33", exec_id="EX-900", symbol="TSLA", side=Side.BUY, last_qty=400, last_px=250.00)
    )
    reconciler.ingest_drop_copy(
        DropCopyExecution(
            exec_id="EX-900",
            cl_ord_id="CLO-33",
            symbol="TSLA",
            side=Side.SELL,
            last_qty=350,
            last_px=250.00,
        )
    )
    summary = reconciler.reconcile()
    break_types = {b.break_type for b in summary.breaks}
    assert BreakType.SIDE_BREAK in break_types
    assert BreakType.QUANTITY_BREAK in break_types


def test_orphan_street_fill_detected():
    reconciler = DropCopyReconciler()
    reconciler.ingest_drop_copy(
        DropCopyExecution(
            exec_id="EX-ORPHAN",
            cl_ord_id="CLO-UNKNOWN",
            symbol="AMZN",
            side=Side.BUY,
            last_qty=100,
            last_px=195.00,
        )
    )
    summary = reconciler.reconcile()
    assert len(summary.breaks) == 1
    assert summary.breaks[0].break_type == BreakType.ORPHAN_STREET_FILL
    assert summary.breaks[0].notional_diff == pytest.approx(19500.0)


def test_stale_bust_and_correct_reference_breaks():
    reconciler = DropCopyReconciler()
    reconciler.ingest_drop_copy(
        DropCopyExecution(
            exec_id="EX-BUST-BAD",
            cl_ord_id="CLO-99",
            symbol="GOOG",
            side=Side.BUY,
            last_qty=100,
            last_px=180.0,
            exec_trans_type=ExecTransType.CANCEL,
            exec_ref_id="NON-EXISTENT-ID",
        )
    )
    reconciler.ingest_drop_copy(
        DropCopyExecution(
            exec_id="EX-CORR-BAD",
            cl_ord_id="CLO-99",
            symbol="GOOG",
            side=Side.BUY,
            last_qty=100,
            last_px=180.0,
            exec_trans_type=ExecTransType.CORRECT,
            exec_ref_id="NON-EXISTENT-ID",
        )
    )
    summary = reconciler.reconcile()
    assert len(summary.breaks) == 2
    assert all(b.break_type == BreakType.STALE_BUST_REFERENCE for b in summary.breaks)


def test_poss_dup_idempotency_and_zero_qty_new_ack_ignored():
    reconciler = DropCopyReconciler()
    reconciler.ingest_oms_fill(
        OmsExecution(cl_ord_id="CLO-77", exec_id="EX-77", symbol="JPM", side=Side.BUY, last_qty=250, last_px=210.0)
    )
    # Order Ack (LastShares=0) should be ignored
    ack_msg = _build_wire_fix(
        [(8, "FIX.4.2"), (35, "8"), (20, "0"), (150, "0"), (11, "CLO-77"), (17, "EX-ACK"), (55, "JPM"), (54, "1"), (32, "0"), (31, "0.0")]
    )
    fill_msg = _build_wire_fix(
        [(8, "FIX.4.2"), (35, "8"), (20, "0"), (150, "2"), (11, "CLO-77"), (17, "EX-77"), (55, "JPM"), (54, "1"), (32, "250"), (31, "210.0")]
    )
    dup_msg = _build_wire_fix(
        [(8, "FIX.4.2"), (35, "8"), (43, "Y"), (20, "0"), (150, "2"), (11, "CLO-77"), (17, "EX-77"), (55, "JPM"), (54, "1"), (32, "250"), (31, "210.0")]
    )
    reconciler.ingest_drop_copy_raw(ack_msg)
    reconciler.ingest_drop_copy_raw(fill_msg)
    reconciler.ingest_drop_copy_raw(dup_msg)

    summary = reconciler.reconcile()
    assert summary.is_clean is True
    assert len(summary.active_street_trades) == 1


def test_fallback_cl_ord_id_matching_when_venue_assigns_different_exec_id():
    reconciler = DropCopyReconciler()
    reconciler.ingest_oms_fill(
        OmsExecution(cl_ord_id="CLO-500", exec_id="INTERNAL-1", symbol="AMD", side=Side.BUY, last_qty=150, last_px=160.0)
    )
    reconciler.ingest_drop_copy(
        DropCopyExecution(
            exec_id="VENUE-9999",
            cl_ord_id="CLO-500",
            symbol="AMD",
            side=Side.BUY,
            last_qty=150,
            last_px=160.0,
        )
    )
    summary = reconciler.reconcile()
    assert summary.is_clean is True
    assert summary.matched_exec_ids == ["VENUE-9999"]


def test_cns_t1_net_settlement_and_regulatory_fees():
    reconciler = DropCopyReconciler()
    # Buy 1,000 AAPL @ $200.00 = $200,000 debit
    # Sell 1,500 AAPL @ $202.00 = $303,000 credit
    # Sell Short 100,000 AAPL @ $201.00 = $20,100,000 credit (tests FINRA TAF $8.30 cap)
    reconciler.ingest_drop_copy(
        DropCopyExecution(exec_id="E1", cl_ord_id="C1", symbol="AAPL", side=Side.BUY, last_qty=1000, last_px=200.00)
    )
    reconciler.ingest_drop_copy(
        DropCopyExecution(exec_id="E2", cl_ord_id="C2", symbol="AAPL", side=Side.SELL, last_qty=1500, last_px=202.00)
    )
    reconciler.ingest_drop_copy(
        DropCopyExecution(
            exec_id="E3", cl_ord_id="C3", symbol="AAPL", side=Side.SELL_SHORT, last_qty=100_000, last_px=201.00
        )
    )

    obligations = reconciler.compute_cns_settlement(trade_date=date(2026, 4, 2))
    aapl = obligations["AAPL"]
    # 2026-04-03 is Good Friday (market holiday), so T+1 settles Monday 2026-04-06
    assert aapl.settlement_date == date(2026, 4, 6)
    assert aapl.gross_buy_qty == 1000
    assert aapl.gross_sell_qty == 101_500
    assert aapl.net_share_position == -100_500
    assert aapl.gross_buy_notional == pytest.approx(200_000.0)
    assert aapl.gross_sell_notional == pytest.approx(20_403_000.0)
    assert aapl.net_cash_obligation == pytest.approx(20_203_000.0)
    # TAF on E2 = 1500 * 0.000166 = 0.249; TAF on E3 = capped at 8.30 -> total 8.55
    assert aapl.taf_fee == pytest.approx(8.55)
    # SEC fee on $20,403,000 * 27.80e-6 = $567.20
    assert aapl.sec_fee == pytest.approx(567.20)
    assert aapl.total_net_settlement_cash == pytest.approx(20_203_000.0 - 567.20 - 8.55)


def test_next_business_day_weekend_and_holiday_roll():
    # Friday 2026-03-13 -> Monday 2026-03-16
    assert next_business_day(date(2026, 3, 13), settlement_days=1) == date(2026, 3, 16)
    # Thursday 2026-06-18 -> Friday 2026-06-19 is Juneteenth holiday -> Monday 2026-06-22
    assert next_business_day(date(2026, 6, 18), settlement_days=1) == date(2026, 6, 22)


def test_symbol_break_and_non_execution_report_rejection():
    reconciler = DropCopyReconciler()
    reconciler.ingest_oms_fill(
        OmsExecution(cl_ord_id="CLO-SYM", exec_id="EX-SYM", symbol="GOOG", side=Side.BUY, last_qty=50, last_px=175.0)
    )
    reconciler.ingest_drop_copy(
        DropCopyExecution(exec_id="EX-SYM", cl_ord_id="CLO-SYM", symbol="GOOGL", side=Side.BUY, last_qty=50, last_px=175.0)
    )
    summary = reconciler.reconcile()
    assert len(summary.breaks) == 1
    assert summary.breaks[0].break_type == BreakType.SYMBOL_BREAK

    with pytest.raises(ValueError, match="Expected ExecutionReport"):
        reconciler.ingest_drop_copy_raw("8=FIX.4.2|35=D|11=CLO-1|55=AAPL|54=1|")


def test_status_exec_trans_type_and_invalid_oms_fill():
    reconciler = DropCopyReconciler()
    reconciler.ingest_drop_copy(
        DropCopyExecution(
            exec_id="EX-STAT",
            cl_ord_id="CLO-STAT",
            symbol="IBM",
            side=Side.BUY,
            last_qty=100,
            last_px=190.0,
            exec_trans_type=ExecTransType.STATUS,
        )
    )
    assert len(reconciler.reconcile().active_street_trades) == 0

    with pytest.raises(ValueError, match="strictly positive"):
        reconciler.ingest_oms_fill(
            OmsExecution(cl_ord_id="C", exec_id="E", symbol="IBM", side=Side.BUY, last_qty=0, last_px=190.0)
        )


def test_trade_correct_resolves_cleanly_when_oms_has_updated_price():
    reconciler = DropCopyReconciler()
    # OMS has already been updated to the corrected price 148.00 under original ExecID EX-800
    reconciler.ingest_oms_fill(
        OmsExecution(cl_ord_id="CLO-88", exec_id="EX-800", symbol="NFLX", side=Side.BUY, last_qty=200, last_px=148.00)
    )
    reconciler.ingest_drop_copy(
        DropCopyExecution(
            exec_id="EX-800",
            cl_ord_id="CLO-88",
            symbol="NFLX",
            side=Side.BUY,
            last_qty=200,
            last_px=149.00,
            exec_trans_type=ExecTransType.NEW,
        )
    )
    reconciler.ingest_drop_copy(
        DropCopyExecution(
            exec_id="EX-801",
            cl_ord_id="CLO-88",
            symbol="NFLX",
            side=Side.BUY,
            last_qty=200,
            last_px=148.00,
            exec_trans_type=ExecTransType.CORRECT,
            exec_ref_id="EX-800",
        )
    )
    summary = reconciler.reconcile()
    assert summary.is_clean is True
    assert summary.matched_exec_ids == ["EX-801"]


def test_price_within_tolerance_does_not_trigger_break():
    reconciler = DropCopyReconciler()
    reconciler.ingest_oms_fill(
        OmsExecution(cl_ord_id="CLO-TOL", exec_id="EX-TOL", symbol="BAC", side=Side.BUY, last_qty=1000, last_px=38.12341)
    )
    reconciler.ingest_drop_copy(
        DropCopyExecution(
            exec_id="EX-TOL",
            cl_ord_id="CLO-TOL",
            symbol="BAC",
            side=Side.BUY,
            last_qty=1000,
            last_px=38.12345,
        )
    )
    summary = reconciler.reconcile(price_tolerance=1e-4)
    assert summary.is_clean is True


def test_missing_required_fix_tags_raises_value_error():
    reconciler = DropCopyReconciler()
    with pytest.raises(ValueError, match="Missing required ExecutionReport FIX tags"):
        reconciler.ingest_drop_copy_raw("8=FIX.4.2|35=8|11=CLO-1|55=AAPL|")
