"""Web API, end to end against recorded Google answers and a SQLite store."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hotelrate.apps.web import create_app
from hotelrate.config import Settings
from hotelrate.report import check_layout
from hotelrate.store import make_engine, session_factory, upgrade
from tests.test_google_rateshop import SAVOY, STRAND_PALACE, TODAY, Recorder
from tests.test_store import _collector


@pytest.fixture
def client(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'web.db'}")
    upgrade(engine)
    settings = Settings(_env_file=None, google_hotels_country="gb", default_currency="GBP")
    app = create_app(settings, factory=session_factory(engine), collector=_collector(Recorder()), today=TODAY)
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()


def _wait_for(client: TestClient, run_id: int) -> dict:
    for _ in range(200):
        run = client.get(f"/api/runs/{run_id}").json()
        if not run["active"]:
            return run
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_page_and_config(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "Rate Shopper" in page.text
    for asset in ("/static/app.js", "/static/chart.js", "/static/app.css"):
        assert client.get(asset).status_code == 200
    assert client.get("/api/config").json()["currency"] == "GBP"


def test_search_lists_candidates(client: TestClient) -> None:
    candidates = client.get("/api/search", params={"q": "The Savoy", "city": "London"}).json()["candidates"]
    assert candidates[0]["name"] == "The Savoy" and candidates[0]["source_id"] == SAVOY


def test_live_hotel_calendar_has_forward_averages(client: TestClient) -> None:
    data = client.get(f"/api/hotels/{SAVOY}/calendar", params={"currency": "GBP"}).json()
    windows = {row["days"]: row for row in data["forward"]}
    assert set(windows) == {7, 30, 60, 90, 180, 365}
    assert windows[365]["dates"] == data["priced_days"] == 300
    assert windows[365]["average"] > 0
    assert len(data["series"]["dates"]) == len(data["series"]["rates"])
    assert data["series"]["dates"][-1] == data["horizon"]


def test_compset_flow(client: TestClient) -> None:
    study = client.post("/api/studies", json={"name": "London Strand", "city": "London", "currency": "gbp",
                                              "calendar_nights": [1], "offers_months": 0})
    assert study.status_code == 201
    study_id = study.json()["id"]
    assert client.post("/api/studies", json={"name": "London Strand", "city": "London",
                                             "currency": "GBP"}).status_code == 400

    # Nothing collected yet: the dashboard is empty but valid.
    empty = client.get(f"/api/studies/{study_id}/dashboard").json()
    assert empty["series"] == {"dates": []} and empty["forward"]["rows"] == []
    assert client.post(f"/api/studies/{study_id}/runs", json={}).status_code == 400

    for source_id, name, role in ((SAVOY, "The Savoy", "target"), (STRAND_PALACE, "Strand Palace", "comp")):
        added = client.post(f"/api/studies/{study_id}/hotels", json={"source_id": source_id, "name": name, "role": role})
        assert added.status_code == 201
    second_target = client.post(f"/api/studies/{study_id}/hotels",
                                json={"source_id": "x", "name": "X", "role": "target"})
    assert second_target.status_code == 400

    started = client.post(f"/api/studies/{study_id}/runs", json={"calendar_only": True})
    assert started.status_code == 202
    run = _wait_for(client, started.json()["run_id"])
    assert run["status"] == "succeeded" and run["counts"]["calendar"] == {"ok": 2}
    assert any("finished" in message for message in run["messages"])

    dash = client.get(f"/api/studies/{study_id}/dashboard").json()
    assert dash["as_of"] == TODAY.isoformat()
    windows = {row[0]: row for row in dash["forward"]["rows"]}
    # Both hotels replay the same calendar, so the target sits exactly at the comp-set median.
    assert windows["Next 365 days"][4] == 0.0 and windows["Next 365 days"][5] == 300
    assert [row[0] for row in dash["forward_hotels"]["rows"]] == ["The Savoy", "Strand Palace"]
    assert dash["forward_hotels"]["total"][0] == "Comp set median"
    series = dash["series"]
    assert series["target"]["name"] == "The Savoy" and len(series["comps"]) == 1
    assert len(series["dates"]) == len(series["median"]) == len(series["target"]["rates"])

    detail = client.get(f"/api/studies/{study_id}").json()
    assert [hotel["role"] for hotel in detail["hotels"]] == ["target", "comp"]
    assert detail["runs"][0]["status"] == "succeeded"
    assert client.get("/api/studies").json()["studies"][0]["hotels"] == 2

    workbook = client.get(f"/api/studies/{study_id}/report.xlsx")
    assert workbook.status_code == 200 and "london-strand" in workbook.headers["content-disposition"]
    assert workbook.content[:2] == b"PK"

    strand_id = detail["hotels"][1]["id"]
    assert client.delete(f"/api/studies/{study_id}/hotels/{strand_id}").status_code == 204
    assert client.delete(f"/api/studies/{study_id}/hotels/{strand_id}").status_code == 404


def test_report_download_follows_house_layout(client: TestClient, tmp_path: Path) -> None:
    study_id = client.post("/api/studies", json={"name": "S", "city": "London", "currency": "GBP",
                                                 "calendar_nights": [1], "offers_months": 0}).json()["id"]
    client.post(f"/api/studies/{study_id}/hotels", json={"source_id": SAVOY, "name": "The Savoy", "role": "target"})
    _wait_for(client, client.post(f"/api/studies/{study_id}/runs", json={"calendar_only": True}).json()["run_id"])
    path = tmp_path / "download.xlsx"
    path.write_bytes(client.get(f"/api/studies/{study_id}/report.xlsx").content)
    assert check_layout(path) == []


def test_locate_is_stored_and_reused(client: TestClient) -> None:
    first = client.post(f"/api/hotels/{STRAND_PALACE}/locate").json()
    assert first["cached"] is False and first["latitude"] == 51.511101 and first["address"].startswith("372 Strand")
    sent = client.get("/api/status").json()["requests_sent"]
    second = client.post(f"/api/hotels/{STRAND_PALACE}/locate").json()
    assert second["cached"] is True and second["latitude"] == first["latitude"]
    assert client.get("/api/status").json()["requests_sent"] == sent

    # A later search shows the stored position for that hotel even though search answers carry none.
    candidates = client.get("/api/search", params={"q": "The Savoy"}).json()["candidates"]
    strand = next(c for c in candidates if c["source_id"] == STRAND_PALACE)
    assert strand["located"] is True and strand["latitude"] == 51.511101
    savoy = next(c for c in candidates if c["source_id"] == SAVOY)
    assert savoy["located"] is False and savoy["latitude"] is None


def test_compset_map_distances_and_nearby_sweep(client: TestClient) -> None:
    study_id = client.post("/api/studies", json={"name": "Map", "city": "London", "currency": "GBP",
                                                 "calendar_nights": [1], "offers_months": 0}).json()["id"]
    # Adding a hotel places it on the map straight away (the replayed prices answer is Strand Palace's).
    added = client.post(f"/api/studies/{study_id}/hotels",
                        json={"source_id": SAVOY, "name": "The Savoy", "role": "target"}).json()
    assert added["latitude"] == 51.511101

    detail = client.get(f"/api/studies/{study_id}").json()
    assert detail["hotels"][0]["distance_km"] == 0.0

    sweep = client.get(f"/api/studies/{study_id}/nearby").json()
    assert sweep["query"] == "hotels near The Savoy"
    ids = [candidate["source_id"] for candidate in sweep["candidates"]]
    assert SAVOY not in ids and STRAND_PALACE in ids

    _wait_for(client, client.post(f"/api/studies/{study_id}/runs", json={"calendar_only": True}).json()["run_id"])
    markers = client.get(f"/api/studies/{study_id}/dashboard").json()["map"]
    assert markers[0]["role"] == "target" and markers[0]["latitude"] == 51.511101
    # The recorded calendar starts on 10 Dec, a month after the test's "today", so only longer windows have prices.
    assert markers[0]["next_30"] is None and markers[0]["next_365"] > 0
    assert markers[0]["distance_km"] == 0.0


def test_status_and_map_config(client: TestClient) -> None:
    status = client.get("/api/status").json()
    assert status["active_runs"] == [] and status["max_in_flight"] == 2
    config = client.get("/api/config").json()
    assert "{z}" in config["map_tile_url"] and "OpenStreetMap" in config["map_tile_attribution"]
    assert config["map_tile_dark_filter"] is True
    for asset in ("/static/map.js", "/static/vendor/leaflet/leaflet.js", "/static/vendor/leaflet/leaflet.css"):
        assert client.get(asset).status_code == 200


def test_events_and_missing_things(client: TestClient) -> None:
    event = client.post("/api/events", json={"city": "London", "event_date": "2026-12-31", "name": "New Year's Eve"})
    assert event.status_code == 201
    assert client.get("/api/studies/999").status_code == 404
    assert client.get("/api/runs/999").status_code == 404
