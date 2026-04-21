"""Core principal-attribution calculator.

Implements the four-step monthly model described in PLAN.md:
  1. interest_share_i(t) = r_i(t-1) * I(t)
  2. raw_principal_i(t) = pay_i(t) - interest_share_i(t)
  3. Negative principals get redistributed to S+ payers weighted by r_i(t-1)
  4. CP_i(t) = CP_i(t-1) + adj_i(t);  r_i(t) = CP_i(t) / CP(t)

Manual mode: r_i(t) = manualRatios[i] directly. CP unchanged. Interest share for display only.

First-month seeding:
  - If downpayment present (sum > 0): r_i(0) = CP0_i / sum(CP0)
  - Else: equal split among active payers (1/n) for first-month interest only,
          replaced by real CP1/CP(1) ratios after computation.
"""

from __future__ import annotations

from typing import Any

CURRENCY_DP = 2
RATIO_DP = 4


def _round(x: float, dp: int) -> float:
    return round(float(x), dp)


def _ym_le(a: str | None, b: str) -> bool:
    """startMonth <= b? Treat None as -infinity (always active)."""
    if a is None or a == "":
        return True
    return a <= b


def active_payer_ids(payers: list[dict], year_month: str) -> list[str]:
    return [p["id"] for p in payers if _ym_le(p.get("startMonth"), year_month)]


def downpayment_cp(downpayment: dict | None, payer_ids: list[str]) -> dict[str, float]:
    """Return CP_i(0) per payer. Empty / no downpayment -> all zeros."""
    cp: dict[str, float] = {pid: 0.0 for pid in payer_ids}
    if not downpayment:
        return cp
    for c in downpayment.get("contributions", []):
        pid = c["payerId"]
        if pid in cp:
            cp[pid] = float(c.get("amount", 0.0))
    return cp


def initial_ratios(
    cp0: dict[str, float], active_ids: list[str]
) -> tuple[dict[str, float], bool]:
    """Compute r_i(0). Returns (ratios, used_downpayment).

    If sum(CP0 over active) > 0: ratio = CP0_i / sum.
    Else: equal split among active (1/n), used_downpayment=False.
    """
    total = sum(cp0.get(pid, 0.0) for pid in active_ids)
    if total > 0:
        return ({pid: cp0.get(pid, 0.0) / total for pid in active_ids}, True)
    n = len(active_ids)
    if n == 0:
        return ({}, False)
    return ({pid: 1.0 / n for pid in active_ids}, False)


def _compute_auto_month(
    *,
    payer_ids_all: list[str],
    active_ids: list[str],
    prev_ratio: dict[str, float],
    prev_cp: dict[str, float],
    payments: dict[str, float],
    total_interest: float,
) -> dict[str, dict[str, float]]:
    """Run the 4-step auto algorithm. Returns perPayer dict for ALL payers.

    Inactive payers get zero everything but their CP carries forward unchanged.
    """
    per: dict[str, dict[str, float]] = {}

    # Inactive payers carry CP forward, no contribution this month.
    for pid in payer_ids_all:
        if pid not in active_ids:
            per[pid] = {
                "interestShare": 0.0,
                "rawPrincipal": 0.0,
                "adjPrincipal": 0.0,
                "cumulativePrincipal": prev_cp.get(pid, 0.0),
                "ratio": 0.0,  # filled after
            }

    # Step 1 + 2 for active
    raw: dict[str, float] = {}
    interest_share: dict[str, float] = {}
    for pid in active_ids:
        i_share = prev_ratio.get(pid, 0.0) * total_interest
        interest_share[pid] = i_share
        raw[pid] = payments.get(pid, 0.0) - i_share

    # Step 3: negative redistribution
    s_plus = [pid for pid in active_ids if raw[pid] >= 0]
    s_minus = [pid for pid in active_ids if raw[pid] < 0]
    neg_total = sum(-raw[pid] for pid in s_minus)

    adj: dict[str, float] = {}
    if neg_total > 0 and s_plus:
        # Per PLAN.md scenario D: negative shortfall is added onto positive contributors
        # weighted by their previous-month ratio.
        denom = sum(prev_ratio.get(pid, 0.0) for pid in s_plus)
        if denom <= 0:
            # Fallback: equal split among S+ (e.g., new payers with prev_ratio=0)
            share = neg_total / len(s_plus)
            for pid in s_plus:
                adj[pid] = raw[pid] + share
        else:
            for pid in s_plus:
                adj[pid] = raw[pid] + (prev_ratio.get(pid, 0.0) / denom) * neg_total
        for pid in s_minus:
            adj[pid] = 0.0
    else:
        for pid in active_ids:
            adj[pid] = raw[pid]

    # Step 4: CP update + ratios
    new_cp: dict[str, float] = {}
    for pid in payer_ids_all:
        if pid in active_ids:
            new_cp[pid] = prev_cp.get(pid, 0.0) + adj[pid]
        else:
            new_cp[pid] = prev_cp.get(pid, 0.0)

    cp_total = sum(new_cp.values())
    for pid in payer_ids_all:
        if pid in active_ids:
            per[pid] = {
                "interestShare": interest_share[pid],
                "rawPrincipal": raw[pid],
                "adjPrincipal": adj[pid],
                "cumulativePrincipal": new_cp[pid],
                "ratio": (new_cp[pid] / cp_total) if cp_total > 0 else 0.0,
            }
        else:
            per[pid]["ratio"] = (new_cp[pid] / cp_total) if cp_total > 0 else 0.0
    return per


def _compute_manual_month(
    *,
    payer_ids_all: list[str],
    active_ids: list[str],
    prev_ratio: dict[str, float],
    prev_cp: dict[str, float],
    payments: dict[str, float],
    total_interest: float,
    manual_ratios: dict[str, float],
) -> dict[str, dict[str, float]]:
    per: dict[str, dict[str, float]] = {}
    for pid in payer_ids_all:
        is_active = pid in active_ids
        i_share = prev_ratio.get(pid, 0.0) * total_interest if is_active else 0.0
        pay = payments.get(pid, 0.0) if is_active else 0.0
        per[pid] = {
            "interestShare": i_share,
            "rawPrincipal": (pay - i_share) if is_active else 0.0,
            "adjPrincipal": 0.0,  # manual doesn't update CP
            "cumulativePrincipal": prev_cp.get(pid, 0.0),
            "ratio": float(manual_ratios.get(pid, 0.0)),
        }
    return per


def _round_per_payer(per: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for pid, vals in per.items():
        out[pid] = {
            "interestShare": _round(vals["interestShare"], CURRENCY_DP),
            "rawPrincipal": _round(vals["rawPrincipal"], CURRENCY_DP),
            "adjPrincipal": _round(vals["adjPrincipal"], CURRENCY_DP),
            "cumulativePrincipal": _round(vals["cumulativePrincipal"], CURRENCY_DP),
            "ratio": _round(vals["ratio"], RATIO_DP),
        }
    return out


def recompute_all(state: dict[str, Any]) -> dict[str, Any]:
    """Recompute every month's `computed` block in order.

    Mutates and returns the state dict. Uses precise (un-rounded) values internally,
    rounds only the displayed `computed` block.
    """
    payers = state.get("payers", [])
    payer_ids_all = [p["id"] for p in payers]
    months = state.get("months", [])
    downpayment = state.get("downpayment")

    # Establish CP0 (downpayment) for all payers
    cp_state: dict[str, float] = downpayment_cp(downpayment, payer_ids_all)

    # Previous-month ratios (None means "use seeding for first month")
    prev_ratio: dict[str, float] | None = None

    for m in months:
        ym = m["yearMonth"]
        active_ids = active_payer_ids(payers, ym)

        if prev_ratio is None:
            init_r, _used_dp = initial_ratios(cp_state, active_ids)
            prev_ratio = init_r

        mode = m.get("mode", "auto")
        payments = {pp["payerId"]: float(pp.get("amount", 0.0)) for pp in m.get("payerPayments", [])}
        loan_details = m.get("loanDetails", []) or []
        total_interest = sum(float(ld.get("interest", 0.0)) for ld in loan_details)

        if mode == "manual":
            manual_ratios = m.get("manualRatios") or {}
            per = _compute_manual_month(
                payer_ids_all=payer_ids_all,
                active_ids=active_ids,
                prev_ratio=prev_ratio,
                prev_cp=cp_state,
                payments=payments,
                total_interest=total_interest,
                manual_ratios=manual_ratios,
            )
            # Manual: CP unchanged, but next-month uses manual ratios as basis
            next_ratio = {pid: float(manual_ratios.get(pid, 0.0)) for pid in payer_ids_all}
        else:
            per = _compute_auto_month(
                payer_ids_all=payer_ids_all,
                active_ids=active_ids,
                prev_ratio=prev_ratio,
                prev_cp=cp_state,
                payments=payments,
                total_interest=total_interest,
            )
            cp_state = {pid: per[pid]["cumulativePrincipal"] for pid in payer_ids_all}
            next_ratio = {pid: per[pid]["ratio"] for pid in payer_ids_all}

        m["computed"] = {
            "totalInterest": _round(total_interest, CURRENCY_DP),
            "perPayer": _round_per_payer(per),
        }
        prev_ratio = next_ratio

    # Update loan remaining principals based on payments
    if state.get("loans"):
        loan_paid: dict[str, float] = {ln["id"]: 0.0 for ln in state["loans"]}
        for m in months:
            for ld in m.get("loanDetails", []) or []:
                lid = ld.get("loanId")
                if lid in loan_paid:
                    loan_paid[lid] += float(ld.get("principal", 0.0))
        for ln in state["loans"]:
            original = float(ln.get("originalAmount", 0.0))
            ln["remainingPrincipal"] = _round(
                max(0.0, original - loan_paid.get(ln["id"], 0.0)), CURRENCY_DP
            )

    return state


# ----- High-level mutation helpers -----------------------------------------


def add_payer(state: dict, name: str, start_month: str | None = None) -> dict:
    payers = state.setdefault("payers", [])
    next_id = f"p{len(payers) + 1}"
    # Ensure unique
    used = {p["id"] for p in payers}
    while next_id in used:
        next_id = f"p{int(next_id[1:]) + 1}"
    payer = {"id": next_id, "name": name, "startMonth": start_month}
    payers.append(payer)
    recompute_all(state)
    return payer


def add_loan(state: dict, name: str, original_amount: float, remaining_principal: float) -> dict:
    loans = state.setdefault("loans", [])
    next_id = f"l{len(loans) + 1}"
    used = {ln["id"] for ln in loans}
    while next_id in used:
        next_id = f"l{int(next_id[1:]) + 1}"
    loan = {
        "id": next_id,
        "name": name,
        "originalAmount": float(original_amount),
        "remainingPrincipal": float(remaining_principal),
    }
    loans.append(loan)
    return loan


def delete_payer(state: dict, payer_id: str, strategy: str) -> None:
    payers = state.get("payers", [])
    if payer_id not in {p["id"] for p in payers}:
        raise ValueError(f"unknown payer {payer_id}")
    if strategy not in {"merge", "delete"}:
        raise ValueError("strategy must be merge or delete")

    months = state.get("months", [])

    if strategy == "merge" and months:
        # Find current ratio of remaining payers from last month
        last = months[-1]
        per = last.get("computed", {}).get("perPayer", {})
        remaining_ids = [p["id"] for p in payers if p["id"] != payer_id]
        weights = {pid: per.get(pid, {}).get("ratio", 0.0) for pid in remaining_ids}
        wsum = sum(weights.values())
        if wsum <= 0:
            # equal weights fallback
            n = len(remaining_ids)
            weights = {pid: (1.0 / n if n else 0.0) for pid in remaining_ids}
            wsum = 1.0 if n else 0.0
        # For each month, take deleted payer's payment and distribute to remaining payers
        for m in months:
            pps = m.get("payerPayments", []) or []
            deleted_amt = 0.0
            kept = []
            for pp in pps:
                if pp["payerId"] == payer_id:
                    deleted_amt += float(pp.get("amount", 0.0))
                else:
                    kept.append(pp)
            if deleted_amt and wsum > 0:
                kept_map = {pp["payerId"]: pp for pp in kept}
                for pid, w in weights.items():
                    extra = deleted_amt * (w / wsum)
                    if pid in kept_map:
                        kept_map[pid]["amount"] = float(kept_map[pid].get("amount", 0.0)) + extra
                    else:
                        kept.append({"payerId": pid, "amount": extra})
            m["payerPayments"] = kept
            if m.get("manualRatios"):
                mr = dict(m["manualRatios"])
                deleted_r = float(mr.pop(payer_id, 0.0))
                if deleted_r and wsum > 0:
                    for pid, w in weights.items():
                        mr[pid] = float(mr.get(pid, 0.0)) + deleted_r * (w / wsum)
                m["manualRatios"] = mr
        # Distribute downpayment too
        dp = state.get("downpayment")
        if dp:
            contribs = dp.get("contributions", []) or []
            deleted_amt = 0.0
            kept = []
            for c in contribs:
                if c["payerId"] == payer_id:
                    deleted_amt += float(c.get("amount", 0.0))
                else:
                    kept.append(c)
            if deleted_amt and wsum > 0:
                kept_map = {c["payerId"]: c for c in kept}
                for pid, w in weights.items():
                    extra = deleted_amt * (w / wsum)
                    if pid in kept_map:
                        kept_map[pid]["amount"] = float(kept_map[pid]["amount"]) + extra
                    else:
                        kept.append({"payerId": pid, "amount": extra})
            dp["contributions"] = kept
    else:
        # delete strategy — drop all the payer's data
        for m in months:
            m["payerPayments"] = [
                pp for pp in (m.get("payerPayments") or []) if pp["payerId"] != payer_id
            ]
            if m.get("manualRatios"):
                m["manualRatios"] = {
                    k: v for k, v in m["manualRatios"].items() if k != payer_id
                }
        dp = state.get("downpayment")
        if dp:
            dp["contributions"] = [
                c for c in (dp.get("contributions") or []) if c["payerId"] != payer_id
            ]

    state["payers"] = [p for p in payers if p["id"] != payer_id]
    recompute_all(state)


def delete_loan(state: dict, loan_id: str, strategy: str, target_id: str | None = None) -> None:
    loans = state.get("loans", [])
    if loan_id not in {ln["id"] for ln in loans}:
        raise ValueError(f"unknown loan {loan_id}")
    if strategy not in {"merge", "delete"}:
        raise ValueError("strategy must be merge or delete")

    months = state.get("months", [])
    if strategy == "merge":
        if not target_id or target_id == loan_id:
            raise ValueError("merge strategy requires targetId")
        if target_id not in {ln["id"] for ln in loans}:
            raise ValueError(f"unknown target loan {target_id}")
        for m in months:
            details = m.get("loanDetails") or []
            target = None
            others = []
            merged_int = 0.0
            merged_prin = 0.0
            for ld in details:
                if ld["loanId"] == loan_id:
                    merged_int += float(ld.get("interest", 0.0))
                    merged_prin += float(ld.get("principal", 0.0))
                elif ld["loanId"] == target_id:
                    target = ld
                else:
                    others.append(ld)
            if target is None:
                target = {"loanId": target_id, "interest": 0.0, "principal": 0.0}
                others.append(target)
            target["interest"] = float(target.get("interest", 0.0)) + merged_int
            target["principal"] = float(target.get("principal", 0.0)) + merged_prin
            if target not in others:
                others.append(target)
            m["loanDetails"] = others
    else:
        for m in months:
            m["loanDetails"] = [
                ld for ld in (m.get("loanDetails") or []) if ld["loanId"] != loan_id
            ]
    state["loans"] = [ln for ln in loans if ln["id"] != loan_id]
    recompute_all(state)
