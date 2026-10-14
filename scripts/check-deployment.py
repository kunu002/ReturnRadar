#!/usr/bin/env python3
"""Local deployment checks. Reads public configuration only; never loads .env."""
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def require(condition, message):
    if not condition:
        raise SystemExit("Deployment check failed: " + message)


def main():
    config = json.loads((ROOT / "firebase.json").read_text())
    hosting = config["hosting"]
    require(hosting["public"] == "dist", "Firebase Hosting must serve the built frontend.")
    require(config["firestore"]["rules"] == "firestore.rules", "Firestore rules must be included.")
    api = hosting["rewrites"][0]
    require(api["source"] == "/api/**" and api["run"].get("pinTag") is True, "The API rewrite must precede the SPA and pin its revision.")
    require(hosting["rewrites"][-1]["destination"] == "/index.html", "The SPA fallback is missing.")
    require("allow read, write: if false" in (ROOT / "firestore.rules").read_text(), "Browser Firestore access must remain denied.")
    docker = (ROOT / "Dockerfile").read_text()
    require("requirements.lock" in docker and "USER radar" in docker, "The container must use pinned requirements and run without root.")
    require("COPY . " not in docker and "COPY .\n" not in docker, "Do not copy the whole workspace into the container.")
    pins = [line for line in (ROOT / "requirements.lock").read_text().splitlines() if line and not line.startswith("#")]
    require(all(re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+(?:; .+)?", line) for line in pins), "Runtime dependency versions must be pinned.")
    for filename in [".gitignore", ".gcloudignore"]:
        contents = (ROOT / filename).read_text()
        require(".env.*" in contents and "!.env.production.example" in contents, filename + " must exclude environment files and allow the public example.")
        require("infra/deploy.env" in contents and "infra/generated" in contents, filename + " must exclude deployment-specific configuration.")
    require((ROOT / ".dockerignore").read_text().startswith("# Allow only runtime"), "Use the Docker build allowlist.")
    # Uploaded Cloud Build source has no .git. In a local checkout also check
    # that ignored credentials/databases have not already been added to Git.
    if (ROOT / ".git").exists() and shutil.which("git"):
        result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
        tracked = result.stdout.decode().split("\0")
        forbidden = [name for name in tracked if name and (
            name.startswith(("data/", "infra/generated/"))
            or name in [".env", "infra/deploy.env", ".firebase.deploy.json"]
            or (Path(name).name.startswith(".env.") and not name.endswith(".example"))
            or name.endswith((".key", ".pem", ".db", ".db-wal", ".db-shm"))
            or "firebase-adminsdk" in Path(name).name or "service-account" in Path(name).name
        )]
        require(not forbidden, "Private/local files are tracked: " + ", ".join(forbidden))
    print("Deployment configuration checks passed. No cloud resources were changed.")


if __name__ == "__main__":
    main()
