"""Minimal helper for running SQL against the Databricks warehouse via the
Databricks CLI's `api` command (Statement Execution API), used by
build_profiles.py. Requires the Databricks CLI and the `academy` profile
configured in ~/.databrickscfg (see the rest of this project for setup).
"""

import json
import os
import subprocess
import tempfile
import time

DB_EXE = (
    r"C:\Users\prash\AppData\Local\Microsoft\WinGet\Packages"
    r"\Databricks.DatabricksCLI_Microsoft.Winget.Source_8wekyb3d8bbwe\databricks.exe"
)
WAREHOUSE_ID = "461364b1c78f2539"
PROFILE = "academy"


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


def run_sql(statement):
    resp = _api(
        "post",
        "/api/2.0/sql/statements",
        {"warehouse_id": WAREHOUSE_ID, "statement": statement, "wait_timeout": "30s"},
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


def fs_cp(local_path, volume_path, overwrite=True):
    args = [DB_EXE, "fs", "cp", local_path, volume_path, "--profile", PROFILE]
    if overwrite:
        args.append("--overwrite")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"fs cp failed: {result.stdout}\n{result.stderr}")


if __name__ == "__main__":
    print(run_sql("SELECT 1 AS x"))
