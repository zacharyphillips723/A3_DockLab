from pathlib import Path

import yaml


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def test_databricks_bundle_declares_targets_and_platform_resources() -> None:
    root = Path(__file__).resolve().parents[1]
    bundle = _load(root / "databricks.yml")
    app = _load(root / "resources/app.yml")["resources"]["apps"]["mission_replay"]
    resources = _load(root / "resources/jobs.yml")["resources"]

    assert bundle["bundle"]["name"] == "a3-docklab"
    assert {"dev", "prod"} <= bundle["targets"].keys()
    assert bundle["targets"]["dev"]["mode"] == "development"
    assert bundle["targets"]["prod"]["mode"] == "production"
    assert {"simulation", "monte_carlo"} <= resources["jobs"].keys()
    assert "a3docklab" in resources["experiments"]
    assert app["source_code_path"] == ".."
    assert {item["name"] for item in app["resources"]} == {
        "simulation_job",
        "monte_carlo_job",
        "experiment",
    }


def test_bundle_contains_no_workspace_credentials() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "databricks.yml").read_text(encoding="utf-8")
    assert "token:" not in text
    assert "client_secret:" not in text
    assert "https://" not in text
