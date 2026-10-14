#!/usr/bin/env python3
"""Exit 0 for ReturnRadar, 10 for a free port, 20 for another/unhealthy service."""
import json
import socket
import sys
from urllib.request import urlopen


def status(kind):
    port=8000 if kind=="api" else 5173
    try:
        with socket.create_connection(("127.0.0.1",port),timeout=1):
            pass
    except ConnectionRefusedError:
        return 10
    except OSError:
        return 20
    try:
        if kind=="api":
            with urlopen(f"http://127.0.0.1:{port}/openapi.json",timeout=2) as response:
                if response.status!=200 or response.geturl()!=f"http://127.0.0.1:{port}/openapi.json":
                    return 20
                schema=json.load(response)
            required={"/api/chat","/api/ai-status","/api/auth/session","/api/connections"}
            valid=schema.get("info",{}).get("title")=="ReturnRadar" and required.issubset(schema.get("paths",{}))
        elif kind=="web":
            with urlopen(f"http://127.0.0.1:{port}/",timeout=2) as response:
                valid=response.status==200 and response.geturl()==f"http://127.0.0.1:{port}/" and "<title>ReturnRadar" in response.read(100_000).decode()
        elif kind=="proxy":
            with urlopen(f"http://127.0.0.1:{port}/api/config",timeout=2) as response:
                data=json.load(response)
                valid=response.status==200 and data.get("auth_provider") in ["local","firebase"] and isinstance(data.get("firebase"),dict)
        else:
            return 20
        return 0 if valid else 20
    except Exception:
        return 20


if __name__=="__main__":
    sys.exit(status(sys.argv[1]))
