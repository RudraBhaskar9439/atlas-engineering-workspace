import os
import tempfile
from uuid import uuid4

os.environ["ATLAS_DATA_DIR"] = tempfile.mkdtemp(prefix="atlas-tests-")

import pytest
from fastapi.testclient import TestClient
from atlas.main import app, user as require_user
from atlas import graph as g
from atlas.fixture import seed


@pytest.fixture
def identity():
    tenant = "test_" + uuid4().hex[:16]
    seed(tenant)
    value = {
        "tenant": tenant,
        "uid": "test_owner",
        "role": "owner",
        "name": "Test owner",
        "email": "test@example.com",
    }
    yield value
    from falkordb import FalkorDB

    db = FalkorDB(host=g.DB_HOST, port=g.DB_PORT, password=g.DB_PASSWORD)
    for name in db.list_graphs():
        if name.startswith(
            ("atlas_" + tenant + "_", "atlas_tasks_" + tenant + "_", "code:atlas_" + tenant + "_")
        ):
            db.select_graph(name).delete()


@pytest.fixture
def client():
    tenant = "test_" + uuid4().hex[:16]
    seed(tenant)

    def test_identity(request):
        return {**require_user(request), "tenant": tenant}

    # Preserve the real session/CSRF checks; isolate all graph writes from the live demo.
    from fastapi import Request

    test_identity.__annotations__["request"] = Request
    app.dependency_overrides[require_user] = test_identity
    with TestClient(app, base_url="http://localhost:8001") as client:
        r = client.post("/auth/demo", json={"role": "owner"})
        client.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield client
    app.dependency_overrides.clear()
    from falkordb import FalkorDB

    db = FalkorDB(host=g.DB_HOST, port=g.DB_PORT, password=g.DB_PASSWORD)
    for name in db.list_graphs():
        if name.startswith(
            ("atlas_" + tenant + "_", "atlas_tasks_" + tenant + "_", "code:atlas_" + tenant + "_")
        ):
            db.select_graph(name).delete()
