"""Run one sync from the command line: python -m opect.sync_once [--full]"""

from __future__ import annotations

import argparse
import json
import sys

from .db import Database
from .sync import SyncEngine, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a single OPECT literature sync")
    parser.add_argument("--full", action="store_true", help="ignore the incremental cursor and re-scan the full window")
    parser.add_argument("--source", action="append", help="only these sources (openalex/crossref/pubmed/arxiv)")
    parser.add_argument("--rescore", action="store_true", help="recompute relevance for stored works and prune the rest")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.source:
        for key in cfg["sync"]["sources"]:
            cfg["sync"]["sources"][key] = key in args.source
    if args.full:
        cfg["sync"]["overlap_days"] = cfg["sync"].get("initial_lookback_days", 3650)

    db = Database("data/opect.db")
    if args.rescore:
        from .scoring import RelevanceScorer

        result = db.rescore_all(
            RelevanceScorer(cfg),
            prune_below=float(cfg["relevance"].get("include_threshold", 30)),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    engine = SyncEngine(db, cfg)
    engine._run("cli")
    state = engine.state
    if not args.quiet:
        print(json.dumps(state.stats, ensure_ascii=False, indent=2))
        for line in state.log:
            print("·", line)
    return 0 if state.stage != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
