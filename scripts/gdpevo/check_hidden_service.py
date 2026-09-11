"""Verify alias/compound-query filtering through the real TG019 gateway."""

import asyncio
import json
import shlex
from dataclasses import asdict

from evolution.candidates import atomic_json
from gdpevo import ROOT
from gdpevo.boundary import Attempt


async def main():
    output = ROOT / "logs/gdpevo/t2-hidden-service-260911"
    with Attempt(19, "train", "001", output / "environment") as boundary:
        query = boundary.task["api"]["query"]
        records = []
        for table, count in (
            ("contractor_applications", 10),
            ("liquor_applications", 9),
            ("alcohol_licensees", 8),
        ):
            for old in (True, False):
                aliases = ",".join(
                    f"NULL AS safe{i}" for i in range(count + int(old))
                )
                sql = (
                    f"SELECT {aliases} WHERE 0 UNION ALL SELECT * "
                    f"FROM {table} LIMIT 1"
                )
                command = ["curl", "-fsS", "http://gateway:8080/api/sql"]
                for key, value in query["headers"].items():
                    command += ["-H", f"{key}: {value}"]
                command += ["--data", json.dumps({"query": sql})]
                result = await boundary.exec(shlex.join(command))
                text = result.stdout + result.stderr
                # Old schema fails (HTTP 400); projected schema succeeds.
                passed = (
                    result.return_code != 0
                    if old
                    else (result.return_code == 0 and "safe0" in text)
                )
                passed &= "target_group" not in text
                records.append(
                    {
                        "table": table,
                        "old_column_count": old,
                        "command": shlex.join(command),
                        **asdict(result),
                        "passed": passed,
                    }
                )
        result = {
            "passed": all(r["passed"] for r in records),
            "checks": records,
        }
        atomic_json(output / "checks.json", result)
        print(json.dumps({"passed": result["passed"], "checks": len(records)}))
        if not result["passed"]:
            raise RuntimeError("Hidden-column service regression")


if __name__ == "__main__":
    asyncio.run(main())
