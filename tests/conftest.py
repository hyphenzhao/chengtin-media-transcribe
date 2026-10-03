import os
import tempfile
from pathlib import Path

# Tests never use the deployed database or production encryption key.
TEST_ROOT=Path(tempfile.mkdtemp(prefix='chengtin-test-'))
os.environ['MEDIA_DATA']=str(TEST_ROOT/'data')
os.environ['MEDIA_KEY_FILE']=str(TEST_ROOT/'key')

import pytest
from fastapi.testclient import TestClient
from backend import db
from backend.api import app

@pytest.fixture(autouse=True)
def clean_database():
    with db.transaction() as c:
        c.execute(db.Record.__table__.delete())
        c.execute(db.Event.__table__.delete())
    yield

@pytest.fixture
def client():
    with TestClient(app) as c: yield c

@pytest.fixture
def project(client):
    return client.post('/api/projects').json()
