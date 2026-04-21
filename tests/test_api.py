"""Flask API tests using the test client."""

from __future__ import annotations

import os

import pytest

from app import main as app_main


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_file = tmp_path / "data.json"
    monkeypatch.setenv("LOANRATIO_ALLOW_RESET", "1")
    app = app_main.create_app(test_data_path=data_file)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_about(client):
    r = client.get("/api/about")
    assert r.status_code == 200
    data = r.get_json()
    assert data["version"]
    assert data["repoUrl"].startswith("https://github.com/mycloudai/")
    assert "Changelog" in data["changelogMarkdown"] or "更新" in data["changelogMarkdown"]
    assert "userguideMarkdown" in data
    assert "使用指南" in data["userguideMarkdown"]


def test_reset_gated(tmp_path, monkeypatch):
    data_file = tmp_path / "data.json"
    monkeypatch.delenv("LOANRATIO_ALLOW_RESET", raising=False)
    app = app_main.create_app(test_data_path=data_file)
    with app.test_client() as c:
        r = c.post("/api/state/reset")
        assert r.status_code == 403


def test_payer_and_loan_crud(client):
    r = client.post("/api/payers", json={"name": "张三"})
    assert r.status_code == 201
    assert r.get_json()["id"] == "p1"
    r = client.post("/api/payers", json={"name": "李四"})
    assert r.get_json()["id"] == "p2"
    r = client.post("/api/payers", json={"name": ""})
    assert r.status_code == 400
    r = client.post("/api/payers", json={"name": "王五", "startMonth": "bad"})
    assert r.status_code == 400

    r = client.post(
        "/api/loans", json={"name": "贷款", "originalAmount": 100_000, "remainingPrincipal": 100_000}
    )
    assert r.status_code == 201
    assert r.get_json()["id"] == "l1"


def test_month_creation_happy_path(client):
    client.post("/api/payers", json={"name": "张三"})
    client.post("/api/payers", json={"name": "李四"})
    client.post(
        "/api/loans", json={"name": "L", "originalAmount": 100_000, "remainingPrincipal": 100_000}
    )
    r = client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 3000, "principal": 2000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 4000},
                {"payerId": "p2", "amount": 2000},
            ],
        },
    )
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["computed"]["perPayer"]["p1"]["rawPrincipal"] == pytest.approx(2500.0, abs=1e-6)


def test_month_consecutive_required(client):
    client.post("/api/payers", json={"name": "张三"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 10, "remainingPrincipal": 10})
    client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 10, "principal": 10}],
            "payerPayments": [{"payerId": "p1", "amount": 20}],
        },
    )
    r = client.post(
        "/api/months",
        json={
            "yearMonth": "2024-03",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 10, "principal": 10}],
            "payerPayments": [{"payerId": "p1", "amount": 20}],
        },
    )
    assert r.status_code == 400
    assert "2024-02" in r.get_json()["error"]


def test_manual_ratios_must_sum_to_one(client):
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/payers", json={"name": "B"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 10, "remainingPrincipal": 10})
    r = client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "manual",
            "loanDetails": [{"loanId": "l1", "interest": 100, "principal": 0}],
            "payerPayments": [
                {"payerId": "p1", "amount": 100},
                {"payerId": "p2", "amount": 0},
            ],
            "manualRatios": {"p1": 0.7, "p2": 0.2},
        },
    )
    assert r.status_code == 400


def test_downpayment_before_months(client):
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/payers", json={"name": "B"})
    r = client.post(
        "/api/months/downpayment",
        json={
            "contributions": [
                {"payerId": "p1", "amount": 100},
                {"payerId": "p2", "amount": 100},
            ]
        },
    )
    assert r.status_code == 201


def test_delete_only_most_recent(client):
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 10, "remainingPrincipal": 10})
    for ym in ["2024-01", "2024-02"]:
        client.post(
            "/api/months",
            json={
                "yearMonth": ym,
                "mode": "auto",
                "loanDetails": [{"loanId": "l1", "interest": 10, "principal": 10}],
                "payerPayments": [{"payerId": "p1", "amount": 20}],
            },
        )
    r = client.delete("/api/months/2024-01")
    assert r.status_code == 400
    r = client.delete("/api/months/2024-02")
    assert r.status_code == 200


def test_state_reset_and_load(client):
    client.post("/api/payers", json={"name": "A"})
    r = client.post("/api/state/reset")
    assert r.status_code == 200
    s = client.get("/api/state").get_json()
    assert s["payers"] == []

    seed = {
        "payers": [{"id": "p1", "name": "X", "startMonth": None}],
        "loans": [{"id": "l1", "name": "L", "originalAmount": 1000, "remainingPrincipal": 1000}],
        "months": [],
    }
    r = client.post("/api/state/load", json={"state": seed})
    assert r.status_code == 200
    s = client.get("/api/state").get_json()
    assert len(s["payers"]) == 1


def test_summary_endpoint(client):
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/payers", json={"name": "B"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 100, "remainingPrincipal": 100})
    client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 10, "principal": 20}],
            "payerPayments": [
                {"payerId": "p1", "amount": 20},
                {"payerId": "p2", "amount": 10},
            ],
        },
    )
    r = client.get("/api/summary")
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["payers"]) == 2
    assert len(body["months"]) == 1


def test_export_excel(client):
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 100, "remainingPrincipal": 100})
    client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 10, "principal": 10}],
            "payerPayments": [{"payerId": "p1", "amount": 20}],
        },
    )
    r = client.get("/api/export/excel")
    assert r.status_code == 200
    assert r.headers["Content-Disposition"].startswith("attachment")
    assert r.data[:2] == b"PK"  # xlsx zip magic


def test_unknown_loan_id_rejected(client):
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 10, "remainingPrincipal": 10})
    r = client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "lX", "interest": 10, "principal": 10}],
            "payerPayments": [{"payerId": "p1", "amount": 20}],
        },
    )
    assert r.status_code == 400


def test_month_detail_endpoint(client):
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 100, "remainingPrincipal": 100})
    client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 10, "principal": 10}],
            "payerPayments": [{"payerId": "p1", "amount": 20}],
        },
    )
    r = client.get("/api/months/2024-01/detail")
    assert r.status_code == 200
    body = r.get_json()
    assert "perPayer" in body
    assert "totalInterest" in body
    assert "steps" in body
    assert "redistribution" in body
    assert "formulas" in body
    assert body["perPayer"]["p1"]["payment"] == 20.0


def test_month_detail_negative_redistribution(client):
    """Detail endpoint shows redistribution steps when a payer has negative raw principal."""
    client.post("/api/payers", json={"name": "Alice"})
    client.post("/api/payers", json={"name": "Bob"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 100000})
    # Month 1: establish ratios
    client.post(
        "/api/months",
        json={
            "yearMonth": "2025-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 1000, "principal": 2000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 2000},
                {"payerId": "p2", "amount": 1000},
            ],
        },
    )
    # Month 2: Bob pays less than interest share -> negative raw principal
    client.post(
        "/api/months",
        json={
            "yearMonth": "2025-02",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 2000, "principal": 1000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 2800},
                {"payerId": "p2", "amount": 200},
            ],
        },
    )
    r = client.get("/api/months/2025-02/detail")
    assert r.status_code == 200
    body = r.get_json()
    # Bob's raw principal should be negative, adj should be 0
    bob = body["perPayer"]["p2"]
    assert bob["rawPrincipal"] < 0
    assert bob["adjPrincipal"] == 0.0
    # Redistribution should be non-empty and mention Bob
    assert len(body["redistribution"]) > 0
    assert any("Bob" in s for s in body["redistribution"])
    # Steps should mention negative redistribution
    assert any("负本金" in s for s in body["steps"])
    # Alice should have adj > raw (absorbed the deficit)
    alice = body["perPayer"]["p1"]
    assert alice["adjPrincipal"] > alice["rawPrincipal"]


def test_month_detail_all_negative_redistribution(client):
    """When all payers have negative raw principal, redistribution explains all zeroed out."""
    client.post("/api/payers", json={"name": "Alice"})
    client.post("/api/payers", json={"name": "Bob"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 100000})
    # Month 1: seed ratios
    client.post(
        "/api/months",
        json={
            "yearMonth": "2025-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 500, "principal": 1000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 1000},
                {"payerId": "p2", "amount": 500},
            ],
        },
    )
    # Month 2: both pay less than their interest share
    client.post(
        "/api/months",
        json={
            "yearMonth": "2025-02",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 5000, "principal": 100}],
            "payerPayments": [
                {"payerId": "p1", "amount": 100},
                {"payerId": "p2", "amount": 50},
            ],
        },
    )
    r = client.get("/api/months/2025-02/detail")
    assert r.status_code == 200
    body = r.get_json()
    # Both should have negative raw and zero adj
    assert body["perPayer"]["p1"]["rawPrincipal"] < 0
    assert body["perPayer"]["p2"]["rawPrincipal"] < 0
    assert body["perPayer"]["p1"]["adjPrincipal"] == 0.0
    assert body["perPayer"]["p2"]["adjPrincipal"] == 0.0
    # Redistribution should explain all zeroed out, not mention "正本金参还人"
    assert any("全部归零" in s for s in body["redistribution"])
    assert not any("正本金参还人" in s for s in body["redistribution"])


def test_patch_month_recomputes(client):
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/payers", json={"name": "B"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 1000, "remainingPrincipal": 1000})
    client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 100, "principal": 100}],
            "payerPayments": [
                {"payerId": "p1", "amount": 150},
                {"payerId": "p2", "amount": 150},
            ],
        },
    )
    before = client.get("/api/summary").get_json()["payers"]
    r = client.patch(
        "/api/months/2024-01",
        json={
            "payerPayments": [
                {"payerId": "p1", "amount": 500},
                {"payerId": "p2", "amount": 100},
            ]
        },
    )
    assert r.status_code == 200
    after = client.get("/api/summary").get_json()["payers"]
    # p1's ratio should increase
    p1_before = next(p for p in before if p["id"] == "p1")["currentRatio"]
    p1_after = next(p for p in after if p["id"] == "p1")["currentRatio"]
    assert p1_after > p1_before


def test_root_serves_placeholder_when_no_frontend(client):
    r = client.get("/")
    assert r.status_code == 200
    # Either real index.html (if generated) or placeholder
    assert b"LoanRatio" in r.data or b"html" in r.data.lower()


def test_forecast_endpoint(client):
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 1000, "remainingPrincipal": 900})
    client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 10, "principal": 100}],
            "payerPayments": [{"payerId": "p1", "amount": 110}],
        },
    )
    r = client.post("/api/forecast", json={"windowMonths": 1, "horizonMonths": 3})
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["projection"]) == 3


def test_forecast_selected_months(client):
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/payers", json={"name": "B"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 10000, "remainingPrincipal": 10000})
    for ym, a1, a2 in [("2024-01", 200, 100), ("2024-02", 500, 300), ("2024-03", 200, 100)]:
        client.post(
            "/api/months",
            json={
                "yearMonth": ym,
                "mode": "auto",
                "loanDetails": [{"loanId": "l1", "interest": 50, "principal": 50}],
                "payerPayments": [
                    {"payerId": "p1", "amount": a1},
                    {"payerId": "p2", "amount": a2},
                ],
            },
        )
    # Use only month 2024-02 (higher payments) as basis
    r = client.post(
        "/api/forecast",
        json={"selectedMonths": ["2024-02"], "horizonMonths": 3},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["projection"]) == 3
    # All history (lower average) should give different ratios
    r2 = client.post(
        "/api/forecast", json={"windowMonths": 0, "horizonMonths": 3}
    )
    body2 = r2.get_json()
    # The selected-month forecast should differ from all-history forecast
    assert body["projection"] != body2["projection"]

    # Invalid selectedMonths returns error
    r3 = client.post(
        "/api/forecast",
        json={"selectedMonths": ["9999-01"], "horizonMonths": 3},
    )
    assert r3.status_code == 400


def test_forecast_selected_months_multiple(client):
    """Selecting multiple months averages their payment patterns."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 10000, "remainingPrincipal": 10000})
    for ym, amt in [("2024-01", 100), ("2024-02", 200), ("2024-03", 300)]:
        client.post(
            "/api/months",
            json={
                "yearMonth": ym,
                "mode": "auto",
                "loanDetails": [{"loanId": "l1", "interest": 20, "principal": 30}],
                "payerPayments": [{"payerId": "p1", "amount": amt}],
            },
        )
    # Select months 1 and 3 (avg payment = 200)
    r = client.post(
        "/api/forecast",
        json={"selectedMonths": ["2024-01", "2024-03"], "horizonMonths": 2},
    )
    assert r.status_code == 200
    assert len(r.get_json()["projection"]) == 2

    # Select only month 2 (payment = 200) — same average, should give same result
    r2 = client.post(
        "/api/forecast",
        json={"selectedMonths": ["2024-02"], "horizonMonths": 2},
    )
    assert r2.status_code == 200
    proj1 = r.get_json()["projection"]
    proj2 = r2.get_json()["projection"]
    # Ratios should be identical since average payment is the same
    for a, b in zip(proj1, proj2, strict=True):
        assert a["ratios"]["p1"] == pytest.approx(b["ratios"]["p1"], abs=1e-4)


def test_forecast_selected_months_bad_type(client):
    """selectedMonths must be an array."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 100, "remainingPrincipal": 100})
    client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 5, "principal": 5}],
            "payerPayments": [{"payerId": "p1", "amount": 10}],
        },
    )
    r = client.post("/api/forecast", json={"selectedMonths": "2024-01", "horizonMonths": 3})
    assert r.status_code == 400


def test_forecast_selected_months_empty_array(client):
    """selectedMonths=[] should return 400, not silently fall back to window."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 100, "remainingPrincipal": 100})
    client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 5, "principal": 5}],
            "payerPayments": [{"payerId": "p1", "amount": 10}],
        },
    )
    r = client.post("/api/forecast", json={"selectedMonths": [], "horizonMonths": 3})
    assert r.status_code == 400


def test_forecast_all_history_window_zero(client):
    """windowMonths=0 uses all historical months."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 5000, "remainingPrincipal": 5000})
    for ym in ["2024-01", "2024-02", "2024-03"]:
        client.post(
            "/api/months",
            json={
                "yearMonth": ym,
                "mode": "auto",
                "loanDetails": [{"loanId": "l1", "interest": 10, "principal": 50}],
                "payerPayments": [{"payerId": "p1", "amount": 60}],
            },
        )
    r = client.post("/api/forecast", json={"windowMonths": 0, "horizonMonths": 6})
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["projection"]) == 6
    assert body["payoffMonth"] is not None


def test_loan_remaining_defaults_to_original(client):
    """When remainingPrincipal is omitted, it defaults to originalAmount."""
    r = client.post("/api/loans", json={"name": "房贷", "originalAmount": 500000})
    assert r.status_code == 201
    loan = r.get_json()
    assert loan["remainingPrincipal"] == pytest.approx(500000.0)

    # Verify the state also reflects the default
    s = client.get("/api/state").get_json()
    ln = next(ln for ln in s["loans"] if ln["id"] == loan["id"])
    assert ln["remainingPrincipal"] == pytest.approx(500000.0)


def test_loan_remaining_explicit_overrides_default(client):
    """Explicit remainingPrincipal is respected."""
    r = client.post(
        "/api/loans",
        json={"name": "二手房贷", "originalAmount": 800000, "remainingPrincipal": 600000},
    )
    assert r.status_code == 201
    assert r.get_json()["remainingPrincipal"] == pytest.approx(600000.0)


def test_manual_mode_with_loan_details_and_payments(client):
    """Manual mode accepts loanDetails and payerPayments alongside manualRatios."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/payers", json={"name": "B"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 100000, "remainingPrincipal": 100000})
    r = client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "manual",
            "loanDetails": [{"loanId": "l1", "interest": 500, "principal": 1000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 1000},
                {"payerId": "p2", "amount": 500},
            ],
            "manualRatios": {"p1": 0.6, "p2": 0.4},
        },
    )
    assert r.status_code == 201
    body = r.get_json()
    computed = body["computed"]["perPayer"]
    # Equity ratio = CP_i/total_CP. Since starting from zero CP:
    # CP: p1=600, p2=400, total=1000 → ratio equals manual ratio by coincidence.
    assert computed["p1"]["ratio"] == pytest.approx(0.6, abs=1e-4)
    assert computed["p2"]["ratio"] == pytest.approx(0.4, abs=1e-4)
    # Interest share computed by manual ratio
    assert computed["p1"]["interestShare"] == pytest.approx(300.0, abs=1e-2)
    assert computed["p2"]["interestShare"] == pytest.approx(200.0, abs=1e-2)
    # adjPrincipal = manual_ratio * actual_principal; actual = (1000+500)-500 = 1000
    assert computed["p1"]["adjPrincipal"] == pytest.approx(600.0, abs=1e-2)
    assert computed["p2"]["adjPrincipal"] == pytest.approx(400.0, abs=1e-2)
    # CP updated: 0 + adjPrincipal
    assert computed["p1"]["cumulativePrincipal"] == pytest.approx(600.0, abs=1e-2)
    assert computed["p2"]["cumulativePrincipal"] == pytest.approx(400.0, abs=1e-2)
    # Loan remaining principal updated by loanDetails
    s = client.get("/api/state").get_json()
    ln = next(ln for ln in s["loans"] if ln["id"] == "l1")
    assert ln["remainingPrincipal"] == pytest.approx(99000.0, abs=1e-2)


def test_summary_snapshot_data_at_specific_month(client):
    """State contains enough computed data to reconstruct any historical time-point."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/payers", json={"name": "B"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 10000, "remainingPrincipal": 10000})
    # Create 3 months with varying payments
    for ym, a1, a2, prin in [
        ("2024-01", 300, 200, 100),
        ("2024-02", 400, 100, 100),
        ("2024-03", 200, 300, 100),
    ]:
        client.post(
            "/api/months",
            json={
                "yearMonth": ym,
                "mode": "auto",
                "loanDetails": [{"loanId": "l1", "interest": 50, "principal": prin}],
                "payerPayments": [
                    {"payerId": "p1", "amount": a1},
                    {"payerId": "p2", "amount": a2},
                ],
            },
        )
    s = client.get("/api/state").get_json()
    months = s["months"]
    assert len(months) == 3
    # Each month has computed data that serves as a snapshot
    for _i, m in enumerate(months):
        assert "computed" in m
        per = m["computed"]["perPayer"]
        assert "p1" in per and "p2" in per
        assert "ratio" in per["p1"]
        assert "cumulativePrincipal" in per["p1"]
        # Ratios must sum to 1
        total_ratio = per["p1"]["ratio"] + per["p2"]["ratio"]
        assert total_ratio == pytest.approx(1.0, abs=1e-3)

    # Month 1 snapshot: p1 paid more, should have higher ratio
    m1 = months[0]["computed"]["perPayer"]
    assert m1["p1"]["ratio"] > m1["p2"]["ratio"]
    # Month 2 snapshot: p1 paid even more, ratio should increase further
    m2 = months[1]["computed"]["perPayer"]
    assert m2["p1"]["ratio"] > m1["p1"]["ratio"]
    # Month 3 snapshot: p2 paid more, p1 ratio should decrease
    m3 = months[2]["computed"]["perPayer"]
    assert m3["p1"]["ratio"] < m2["p1"]["ratio"]

    # Loan remaining at each snapshot can be computed from cumulative principal
    total_principal_paid = sum(
        ld["principal"]
        for m in months
        for ld in m.get("loanDetails", [])
    )
    final_remaining = s["loans"][0]["remainingPrincipal"]
    assert final_remaining == pytest.approx(10000 - total_principal_paid, abs=1e-2)


def test_storage_roundtrip(tmp_path):
    from app import storage

    p = tmp_path / "d.json"
    s = storage.empty_state(str(p))
    s["payers"].append({"id": "p1", "name": "A", "startMonth": None})
    storage.save_state(p, s)
    loaded = storage.load_state(p)
    assert loaded["payers"][0]["name"] == "A"


def test_config_endpoint(client, tmp_path, monkeypatch):
    # Use an isolated config path to avoid clobbering the user's real one
    from app import storage
    fake_cfg = tmp_path / ".loanratio_config.json"
    monkeypatch.setattr(storage, "CONFIG_PATH", fake_cfg)
    dp = str(tmp_path / "data2.json")
    r = client.post("/api/config", json={"dataPath": dp})
    assert r.status_code == 200
    assert r.get_json()["initialized"] is True
    # Clean up any config written before test tmp_path fixture tears down
    if fake_cfg.exists():
        os.unlink(fake_cfg)


def test_forecast_no_months_returns_error(client):
    """Forecast with no month data should return 400."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 10000})
    r = client.post("/api/forecast", json={"windowMonths": 0, "horizonMonths": 6})
    assert r.status_code == 400


def test_forecast_returns_loan_forecasts_and_series(client):
    """Forecast should include loanForecasts, series, and months."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/payers", json={"name": "B"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 10000, "remainingPrincipal": 10000})
    for ym, a1, a2 in [("2024-01", 200, 100), ("2024-02", 200, 100)]:
        client.post(
            "/api/months",
            json={
                "yearMonth": ym,
                "mode": "auto",
                "loanDetails": [{"loanId": "l1", "interest": 50, "principal": 100}],
                "payerPayments": [
                    {"payerId": "p1", "amount": a1},
                    {"payerId": "p2", "amount": a2},
                ],
            },
        )
    r = client.post("/api/forecast", json={"windowMonths": 0, "horizonMonths": 6})
    assert r.status_code == 200
    body = r.get_json()
    # New: loanForecasts replaces estimatedRemainingInterest
    assert "loanForecasts" in body
    lfs = body["loanForecasts"]
    assert len(lfs) == 1
    assert lfs[0]["loanId"] == "l1"
    assert lfs[0]["totalFutureInterest"] > 0
    assert lfs[0]["monthsToPayoff"] > 0
    assert lfs[0]["payoffMonth"] is not None
    assert "series" in body
    assert "months" in body
    assert len(body["months"]) == 6
    assert "p1" in body["series"]
    assert len(body["series"]["p1"]) == 6


def test_forecast_multi_loan_one_missing_data(client):
    """When one loan has no loanDetails in historical months, noData=True."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/loans", json={"name": "商贷", "originalAmount": 500000, "remainingPrincipal": 400000})
    client.post("/api/loans", json={"name": "公积金", "originalAmount": 800000, "remainingPrincipal": 800000})
    # Only provide loanDetails for l1 (商贷), not l2 (公积金)
    client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [
                {"loanId": "l1", "interest": 3500, "principal": 5000},
                {"loanId": "l2", "interest": 0, "principal": 0},
            ],
            "payerPayments": [{"payerId": "p1", "amount": 8500}],
        },
    )
    r = client.post("/api/forecast", json={"windowMonths": 0, "horizonMonths": 6})
    assert r.status_code == 200
    body = r.get_json()
    lfs = body["loanForecasts"]
    assert len(lfs) == 2
    # l1 should have valid forecast
    lf1 = next(lf for lf in lfs if lf["loanId"] == "l1")
    assert lf1["noData"] is False
    assert lf1["monthsToPayoff"] > 0
    assert lf1["totalFutureInterest"] > 0
    # l2 should be noData because avg_principal=0
    lf2 = next(lf for lf in lfs if lf["loanId"] == "l2")
    assert lf2["noData"] is True
    assert lf2["monthsToPayoff"] is None
    assert lf2["totalFutureInterest"] is None
    assert lf2["remainingPrincipal"] == 800000.0
    # Overall payoff should be None when any loan is unpayable
    assert body["payoffMonth"] is None


def test_forecast_multi_loan_both_payable(client):
    """When both loans have data, overall payoff is the later one."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/loans", json={"name": "商贷", "originalAmount": 10000, "remainingPrincipal": 10000})
    client.post("/api/loans", json={"name": "公积金", "originalAmount": 20000, "remainingPrincipal": 20000})
    client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [
                {"loanId": "l1", "interest": 50, "principal": 100},
                {"loanId": "l2", "interest": 80, "principal": 200},
            ],
            "payerPayments": [{"payerId": "p1", "amount": 430}],
        },
    )
    r = client.post("/api/forecast", json={"windowMonths": 0, "horizonMonths": 6})
    assert r.status_code == 200
    body = r.get_json()
    lfs = body["loanForecasts"]
    assert len(lfs) == 2
    lf1 = next(lf for lf in lfs if lf["loanId"] == "l1")
    lf2 = next(lf for lf in lfs if lf["loanId"] == "l2")
    assert lf1["noData"] is False
    assert lf2["noData"] is False
    # l1: remaining after 1 month = 10000-100=9900, 9900/100 = 99 months
    # l2: remaining after 1 month = 20000-200=19800, 19800/200 = 99 months
    assert lf1["monthsToPayoff"] == 99
    assert lf2["monthsToPayoff"] == 99
    # Both have interest forecasts
    assert lf1["totalFutureInterest"] == pytest.approx(50 * 99, abs=1)
    assert lf2["totalFutureInterest"] == pytest.approx(80 * 99, abs=1)
    # Overall payoff should be the later month
    assert body["payoffMonth"] is not None


def test_forecast_excludes_downpayment_from_averaging(client):
    """Downpayment month (0000-00) must not be included in average calculations."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/payers", json={"name": "B"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 10000, "remainingPrincipal": 10000})
    client.post(
        "/api/months/downpayment",
        json={"contributions": [{"payerId": "p1", "amount": 5000}, {"payerId": "p2", "amount": 5000}]},
    )
    # Only one regular month
    client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "auto",
            "loanDetails": [{"loanId": "l1", "interest": 50, "principal": 100}],
            "payerPayments": [{"payerId": "p1", "amount": 100}, {"payerId": "p2", "amount": 50}],
        },
    )
    r = client.post("/api/forecast", json={"windowMonths": 0, "horizonMonths": 3})
    assert r.status_code == 200
    body = r.get_json()
    # Should average only the one regular month, not the downpayment
    lfs = body["loanForecasts"]
    assert len(lfs) == 1
    assert lfs[0]["noData"] is False
    assert lfs[0]["monthsToPayoff"] == 99  # (10000-100)/100 = 99 after 1 month paid
    # Projection should exist and have 3 months
    assert len(body["projection"]) == 3


def test_forecast_single_payer_100_percent(client):
    """Single payer paying everything should converge to ~100% ratio."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/payers", json={"name": "B"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 100000, "remainingPrincipal": 100000})
    # A pays all downpayment, B pays nothing
    client.post(
        "/api/months/downpayment",
        json={"contributions": [{"payerId": "p1", "amount": 100000}, {"payerId": "p2", "amount": 0}]},
    )
    # A pays everything monthly, B pays 0
    for ym in ["2024-01", "2024-02"]:
        client.post(
            "/api/months",
            json={
                "yearMonth": ym,
                "mode": "auto",
                "loanDetails": [{"loanId": "l1", "interest": 500, "principal": 1000}],
                "payerPayments": [{"payerId": "p1", "amount": 1500}, {"payerId": "p2", "amount": 0}],
            },
        )
    r = client.post("/api/forecast", json={"windowMonths": 0, "horizonMonths": 12})
    assert r.status_code == 200
    body = r.get_json()
    # p1 should have 100% at the end (p2 pays nothing, gets 0 principal)
    proj = body["projection"]
    last_ratios = proj[-1]["ratios"]
    assert last_ratios["p1"] == pytest.approx(1.0, abs=1e-4)
    assert last_ratios["p2"] == pytest.approx(0.0, abs=1e-4)


def test_manual_mode_underpayment_principal(client):
    """When payers pay less than interest in manual mode, CP should not increase."""
    client.post("/api/payers", json={"name": "A"})
    client.post("/api/payers", json={"name": "B"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 100000, "remainingPrincipal": 100000})
    client.post(
        "/api/months/downpayment",
        json={"contributions": [{"payerId": "p1", "amount": 50000}, {"payerId": "p2", "amount": 50000}]},
    )
    r = client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "manual",
            "loanDetails": [{"loanId": "l1", "interest": 1000, "principal": 500}],
            "payerPayments": [
                {"payerId": "p1", "amount": 400},
                {"payerId": "p2", "amount": 300},
            ],
            "manualRatios": {"p1": 0.6, "p2": 0.4},
        },
    )
    assert r.status_code == 201
    computed = r.get_json()["computed"]["perPayer"]
    # Total payments 700 < interest 1000, so actual_principal = 0
    assert computed["p1"]["adjPrincipal"] == pytest.approx(0.0, abs=1e-2)
    assert computed["p2"]["adjPrincipal"] == pytest.approx(0.0, abs=1e-2)
    # CP unchanged
    assert computed["p1"]["cumulativePrincipal"] == pytest.approx(50000.0, abs=1e-2)
    assert computed["p2"]["cumulativePrincipal"] == pytest.approx(50000.0, abs=1e-2)


def test_manual_month_detail_steps(client):
    """Detail endpoint generates manual-specific steps showing payments and ratio allocation."""
    client.post("/api/payers", json={"name": "Alice"})
    client.post("/api/payers", json={"name": "Bob"})
    client.post("/api/loans", json={"name": "L", "originalAmount": 100000})
    r = client.post(
        "/api/months",
        json={
            "yearMonth": "2024-01",
            "mode": "manual",
            "loanDetails": [{"loanId": "l1", "interest": 500, "principal": 1000}],
            "payerPayments": [
                {"payerId": "p1", "amount": 1000},
                {"payerId": "p2", "amount": 500},
            ],
            "manualRatios": {"p1": 0.6, "p2": 0.4},
        },
    )
    assert r.status_code == 201
    detail = client.get("/api/months/2024-01/detail").get_json()
    assert detail["mode"] == "manual"
    steps = detail["steps"]
    assert any("手动比例月份" in s for s in steps)
    assert any("还款总额" in s for s in steps)
    assert any("实际本金" in s for s in steps)
    assert any("手动比例" in s for s in steps)
    # Should NOT contain auto mode steps
    assert not any("Step 1" in s for s in steps)
    assert not any("Step 2" in s for s in steps)
