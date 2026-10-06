"""
CoGNETs Swarm Arena - Live Tournament Launcher
Run in competitive mode (60 rounds with network chaos) or practice mode (40 rounds).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path
import threading

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "agent-template"))

from arena.config import ScenarioConfig
from arena.main import create_app
import uvicorn

from baselines.bot import naive_max, even_split, proportional
from strategy import decide_bid
from runner import run_strategy


def start_arena(scenario_name: str, port: int = 8080):
    config = ScenarioConfig.load(scenario_name)
    # Give a short start delay so all bots connect before round 1 begins
    config.start_delay_seconds = 5.0
    app = create_app(config)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def run_bot(name: str, strategy_fn, port: int = 8080):
    time.sleep(2.0)  # wait for arena boot
    run_strategy(strategy_fn, f"http://127.0.0.1:{port}", f"bot-{name}")


def run_my_agent(port: int = 8080):
    time.sleep(2.5)  # wait for arena boot
    team_name = os.environ.get("TEAM_NAME", "team-cogni-vitor")
    run_strategy(decide_bid, f"http://127.0.0.1:{port}", team_name)


def main():
    parser = argparse.ArgumentParser(description="Run the live tournament.")
    parser.add_argument(
        "scenario",
        nargs="?",
        default="graded",
        choices=["graded", "practice"],
        help="Choose scenario: 'graded' (official competitive 60 rounds) or 'practice' (40 rounds)",
    )
    parser.add_argument(
        "--mode",
        default="predator",
        choices=["predator", "guardian"],
        help="Strategy mode: 'predator' (max score) or 'guardian' (swarm welfare)",
    )
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default 8080)")
    args = parser.parse_args()

    os.environ["SWARM_MODE"] = args.mode
    mode_label = "COMPETITIVO OFICIAL (Graded)" if args.scenario == "graded" else "TREINO (Practice)"
    rounds_count = 60 if args.scenario == "graded" else 40

    print("=" * 70)
    print(f"  🏆 CoGNETs Swarm Arena — MODO {mode_label}")
    print(f"  • Rondas Totais: {rounds_count} rondas (4.0s por ronda)")
    if args.scenario == "graded":
        print(f"  • Injeção de Falhas de Rede: ATIVA (testes reais de resiliência 503/429)")
    print(f"  • Placar ao Vivo: http://localhost:{args.port}/")
    print("=" * 70)

    # Clean previous tournament's CogniSense feed
    cogni_path = REPO_ROOT / "arena" / "static" / "cognisense.json"
    try:
        cogni_path.parent.mkdir(parents=True, exist_ok=True)
        cogni_path.write_text("[]", encoding="utf-8")
    except Exception:
        pass

    # 1. Start Arena
    t_arena = threading.Thread(target=start_arena, args=(args.scenario, args.port), daemon=True)
    t_arena.start()

    # 2. Start the 3 opponents
    for name, fn in [("naive-max", naive_max), ("even-split", even_split), ("proportional", proportional)]:
        t = threading.Thread(target=run_bot, args=(name, fn, args.port), daemon=True)
        t.start()

    # 3. Start our agent
    t_my = threading.Thread(target=run_my_agent, args=(args.port,), daemon=True)
    t_my.start()

    # Open browser
    time.sleep(1.5)
    try:
        webbrowser.open(f"http://localhost:{args.port}")
    except Exception:
        pass

    print(f"\nTorneio a decorrer! Abre http://localhost:{args.port} no teu navegador.")
    print("Pressiona Ctrl+C neste terminal para terminar o jogo a qualquer momento.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nTorneio terminado.")


if __name__ == "__main__":
    main()
