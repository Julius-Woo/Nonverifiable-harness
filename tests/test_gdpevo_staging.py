import json

import pytest

from gdpevo import GROUPS, SOURCE
from gdpevo.services import stage_service
from gdpevo.staging import descriptor, stage_task


@pytest.mark.parametrize("group", GROUPS)
def test_staging_positive_list(tmp_path, group):
    work = tmp_path / "attempt"
    result = stage_task(group, "train", "001", work)
    assert {p.name for p in work.iterdir()} == {
        "input",
        "environment_access.md",
        "task.json",
    }
    upstream = (
        SOURCE
        / "data/task_groups"
        / f"task_group_{group:03}"
        / "train_tasks/001/input"
    )
    assert {
        p.relative_to(work / "input") for p in (work / "input").rglob("*")
    } == {p.relative_to(upstream) for p in upstream.rglob("*")}
    assert "<TASK_ENV_BASE_URL>" not in (work / "input/prompt.txt").read_text()
    assert set(result) == {
        "group",
        "split",
        "task_id",
        "input",
        "answer",
        "state_mode",
        "api",
    }
    assert "rubric" not in json.dumps(result)
    with pytest.raises(FileExistsError):
        stage_task(group, "train", "001", work)


def test_reject_symlink_and_unexpected_input(tmp_path):
    source = tmp_path / "source"
    task = source / "data/task_groups/task_group_013/train_tasks/001/input"
    task.mkdir(parents=True)
    (task / "prompt.txt").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="Unsafe"):
        stage_task(13, "train", "001", tmp_path / "out", source=source)
    (task / "prompt.txt").unlink()
    (task / "prompt.txt").write_text("prompt")
    (task / "secret.txt").write_text("unexpected")
    with pytest.raises(ValueError, match="Unexpected"):
        stage_task(13, "train", "001", tmp_path / "out", source=source)


@pytest.mark.parametrize(
    "group,header,field",
    [
        (13, None, "sql"),
        (14, "Authorization", "sql"),
        (16, "X-Clinic-Token", "sql"),
        (17, "X-API-Key", "sql"),
        (19, "X-Task-Token", "query"),
        (20, None, "sql"),
    ],
)
def test_descriptor_contract(group, header, field):
    desc = descriptor(
        SOURCE / "data/task_groups" / f"task_group_{group:03}",
        "http://gateway:8080",
    )
    query = desc["query"]
    assert query["example_body"][field] == "SELECT 1"
    if header:
        assert query["headers"][header]
    if group == 20:
        assert query["example_body"]["token"]
    assert all(
        not any(x in r["path"] for x in ("judge", "reset", "reseed", "health"))
        for r in desc["routes"]
    )


@pytest.mark.parametrize("group", GROUPS)
def test_filtered_service_has_no_grader(tmp_path, group):
    stage_service(group, tmp_path / "service")
    files = list((tmp_path / "service").rglob("*"))
    assert not any(
        any(x in p.name for x in ("judge", "eval", "construction"))
        for p in files
    )
    app = (tmp_path / "service/env/app.py").read_text()
    assert "judge_api" not in app
    assert "/api/judge" not in app
    assert "TASK_ENV_ENABLE_JUDGE" not in app
    compile(app, "app.py", "exec")


@pytest.mark.parametrize(
    "group,split,task",
    [
        (1, "train", "001"),
        (13, "../test", "001"),
        (13, "train", "../001"),
        (13, "train", "006"),
    ],
)
def test_invalid_task_identity(tmp_path, group, split, task):
    with pytest.raises(ValueError):
        stage_task(group, split, task, tmp_path / "attempt")


def test_physical_column_filter_stops_alias_and_union(tmp_path):
    import sqlite3

    stage_service(19, tmp_path / "service")
    database = tmp_path / "service/env/data/licensing.db"
    with sqlite3.connect(database) as db:
        for table, count in (
            ("contractor_applications", 10),
            ("liquor_applications", 9),
            ("alcohol_licensees", 8),
        ):
            columns = [
                r[1] for r in db.execute(f'PRAGMA table_info("{table}")')
            ]
            assert len(columns) == count
            assert "target_group" not in columns
            aliases = ",".join(f"NULL AS safe{i}" for i in range(count + 1))
            with pytest.raises(sqlite3.OperationalError, match="same number"):
                db.execute(
                    f"SELECT {aliases} WHERE 0 UNION ALL SELECT * FROM {table}"
                ).fetchall()
            assert (
                len(db.execute(f"SELECT * FROM {table} LIMIT 1").fetchone())
                == count
            )
    assert b"target_group" not in database.read_bytes()


def test_manifest_removes_nested_task_mappings():
    from gdpevo.services import clean_metadata

    assert clean_metadata(
        {
            "primary_matters": [{"task_id": "train_001", "matter": "A"}],
            "task_relevant_seed_objects": {"test_001": "B"},
            "service": "public",
        }
    ) == {"service": "public"}


@pytest.mark.parametrize("group", [11, 13, 14, 16, 17, 18, 19, 20])
def test_public_projection_preserves_every_business_row(tmp_path, group):
    import sqlite3
    from collections import Counter

    from gdpevo import ROOT

    report = json.loads((ROOT / "data/gdpevo_hidden_columns.json").read_text())
    spec = next(g for g in report["groups"] if g["group"] == group)
    stage_service(group, tmp_path / "service")
    original = SOURCE / f"data/task_groups/task_group_{group:03}/env"
    for database in spec["databases"]:
        with sqlite3.connect(
            f"file:{original / database['path']}?mode=ro", uri=True
        ) as source:
            with sqlite3.connect(
                tmp_path / "service/env" / database["path"]
            ) as staged:
                for table in database["tables"]:
                    columns = ",".join(
                        '"' + c.replace('"', '""') + '"'
                        for c in table["public_columns"]
                    )
                    query = f'SELECT {columns} FROM "{table["table"]}"'
                    expected = Counter(source.execute(query).fetchall())
                    assert (
                        Counter(staged.execute(query).fetchall()) == expected
                    )
                    assert sum(expected.values()) == table["row_count"]
