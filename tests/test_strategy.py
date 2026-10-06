"""
Comprehensive tuning and optimization of CogniOptimal strategy.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests.simulate import run_benchmark

RESOURCES = ("compute", "energy", "security")


def estimate_others_smoothed(history: List[Dict[str, Any]], alpha: float = 0.45) -> Dict[str, float]:
    """Exponential moving average estimation of competitor aggregate bids."""
    if not history:
        return {k: 1.0 for k in RESOURCES}

    est = {k: 1.0 for k in RESOURCES}
    # Look over the last 6 rounds
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


def cogni_optimal_v2(
    budget: float,
    prices: Dict[str, float],
    capacities: Dict[str, float],
    profile: Dict[str, Any],
    history: List[Dict[str, Any]],
) -> Dict[str, float]:
    weights = profile.get("weights", {})
    features = profile.get("features", {})
    q_min = float(profile.get("q_min", 0.0))
    s_min = float(profile.get("s_min", 0.0))
    mobility = float(features.get("mobility", 0.0))

    # Read current live battery
    battery = float(features.get("battery", 1.0))
    if history and "battery" in history[-1]:
        battery = float(history[-1]["battery"])

    current_round = len(history) + 1
    total_rounds = 60
    rounds_left = max(1, total_rounds - current_round + 1)

    others = estimate_others_smoothed(history)

    # -------------------------------------------------------------------------
    # 1. SERVICE FLOORS (Compute and Security)
    # Guarantee admissibility and avoid 50% / 75% score annihilation
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
    # 2. BATTERY & ENERGY SIZING
    # Intertemporal optimization: never drop <= 0.05 cutoff
    # -------------------------------------------------------------------------
    cutoff = 0.05
    safe_floor = 0.065
    
    # If battery is low, ZERO energy bid - only pay idle_drain (0.004)
    if battery <= safe_floor + 0.02:
        energy_bid = 0.0
    else:
        # Paced budget of usable battery
        usable_battery = battery - safe_floor
        # Allow slightly higher draw early when battery is full (> 0.8), then smoothly decay
        pace_multiplier = 1.15 if battery > 0.65 else 0.95
        target_drain = (usable_battery / rounds_left) * pace_multiplier

        # drain = 0.30 * x_E * (1 + mobility) + 0.004
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

    # -------------------------------------------------------------------------
    # 3. COMPUTE & SECURITY OPTIMAL ALLOCATION (CES Best-Response)
    # Reallocate saved energy budget into compute and security
    # -------------------------------------------------------------------------
    committed = floor_bids["compute"] + floor_bids["security"] + energy_bid
    
    # If floors + energy exceed budget, scale energy down first to protect floors
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
    score_sum = score_C + score_S

    if score_sum > 1e-9:
        bid["compute"] += remaining * (score_C / score_sum)
        bid["security"] += remaining * (score_S / score_sum)
    else:
        bid["compute"] += remaining * 0.5
        bid["security"] += remaining * 0.5

    # Final budget rescale check
    total = sum(bid.values())
    if total > 0:
        bid = {k: v * (budget / total) for k, v in bid.items()}

    return {k: max(0.0, v) for k, v in bid.items()}


if __name__ == "__main__":
    run_benchmark(cogni_optimal_v2, n_seeds=20)
