"""Flask HTTP entry point for LoanRatio.

All routes live under /api. Static index.html served at /.
Bound to 127.0.0.1 only (local-only).
"""

from __future__ import annotations

import os
import re
import threading
import webbrowser
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from . import REPO_URL, __version__, calculator, exporter, storage

_CHANGELOG_PATH = Path(__file__).parent.parent / "CHANGELOG.md"

YM_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DOWNPAYMENT_YM = "0000-00"

# In-memory state (file-backed). Loaded lazily.
_state_lock = threading.Lock()
_state: dict[str, Any] | None = None
_data_path: Path | None = None


def _is_reset_allowed() -> bool:
    return os.environ.get("LOANRATIO_ALLOW_RESET") == "1"


def _load_state_into_memory() -> dict[str, Any]:
    global _state, _data_path
    cfg = storage.read_config()
    if cfg.get("dataPath"):
        _data_path = Path(cfg["dataPath"])
        _state = storage.load_state(_data_path)
    else:
        _data_path = None
        _state = storage.empty_state(None)
    calculator.recompute_all(_state)
    return _state


def _ensure_state() -> dict[str, Any]:
    global _state
    if _state is None:
        _load_state_into_memory()
    assert _state is not None
    return _state


def _persist() -> None:
    if _data_path is not None and _state is not None:
        storage.save_state(_data_path, _state)


def _next_expected_month(state: dict[str, Any]) -> str | None:
    months = state.get("months", [])
    if not months:
        return None
    last = months[-1]["yearMonth"]
    y, m = map(int, last.split("-"))
    m += 1
    if m > 12:
        y += 1
        m = 1
    return f"{y:04d}-{m:02d}"


def _err(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


def create_app(test_data_path: str | Path | None = None) -> Flask:
    """Application factory.

    test_data_path: if provided, overrides config-based loading and uses this file
    (used by pytest fixtures).
    """
    global _state, _data_path
    static_dir = Path(__file__).parent.parent / "static"
    app = Flask(__name__, static_folder=str(static_dir), static_url_path="/static")

    if test_data_path is not None:
        with _state_lock:
            _data_path = Path(test_data_path)
            _state = storage.load_state(_data_path)
            calculator.recompute_all(_state)

    # ---------------- Static / index --------------------------------------
    @app.route("/")
    def index():
        idx = static_dir / "index.html"
        if not idx.exists():
            return (
                "<html><body><h1>LoanRatio</h1><p>Frontend not yet generated. "
                "API is up at <code>/api/health</code>.</p></body></html>",
                200,
                {"Content-Type": "text/html; charset=utf-8"},
            )
        return send_from_directory(str(static_dir), "index.html")

    # ---------------- Health ----------------------------------------------
    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "version": __version__})

    # ---------------- About (version + repo + changelog) ------------------
    @app.get("/api/about")
    def about():
        try:
            changelog = _CHANGELOG_PATH.read_text(encoding="utf-8")
        except OSError:
            changelog = ""
        return jsonify(
            {
                "version": __version__,
                "repoUrl": REPO_URL,
                "changelogMarkdown": changelog,
            }
        )

    # ---------------- Config ----------------------------------------------
    @app.get("/api/config")
    def get_config():
        cfg = storage.read_config()
        return jsonify({"dataPath": cfg.get("dataPath"), "initialized": cfg.get("initialized", False)})

    @app.post("/api/config")
    def post_config():
        body = request.get_json(silent=True) or {}
        dp = body.get("dataPath")
        if not dp or not isinstance(dp, str):
            return _err("dataPath required")
        result = storage.write_config(dp)
        with _state_lock:
            global _state, _data_path
            _data_path = Path(result["dataPath"])
            _state = storage.load_state(_data_path)
            calculator.recompute_all(_state)
        return jsonify(result)

    # ---------------- State -----------------------------------------------
    @app.get("/api/state")
    def get_state():
        with _state_lock:
            s = _ensure_state()
            # Strip internal cache for response
            sanitized = {
                "config": s.get("config", {}),
                "payers": s.get("payers", []),
                "loans": s.get("loans", []),
                "downpayment": s.get("downpayment"),
                "months": [
                    {k: v for k, v in m.items() if not k.startswith("_")}
                    for m in s.get("months", [])
                ],
            }
        return jsonify(sanitized)

    @app.post("/api/state/reset")
    def reset_state_route():
        if not _is_reset_allowed():
            return _err("forbidden", 403)
        with _state_lock:
            global _state
            _state = storage.empty_state(str(_data_path) if _data_path else None)
            if _data_path is not None:
                storage.save_state(_data_path, _state)
        return jsonify({"ok": True})

    @app.post("/api/state/load")
    def load_state_route():
        if not _is_reset_allowed():
            return _err("forbidden", 403)
        body = request.get_json(silent=True) or {}
        st = body.get("state")
        if not isinstance(st, dict):
            return _err("state object required")
        st.setdefault("payers", [])
        st.setdefault("loans", [])
        st.setdefault("downpayment", None)
        st.setdefault("months", [])
        st.setdefault("config", {})
        with _state_lock:
            global _state
            _state = st
            calculator.recompute_all(_state)
            _persist()
        return jsonify({"ok": True})

    # ---------------- Payers ----------------------------------------------
    @app.post("/api/payers")
    def create_payer():
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            return _err("name required")
        sm = body.get("startMonth")
        if sm is not None and sm != "" and not YM_RE.match(sm):
            return _err("startMonth must be YYYY-MM")
        with _state_lock:
            payer = calculator.add_payer(_ensure_state(), name, sm or None)
            _persist()
        return jsonify(payer), 201

    @app.patch("/api/payers/<pid>")
    def update_payer(pid):
        body = request.get_json(silent=True) or {}
        with _state_lock:
            s = _ensure_state()
            for p in s.get("payers", []):
                if p["id"] == pid:
                    if "name" in body:
                        p["name"] = str(body["name"])
                    if "startMonth" in body:
                        sm = body["startMonth"]
                        if sm is not None and sm != "" and not YM_RE.match(sm):
                            return _err("startMonth must be YYYY-MM")
                        p["startMonth"] = sm or None
                    calculator.recompute_all(s)
                    _persist()
                    return jsonify(p)
        return _err("payer not found", 404)

    @app.delete("/api/payers/<pid>")
    def remove_payer(pid):
        strategy = request.args.get("strategy", "delete")
        with _state_lock:
            try:
                calculator.delete_payer(_ensure_state(), pid, strategy)
            except ValueError as e:
                return _err(str(e))
            _persist()
        return jsonify({"ok": True})

    # ---------------- Loans -----------------------------------------------
    @app.post("/api/loans")
    def create_loan():
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            return _err("name required")
        try:
            oa = float(body.get("originalAmount", 0))
            rp = float(body.get("remainingPrincipal", oa))
        except (TypeError, ValueError):
            return _err("amounts must be numeric")
        if oa < 0 or rp < 0:
            return _err("amounts must be non-negative")
        with _state_lock:
            loan = calculator.add_loan(_ensure_state(), name, oa, rp)
            _persist()
        return jsonify(loan), 201

    @app.patch("/api/loans/<lid>")
    def update_loan(lid):
        body = request.get_json(silent=True) or {}
        with _state_lock:
            s = _ensure_state()
            for ln in s.get("loans", []):
                if ln["id"] == lid:
                    if "name" in body:
                        ln["name"] = str(body["name"])
                    if "originalAmount" in body:
                        ln["originalAmount"] = float(body["originalAmount"])
                    if "remainingPrincipal" in body:
                        ln["remainingPrincipal"] = float(body["remainingPrincipal"])
                    calculator.recompute_all(s)
                    _persist()
                    return jsonify(ln)
        return _err("loan not found", 404)

    @app.delete("/api/loans/<lid>")
    def remove_loan(lid):
        strategy = request.args.get("strategy", "delete")
        target = request.args.get("targetId")
        with _state_lock:
            try:
                calculator.delete_loan(_ensure_state(), lid, strategy, target)
            except ValueError as e:
                return _err(str(e))
            _persist()
        return jsonify({"ok": True})

    # ---------------- Months ----------------------------------------------
    @app.get("/api/months")
    def list_months():
        with _state_lock:
            s = _ensure_state()
            return jsonify(
                [{k: v for k, v in m.items() if not k.startswith("_")} for m in s.get("months", [])]
            )

    @app.post("/api/months/downpayment")
    def post_downpayment():
        body = request.get_json(silent=True) or {}
        contribs = body.get("contributions")
        if not isinstance(contribs, list):
            return _err("contributions required")
        with _state_lock:
            s = _ensure_state()
            if s.get("months"):
                return _err("downpayment must be set before any months exist")
            payer_ids = {p["id"] for p in s.get("payers", [])}
            normalized = []
            cp0: dict[str, float] = {}
            for c in contribs:
                pid = c.get("payerId")
                if pid not in payer_ids:
                    return _err(f"unknown payerId {pid}")
                amt = float(c.get("amount", 0))
                normalized.append({"payerId": pid, "amount": amt})
                cp0[pid] = amt
            s["downpayment"] = {"contributions": normalized, "CP0": cp0}
            calculator.recompute_all(s)
            _persist()
            return jsonify(s["downpayment"]), 201

    def _validate_month_body(body: dict, s: dict) -> tuple[str | None, dict | None]:
        ym = body.get("yearMonth")
        if not ym or not YM_RE.match(ym):
            return ("yearMonth must be YYYY-MM", None)
        if not s.get("payers"):
            return ("at least one payer required", None)
        if not s.get("loans"):
            return ("at least one loan required", None)
        # Consecutive months check
        nxt = _next_expected_month(s)
        if nxt is not None and ym != nxt:
            return (f"yearMonth must be {nxt} (months must be consecutive)", None)
        if nxt is None:
            # First month: cannot precede any payer's startMonth? we just allow.
            pass
        mode = body.get("mode", "auto")
        if mode not in {"auto", "manual"}:
            return ("mode must be auto or manual", None)
        loan_details = body.get("loanDetails") or []
        payer_payments = body.get("payerPayments") or []
        if mode == "auto":
            if not loan_details:
                return ("loanDetails required for auto mode", None)
            if not payer_payments:
                return ("payerPayments required for auto mode", None)
        manual_ratios = body.get("manualRatios")
        if mode == "manual":
            if not isinstance(manual_ratios, dict):
                return ("manualRatios required for manual mode", None)
            active = set(calculator.active_payer_ids(s["payers"], ym))
            try:
                vals = {k: float(v) for k, v in manual_ratios.items() if k in active}
            except (TypeError, ValueError):
                return ("manualRatios must be numeric", None)
            if abs(sum(vals.values()) - 1.0) > 1e-6:
                return ("manualRatios must sum to 1.0", None)
            manual_ratios = vals
        # Validate payerIds / loanIds
        payer_set = {p["id"] for p in s["payers"]}
        loan_set = {ln["id"] for ln in s["loans"]}
        for pp in payer_payments:
            if pp.get("payerId") not in payer_set:
                return (f"unknown payerId {pp.get('payerId')}", None)
        for ld in loan_details:
            if ld.get("loanId") not in loan_set:
                return (f"unknown loanId {ld.get('loanId')}", None)
        # Negative amounts rejected
        for ld in loan_details:
            if float(ld.get("interest", 0)) < 0 or float(ld.get("principal", 0)) < 0:
                return ("interest/principal must be non-negative", None)
        for pp in payer_payments:
            if float(pp.get("amount", 0)) < 0:
                return ("payment amount must be non-negative", None)
        month = {
            "yearMonth": ym,
            "mode": mode,
            "loanDetails": [
                {
                    "loanId": ld["loanId"],
                    "interest": float(ld.get("interest", 0)),
                    "principal": float(ld.get("principal", 0)),
                }
                for ld in loan_details
            ],
            "payerPayments": [
                {"payerId": pp["payerId"], "amount": float(pp.get("amount", 0))}
                for pp in payer_payments
            ],
            "manualRatios": manual_ratios if mode == "manual" else None,
        }
        return (None, month)

    @app.post("/api/months")
    def create_month():
        body = request.get_json(silent=True) or {}
        with _state_lock:
            s = _ensure_state()
            err, month = _validate_month_body(body, s)
            if err:
                return _err(err)
            s.setdefault("months", []).append(month)
            calculator.recompute_all(s)
            _persist()
            stored = {k: v for k, v in s["months"][-1].items() if not k.startswith("_")}
            return jsonify(stored), 201

    @app.patch("/api/months/<ym>")
    def update_month(ym):
        body = request.get_json(silent=True) or {}
        with _state_lock:
            s = _ensure_state()
            for m in s.get("months", []):
                if m["yearMonth"] == ym:
                    # Allow partial updates: mode, loanDetails, payerPayments, manualRatios
                    if "mode" in body:
                        if body["mode"] not in {"auto", "manual"}:
                            return _err("mode must be auto or manual")
                        m["mode"] = body["mode"]
                    if "loanDetails" in body:
                        loan_set = {ln["id"] for ln in s["loans"]}
                        for ld in body["loanDetails"]:
                            if ld.get("loanId") not in loan_set:
                                return _err(f"unknown loanId {ld.get('loanId')}")
                        m["loanDetails"] = [
                            {
                                "loanId": ld["loanId"],
                                "interest": float(ld.get("interest", 0)),
                                "principal": float(ld.get("principal", 0)),
                            }
                            for ld in body["loanDetails"]
                        ]
                    if "payerPayments" in body:
                        payer_set = {p["id"] for p in s["payers"]}
                        for pp in body["payerPayments"]:
                            if pp.get("payerId") not in payer_set:
                                return _err(f"unknown payerId {pp.get('payerId')}")
                        m["payerPayments"] = [
                            {"payerId": pp["payerId"], "amount": float(pp.get("amount", 0))}
                            for pp in body["payerPayments"]
                        ]
                    if "manualRatios" in body:
                        mr = body["manualRatios"]
                        if mr is None:
                            m["manualRatios"] = None
                        else:
                            try:
                                vals = {k: float(v) for k, v in mr.items()}
                            except (TypeError, ValueError):
                                return _err("manualRatios must be numeric")
                            if abs(sum(vals.values()) - 1.0) > 1e-6:
                                return _err("manualRatios must sum to 1.0")
                            m["manualRatios"] = vals
                    if m.get("mode") == "manual" and not m.get("manualRatios"):
                        return _err("manualRatios required for manual mode")
                    calculator.recompute_all(s)
                    _persist()
                    return jsonify({k: v for k, v in m.items() if not k.startswith("_")})
        return _err("month not found", 404)

    @app.delete("/api/months/<ym>")
    def delete_month(ym):
        with _state_lock:
            s = _ensure_state()
            months = s.get("months", [])
            if not months or months[-1]["yearMonth"] != ym:
                return _err("only the most recent month may be deleted")
            months.pop()
            calculator.recompute_all(s)
            _persist()
        return jsonify({"ok": True})

    # ---------------- Summary / Detail ------------------------------------
    @app.get("/api/summary")
    def summary():
        with _state_lock:
            s = _ensure_state()
            months = s.get("months", [])
            last_per = months[-1].get("computed", {}).get("perPayer", {}) if months else {}
            payers_out = []
            for p in s.get("payers", []):
                info = last_per.get(p["id"], {})
                payers_out.append(
                    {
                        "id": p["id"],
                        "name": p.get("name", ""),
                        "cumulativePrincipal": info.get("cumulativePrincipal", 0.0),
                        "currentRatio": info.get("ratio", 0.0),
                    }
                )
            loans_out = [
                {
                    "id": ln["id"],
                    "name": ln.get("name", ""),
                    "originalAmount": ln.get("originalAmount", 0.0),
                    "remainingPrincipal": ln.get("remainingPrincipal", 0.0),
                }
                for ln in s.get("loans", [])
            ]
            months_out = []
            for m in months:
                per = m.get("computed", {}).get("perPayer", {})
                months_out.append(
                    {
                        "yearMonth": m["yearMonth"],
                        "mode": m.get("mode", "auto"),
                        "ratios": {pid: info.get("ratio", 0.0) for pid, info in per.items()},
                    }
                )
            return jsonify({"payers": payers_out, "loans": loans_out, "months": months_out})

    @app.get("/api/months/<ym>/detail")
    def month_detail(ym):
        with _state_lock:
            s = _ensure_state()
            months = s.get("months", [])
            for idx, m in enumerate(months):
                if m["yearMonth"] == ym:
                    prev_per = (
                        months[idx - 1].get("computed", {}).get("perPayer", {}) if idx > 0 else {}
                    )
                    per = m.get("computed", {}).get("perPayer", {})
                    formulas = {}
                    for pid, info in per.items():
                        prev_r = prev_per.get(pid, {}).get("ratio", 0.0) if idx > 0 else None
                        formulas[pid] = {
                            "interestShareFormula": (
                                f"{prev_r} * {m.get('computed', {}).get('totalInterest', 0)}"
                                if prev_r is not None
                                else "first-month seeding"
                            ),
                            "rawPrincipalFormula": (
                                f"payment - interestShare = {info.get('rawPrincipal')}"
                            ),
                        }
                    return jsonify(
                        {
                            "yearMonth": ym,
                            "mode": m.get("mode", "auto"),
                            "loanDetails": m.get("loanDetails", []),
                            "payerPayments": m.get("payerPayments", []),
                            "manualRatios": m.get("manualRatios"),
                            "computed": m.get("computed", {}),
                            "formulas": formulas,
                        }
                    )
        return _err("month not found", 404)

    # ---------------- Forecast --------------------------------------------
    @app.post("/api/forecast")
    def forecast():
        body = request.get_json(silent=True) or {}
        try:
            window = int(body.get("windowMonths", 3))
            horizon = int(body.get("horizonMonths", 12))
        except (TypeError, ValueError):
            return _err("windowMonths/horizonMonths must be ints")
        with _state_lock:
            s = _ensure_state()
            months = s.get("months", [])
            if not months:
                return jsonify({"projection": [], "payoffMonth": None})
            recent = months[-window:] if window > 0 else months
            # Average payments per payer
            payer_ids = [p["id"] for p in s.get("payers", [])]
            sums = {pid: 0.0 for pid in payer_ids}
            interest_sums = 0.0
            principal_sums = 0.0
            for m in recent:
                for pp in m.get("payerPayments") or []:
                    if pp["payerId"] in sums:
                        sums[pp["payerId"]] += float(pp.get("amount", 0.0))
                for ld in m.get("loanDetails") or []:
                    interest_sums += float(ld.get("interest", 0.0))
                    principal_sums += float(ld.get("principal", 0.0))
            n = max(1, len(recent))
            avg_payments = {pid: sums[pid] / n for pid in payer_ids}
            avg_interest = interest_sums / n
            avg_principal = principal_sums / n

            # Project forward by simulating
            projection = []
            sim_state = {
                "payers": s.get("payers", []),
                "loans": s.get("loans", []),
                "downpayment": s.get("downpayment"),
                "months": [
                    {k: v for k, v in m.items() if not k.startswith("_")} for m in months
                ],
            }
            last_ym = months[-1]["yearMonth"]
            y, mm = map(int, last_ym.split("-"))
            for _ in range(horizon):
                mm += 1
                if mm > 12:
                    y += 1
                    mm = 1
                ym = f"{y:04d}-{mm:02d}"
                synth = {
                    "yearMonth": ym,
                    "mode": "auto",
                    "loanDetails": (
                        [{"loanId": s["loans"][0]["id"], "interest": avg_interest, "principal": avg_principal}]
                        if s.get("loans")
                        else []
                    ),
                    "payerPayments": [
                        {"payerId": pid, "amount": avg_payments[pid]} for pid in payer_ids
                    ],
                    "manualRatios": None,
                }
                sim_state["months"].append(synth)
                calculator.recompute_all(sim_state)
                last = sim_state["months"][-1]
                projection.append(
                    {
                        "yearMonth": ym,
                        "ratios": {
                            pid: last["computed"]["perPayer"].get(pid, {}).get("ratio", 0.0)
                            for pid in payer_ids
                        },
                    }
                )

            # Payoff month estimate
            payoff = None
            if avg_principal > 0 and s.get("loans"):
                remaining = sum(float(ln.get("remainingPrincipal", 0.0)) for ln in s["loans"])
                if remaining > 0:
                    months_left = int(remaining / avg_principal) + 1
                    yy, mmx = map(int, last_ym.split("-"))
                    mmx += months_left
                    yy += (mmx - 1) // 12
                    mmx = ((mmx - 1) % 12) + 1
                    payoff = f"{yy:04d}-{mmx:02d}"
            return jsonify({"projection": projection, "payoffMonth": payoff})

    # ---------------- Export ----------------------------------------------
    @app.get("/api/export/excel")
    def export_xlsx():
        with _state_lock:
            data = exporter.export_excel(_ensure_state())
        from flask import Response

        return Response(
            data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="loan-ratio.xlsx"'},
        )

    return app


def cli() -> None:
    """Entry point: start server + open browser."""
    app = create_app()
    host, port = "127.0.0.1", 5000
    url = f"http://{host}:{port}/"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"LoanRatio v{__version__} listening on {url}")
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    cli()
