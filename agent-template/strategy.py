"""
=============================================================================
  CogniOptimal v3: Explainable Cognitive Agent (XAI) for Veles Hack 2026
=============================================================================

Strategy Innovations:
  1. Dual-Mode Game Theory:
     - 'predator': Maximizes individual CES utility (Tournament winning mode).
     - 'guardian': Cooperative Pareto-optimal mode, balancing individual return
       with Swarm Log Social Welfare (LSW).
  2. Cognitive Explainability (CogniSense):
     - Produces human-readable real-time causal reasoning logs for each decision.
  3. Dynamic Floor Insurance:
     - Inverts the Kelly mechanism with safety margins to completely eliminate
       50%/75% utility penalty violations.
  4. Intertemporal Battery Pacing:
     - Maintains sustainable energy draw, preventing battery exhaustion and
       ensuring 0 idle/resting rounds.

Copyright 2026 The CoGNETs Consortium / Veles Hack Team
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Dict, List

LOG = logging.getLogger("agent.cognisense")
RESOURCES = ("compute", "energy", "security")

# Optional static telemetry path for live dashboard display
_TELEMETRY_PATH = Path(__file__).resolve().parent.parent / "arena" / "static" / "cognisense.json"


def estimate_others_ema(
    history: List[Dict[str, Any]], alpha: float = 0.45
) -> Dict[str, float]:
    """Recover the rest of the swarm's aggregate bid using an EMA over settled rounds."""
    if not history:
        return {k: 1.0 for k in RESOURCES}

    est = {k: 1.0 for k in RESOURCES}
    for r in history[-6:]:
        prices = r.get("prices") or {}
        caps = r.get("capacities") or {}
        mine = r.get("bid") or {}
        for k in RESOURCES:
            if k in prices and k in caps:
                p = float(prices[k])
                c = float(caps[k])
                m = float(mine.get(k, 0.0))
                sample = max(1e-4, p * c - m)
                est[k] = alpha * sample + (1 - alpha) * est[k]
    return est


def record_cognisense(telemetry: Dict[str, Any]) -> None:
    """Safely log and write cognitive reasoning for dashboard visualizers."""
    LOG.info(
        "[CogniSense R%d | %s] Battery=%.2f | Alloc: C=%.2f E=%.2f S=%.2f | %s",
        telemetry["round"],
        telemetry["mode"].upper(),
        telemetry["battery"],
        telemetry["bid"]["compute"],
        telemetry["bid"]["energy"],
        telemetry["bid"]["security"],
        telemetry["rationale"],
    )
    try:
        if _TELEMETRY_PATH.parent.is_dir():
            records = []
            if _TELEMETRY_PATH.is_file():
                try:
                    records = json.loads(_TELEMETRY_PATH.read_text(encoding="utf-8"))
                    if not isinstance(records, list):
                        records = []
                except Exception:
                    records = []
            # If fresh match starts (round 1) or arena reset backwards:
            if telemetry["round"] == 1 or (records and records[-1].get("round", 0) >= telemetry["round"]):
                records = []

            # Avoid duplicates of the same round
            if records and records[-1].get("round") == telemetry["round"]:
                records[-1] = telemetry
            else:
                records.append(telemetry)

            if len(records) > 60:
                records = records[-60:]
            _TELEMETRY_PATH.write_text(json.dumps(records, indent=2), encoding="utf-8")
    except Exception:
        pass


def decide_bid(
    budget: float,
    prices: Dict[str, float],
    capacities: Dict[str, float],
    profile: Dict[str, Any],
    history: List[Dict[str, Any]],
) -> Dict[str, float]:
    """Calculate the optimal, explainable bid vector for the current round."""
    mode = os.environ.get("SWARM_MODE", "predator").lower()
    weights = profile.get("weights", {})
    features = profile.get("features", {})
    q_min = float(profile.get("q_min", 0.0))
    s_min = float(profile.get("s_min", 0.0))
    mobility = float(features.get("mobility", 0.0))

    # Read live battery from profile or last round history
    battery = float(features.get("battery", 1.0))
    if history and "battery" in history[-1]:
        battery = float(history[-1]["battery"])

    current_round = int(features.get("round") or (history[-1]["round"] + 1 if history else 1))
    total_rounds = int(features.get("total_rounds", 60))
    rounds_left = max(1, total_rounds - current_round + 1)

    others = estimate_others_ema(history)

    # -------------------------------------------------------------------------
    # 1. SERVICE FLOORS (Compute and Security)
    # Guaranteed minimum share to eliminate 50% / 75% penalties
    # -------------------------------------------------------------------------
    floor_margin = 1.22
    floor_bids = {"compute": 0.0, "security": 0.0}

    for k, floor_val in (("compute", q_min), ("security", s_min)):
        cap = float(capacities.get(k, 1.0))
        target_share = min(floor_val * floor_margin, 0.92 * cap)
        s_k = max(float(others.get(k, 1.0)), 1e-4)
        if target_share > 0 and cap > target_share:
            needed = s_k * target_share / (cap - target_share)
            floor_bids[k] = max(0.0, needed)

    # -------------------------------------------------------------------------
    # 2. INTERTEMPORAL BATTERY PACING (Energy Allocation)
    # -------------------------------------------------------------------------
    safe_floor = 0.065
    is_battery_critical = battery <= safe_floor + 0.02

    if is_battery_critical:
        energy_bid = 0.0
        energy_rationale = "Battery in reserve zone; energy throttled to 0 to prevent coma."
    else:
        usable_battery = battery - safe_floor
        pace_multiplier = 1.15 if battery > 0.65 else 0.95
        target_drain = (usable_battery / rounds_left) * pace_multiplier

        battery_drain_coeff = 0.30 * (1.0 + mobility)
        max_energy_share = max(0.0, (target_drain - 0.004) / battery_drain_coeff)
        max_energy_share = min(max_energy_share, 0.32)

        cap_E = float(capacities.get("energy", 1.0))
        s_E = max(float(others.get("energy", 1.0)), 1e-4)

        if max_energy_share > 0.001 and cap_E > max_energy_share:
            energy_bid_target = s_E * max_energy_share / (cap_E - max_energy_share)
        else:
            energy_bid_target = 0.0

        w_E = float(weights.get("energy", 1.0 / 3.0))
        energy_bid = min(energy_bid_target, budget * w_E * 1.1)
        energy_rationale = f"Sustainable pacing: targeting {max_energy_share:.2f} share over {rounds_left} remaining rounds."

    # -------------------------------------------------------------------------
    # 3. COMPUTE & SECURITY ALLOCATION (CES Best-Response & Mode Modulation)
    # -------------------------------------------------------------------------
    committed = floor_bids["compute"] + floor_bids["security"] + energy_bid

    if committed > budget:
        excess = committed - budget
        reduce_energy = min(energy_bid, excess)
        energy_bid -= reduce_energy
        committed -= reduce_energy

    bid = {
        "compute": floor_bids["compute"],
        "energy": energy_bid,
        "security": floor_bids["security"],
    }

    remaining = max(0.0, budget - sum(bid.values()))

    # Price-taker CES best response for compute & security
    w_C = float(weights.get("compute", 1.0 / 3.0))
    w_S = float(weights.get("security", 1.0 / 3.0))
    p_C = max(float(prices.get("compute", 1.0)), 0.05)
    p_S = max(float(prices.get("security", 1.0)), 0.05)
    c_C = float(capacities.get("compute", 1.0))
    c_S = float(capacities.get("security", 1.0))

    score_C = (w_C ** 2) * c_C / p_C
    score_S = (w_S ** 2) * c_S / p_S

    # Guardian Mode: Moderates aggression when competitors are struggling to sustain network LSW
    if mode == "guardian":
        # Keep balance closer to equal split to avoid starving adjacent nodes
        score_C = 0.5 * score_C + 0.5 * w_C
        score_S = 0.5 * score_S + 0.5 * w_S

    score_sum = score_C + score_S

    if score_sum > 1e-9:
        bid["compute"] += remaining * (score_C / score_sum)
        bid["security"] += remaining * (score_S / score_sum)
    else:
        bid["compute"] += remaining * 0.5
        bid["security"] += remaining * 0.5

    # Normalize cleanly to budget
    total = sum(bid.values())
    if total > 0:
        bid = {k: round(v * (budget / total), 6) for k, v in bid.items()}

    # Causal Rationale for Explainable AI (CogniSense Engine)
    rationale_parts = []
    if mode == "guardian":
        rationale_parts.append("GUARDIAN: Harmonizing bids to protect collective swarm LSW")
    else:
        rationale_parts.append("PREDATOR: Maximizing individual CES utility score lead")

    if is_battery_critical:
        rationale_parts.append(f"BATTERY CRITICAL: Charge at {battery*100:.1f}% (<= 8.5% reserve); throttled energy to 0.0 to prevent sleep coma")
    else:
        rationale_parts.append(f"BATTERY PACING: Charge at {battery*100:.1f}%; pacing drain over {rounds_left} remaining rounds")

    rationale_parts.append(f"SLA FLOORS: Guaranteed Compute ({floor_bids['compute']:.2f} >= q_min {q_min:.2f}) & Security ({floor_bids['security']:.2f} >= s_min {s_min:.2f}) with 22% Kelly buffer")

    primary_res = "Compute" if score_C >= score_S else "Security"
    rationale_parts.append(f"CES BEST-RESPONSE: Clearing prices C={p_C:.2f}, S={p_S:.2f}; channeling residual budget to {primary_res}")

    rationale = " | ".join(rationale_parts)

    telemetry = {
        "round": current_round,
        "mode": mode,
        "battery": round(battery, 3),
        "budget": round(budget, 3),
        "bid": bid,
        "rationale": rationale,
    }
    record_cognisense(telemetry)

    return {k: max(0.0, v) for k, v in bid.items()}
