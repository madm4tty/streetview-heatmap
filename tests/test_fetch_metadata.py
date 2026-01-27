import logging
import requests
import generate_heatmap as gh


def test_fetch_metadata_network_error(monkeypatch, caplog):
    """Network errors return empty dict and log warning."""

    def mock_get(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(requests, "get", mock_get)

    with caplog.at_level(logging.WARNING):
        data = gh.fetch_streetview_metadata(1.0, 2.0, "KEY")

    assert data == {}
    assert "Error fetching Street View metadata" in caplog.text


def test_fetch_metadata_success(monkeypatch):
    """Successful response returns JSON data."""

    class MockResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "OK", "date": "2024-01"}

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr(requests, "get", mock_get)

    data = gh.fetch_streetview_metadata(1.0, 2.0, "KEY")
    assert data == {"status": "OK", "date": "2024-01"}
