import json

import pytest

from scripts.gdpevo.run_seed import validate_evidence


def test_evidence_rejects_stale_image_and_missing_group(tmp_path, monkeypatch):
    manifest = {"tree_sha256": "frozen"}
    monkeypatch.setattr(
        "scripts.gdpevo.run_seed.verify_frozen", lambda: manifest
    )
    monkeypatch.setattr(
        "scripts.gdpevo.run_seed.image_ref", lambda _: "current"
    )
    controls = tmp_path / "controls.json"
    controls.write_text(json.dumps({"passed": True, "manifest": manifest}))
    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text(
        json.dumps(
            {
                "passed": True,
                "services": [{"group": 13, "passed": True}],
            }
        )
    )
    folder = tmp_path / "g013"
    folder.mkdir()
    boundary = folder / "boundary.json"
    boundary.write_text(
        json.dumps(
            {
                "containers": [
                    {"Name": "/nvh-gdpevo-test-solver", "Image": "stale"},
                ]
            }
        )
    )
    with pytest.raises(RuntimeError, match="Images changed"):
        validate_evidence(13, controls, acceptance)
    with pytest.raises(RuntimeError, match="cover"):
        validate_evidence(17, controls, acceptance)
    boundary.write_text(
        json.dumps(
            {
                "containers": [
                    {"Name": "/nvh-gdpevo-test-solver", "Image": "current"},
                ]
            }
        )
    )
    validate_evidence(13, controls, acceptance)
    controls.write_text(json.dumps({"passed": False, "manifest": manifest}))
    with pytest.raises(RuntimeError, match="controls"):
        validate_evidence(13, controls, acceptance)
