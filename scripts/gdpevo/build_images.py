"""Build only nvh-gdpevo-prefixed images from positive-list contexts."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from gdpevo import GROUPS, ROOT
from gdpevo.services import stage_service


def build(groups, directory):
    directory.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, "BUILDX_CONFIG": str(directory / "buildx")}

    def image(name, dockerfile, context):
        with (directory / f"{name}.log").open("w") as log:
            subprocess.run(
                [
                    "docker",
                    "build",
                    "-t",
                    f"nvh-gdpevo-{name}:v1",
                    "-f",
                    str(dockerfile),
                    str(context),
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        print(f"Built nvh-gdpevo-{name}:v1", flush=True)

    spec = ROOT / "docker/gdpevo"
    image("base", spec / "base.Dockerfile", spec)
    for name in ("solver", "gateway"):
        image(name, spec / f"{name}.Dockerfile", spec)
    for group in groups:
        context = directory / f"service-{group:03}"
        stage_service(group, context)
        shutil.copyfile(spec / "service.Dockerfile", context / "Dockerfile")
        image(f"service-{group:03}", context / "Dockerfile", context)
    names = ["base", "solver", "gateway"] + [f"service-{g:03}" for g in groups]
    metadata = json.loads(
        subprocess.check_output(
            [
                "docker",
                "image",
                "inspect",
                *[f"nvh-gdpevo-{n}:v1" for n in names],
            ],
            text=True,
        )
    )
    (directory / "images.json").write_text(json.dumps(metadata, indent=2))
    lock = {"platform": "linux/amd64", "images": {}}
    lock_path = spec / "images.lock.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
    for info in metadata:
        for tag in info["RepoTags"]:
            if tag.startswith("nvh-gdpevo-"):
                lock["images"][tag] = info["Id"]
    lock_path.write_text(json.dumps(lock, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groups", type=int, nargs="+", default=GROUPS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.groups, args.output.resolve())
