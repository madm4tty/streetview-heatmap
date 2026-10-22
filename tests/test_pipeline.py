"""Tests for the batch pipeline (pipeline/). No network or osmium needed."""

import asyncio
import gzip
import os
from datetime import datetime, timezone

import pytest

from pipeline import publish
from pipeline.cache import Lookup, MetadataCache, make_key
from pipeline.export import agreement_by_length, month_index, piece_properties
from pipeline.geo import cumulative_lengths, haversine_m, point_at, sub_line
from pipeline.refresh import refresh
from pipeline.roads import Segment, include_way
from pipeline.sampler import Piece, merge_pieces, sample_bisect, sample_dense
from pipeline.streetview import NO_COVERAGE, NON_GOOGLE, RateLimiter, classify

# A straight east-west road in Leeds, roughly 1 km long
START = (53.8, -1.55)
END = (53.8, -1.535)


def _road(seg_id="r_0", coords=(START, END), highway="residential"):
    return Segment(id=seg_id, highway=highway, name="Test Road", coords=list(coords))


def _resolver_with_change_at(change_m, before="2023-05", after="2019-08"):
    """Fake resolver: imagery date changes `change_m` metres along the road."""
    calls = []

    async def resolve(keys):
        calls.extend(keys)
        out = {}
        for key in keys:
            dist = haversine_m(START, (key[0] / 1e6, key[1] / 1e6))
            out[key] = before if dist < change_m else after
        return out

    return resolve, calls


class TestGeo:
    def test_haversine_known_distance(self):
        # 0.01 degrees of latitude is about 1.11 km
        assert haversine_m((53.0, -1.0), (53.01, -1.0)) == pytest.approx(1112, rel=0.01)

    def test_point_at_midpoint(self):
        coords = [START, END]
        cum = cumulative_lengths(coords)
        lat, lon = point_at(coords, cum, cum[-1] / 2)
        assert lat == pytest.approx(53.8)
        assert lon == pytest.approx(-1.5425)

    def test_point_at_clamps(self):
        coords = [START, END]
        cum = cumulative_lengths(coords)
        assert point_at(coords, cum, -5) == START
        assert point_at(coords, cum, cum[-1] + 5) == END

    def test_sub_line_keeps_inner_vertices(self):
        coords = [(53.8, -1.55), (53.8, -1.545), (53.8, -1.54)]
        cum = cumulative_lengths(coords)
        part = sub_line(coords, cum, 10, cum[-1] - 10)
        assert len(part) == 3
        assert part[1] == coords[1]


class TestCache:
    def test_round_trip_uses_exact_integer_keys(self, tmp_path):
        """The old cache missed because of float rounding; this one must not."""
        cache = MetadataCache(str(tmp_path / "c.sqlite"))
        osm_point = (53.5007292, -1.1246254)  # OSM coordinates have 7 decimal places
        key = make_key(*osm_point, 25)
        cache.put_many([(key, Lookup("OK", "pano1", "2023-05", 53.5, -1.12, "© Google"))])
        assert cache.get_many([make_key(*osm_point, 25)])[key].pano_id == "pano1"

    def test_zero_results_are_cached(self, tmp_path):
        cache = MetadataCache(str(tmp_path / "c.sqlite"))
        key = make_key(53.8, -1.55, 25)
        cache.put_many([(key, Lookup("ZERO_RESULTS"))])
        assert cache.get_many([key]) == {key: Lookup("ZERO_RESULTS")}

    def test_radius_is_part_of_key(self, tmp_path):
        cache = MetadataCache(str(tmp_path / "c.sqlite"))
        cache.put_many([(make_key(53.8, -1.55, 25), Lookup("ZERO_RESULTS"))])
        assert cache.get_many([make_key(53.8, -1.55, 10)]) == {}


class TestClassify:
    def test_google_imagery_gives_month(self):
        assert classify(Lookup("OK", "p", "2023-05", copyright="© Google")) == "2023-05"

    def test_full_date_truncated_to_month(self):
        assert classify(Lookup("OK", "p", "2023-05-17", copyright="© Google")) == "2023-05"

    def test_user_photosphere_is_non_google(self):
        assert classify(Lookup("OK", "p", "2023-05", copyright="© Jane Smith")) == NON_GOOGLE

    def test_zero_results_is_no_coverage(self):
        assert classify(Lookup("ZERO_RESULTS")) == NO_COVERAGE

    def test_persistent_unknown_error_is_no_coverage(self):
        assert classify(Lookup("UNKNOWN_ERROR")) == NO_COVERAGE


class TestRoads:
    def test_excludes_sidewalks_and_proposed(self):
        assert include_way({"highway": "residential"})
        assert include_way({"highway": "footway"})
        assert not include_way({"highway": "footway", "footway": "sidewalk"})
        assert not include_way({"highway": "proposed"})
        assert not include_way({"highway": "pedestrian", "area": "yes"})


class TestSampler:
    def test_dense_sample_count(self):
        road = _road()
        resolve, calls = _resolver_with_change_at(10_000)
        pieces = asyncio.run(sample_dense([road], resolve, spacing=20))
        assert len(calls) == pytest.approx(road.length / 20, abs=1)
        assert len(pieces) == 1 and pieces[0].value == "2023-05"

    def test_bisect_uniform_road_needs_few_calls(self):
        road = _road()
        resolve, calls = _resolver_with_change_at(10_000)
        pieces = asyncio.run(sample_bisect([road], resolve, max_span=400))
        assert len(set(calls)) <= 5
        assert pieces == [Piece("r_0", 0.0, road.length, "2023-05")]

    def test_bisect_finds_change_point(self):
        road = _road()
        resolve, calls = _resolver_with_change_at(600)
        pieces = asyncio.run(sample_bisect([road], resolve, max_span=400, min_span=30))
        assert [p.value for p in pieces] == ["2023-05", "2019-08"]
        assert pieces[0].end_m == pytest.approx(600, abs=30)
        assert pieces[0].start_m == 0.0 and pieces[-1].end_m == pytest.approx(road.length)
        # Far fewer lookups than dense sampling every 20 m (~50)
        assert len(set(calls)) < 20

    def test_bisect_short_segment_single_lookup(self):
        short = _road(coords=(START, (53.8, -1.5497)))  # about 20 m
        resolve, calls = _resolver_with_change_at(10_000)
        pieces = asyncio.run(sample_bisect([short], resolve))
        assert len(set(calls)) == 1
        assert pieces[0].start_m == 0.0 and pieces[0].end_m == pytest.approx(short.length)

    def test_path_uses_tighter_radius(self):
        resolve, calls = _resolver_with_change_at(10_000)
        asyncio.run(sample_bisect([_road(highway="footway")], resolve))
        assert {k[2] for k in calls} == {10}

    def test_merge_pieces_joins_neighbours(self):
        merged = merge_pieces([Piece("a", 0, 10, "x"), Piece("a", 10, 20, "x"),
                               Piece("a", 20, 30, "y")])
        assert merged == [Piece("a", 0, 20, "x"), Piece("a", 20, 30, "y")]


class TestExport:
    def test_month_index(self):
        assert month_index("2023-05") == 2023 * 12 + 4

    def test_properties_for_dated_and_missing(self):
        road = _road()
        assert piece_properties(road, "2023-05")["cov"] == "google"
        assert piece_properties(road, NO_COVERAGE) == {"hw": "residential", "name": "Test Road",
                                                       "cov": "none"}

    def test_agreement_by_length(self):
        dense = [Piece("a", 0, 100, "2023-05"), Piece("a", 100, 200, "2019-08")]
        bisect = [Piece("a", 0, 120, "2023-05"), Piece("a", 120, 200, "2019-08")]
        result = agreement_by_length(dense, bisect)
        assert result["agreement_pct"] == 90.0
        assert result["disagreement_km"] == {"different month": 0.02}


class TestRateLimiter:
    def test_spaces_requests(self):
        async def run():
            limiter = RateLimiter(per_second=200)
            loop = asyncio.get_running_loop()
            start = loop.time()
            await asyncio.gather(*(limiter.wait() for _ in range(21)))
            return loop.time() - start

        # 21 requests at 200/s need at least 20 intervals of 5 ms
        assert asyncio.run(run()) >= 0.095


def _fixed_resolver(value_for_lat):
    """Fake resolver whose value depends only on latitude; records calls."""
    calls = []

    async def resolve(keys):
        calls.extend(keys)
        return {k: value_for_lat(k[0] / 1e6) for k in keys}

    return resolve, calls


class TestRefresh:
    # Two parallel 1 km roads about 1.1 km apart, so in different grid cells
    ROAD_A = (START, END)
    ROAD_B = ((53.81, -1.55), (53.81, -1.535))

    def _previous_run(self, segments):
        resolve, _ = _fixed_resolver(lambda lat: "2019-08")
        return asyncio.run(sample_bisect(segments, resolve))

    def test_nothing_changed_only_checks_sentinels(self):
        segments = [_road("a", self.ROAD_A), _road("b", self.ROAD_B)]
        old = self._previous_run(segments)
        resolve, calls = _fixed_resolver(lambda lat: "2019-08")
        result = asyncio.run(refresh(segments, old, resolve))
        assert result.changed_cells == set() and result.resampled == 0
        assert len(calls) == result.sentinels
        assert result.pieces == old

    def test_new_capture_resamples_only_that_area(self):
        segments = [_road("a", self.ROAD_A), _road("b", self.ROAD_B)]
        old = self._previous_run(segments)
        # Road A (lat 53.80) was re-driven; road B (53.81) was not
        resolve, calls = _fixed_resolver(lambda lat: "2026-09" if lat < 53.805 else "2019-08")
        result = asyncio.run(refresh(segments, old, resolve))
        values = {p.segment_id: p.value for p in result.pieces}
        assert values == {"a": "2026-09", "b": "2019-08"}
        assert result.resampled == 1
        assert all(k[0] < 53_805_000 for k in calls[result.sentinels:])

    def test_new_osm_road_is_sampled(self):
        old = self._previous_run([_road("a", self.ROAD_A)])
        segments = [_road("a", self.ROAD_A), _road("b", self.ROAD_B)]
        resolve, _ = _fixed_resolver(lambda lat: "2019-08")
        result = asyncio.run(refresh(segments, old, resolve))
        assert result.new_or_reshaped == 1
        assert {p.segment_id for p in result.pieces} == {"a", "b"}

    def test_removed_osm_road_is_dropped(self):
        old = self._previous_run([_road("a", self.ROAD_A), _road("b", self.ROAD_B)])
        resolve, _ = _fixed_resolver(lambda lat: "2019-08")
        result = asyncio.run(refresh([_road("a", self.ROAD_A)], old, resolve))
        assert {p.segment_id for p in result.pieces} == {"a"}

    def test_cache_ignores_entries_older_than_cutoff(self, tmp_path):
        cache = MetadataCache(str(tmp_path / "c.sqlite"))
        key = make_key(53.8, -1.55, 25)
        cache.put_many([(key, Lookup("ZERO_RESULTS"))])
        assert cache.get_many([key], fresh_after="2000-01-01 00:00:00") != {}
        assert cache.get_many([key], fresh_after="2999-01-01 00:00:00") == {}


class FakeR2:
    """In-memory stand-in for the boto3 S3 client, recording call order."""

    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.calls = []

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.calls.append(("upload", key))
        with open(path, "rb") as f:
            self.objects[key] = {"Body": f.read(), **(ExtraArgs or {})}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.calls.append(("put", Key))
        self.objects[Key] = {"Body": Body, **kwargs}

    def list_objects_v2(self, Bucket, Prefix):
        return {"Contents": [{"Key": k} for k in self.objects if k.startswith(Prefix)]}

    def delete_object(self, Bucket, Key):
        self.calls.append(("delete", Key))
        del self.objects[Key]

    def download_file(self, bucket, key, path):
        if key not in self.objects:
            exc = Exception("Not Found")
            exc.response = {"Error": {"Code": "404"}}
            raise exc
        with open(path, "wb") as f:
            f.write(self.objects[key]["Body"])


PAGE = '<script>\n  const DEFAULT_TILES = "leeds.pmtiles";\n</script>\n'


def _site(tmp_path, area="leeds"):
    (tmp_path / f"{area}.pmtiles").write_bytes(b"PMTiles")
    (tmp_path / "index.html").write_text(PAGE)
    return str(tmp_path)


class TestPublish:
    NOW = datetime(2026, 10, 22, 10, 45, 30, tzinfo=timezone.utc)

    def test_point_page_at_rewrites_default(self):
        html = publish.point_page_at(PAGE, "tiles/leeds-20261022-104530.pmtiles")
        assert 'const DEFAULT_TILES = "tiles/leeds-20261022-104530.pmtiles";' in html

    def test_point_page_at_requires_marker(self):
        with pytest.raises(ValueError):
            publish.point_page_at("<html></html>", "x.pmtiles")

    def test_shipped_page_has_marker(self):
        path = os.path.join(os.path.dirname(publish.__file__), "web", "index.html")
        with open(path, encoding="utf-8") as f:
            assert "tiles/x.pmtiles" in publish.point_page_at(f.read(), "tiles/x.pmtiles")

    def test_uploads_tiles_before_page(self, tmp_path):
        client = FakeR2()
        key = publish.publish_site(client, "site", "leeds", _site(tmp_path), now=self.NOW)
        assert key == "tiles/leeds-20261022-104530.pmtiles"
        assert client.calls == [("upload", key), ("put", "index.html")]
        assert client.objects[key]["CacheControl"] == publish.IMMUTABLE
        page = client.objects["index.html"]
        assert page["CacheControl"] == "no-cache"
        assert f'"{key}"'.encode() in page["Body"]

    def test_keeps_two_newest_versions_of_this_area_only(self, tmp_path):
        client = FakeR2({k: {"Body": b""} for k in (
            "tiles/leeds-20260801-000000.pmtiles",
            "tiles/leeds-20260901-000000.pmtiles",
            "tiles/leeds-centre-20260101-000000.pmtiles",
        )})
        publish.publish_site(client, "site", "leeds", _site(tmp_path), now=self.NOW)
        assert sorted(k for k in client.objects if k.startswith("tiles/")) == [
            "tiles/leeds-20260901-000000.pmtiles",
            "tiles/leeds-20261022-104530.pmtiles",
            "tiles/leeds-centre-20260101-000000.pmtiles",
        ]

    def test_missing_build_fails_before_uploading(self, tmp_path):
        client = FakeR2()
        with pytest.raises(SystemExit):
            publish.publish_site(client, "site", "leeds", str(tmp_path))
        assert client.calls == []


class TestState:
    def test_round_trip(self, tmp_path):
        cache, pieces = tmp_path / "cache.sqlite", tmp_path / "leeds-bisect.pieces.jsonl"
        cache.write_bytes(b"sqlite bytes" * 100)
        client = FakeR2()
        assert publish.push_state(client, "state", [str(cache), str(pieces)]) == ["cache.sqlite.gz"]

        restored = tmp_path / "restored" / "cache.sqlite"
        assert publish.pull_state(client, "state", [str(restored)]) == [str(restored)]
        assert restored.read_bytes() == cache.read_bytes()

    def test_pull_skips_missing_and_keeps_local(self, tmp_path):
        local = tmp_path / "cache.sqlite"
        local.write_bytes(b"local")
        client = FakeR2({"cache.sqlite.gz": {"Body": gzip.compress(b"remote")}})
        missing = tmp_path / "leeds-bisect.pieces.jsonl"

        assert publish.pull_state(client, "state", [str(local), str(missing)]) == []
        assert local.read_bytes() == b"local"
        assert not missing.exists()

        assert publish.pull_state(client, "state", [str(local)], force=True) == [str(local)]
        assert local.read_bytes() == b"remote"

    def test_pull_reraises_other_errors(self, tmp_path):
        class Broken(FakeR2):
            def download_file(self, bucket, key, path):
                raise PermissionError("denied")

        with pytest.raises(PermissionError):
            publish.pull_state(Broken(), "state", [str(tmp_path / "cache.sqlite")])
