"""Read-only, offline comparison of the pinned population and split."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.make_tb2_split import COMMIT, URL, make_split


def compare(population_path, split_path):
    population = json.loads(population_path.read_text())
    committed = json.loads(split_path.read_text())
    assert population["git_commit"] == COMMIT
    assert population["git_url"] == URL
    tasks = population["tasks"]
    assert len(tasks) == len({t["name"] for t in tasks}) == 89
    regenerated = make_split(tasks, committed["seed"])
    assert regenerated == committed, (
        "Population does not reproduce committed split"
    )
    return {
        "population_tasks": len(tasks),
        "population_counts": regenerated["population_counts"],
        "exact_split_and_selected_hashes_match": True,
        "population_sha256": hashlib.sha256(
            population_path.read_bytes()
        ).hexdigest(),
        "split_sha256": hashlib.sha256(split_path.read_bytes()).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--population", type=Path, default=Path("data/tb2_population.json")
    )
    parser.add_argument(
        "--split", type=Path, default=Path("data/tb2_split.json")
    )
    args = parser.parse_args()
    print(json.dumps(compare(args.population, args.split), indent=2))


if __name__ == "__main__":
    main()
