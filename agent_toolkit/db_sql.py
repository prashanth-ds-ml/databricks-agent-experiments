"""Shared helper for running SQL against a Databricks warehouse via the
Databricks CLI's `api` command (Statement Execution API), and for copying
local files into a Unity Catalog volume. Used by every script in this
toolkit.

Requires the Databricks CLI (`databricks auth login` to set up a profile)
and a SQL warehouse. Three things are environment-specific and can be
overridden without editing this file:
    DATABRICKS_CLI_PATH     path to the databricks executable
                            (default: whatever "databricks" resolves to
                            on PATH; falls back to the exact path this
                            was developed with if PATH lookup fails)
    DATABRICKS_CLI_PROFILE  ~/.databrickscfg profile name (default: DEFAULT)
    DATABRICKS_WAREHOUSE_ID SQL warehouse to run statements against
                            (default: the one this was developed with --
                            you almost certainly want to override this)
"""

import json
import os
import shutil
import subprocess
import tempfile
import time

DB_EXE = os.environ.get("DATABRICKS_CLI_PATH") or shutil.which("databricks") or (
    r"C:\Users\prash\AppData\Local\Microsoft\WinGet\Packages"
    r"\Databricks.DatabricksCLI_Microsoft.Winget.Source_8wekyb3d8bbwe\databricks.exe"
)
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "461364b1c78f2539")
PROFILE = os.environ.get("DATABRICKS_CLI_PROFILE", "DEFAULT")


def _api(method, path, body=None):
    args = [DB_EXE, "api", method, path, "--profile", PROFILE, "-o", "json"]
    if body is not None:
        fd, tmp = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(body, f)
        args += ["--json", f"@{tmp}"]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"CLI call failed: {result.stdout}\n{result.stderr}")
    return json.loads(result.stdout)


def run_sql(statement, warehouse_id=WAREHOUSE_ID):
    resp = _api(
        "post",
        "/api/2.0/sql/statements",
        {"warehouse_id": warehouse_id, "statement": statement, "wait_timeout": "30s"},
    )
    statement_id = resp["statement_id"]
    while resp["status"]["state"] in ("PENDING", "RUNNING"):
        time.sleep(1)
        resp = _api("get", f"/api/2.0/sql/statements/{statement_id}")
    if resp["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(f"Statement failed: {json.dumps(resp['status'])}\nSQL:\n{statement}")

    columns = [c["name"] for c in resp["manifest"]["schema"]["columns"]]
    rows = resp.get("result", {}).get("data_array", []) or []
    return [dict(zip(columns, row)) for row in rows]


def run_cli(*args):
    """Run an arbitrary databricks CLI subcommand, e.g. run_cli('fs', 'cp', a, b)."""
    result = subprocess.run(
        [DB_EXE, *args, "--profile", PROFILE], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"databricks {' '.join(args)} failed: {result.stdout}\n{result.stderr}")
    return result.stdout


def fs_cp(local_path, volume_path, overwrite=True):
    args = ["fs", "cp", local_path, volume_path]
    if overwrite:
        args.append("--overwrite")
    return run_cli(*args)


if __name__ == "__main__":
    print(run_sql("SELECT 1 AS x"))
