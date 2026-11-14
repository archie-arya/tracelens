from __future__ import annotations

import argparse
import os


def main() -> None:
    p = argparse.ArgumentParser("tracelens-server")
    p.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    p.add_argument("--reload", action="store_true")
    a = p.parse_args()
    import uvicorn
    uvicorn.run("tracelens_server.main:app", host=a.host, port=a.port, reload=a.reload, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
