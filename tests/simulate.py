"""
Fast in-process simulator to evaluate bidding strategies against the three baseline bots.
Runs without network or docker overhead in milliseconds across multiple seeds.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "agent-template"))

from arena.config import ScenarioConfig
from arena.state import Arena
from baselines.bot import naive_max, even_split, proportional
from strategy import decide_bid


def run_single_tournament(
    strategy_fn: Callable,
    scenario_name: str = "graded",
    seed: int = 20261006,
    team_name: str = "my-team",
) -> Dict[str, Any]:
    config = ScenarioConfig.load(scenario_name)
    config.seed = seed
    # Avoid real wall-clock timeouts during in-memory simulation
    config.start_delay_seconds = 0.0
    config.autostart = True

    arena = Arena(config)

    # Register our team
    my_node = arena.register(team_name)

    # Register baseline bots
    bot_nodes = {
        "naive-max": arena.register("bot-naive-max", is_baseline=True),
        "even-split": arena.register("bot-even-split", is_baseline=True),
        "proportional": arena.register("bot-proportional", is_baseline=True),
    }

    # Map strategy functions
    strategies = {
        my_node.node_id: (strategy_fn, my_node),
        bot_nodes["naive-max"].node_id: (naive_max, bot_nodes["naive-max"]),
        bot_nodes["even-split"].node_id: (even_split, bot_nodes["even-split"]),
        bot_nodes["proportional"].node_id: (proportional, bot_nodes["proportional"]),
    }

    for round_num in range(1, config.total_rounds + 1):
        round_state = arena.open_round()

        # Each eligible node submits a bid
        for node_id, (strat, node) in strategies.items():
            if not node.admissible(config.battery_cutoff, config.kappa_bar):
                continue
            
            # Profile with live features
            prof = node.profile()
            prof["features"]["battery"] = node.battery
            prof["features"]["compromise"] = node.compromise
            
            try:
                bid = strat(
                    budget=node.budget,
                    prices=round_state.prices,
                    capacities=round_state.capacities,
                    profile=prof,
                    history=node.history,
                )
            except Exception as e:
                # Fallback to weights
                w = node.weights
                bid = {k: node.budget * float(w.get(k, 1/3)) for k in ("compute", "energy", "security")}
            
            arena.submit(node, round_state.index, bid)

        arena.settle()

    # Collect stats
    results = {}
    for node in arena.nodes.values():
        results[node.team] = {
            "score": round(node.score, 4),
            "rounds_participated": node.rounds_participated,
            "rounds_idle": node.rounds_idle,
            "rounds_missed": node.rounds_missed,
            "floor_violations": node.floor_violations,
            "final_battery": round(node.battery, 4),
        }
    return results


def run_benchmark(strategy_fn: Callable, n_seeds: int = 10, scenario: str = "graded"):
    seeds = [20261006 + i * 17 for i in range(n_seeds)]
    print(f"Running benchmark across {n_seeds} seeds on scenario '{scenario}'...")

    my_scores = []
    naive_scores = []
    even_scores = []
    prop_scores = []
    my_idles = []
    my_floors = []

    for s in seeds:
        res = run_single_tournament(strategy_fn, scenario_name=scenario, seed=s)
        my = res["my-team"]
        naive = res["bot-naive-max"]
        even = res["bot-even-split"]
        prop = res["bot-proportional"]

        my_scores.append(my["score"])
        naive_scores.append(naive["score"])
        even_scores.append(even["score"])
        prop_scores.append(prop["score"])
        my_idles.append(my["rounds_idle"])
        my_floors.append(my["floor_violations"])

    avg_my = sum(my_scores) / n_seeds
    avg_naive = sum(naive_scores) / n_seeds
    avg_even = sum(even_scores) / n_seeds
    avg_prop = sum(prop_scores) / n_seeds
    avg_idle = sum(my_idles) / n_seeds
    avg_floors = sum(my_floors) / n_seeds

    print(f"\n{'Team':<20} | {'Avg Score':<10} | {'Advantage vs Opponent':<20}")
    print("-" * 55)
    print(f"{'My Strategy':<20} | {avg_my:<10.3f} | {'---':<20}")
    print(f"{'bot-proportional':<20} | {avg_prop:<10.3f} | {((avg_my - avg_prop)/avg_prop * 100):+6.1f}%")
    print(f"{'bot-even-split':<20} | {avg_even:<10.3f} | {((avg_my - avg_even)/avg_even * 100):+6.1f}%")
    print(f"{'bot-naive-max':<20} | {avg_naive:<10.3f} | {((avg_my - avg_naive)/avg_naive * 100):+6.1f}%")
    print(f"\nDiagnostics for My Strategy:")
    print(f"  Average idle rounds (battery flat): {avg_idle:.1f} / 60")
    print(f"  Average floor violations:           {avg_floors:.1f}")


if __name__ == "__main__":
    run_benchmark(decide_bid, n_seeds=5)
