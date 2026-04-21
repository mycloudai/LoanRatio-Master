"""Unit tests for the principal-attribution calculator.

Scenarios A-E from PLAN.md are computed with exact expected values.
"""

from __future__ import annotations

import pytest

from app import calculator


def _empty_state():
    return {
        "config": {"dataPath": None},
        "payers": [],
        "loans": [],
        "downpayment": None,
        "months": [],
    }


def _seed_two_payers_one_loan(state):
    calculator.add_payer(state, "张三")
    calculator.add_payer(state, "李四")
    calculator.add_loan(state, "商业贷款", 1_000_000, 1_000_000)
    return state


# --------- Scenario A: with downpayment ------------------------------------

def test_scenario_a_downpayment_first_month():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    s["downpayment"] = {
        "contributions": [
            {"payerId": "p1", "amount": 600_000},
            {"payerId": "p2", "amount": 400_000},
        ],
        "CP0": {"p1": 600_000, "p2": 400_000},
    }
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 3000, "principal": 5000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 5000},
                {"payerId": "p2", "amount": 3000},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    per = s["months"][-1]["computed"]["perPayer"]
    assert per["p1"]["interestShare"] == pytest.approx(1800.0, abs=1e-6)
    assert per["p2"]["interestShare"] == pytest.approx(1200.0, abs=1e-6)
    assert per["p1"]["rawPrincipal"] == pytest.approx(3200.0, abs=1e-6)
    assert per["p2"]["rawPrincipal"] == pytest.approx(1800.0, abs=1e-6)
    assert per["p1"]["cumulativePrincipal"] == pytest.approx(603_200.0, abs=1e-6)
    assert per["p2"]["cumulativePrincipal"] == pytest.approx(401_800.0, abs=1e-6)
    assert per["p1"]["ratio"] == pytest.approx(603200 / 1005000, abs=1e-4)
    assert per["p2"]["ratio"] == pytest.approx(401800 / 1005000, abs=1e-4)


# --------- Scenario B: zero downpayment, equal split first month -----------

def test_scenario_b_zero_downpayment_equal_split():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 3000, "principal": 2000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 4000},
                {"payerId": "p2", "amount": 2000},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    per = s["months"][-1]["computed"]["perPayer"]
    # Equal 50/50 seeding -> interest 1500 each
    assert per["p1"]["interestShare"] == pytest.approx(1500.0, abs=1e-6)
    assert per["p2"]["interestShare"] == pytest.approx(1500.0, abs=1e-6)
    assert per["p1"]["rawPrincipal"] == pytest.approx(2500.0, abs=1e-6)
    assert per["p2"]["rawPrincipal"] == pytest.approx(500.0, abs=1e-6)
    assert per["p1"]["cumulativePrincipal"] == pytest.approx(2500.0, abs=1e-6)
    assert per["p2"]["cumulativePrincipal"] == pytest.approx(500.0, abs=1e-6)
    assert per["p1"]["ratio"] == pytest.approx(2500 / 3000, abs=1e-4)
    assert per["p2"]["ratio"] == pytest.approx(500 / 3000, abs=1e-4)


# --------- Scenario C: normal month ---------------------------------------

def test_scenario_c_normal_month():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    # Use downpayment to seed CPs 110k/90k directly
    s["downpayment"] = {
        "contributions": [
            {"payerId": "p1", "amount": 110_000},
            {"payerId": "p2", "amount": 90_000},
        ],
        "CP0": {"p1": 110_000, "p2": 90_000},
    }
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 3000, "principal": 3000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 4000},
                {"payerId": "p2", "amount": 2000},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    per = s["months"][-1]["computed"]["perPayer"]
    assert per["p1"]["interestShare"] == pytest.approx(1650.0, abs=1e-6)
    assert per["p2"]["interestShare"] == pytest.approx(1350.0, abs=1e-6)
    assert per["p1"]["rawPrincipal"] == pytest.approx(2350.0, abs=1e-6)
    assert per["p2"]["rawPrincipal"] == pytest.approx(650.0, abs=1e-6)
    assert per["p1"]["cumulativePrincipal"] == pytest.approx(112_350.0, abs=1e-6)
    assert per["p2"]["cumulativePrincipal"] == pytest.approx(90_650.0, abs=1e-6)
    assert per["p1"]["ratio"] == pytest.approx(112350 / 203000, abs=1e-4)
    assert per["p2"]["ratio"] == pytest.approx(90650 / 203000, abs=1e-4)


# --------- Scenario D: negative principal redistribution ------------------

def test_scenario_d_negative_principal_redistribution():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    s["downpayment"] = {
        "contributions": [
            {"payerId": "p1", "amount": 110_000},
            {"payerId": "p2", "amount": 90_000},
        ],
        "CP0": {"p1": 110_000, "p2": 90_000},
    }
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 3000, "principal": 2000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 4000},
                {"payerId": "p2", "amount": 1000},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    per = s["months"][-1]["computed"]["perPayer"]
    assert per["p1"]["rawPrincipal"] == pytest.approx(2350.0, abs=1e-6)
    assert per["p2"]["rawPrincipal"] == pytest.approx(-350.0, abs=1e-6)
    # Negative shortfall added to positive contributors (per Scenario D explanation)
    assert per["p1"]["adjPrincipal"] == pytest.approx(2700.0, abs=1e-6)
    assert per["p2"]["adjPrincipal"] == pytest.approx(0.0, abs=1e-6)
    assert per["p1"]["cumulativePrincipal"] == pytest.approx(112_700.0, abs=1e-6)
    assert per["p2"]["cumulativePrincipal"] == pytest.approx(90_000.0, abs=1e-6)


# --------- Scenario E: manual mode interrupts CP ---------------------------

def test_scenario_e_manual_mode_preserves_cp():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    s["downpayment"] = {
        "contributions": [
            {"payerId": "p1", "amount": 110_000},
            {"payerId": "p2", "amount": 90_000},
        ],
        "CP0": {"p1": 110_000, "p2": 90_000},
    }
    # Month 1 auto (normal)
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 3000, "principal": 3000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 4000},
                {"payerId": "p2", "amount": 2000},
            ],
            "manualRatios": None,
        }
    )
    # Month 2 manual 50/50
    s["months"].append(
        {
            "yearMonth": "2024-02",
            "mode": "manual",
            "loanDetails": [{"loanId": "l1", "interest": 3000, "principal": 0}],
            "payerPayments": [
                {"payerId": "p1", "amount": 4000},
                {"payerId": "p2", "amount": 2000},
            ],
            "manualRatios": {"p1": 0.5, "p2": 0.5},
        }
    )
    calculator.recompute_all(s)
    m1 = s["months"][0]["computed"]["perPayer"]
    m2 = s["months"][1]["computed"]["perPayer"]
    # Manual month CP unchanged vs previous
    assert m2["p1"]["cumulativePrincipal"] == pytest.approx(m1["p1"]["cumulativePrincipal"], abs=1e-6)
    assert m2["p2"]["cumulativePrincipal"] == pytest.approx(m1["p2"]["cumulativePrincipal"], abs=1e-6)
    assert m2["p1"]["ratio"] == pytest.approx(0.5, abs=1e-4)
    assert m2["p2"]["ratio"] == pytest.approx(0.5, abs=1e-4)
    # Add a third auto month; basis should be 50/50 from manual month
    s["months"].append(
        {
            "yearMonth": "2024-03",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 3000, "principal": 3000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 3000},
                {"payerId": "p2", "amount": 3000},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    m3 = s["months"][2]["computed"]["perPayer"]
    # interest shares based on 50/50
    assert m3["p1"]["interestShare"] == pytest.approx(1500.0, abs=1e-6)
    assert m3["p2"]["interestShare"] == pytest.approx(1500.0, abs=1e-6)


# --------- Cascading recompute when editing history ------------------------

def test_cascading_recompute_on_history_edit():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    s["downpayment"] = {
        "contributions": [
            {"payerId": "p1", "amount": 100_000},
            {"payerId": "p2", "amount": 100_000},
        ],
        "CP0": {"p1": 100_000, "p2": 100_000},
    }
    for ym, p1_pay, p2_pay in [
        ("2024-01", 3000, 3000),
        ("2024-02", 3000, 3000),
        ("2024-03", 3000, 3000),
    ]:
        s["months"].append(
            {
                "yearMonth": ym,
                "mode": "auto",
                "loanDetails": [{"loanId": "l1", "interest": 2000, "principal": 4000}],
                "payerPayments": [
                    {"payerId": "p1", "amount": p1_pay},
                    {"payerId": "p2", "amount": p2_pay},
                ],
                "manualRatios": None,
            }
        )
    calculator.recompute_all(s)
    before = [m["computed"]["perPayer"]["p1"]["ratio"] for m in s["months"]]
    # Edit first month: p1 pays much more
    s["months"][0]["payerPayments"][0]["amount"] = 10000
    calculator.recompute_all(s)
    after = [m["computed"]["perPayer"]["p1"]["ratio"] for m in s["months"]]
    # All subsequent ratios must change
    assert after[0] > before[0]
    assert after[1] != before[1]
    assert after[2] != before[2]


# --------- Payer delete with merge strategy --------------------------------

def test_payer_delete_merge_preserves_total_cp():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    calculator.add_payer(s, "王五")  # p3
    s["downpayment"] = {
        "contributions": [
            {"payerId": "p1", "amount": 100_000},
            {"payerId": "p2", "amount": 100_000},
            {"payerId": "p3", "amount": 100_000},
        ],
        "CP0": {"p1": 100_000, "p2": 100_000, "p3": 100_000},
    }
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 3000, "principal": 6000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 4000},
                {"payerId": "p2", "amount": 3000},
                {"payerId": "p3", "amount": 2000},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    total_cp_before = sum(
        s["months"][-1]["computed"]["perPayer"][pid]["cumulativePrincipal"]
        for pid in ["p1", "p2", "p3"]
    )
    calculator.delete_payer(s, "p3", strategy="merge")
    assert len(s["payers"]) == 2
    total_cp_after = sum(
        s["months"][-1]["computed"]["perPayer"][pid]["cumulativePrincipal"]
        for pid in ["p1", "p2"]
    )
    # Sum should be preserved (within rounding)
    assert total_cp_after == pytest.approx(total_cp_before, rel=1e-3)


def test_payer_delete_strategy_removes_records():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 3000, "principal": 3000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 4000},
                {"payerId": "p2", "amount": 2000},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    calculator.delete_payer(s, "p2", strategy="delete")
    assert len(s["payers"]) == 1
    assert all(
        pp["payerId"] != "p2" for m in s["months"] for pp in m.get("payerPayments", [])
    )
    # Only p1 should remain and have ratio 1.0
    assert s["months"][-1]["computed"]["perPayer"]["p1"]["ratio"] == pytest.approx(1.0, abs=1e-4)


# --------- startMonth honored ----------------------------------------------

def test_payer_start_month_excludes_early_months():
    s = _empty_state()
    calculator.add_payer(s, "张三")
    calculator.add_payer(s, "李四", start_month="2024-02")
    calculator.add_loan(s, "贷款", 100_000, 100_000)
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 1000, "principal": 2000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 3000},
                {"payerId": "p2", "amount": 5000},  # should be ignored: not active yet
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    per = s["months"][-1]["computed"]["perPayer"]
    # p2 is inactive in 2024-01
    assert per["p2"]["adjPrincipal"] == pytest.approx(0.0, abs=1e-6)
    assert per["p2"]["cumulativePrincipal"] == pytest.approx(0.0, abs=1e-6)
    assert per["p1"]["ratio"] == pytest.approx(1.0, abs=1e-4)


# --------- Downpayment seeding basics --------------------------------------

def test_downpayment_seeds_initial_ratio():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    s["downpayment"] = {
        "contributions": [
            {"payerId": "p1", "amount": 700_000},
            {"payerId": "p2", "amount": 300_000},
        ],
        "CP0": {"p1": 700_000, "p2": 300_000},
    }
    # Single month with zero payments to verify ratio stays 70/30
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 0, "principal": 0}],
            "payerPayments": [
                {"payerId": "p1", "amount": 0},
                {"payerId": "p2", "amount": 0},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    per = s["months"][-1]["computed"]["perPayer"]
    assert per["p1"]["ratio"] == pytest.approx(0.7, abs=1e-4)
    assert per["p2"]["ratio"] == pytest.approx(0.3, abs=1e-4)


# --------- Manual mode with multiple subsequent months --------------------

def test_manual_month_basis_carries_to_next_auto():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    s["downpayment"] = {
        "contributions": [
            {"payerId": "p1", "amount": 100_000},
            {"payerId": "p2", "amount": 100_000},
        ],
        "CP0": {"p1": 100_000, "p2": 100_000},
    }
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "manual",
            "loanDetails": [{"loanId": "l1", "interest": 2000, "principal": 0}],
            "payerPayments": [
                {"payerId": "p1", "amount": 1000},
                {"payerId": "p2", "amount": 1000},
            ],
            "manualRatios": {"p1": 0.7, "p2": 0.3},
        }
    )
    s["months"].append(
        {
            "yearMonth": "2024-02",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 1000, "principal": 2000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 1000},
                {"payerId": "p2", "amount": 1000},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    m2 = s["months"][1]["computed"]["perPayer"]
    # interest share for month 2 uses manual 70/30 basis
    assert m2["p1"]["interestShare"] == pytest.approx(700.0, abs=1e-6)
    assert m2["p2"]["interestShare"] == pytest.approx(300.0, abs=1e-6)


# --------- Edge: three-way redistribution ----------------------------------

def test_three_payer_negative_redistribution_weighted():
    s = _empty_state()
    calculator.add_payer(s, "A")
    calculator.add_payer(s, "B")
    calculator.add_payer(s, "C")
    calculator.add_loan(s, "L", 100_000, 100_000)
    s["downpayment"] = {
        "contributions": [
            {"payerId": "p1", "amount": 600_000},  # 60%
            {"payerId": "p2", "amount": 300_000},  # 30%
            {"payerId": "p3", "amount": 100_000},  # 10%
        ],
        "CP0": {"p1": 600_000, "p2": 300_000, "p3": 100_000},
    }
    # Month 1: interest 1000. p1,p2 positive; p3 negative.
    # shares: 600, 300, 100
    # raw: 1000-600=+400, 500-300=+200, 0-100=-100 -> neg_total=100
    # denom = 0.6+0.3 = 0.9
    # adj p1 = 400 + (0.6/0.9)*100 = 400 + 66.667 = 466.667
    # adj p2 = 200 + (0.3/0.9)*100 = 200 + 33.333 = 233.333
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 1000, "principal": 600}],
            "payerPayments": [
                {"payerId": "p1", "amount": 1000},
                {"payerId": "p2", "amount": 500},
                {"payerId": "p3", "amount": 0},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    per = s["months"][-1]["computed"]["perPayer"]
    # Displayed values are rounded to 2 dp
    assert per["p1"]["adjPrincipal"] == pytest.approx(466.67, abs=1e-2)
    assert per["p2"]["adjPrincipal"] == pytest.approx(233.33, abs=1e-2)
    assert per["p3"]["adjPrincipal"] == pytest.approx(0.0, abs=1e-6)


# --------- Loan remaining principal auto-updated ---------------------------

def test_loan_remaining_principal_decreases():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 3000, "principal": 5000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 5000},
                {"payerId": "p2", "amount": 3000},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    assert s["loans"][0]["remainingPrincipal"] == pytest.approx(995_000.0, abs=1e-2)


# --------- delete_loan coverage ------------------------------------------


def test_delete_loan_merge_combines_records():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    calculator.add_loan(s, "L2", 50_000, 50_000)
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [
                {"loanId": "l1", "interest": 2000, "principal": 1000},
                {"loanId": "l2", "interest": 500, "principal": 500},
            ],
            "payerPayments": [
                {"payerId": "p1", "amount": 3000},
                {"payerId": "p2", "amount": 1000},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    calculator.delete_loan(s, "l2", strategy="merge", target_id="l1")
    assert len(s["loans"]) == 1
    details = s["months"][0]["loanDetails"]
    assert len(details) == 1
    assert details[0]["loanId"] == "l1"
    assert details[0]["interest"] == pytest.approx(2500, abs=1e-6)
    assert details[0]["principal"] == pytest.approx(1500, abs=1e-6)


def test_delete_loan_delete_strategy():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    calculator.add_loan(s, "L2", 50_000, 50_000)
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [
                {"loanId": "l1", "interest": 2000, "principal": 1000},
                {"loanId": "l2", "interest": 500, "principal": 500},
            ],
            "payerPayments": [
                {"payerId": "p1", "amount": 2500},
                {"payerId": "p2", "amount": 1000},
            ],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    calculator.delete_loan(s, "l2", strategy="delete")
    details = s["months"][0]["loanDetails"]
    assert all(ld["loanId"] != "l2" for ld in details)


def test_delete_loan_validation():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    with pytest.raises(ValueError):
        calculator.delete_loan(s, "lX", strategy="delete")
    with pytest.raises(ValueError):
        calculator.delete_loan(s, "l1", strategy="bogus")
    with pytest.raises(ValueError):
        calculator.delete_loan(s, "l1", strategy="merge", target_id=None)
    with pytest.raises(ValueError):
        calculator.delete_loan(s, "l1", strategy="merge", target_id="lX")


def test_delete_payer_validation():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    with pytest.raises(ValueError):
        calculator.delete_payer(s, "pX", strategy="delete")
    with pytest.raises(ValueError):
        calculator.delete_payer(s, "p1", strategy="bogus")


def test_payer_delete_merge_with_downpayment_and_manual():
    s = _empty_state()
    _seed_two_payers_one_loan(s)
    calculator.add_payer(s, "王五")
    s["downpayment"] = {
        "contributions": [
            {"payerId": "p1", "amount": 100_000},
            {"payerId": "p2", "amount": 100_000},
            {"payerId": "p3", "amount": 100_000},
        ],
        "CP0": {"p1": 100_000, "p2": 100_000, "p3": 100_000},
    }
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "manual",
            "loanDetails": [{"loanId": "l1", "interest": 300, "principal": 0}],
            "payerPayments": [
                {"payerId": "p1", "amount": 100},
                {"payerId": "p2", "amount": 100},
                {"payerId": "p3", "amount": 100},
            ],
            "manualRatios": {"p1": 1 / 3, "p2": 1 / 3, "p3": 1 / 3},
        }
    )
    calculator.recompute_all(s)
    calculator.delete_payer(s, "p3", strategy="merge")
    # downpayment contributions redistributed
    contribs = s["downpayment"]["contributions"]
    assert all(c["payerId"] != "p3" for c in contribs)
    # manual ratios redistributed
    mr = s["months"][0]["manualRatios"]
    assert "p3" not in mr
    assert abs(sum(mr.values()) - 1.0) < 1e-6


def test_fallback_redistribution_when_all_prev_ratio_zero():
    # New payer entering during 0-ratio world shouldn't crash.
    s = _empty_state()
    calculator.add_payer(s, "A")
    calculator.add_payer(s, "B", start_month="2024-02")
    calculator.add_loan(s, "L", 100_000, 100_000)
    # Month 1: only p1 active, makes negative raw (pays less than interest)
    s["months"].append(
        {
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 2000, "principal": 0}],
            "payerPayments": [{"payerId": "p1", "amount": 500}],
            "manualRatios": None,
        }
    )
    calculator.recompute_all(s)
    # Only one active, raw is -1500, no S+ at all -> adj=raw (no redistribution possible)
    # Just ensure no exception and output is sensible.
    assert "perPayer" in s["months"][-1]["computed"]
