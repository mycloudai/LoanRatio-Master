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
    assert "computed" in body
    assert "formulas" in body


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
