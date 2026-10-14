#!/usr/bin/env python3
"""Explicit local provisioning; passwords are read without echoing or storing a fixture."""
import argparse
import getpass
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1]/".env")
from backend.accounts import provider
from backend.demo_account import create_demo_account
from backend.store import store

parser=argparse.ArgumentParser(description="Create an isolated, synthetic UI test merchant account.")
parser.add_argument("--email",default="reviewer@returnradar.test")
parser.add_argument("--name",default="UI Demo Merchant")
args=parser.parse_args()
if provider()!="local":
    raise SystemExit("This helper creates local accounts. AUTH_PROVIDER must be local.")
try:
    result=create_demo_account(store,args.email,getpass.getpass("Test password (8–15 characters): "),args.name)
except ValueError as error:
    raise SystemExit(str(error))
print(json.dumps(result,indent=2))
if not result["created"]:
    print("Existing test account reused. Its password and saved UI decisions were preserved.")
