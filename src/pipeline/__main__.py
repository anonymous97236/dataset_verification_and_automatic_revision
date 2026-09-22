"""Run an episode or a single step with ``python -m pipeline``."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from utils.configuration import load_dotenv, repository_root
from .runner import EndToEndPipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Actor–Arbiter–Gatekeeper pipeline.")
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--step-index", type=int, help="Zero-based step index; omit to run the whole episode.")
    parser.add_argument("--output-id", help="Output snapshot to update; omit to create a new output.")
    args = parser.parse_args()
    try:
        errors = load_dotenv(repository_root() / ".env")
        if errors:
            raise ValueError("; ".join(errors))
        pipeline = EndToEndPipeline()
        results = (pipeline.run_episode(args.episode_id, output_id=args.output_id)
                   if args.step_index is None else
                   [pipeline.run_step(args.episode_id, args.step_index, output_id=args.output_id)])
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")
    print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2, default=str))
    return int(any(result.status == "failed" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
