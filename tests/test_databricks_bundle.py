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
    lakebase = _load(root / "resources/lakebase.yml")["resources"]

    assert bundle["bundle"]["name"] == "a3-docklab"
    assert {"dev", "prod"} <= bundle["targets"].keys()
    assert bundle["targets"]["dev"]["mode"] == "development"
    assert bundle["targets"]["prod"]["mode"] == "production"
    assert "default" not in bundle["variables"]["warehouse_id"]
    assert "smoke" in bundle["scripts"]
    assert {
        "simulation",
        "monte_carlo",
        "session_materialization",
        "risk_sample_materialization",
    } <= resources["jobs"].keys()
    for job in resources["jobs"].values():
        tasks = {task["task_key"]: task for task in job["tasks"]}
        assert "setup_lakehouse" in tasks
        worker = next(task for key, task in tasks.items() if key != "setup_lakehouse")
        assert worker["depends_on"] == [{"task_key": "setup_lakehouse"}]
    assert "a3docklab" in resources["experiments"]
    assert "app_state" in lakebase["database_instances"]
    assert "database_catalogs" not in lakebase
    assert "bootstrap" in bundle["scripts"]
    assert "--bootstrap-only" in bundle["scripts"]["bootstrap"]["content"]
    assert app["source_code_path"] == ".."
    assert {item["name"] for item in app["resources"]} == {
        "sql_warehouse",
        "application_state",
        "simulation_job",
        "monte_carlo_job",
            "session_materialization_job",
            "risk_sample_materialization_job",
            "experiment",
    }


def test_bundle_contains_no_workspace_credentials() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "databricks.yml").read_text(encoding="utf-8")
    assert "token:" not in text
    assert "client_secret:" not in text
    assert "https://" not in text


def test_workspace_jobs_resolve_files_from_the_deployed_source_tree() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("simulation.py", "monte_carlo.py"):
        source = (root / "databricks/jobs" / name).read_text(encoding="utf-8")
        assert "Path(__file__).resolve().parents[2]" in source
        assert "Path.cwd()" not in source
