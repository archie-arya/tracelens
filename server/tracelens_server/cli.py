from __future__ import annotations

import argparse
import os


def main() -> None:
    p = argparse.ArgumentParser("tracelens-server")
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    p.add_argument("--reload", action="store_true")
    p.add_argument("--data-dir", default=None, help="where the SQLite DB lives (default ./data)")
    a = p.parse_args()
    if a.data_dir and not os.environ.get("TRACELENS_DATABASE_URL"):
        os.environ["TRACELENS_DATABASE_URL"] = "sqlite:///" + os.path.join(os.path.abspath(a.data_dir), "tracelens.db")
    print(f"TraceLens -> http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port}  (Ctrl+C to stop)")
    import uvicorn
    uvicorn.run("tracelens_server.main:app", host=a.host, port=a.port, reload=a.reload, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
