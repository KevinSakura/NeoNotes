"""Refuse a dependency installation plan that downgrades any existing package."""

import importlib.metadata
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from pip._vendor.packaging.version import Version


def main():
    with tempfile.TemporaryDirectory(prefix="neonote-dependency-plan-") as folder:
        report = Path(folder) / "plan.json"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--dry-run",
                "--report",
                str(report),
                *sys.argv[1:],
            ],
            check=True,
        )
        planned = json.loads(report.read_text(encoding="utf-8"))
        conflicts = []
        for item in planned["install"]:
            package = item["metadata"]
            name = package["name"]
            target = package["version"]
            try:
                existing = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                continue
            if Version(target) < Version(existing):
                conflicts.append(f"{name}: {existing} -> {target}")
        if conflicts:
            raise SystemExit(
                "Refusing existing dependency downgrade; review environment options first: "
                + ", ".join(conflicts)
            )
        print("No existing package downgrade in this plan.")


if __name__ == "__main__":
    main()
