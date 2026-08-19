"""Offline kamerada surat berilmasligi (B): holat, yosh, stale, 304 tartibi."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from api import create_app
from core import health, snapshots
from core.db import get_db

KEY = {"X-API-Key": "test-kalit"}
IP, PORT = "10.88.0.1", 10554


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def cam(client):
    with get_db() as db:
        db.execute(
            "INSERT INTO cameras (name, region, lat, lng, stream_url, slug, "
            "ip, port, enabled) VALUES ('Snap EP', 'T', 0, 0, '', "
            "'snap_ep_sinov', ?, ?, 1)", (IP, PORT))
        cam_id = db.execute("SELECT id FROM cameras WHERE slug = "
                            "'snap_ep_sinov'").fetchone()[0]
    p = snapshots.path_for("snap_ep_sinov")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xd8sinov-jpeg")
    yield cam_id
    p.unlink(missing_ok=True)
    health._statuses.pop((IP, PORT), None)
    snapshots._done.pop(cam_id, None)
    with get_db() as db:
        db.execute("DELETE FROM cameras WHERE id = ?", (cam_id,))


def test_online_kamera_sarlavhalar_bilan(client, cam):
    health._statuses[(IP, PORT)] = True
    r = client.get(f"/api/v1/cameras/{cam}/snapshot", headers=KEY)
    assert r.status_code == 200
    assert r.headers["x-snapshot-at"].startswith("20")
    assert int(r.headers["x-snapshot-age"]) < 60
    etag = r.headers["etag"]
    # 304 ham sarlavhalar bilan qaytadi
    r2 = client.get(f"/api/v1/cameras/{cam}/snapshot",
                    headers={**KEY, "If-None-Match": etag})
    assert r2.status_code == 304 and "x-snapshot-age" in r2.headers


def test_offline_404_hatto_kesh_bilan(client, cam):
    health._statuses[(IP, PORT)] = True
    etag = client.get(f"/api/v1/cameras/{cam}/snapshot",
                      headers=KEY).headers["etag"]
    health._statuses[(IP, PORT)] = False       # kamera o'chdi
    # oddiy so'rov ham, keshli (If-None-Match) so'rov ham 404 —
    # holat tekshiruvi 304 shoxidan OLDIN turganining isboti
    assert client.get(f"/api/v1/cameras/{cam}/snapshot",
                      headers=KEY).status_code == 404
    r = client.get(f"/api/v1/cameras/{cam}/snapshot",
                   headers={**KEY, "If-None-Match": etag})
    assert r.status_code == 404


def test_stale_eshigi_ochiq(client, cam):
    health._statuses[(IP, PORT)] = False
    r = client.get(f"/api/v1/cameras/{cam}/snapshot?stale=1", headers=KEY)
    assert r.status_code == 200 and "x-snapshot-age" in r.headers


def test_eski_fayl_404_stale_bilan_ochiladi(client, cam):
    health._statuses[(IP, PORT)] = True        # holat "online" deb yolg'on
    p = snapshots.path_for("snap_ep_sinov")
    old = time.time() - snapshots.max_age() - 60
    os.utime(p, (old, old))
    assert client.get(f"/api/v1/cameras/{cam}/snapshot",
                      headers=KEY).status_code == 404
    r = client.get(f"/api/v1/cameras/{cam}/snapshot?stale=1", headers=KEY)
    assert r.status_code == 200
    assert int(r.headers["x-snapshot-age"]) >= snapshots.max_age()
