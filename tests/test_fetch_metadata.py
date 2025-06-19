import requests
import generate_heatmap as gh


def test_fetch_metadata_network_error(monkeypatch, capsys):
    def mock_get(*args, **kwargs):
        raise requests.RequestException('boom')

    monkeypatch.setattr(requests, 'get', mock_get)

    data = gh.fetch_streetview_metadata(1.0, 2.0, 'KEY')
    assert data == {}
    captured = capsys.readouterr()
    assert 'Error fetching Street View metadata' in captured.err
