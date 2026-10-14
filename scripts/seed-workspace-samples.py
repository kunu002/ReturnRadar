#!/usr/bin/env python3
"""Preview or explicitly provision synthetic data for an existing hosted workspace."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="The intended Google Cloud project ID.")
    parser.add_argument("--workspace", required=True, help="Existing workspace ID, verified by its administrator.")
    parser.add_argument("--owner", required=True, help="Existing active Owner/Admin membership ID; this creates no account.")
    parser.add_argument("--apply", action="store_true", help="Create missing samples. The default only previews counts.")
    args = parser.parse_args()
    # Use standard application-default credentials; never load local connector,
    # Firebase-session, Gemini or password secrets for sample provisioning.
    os.environ["GOOGLE_CLOUD_PROJECT"] = args.project
    os.environ["STORAGE_BACKEND"] = "firestore"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.store import store
    from backend.workspace_samples import seed_workspace_samples
    try:
        result = seed_workspace_samples(store, args.workspace, args.owner, apply=args.apply)
    except ValueError as error:
        raise SystemExit(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
