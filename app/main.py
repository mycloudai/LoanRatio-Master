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
_USERGUIDE_PATH = Path(__file__).parent.parent / "USERGUIDE.md"

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
        try:
            userguide = _USERGUIDE_PATH.read_text(encoding="utf-8")
        except OSError:
            userguide = ""
        return jsonify(
            {
                "version": __version__,
                "repoUrl": REPO_URL,
                "changelogMarkdown": changelog,
                "userguideMarkdown": userguide,
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
            # Compute CP-based ratios (not stored ratio, which may be manual override)
            total_cp = sum(
                last_per.get(p["id"], {}).get("cumulativePrincipal", 0.0)
                for p in s.get("payers", [])
            )
            payers_out = []
            for p in s.get("payers", []):
                info = last_per.get(p["id"], {})
                cp = info.get("cumulativePrincipal", 0.0)
                payers_out.append(
                    {
                        "id": p["id"],
                        "name": p.get("name", ""),
                        "cumulativePrincipal": cp,
                        "currentRatio": cp / total_cp if total_cp > 0 else 0.0,
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
            payers = s.get("payers", [])
            payer_names = {p["id"]: p.get("name", p["id"]) for p in payers}
            for idx, m in enumerate(months):
                if m["yearMonth"] == ym:
                    prev_per = (
                        months[idx - 1].get("computed", {}).get("perPayer", {}) if idx > 0 else {}
                    )
                    computed = m.get("computed", {})
                    per = computed.get("perPayer", {})
                    total_interest = computed.get("totalInterest", 0.0)

                    # Build payment lookup
                    pay_map = {
                        pp["payerId"]: float(pp.get("amount", 0))
                        for pp in m.get("payerPayments", [])
                    }

                    # Enrich perPayer with payment amount
                    enriched = {}
                    for pid, info in per.items():
                        enriched[pid] = {**info, "payment": pay_map.get(pid, 0.0)}

                    # Generate human-readable calculation steps
                    steps: list[str] = []
                    mode = m.get("mode", "auto")
                    manual_ratios = m.get("manualRatios") or {}
                    if mode == "manual":
                        total_payments = sum(
                            pay_map.get(pid, 0.0) for pid in per
                        )
                        actual_principal = max(0.0, total_payments - total_interest)
                        steps.append("本月为手动比例月份")
                        steps.append("")
                        steps.append("各参还人还款额：")
                        for pid in per:
                            name = payer_names.get(pid, pid)
                            pay = pay_map.get(pid, 0)
                            steps.append(f"  {name}: {pay}")
                        steps.append(f"  还款总额 = {total_payments}")
                        steps.append("")
                        steps.append(
                            f"实际本金 = max(0, 还款总额 − 总利息) = max(0, {total_payments} − {total_interest}) = {actual_principal}"
                        )
                        steps.append("")
                        steps.append("按手动比例计入累计本金：")
                        for pid in per:
                            name = payer_names.get(pid, pid)
                            mr = manual_ratios.get(pid, 0.0)
                            adj = per[pid].get("adjPrincipal", 0)
                            cp = per[pid].get("cumulativePrincipal", 0)
                            steps.append(
                                f"  {name}: 手动比例 = {mr:.4f}，本月计入本金 = {mr:.4f} × {actual_principal} = {adj}，累计 = {cp}"
                            )
                    elif mode == "auto":
                        steps.append(f"本月总利息 = {total_interest}")
                        steps.append("")
                        steps.append("Step 1 — 利息分摊 (按上月比例)：")
                        for pid in per:
                            prev_r = prev_per.get(pid, {}).get("ratio")
                            i_share = per[pid].get("interestShare", 0)
                            name = payer_names.get(pid, pid)
                            if prev_r is not None:
                                steps.append(
                                    f"  {name}: {prev_r:.4f} × {total_interest} = {i_share}"
                                )
                            else:
                                steps.append(f"  {name}: 首月均分 → {i_share}")

                        steps.append("")
                        steps.append("Step 2 — 原始净本金 (还款 − 利息分摊)：")
                        for pid in per:
                            name = payer_names.get(pid, pid)
                            pay = pay_map.get(pid, 0)
                            raw = per[pid].get("rawPrincipal", 0)
                            i_share = per[pid].get("interestShare", 0)
                            steps.append(f"  {name}: {pay} − {i_share} = {raw}")

                        # Step 3: negative redistribution
                        s_minus = [
                            pid
                            for pid in per
                            if per[pid].get("rawPrincipal", 0) < 0
                        ]
                        if s_minus:
                            steps.append("")
                            steps.append("Step 3 — 负本金再分配：")
                            for pid in s_minus:
                                name = payer_names.get(pid, pid)
                                raw = per[pid].get("rawPrincipal", 0)
                                steps.append(
                                    f"  {name} 原始净本金 = {raw} < 0，归零处理"
                                )
                        else:
                            steps.append("")
                            steps.append("Step 3 — 无负本金，无需再分配")

                        steps.append("")
                        steps.append("Step 4 — 累计本金更新 & 新比例：")
                        for pid in per:
                            name = payer_names.get(pid, pid)
                            adj = per[pid].get("adjPrincipal", 0)
                            cp = per[pid].get("cumulativePrincipal", 0)
                            ratio = per[pid].get("ratio", 0)
                            steps.append(
                                f"  {name}: 累计本金 = {cp}，比例 = {ratio:.4f}"
                            )

                    # Generate redistribution detail
                    redistribution: list[str] = []
                    s_minus = [
                        pid for pid in per if per[pid].get("rawPrincipal", 0) < 0
                    ]
                    s_plus = [
                        pid for pid in per if per[pid].get("rawPrincipal", 0) >= 0
                    ]
                    if s_minus:
                        neg_total = sum(
                            -per[pid].get("rawPrincipal", 0) for pid in s_minus
                        )
                        redistribution.append("以下参还人本月还款不足以覆盖利息：")
                        for pid in s_minus:
                            name = payer_names.get(pid, pid)
                            raw = per[pid].get("rawPrincipal", 0)
                            redistribution.append(
                                f"  {name}: 原始净本金 = {raw}，差额 = {-raw}"
                            )
                        redistribution.append(f"负差额总计 = {neg_total}")
                        redistribution.append("")
                        if not s_plus:
                            redistribution.append(
                                "所有参还人均未覆盖利息，调整后净本金全部归零。"
                            )
                        else:
                            redistribution.append(
                                "该差额按上月比例分摊给正本金参还人："
                            )
                            denom = sum(
                                prev_per.get(pid, {}).get("ratio", 0)
                                for pid in s_plus
                            )
                            for pid in s_plus:
                                name = payer_names.get(pid, pid)
                                raw = per[pid].get("rawPrincipal", 0)
                                adj = per[pid].get("adjPrincipal", 0)
                                extra = adj - raw
                                prev_r = prev_per.get(pid, {}).get("ratio", 0)
                                if denom > 0:
                                    redistribution.append(
                                        f"  {name}: {raw} + ({prev_r:.4f}/{denom:.4f}) × {neg_total} = {adj}"
                                    )
                                else:
                                    redistribution.append(
                                        f"  {name}: {raw} + {extra} (均分) = {adj}"
                                    )

                    return jsonify(
                        {
                            "yearMonth": ym,
                            "mode": mode,
                            "loanDetails": m.get("loanDetails", []),
                            "payerPayments": m.get("payerPayments", []),
                            "manualRatios": m.get("manualRatios"),
                            "totalInterest": total_interest,
                            "perPayer": enriched,
                            "steps": steps,
                            "redistribution": redistribution,
                            "formulas": {
                                pid: {
                                    "interestShareFormula": (
                                        f"{prev_per.get(pid, {}).get('ratio', 0):.4f} × {total_interest}"
                                        if prev_per.get(pid)
                                        else "首月均分"
                                    ),
                                    "rawPrincipalFormula": (
                                        f"{pay_map.get(pid, 0)} − {per[pid].get('interestShare', 0)} = {per[pid].get('rawPrincipal', 0)}"
                                    ),
                                }
                                for pid in per
                            },
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
        selected_months: list[str] | None = body.get("selectedMonths")
        if selected_months is not None and not isinstance(selected_months, list):
            return _err("selectedMonths must be an array of YYYY-MM strings")
        if isinstance(selected_months, list) and len(selected_months) == 0:
            return _err("selectedMonths cannot be empty")
        with _state_lock:
            s = _ensure_state()
            months = s.get("months", [])
            if not months:
                return _err("at least one month of data is required for forecast")
            if selected_months:
                sel_set = set(selected_months)
                recent = [m for m in months if m["yearMonth"] in sel_set]
                if not recent:
                    return _err("none of the selectedMonths matched existing months")
            else:
                recent = months[-window:] if window > 0 else months
            # Average payments per payer and per loan
            payer_ids = [p["id"] for p in s.get("payers", [])]
            loans_list = s.get("loans", [])
            loan_ids = [ln["id"] for ln in loans_list]
            loan_names = {ln["id"]: ln.get("name", ln["id"]) for ln in loans_list}

            # Exclude downpayment month (0000-00) from averaging
            avg_months = [m for m in recent if m.get("yearMonth") != "0000-00"]
            if not avg_months:
                return _err("no non-downpayment months available for forecast")

            sums = {pid: 0.0 for pid in payer_ids}
            loan_interest_sums: dict[str, float] = {lid: 0.0 for lid in loan_ids}
            loan_principal_sums: dict[str, float] = {lid: 0.0 for lid in loan_ids}
            for m in avg_months:
                mode = m.get("mode", "auto")
                manual_ratios = m.get("manualRatios") or {}
                if mode == "manual" and manual_ratios:
                    # Manual months: attribute payments by manual ratios,
                    # not raw payerPayments, so the forecast respects the
                    # user's intended equity split.
                    total_pay = sum(
                        float(pp.get("amount", 0.0))
                        for pp in (m.get("payerPayments") or [])
                    )
                    for pid in payer_ids:
                        sums[pid] += float(manual_ratios.get(pid, 0.0)) * total_pay
                else:
                    for pp in m.get("payerPayments") or []:
                        if pp["payerId"] in sums:
                            sums[pp["payerId"]] += float(pp.get("amount", 0.0))
                for ld in m.get("loanDetails") or []:
                    lid = ld.get("loanId")
                    interest = float(ld.get("interest", 0.0))
                    principal = float(ld.get("principal", 0.0))
                    if lid in loan_interest_sums:
                        loan_interest_sums[lid] += interest
                        loan_principal_sums[lid] += principal
            n = max(1, len(avg_months))
            avg_payments = {pid: sums[pid] / n for pid in payer_ids}
            avg_loan_interest = {lid: loan_interest_sums[lid] / n for lid in loan_ids}
            avg_loan_principal = {lid: loan_principal_sums[lid] / n for lid in loan_ids}

            # Project forward by simulating
            projection = []
            sim_state = {
                "payers": s.get("payers", []),
                "loans": [dict(ln) for ln in loans_list],
                "downpayment": s.get("downpayment"),
                "months": [
                    {k: v for k, v in m.items() if not k.startswith("_")} for m in months
                ],
            }
            last_ym = months[-1]["yearMonth"]
            if last_ym == "0000-00":
                return _err("at least one regular month is required for forecast")
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
                    "loanDetails": [
                        {"loanId": lid, "interest": avg_loan_interest[lid], "principal": avg_loan_principal[lid]}
                        for lid in loan_ids
                    ],
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

            # Per-loan forecast: total interest until payoff
            loan_forecasts = []
            for lid in loan_ids:
                avg_int = avg_loan_interest[lid]
                avg_prin = avg_loan_principal[lid]
                rem = float(
                    next(
                        (ln.get("remainingPrincipal", 0) for ln in s["loans"] if ln["id"] == lid),
                        0,
                    )
                )
                if rem <= 0:
                    loan_forecasts.append({
                        "loanId": lid,
                        "loanName": loan_names.get(lid, lid),
                        "remainingPrincipal": 0.0,
                        "monthsToPayoff": 0,
                        "payoffMonth": None,
                        "totalFutureInterest": 0.0,
                        "noData": False,
                    })
                elif avg_prin <= 0:
                    # Loan has remaining principal but no average principal repayment
                    # — historical data missing or interest-only period
                    loan_forecasts.append({
                        "loanId": lid,
                        "loanName": loan_names.get(lid, lid),
                        "remainingPrincipal": round(rem, 2),
                        "monthsToPayoff": None,
                        "payoffMonth": None,
                        "totalFutureInterest": None,
                        "noData": True,
                    })
                else:
                    months_left = 0
                    total_interest = 0.0
                    r = rem
                    while r > 0 and months_left < 1200:
                        total_interest += avg_int
                        r -= avg_prin
                        months_left += 1
                    yy, mmx = map(int, last_ym.split("-"))
                    mmx += months_left
                    yy += (mmx - 1) // 12
                    mmx = ((mmx - 1) % 12) + 1
                    loan_forecasts.append({
                        "loanId": lid,
                        "loanName": loan_names.get(lid, lid),
                        "remainingPrincipal": round(rem, 2),
                        "monthsToPayoff": months_left,
                        "payoffMonth": f"{yy:04d}-{mmx:02d}",
                        "totalFutureInterest": round(total_interest, 2),
                        "noData": False,
                    })

            # Overall payoff = latest per-loan payoff; None if any loan can't pay off
            has_unpayable = any(
                lf["noData"] for lf in loan_forecasts
            )
            payoff = None
            if not has_unpayable:
                for lf in loan_forecasts:
                    if lf["payoffMonth"]:
                        if payoff is None or lf["payoffMonth"] > payoff:
                            payoff = lf["payoffMonth"]

            # Build series/months for chart consumption
            series: dict[str, list[float]] = {pid: [] for pid in payer_ids}
            proj_months: list[str] = []
            for p in projection:
                proj_months.append(p["yearMonth"])
                for pid in payer_ids:
                    series[pid].append(p["ratios"].get(pid, 0.0))

            return jsonify({
                "projection": projection,
                "payoffMonth": payoff,
                "loanForecasts": loan_forecasts,
                "series": series,
                "months": proj_months,
            })

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
