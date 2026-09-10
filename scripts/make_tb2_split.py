"""Reproduce the pinned difficulty-stratified Terminal-Bench 2 split."""

import argparse
import asyncio
import hashlib
import json
import random
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

from harbor.models.job.config import DatasetConfig
from harbor.models.task.id import GitTaskId

COMMIT = "69671fbaac6d67a7ef0dfec016cc38a64ef7a77c"
URL = "https://github.com/laude-institute/terminal-bench-2.git"
SEED = 260910


def apportion(counts, size):
    """Hamilton largest remainders; lexical difficulty breaks ties."""
    total = sum(counts.values())
    result = {k: v * size // total for k, v in counts.items()}
    order = sorted(counts, key=lambda k: (-(counts[k] * size % total), k))
    for key in order[: size - sum(result.values())]:
        result[key] += 1
    return result


def make_split(tasks, seed=SEED):
    pools = defaultdict(list)
    for task in sorted(tasks, key=lambda t: t["name"]):
        pools[task["difficulty"]].append(task)
    counts = {k: len(v) for k, v in sorted(pools.items())}
    selected_counts = apportion(counts, 30)
    rng = random.Random(seed)
    selected = {}
    for difficulty in sorted(pools):
        rng.shuffle(pools[difficulty])
        selected[difficulty] = pools[difficulty][: selected_counts[difficulty]]
    # Allocate search proportionally, then anchor from the remainder. Sealed
    # receives the remainder, preserving exactly 30 unique selected tasks.
    remaining = dict(selected_counts)
    splits = {}
    for name, size in (("search", 18), ("anchor", 6), ("sealed", 6)):
        quota = apportion(remaining, size)
        rows = []
        for difficulty in sorted(selected):
            count = quota[difficulty]
            rows.extend(selected[difficulty][:count])
            selected[difficulty] = selected[difficulty][count:]
            remaining[difficulty] -= count
        splits[name] = sorted(rows, key=lambda t: t["name"])
    return {
        "dataset_id": "terminal-bench@2.0",
        "git_url": URL,
        "git_commit": COMMIT,
        "seed": seed,
        "stratification_field": "task.toml:metadata.difficulty",
        "algorithm": "Sorted tasks; Python Random(seed) shuffle per lexical "
        "stratum; Hamilton largest remainders for 30, then search, anchor, "
        "sealed. Lexical stratum breaks remainder ties.",
        "population_counts": counts,
        "sample_counts": selected_counts,
        "split_counts": {
            key: dict(sorted(Counter(t["difficulty"] for t in rows).items()))
            for key, rows in splits.items()
        },
        "splits": splits,
    }


async def cached_metadata():
    configs = await DatasetConfig(
        name="terminal-bench", version="2.0"
    ).get_task_configs()
    tasks = []
    for config in configs:
        if config.git_commit_id != COMMIT or config.git_url != URL:
            raise ValueError("Registry does not match the pinned dataset")
        task_id = GitTaskId(
            git_url=URL, git_commit_id=COMMIT, path=config.path
        )
        path = task_id.get_local_path() / "task.toml"
        raw = path.read_bytes()
        metadata = tomllib.loads(raw.decode())["metadata"]
        tasks.append(
            {
                "name": config.path.name,
                "difficulty": metadata["difficulty"],
                "task_toml_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return tasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("data/tb2_split.json")
    )
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    result = make_split(asyncio.run(cached_metadata()), args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("population_counts", "sample_counts", "split_counts")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
