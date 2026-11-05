"""Run the labelled fixture. --grok makes paid policy calls, still simulator only."""
import argparse
import json

import httpx

from .graph import Recovery
from .policy import GrokPolicy
from .simulator import ScriptedPolicy, Simulator, fixture_mission


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grok", action="store_true", help="Use XAI_API_KEY with Grok 4.7")
    args = parser.parse_args()
    with httpx.Client(follow_redirects=False) as client:
        policy = GrokPolicy.from_env(client) if args.grok else ScriptedPolicy()
        result = Recovery(Simulator(), policy, now=lambda: 100.0).run(fixture_mission())
    print(json.dumps({"mode": "LABELLED_SIMULATOR_FIXTURE", "policy": "grok" if args.grok else "scripted",
                      "phase": result["phase"], "error": result.get("error"), "events": result["events"]}, indent=2))
    raise SystemExit(0 if result["phase"] == "COMPLETE" else 1)


if __name__ == "__main__":
    main()
