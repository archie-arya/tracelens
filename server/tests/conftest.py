import os

os.environ.setdefault("TRACELENS_DATABASE_URL", "sqlite://")
os.environ["TRACELENS_INIT_API_KEY"] = "tl-sk-test-default"
os.environ["TRACELENS_DEMO_KEY"] = "tl-sk-test-demo"
os.environ["TRACELENS_ALERT_INTERVAL"] = "3600"
os.environ["TRACELENS_PUBLIC_DEMO"] = "true"

import pytest
from fastapi.testclient import TestClient

from tracelens_server.main import create_app
from tracelens_server.storage.sql import SQLStorage

KEY = {"Authorization": "Bearer tl-sk-test-default"}
DEMO = {"Authorization": "Bearer tl-sk-test-demo"}


@pytest.fixture
def client(tmp_path):
    url = os.environ.get("TRACELENS_TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/t.db"
    storage = SQLStorage(url)
    if os.environ.get("TRACELENS_TEST_DATABASE_URL"):
        from tracelens_server import db
        db.metadata.drop_all(storage.engine)
    app = create_app(storage, start_background=False)
    with TestClient(app) as c:
        yield c
