"""Tests for the batch pipeline (pipeline/). No network or osmium needed."""

import asyncio
import json
import gzip
import io
import os
from datetime import datetime, timezone

import pytest

from pipeline import areas, audit, measure, overview, publish, run, shards, toplists
from pipeline.cache import Lookup, MetadataCache, make_key
from pipeline.export import (agreement_by_length, month_index, piece_panos, piece_properties,
                             save_pieces, updates_since, write_geojsonseq)
from pipeline.geo import cumulative_lengths, haversine_m, point_at, sub_line
from pipeline.refresh import plan as refresh_plan, refresh
from pipeline.roads import Segment, include_way, is_private
from pipeline.sampler import (PATH_RADIUS_M, ROAD_RADIUS_M, Piece, key_at, merge_pieces,
                              min_bisect_lookups, radius_for, sample_bisect, sample_dense)
from pipeline.streetview import (NO_COVERAGE, NON_GOOGLE, BudgetExceeded, RateLimiter,
                                 StreetViewClient, classify)

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
        return {"Contents": [{"Key": k, "Size": len(v["Body"])}
                             for k, v in self.objects.items() if k.startswith(Prefix)]}

    def delete_object(self, Bucket, Key):
        self.calls.append(("delete", Key))
        del self.objects[Key]

    def _missing(self, key):
        if key not in self.objects:
            exc = Exception("Not Found")
            exc.response = {"Error": {"Code": "NoSuchKey"}}
            raise exc

    def download_file(self, bucket, key, path):
        self._missing(key)
        with open(path, "wb") as f:
            f.write(self.objects[key]["Body"])

    def get_object(self, Bucket, Key):
        self._missing(Key)
        return {"Body": io.BytesIO(self.objects[Key]["Body"])}


PAGE = ('<script>\n  const DEFAULT_TILES = "leeds.pmtiles";\n'
        '  const DEFAULT_OVERVIEW = "leeds.overview.json";\n'
        '  const DEFAULT_HISTORY = "";\n</script>\n')


def _site(tmp_path, area="leeds"):
    (tmp_path / f"{area}.pmtiles").write_bytes(b"PMTiles")
    (tmp_path / "index.html").write_text(PAGE)
    return str(tmp_path)


class TestPublish:
    NOW = datetime(2026, 10, 22, 10, 45, 30, tzinfo=timezone.utc)
    PREFIX = "k7f3q9x2v8m1w4z6"

    def test_point_page_at_rewrites_default(self):
        html = publish.point_page_at(PAGE, "tiles/leeds-20261022-104530.pmtiles")
        assert 'const DEFAULT_TILES = "tiles/leeds-20261022-104530.pmtiles";' in html

    def test_point_page_at_requires_marker(self):
        with pytest.raises(ValueError):
            publish.point_page_at("<html></html>", "x.pmtiles")

    def test_shipped_page_has_marker_and_noindex(self):
        path = os.path.join(os.path.dirname(publish.__file__), "web", "index.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        assert "tiles/x.pmtiles" in publish.point_page_at(html, "tiles/x.pmtiles")
        assert '<meta name="robots" content="noindex, nofollow">' in html

    @pytest.mark.parametrize("prefix", ["", "short", "has/slash-1234567890", "has space 1234567890"])
    def test_site_prefix_rejects_weak_or_unsafe(self, monkeypatch, prefix):
        monkeypatch.setenv("R2_SITE_PREFIX", prefix)
        with pytest.raises(SystemExit):
            publish.site_prefix()

    def test_site_prefix_accepts_random_string(self, monkeypatch):
        monkeypatch.setenv("R2_SITE_PREFIX", self.PREFIX)
        assert publish.site_prefix() == self.PREFIX

    def test_uploads_tiles_before_page_under_prefix(self, tmp_path):
        client = FakeR2()
        page_key = publish.publish_site(client, "site", self.PREFIX, "leeds", _site(tmp_path),
                                        now=self.NOW)
        tiles_key = f"{self.PREFIX}/tiles/leeds-20261022-104530.pmtiles"
        assert page_key == f"{self.PREFIX}/index.html"
        assert client.calls == [("upload", tiles_key), ("put", page_key)]
        assert client.objects[tiles_key]["CacheControl"] == publish.IMMUTABLE
        page = client.objects[page_key]
        assert page["CacheControl"] == "no-cache"
        # The page refers to its tiles relative to itself, without the prefix
        assert b'"tiles/leeds-20261022-104530.pmtiles"' in page["Body"]
        assert self.PREFIX.encode() not in page["Body"]

    def test_keeps_two_newest_versions_of_this_area_only(self, tmp_path):
        p = self.PREFIX
        client = FakeR2({k: {"Body": b""} for k in (
            f"{p}/tiles/leeds-20260801-000000.pmtiles",
            f"{p}/tiles/leeds-20260901-000000.pmtiles",
            f"{p}/tiles/leeds-centre-20260101-000000.pmtiles",
            "otherprefix12345678/tiles/leeds-20260101-000000.pmtiles",
        )})
        publish.publish_site(client, "site", p, "leeds", _site(tmp_path), now=self.NOW)
        assert sorted(k for k in client.objects if "/tiles/" in k) == [
            f"{p}/tiles/leeds-20260901-000000.pmtiles",
            f"{p}/tiles/leeds-20261022-104530.pmtiles",
            f"{p}/tiles/leeds-centre-20260101-000000.pmtiles",
            "otherprefix12345678/tiles/leeds-20260101-000000.pmtiles",
        ]

    def test_missing_build_fails_before_uploading(self, tmp_path):
        client = FakeR2()
        with pytest.raises(SystemExit):
            publish.publish_site(client, "site", self.PREFIX, "leeds", str(tmp_path))
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


class TestMeasure:
    LEEDS = (-1.65, 53.72, -1.40, 53.87)

    @pytest.mark.parametrize("length", [5, 50, 51, 420, 421, 1000, 5000])
    def test_min_lookups_matches_bisect_on_uniform_road(self, length):
        # A road due north from Leeds, `length` metres long, all one value
        end = (53.8 + length / 111_195, -1.55)
        seg = Segment(id="r", highway="residential", name=None, coords=[(53.8, -1.55), end])
        calls = []

        async def resolve(keys):
            calls.extend(keys)
            return {k: "2020-01" for k in keys}

        asyncio.run(sample_bisect([seg], resolve))
        assert len(set(calls)) == min_bisect_lookups(seg.length)

    def test_cell_names(self):
        assert measure.cell_of(53.8, -1.55) == "53N 2W"
        assert measure.cell_of(51.5, 0.1) == "51N 0E"
        assert measure.cell_of(54.6, -5.9) == "54N 6W"

    def test_tally_counts_each_way_once_and_calibrates(self):
        roads = [
            _road("a"),  # in Leeds
            Segment(id="b", highway="footway", name=None,
                    coords=[(54.6, -5.93), (54.6, -5.92)]),  # Belfast
        ]
        result = measure.tally(roads, self.LEEDS)
        assert result["total"]["ways"] == 2
        assert set(result["cells"]) == {"53N 2W", "54N 6W"}
        assert result["cells"]["54N 6W"]["path_km"] == pytest.approx(
            result["cells"]["54N 6W"]["km"])
        assert result["calibration"]["ways"] == 1

        est = measure.estimates(result)
        assert est["lookups_low"] == result["total"]["min_lookups"]
        assert est["lookups_high"] > est["lookups_low"]
        assert "54N 6W" in measure.report_markdown(result, est)


def test_redact_hides_prefix():
    assert publish.redact("k7f3q9x2v8m1w4z6/tiles/x.pmtiles", "k7f3q9x2v8m1w4z6") == "<prefix>/tiles/x.pmtiles"


def test_service_roads_use_tight_radius():
    assert radius_for(_road(highway="service")) == PATH_RADIUS_M
    assert radius_for(_road(highway="footway")) == PATH_RADIUS_M
    assert radius_for(_road(highway="residential")) == ROAD_RADIUS_M


class TestShards:
    def test_each_cell_in_one_shard(self):
        cells = [c for cs in shards.SHARD_CELLS.values() for c in cs]
        assert len(cells) == len(set(cells))
        assert shards.shard_of("53N 2W") == "uk-8"

    def test_unknown_cell_falls_back(self):
        assert shards.shard_of("49N 3W") == shards.FALLBACK_SHARD

    def test_shards_are_areas_on_the_uk_extract(self):
        for name in shards.SHARDS:
            assert areas.get_area(name)["cells"] == set(shards.SHARD_CELLS[name])
            assert areas.extract_url(name).endswith("/europe/united-kingdom-latest.osm.pbf")
        assert areas.extract_url("leeds").endswith(
            "/europe/united-kingdom/england/west-yorkshire-latest.osm.pbf")
        assert areas.extract_name("leeds") == "west-yorkshire"


class TestClient:
    def _client(self, tmp_path, **kwargs):
        client = StreetViewClient("key", MetadataCache(str(tmp_path / "c.sqlite")), **kwargs)
        fetched = []

        async def fake_fetch(session, key, attempts=5):
            fetched.append(key)
            return Lookup("ZERO_RESULTS")

        client._fetch_one = fake_fetch
        return client, fetched

    def test_fetches_in_chunks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(StreetViewClient, "CHUNK", 3)
        client, fetched = self._client(tmp_path)
        keys = [make_key(53.8 + i / 1000, -1.55, 25) for i in range(10)]
        assert len(asyncio.run(client.lookup_many(keys))) == 10
        assert sorted(fetched) == sorted(keys)
        assert len(client.cache.get_many(keys)) == 10

    def test_stale_cache_entries_are_asked_again(self, tmp_path):
        """What a full rebuild relies on: an old cached answer is re-queried."""
        client, fetched = self._client(tmp_path)
        key = make_key(53.8, -1.55, 25)
        client.cache.put_many([(key, Lookup("OK", "pano", "2015-06", 53.8, -1.55, "© Google"))])
        asyncio.run(client.lookup_many([key]))
        assert fetched == []  # reused by default
        asyncio.run(client.lookup_many([key], fresh_after="2999-01-01 00:00:00"))
        assert fetched == [key]

    def test_deadline_stops_before_next_chunk(self, tmp_path, monkeypatch):
        monkeypatch.setattr(StreetViewClient, "CHUNK", 3)
        client, fetched = self._client(tmp_path, deadline=0.0)  # already passed
        keys = [make_key(53.8 + i / 1000, -1.55, 25) for i in range(10)]
        with pytest.raises(BudgetExceeded):
            asyncio.run(client.lookup_many(keys))
        assert fetched == []


class TestStorageGuard:
    PREFIX = "k7f3q9x2v8m1w4z6"

    def test_refuses_before_uploading_when_over_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(publish, "STORAGE_LIMIT_BYTES", 100)
        client = FakeR2({"big.sqlite.gz": {"Body": b"x" * 99}})
        with pytest.raises(SystemExit):
            publish.publish_site(client, "site", self.PREFIX, "leeds", _site(tmp_path))
        assert client.calls == []

    def test_counts_tiles_that_will_be_pruned_as_freed(self, tmp_path, monkeypatch):
        p = self.PREFIX
        client = FakeR2({
            f"{p}/tiles/leeds-20260801-000000.pmtiles": {"Body": b"x" * 60},
            f"{p}/tiles/leeds-20260901-000000.pmtiles": {"Body": b"x" * 30},
        })
        # 90 now + 7 new - 60 pruned = 37, under 50; without counting the prune it's 97
        monkeypatch.setattr(publish, "STORAGE_LIMIT_BYTES", 50)
        publish.publish_site(client, "site", p, "leeds", _site(tmp_path))
        assert f"{p}/tiles/leeds-20260801-000000.pmtiles" not in client.objects

    def test_list_objects_follows_pages(self):
        class Paged:
            def list_objects_v2(self, **kw):
                if "ContinuationToken" not in kw:
                    return {"Contents": [{"Key": "a"}], "IsTruncated": True,
                            "NextContinuationToken": "t"}
                return {"Contents": [{"Key": "b"}], "IsTruncated": False}

        assert [o["Key"] for o in publish.list_objects(Paged(), "b")] == ["a", "b"]


class TestOverview:
    # Two points about 100 m apart in Leeds: same resolution-6 hexagon
    LEEDS_A, LEEDS_B = (53.8, -1.55), (53.8, -1.5485)

    def test_band_edges_match_the_map(self):
        # MapLibre's "step": an age equal to an edge belongs to the older band
        assert [overview.band_of(a) for a in (0, 5, 6, 11, 12, 35, 36, 60, 119, 120, 300)] == \
            [0, 0, 1, 1, 2, 2, 3, 4, 4, 5, 5]

    def test_hex_stats_merge_across_shards(self):
        a = overview.hex_stats([(*self.LEEDS_A, 100.0, "2024-01")])
        b = overview.hex_stats([(*self.LEEDS_B, 50.0, "2024-01"), (*self.LEEDS_B, 30.0, NO_COVERAGE)])
        merged = overview.merge_stats([a, b])
        assert len(merged) == 1
        assert list(merged.values())[0] == {"2024-01": 150.0, NO_COVERAGE: 30.0}

    def test_summarise_bands_and_median(self):
        built = overview.month_index("2026-10")
        props = overview.summarise(
            {"2026-08": 1000, "2020-01": 3000, NO_COVERAGE: 500, NON_GOOGLE: 500}, built)
        assert props["b0"] == 1.0 and props["b4"] == 3.0   # 2 months; 6 years 9 months
        assert props["n"] == 0.5 and props["o"] == 0.5 and props["k"] == 5.0
        assert props["m"] == overview.month_index("2020-01")  # most of the length

    def test_no_google_imagery_has_no_median(self):
        assert overview.summarise({NO_COVERAGE: 100}, 24000)["m"] is None

    def test_weighted_median(self):
        assert overview.weighted_median([(1, 1.0), (2, 1.0), (3, 5.0)]) == 3
        assert overview.weighted_median([]) is None

    def test_build_overview_has_both_resolutions(self):
        stats = overview.hex_stats([(*self.LEEDS_A, 100.0, "2024-01")])
        fc = overview.build_overview(stats, "2026-10")
        assert fc["built"] == "2026-10"
        assert sorted(f["properties"]["r"] for f in fc["features"]) == [5, 6]
        ring = fc["features"][0]["geometry"]["coordinates"][0]
        assert ring[0] == ring[-1] and len(ring) == 7  # closed hexagon
        lng, lat = ring[0]
        assert lng == pytest.approx(-1.55, abs=0.05) and lat == pytest.approx(53.8, abs=0.05)

    def test_piece_points_use_piece_middle(self):
        seg = _road()
        (lat, lon, length, value), = overview.piece_points([Piece("r_0", 0, 200, "2024-01")], [seg])
        assert length == 200 and value == "2024-01"
        assert haversine_m(START, (lat, lon)) == pytest.approx(100, abs=1)


class TestPublishOverview:
    NOW = datetime(2026, 10, 22, 10, 45, 30, tzinfo=timezone.utc)
    PREFIX = "k7f3q9x2v8m1w4z6"

    def test_uploads_overview_and_points_page_at_it(self, tmp_path):
        site = _site(tmp_path)
        (tmp_path / "leeds.overview.json").write_text('{"type":"FeatureCollection"}')
        client = FakeR2()
        publish.publish_site(client, "site", self.PREFIX, "leeds", site, now=self.NOW)
        stem = f"{self.PREFIX}/tiles/leeds-20261022-104530"
        archive = f"{self.PREFIX}/history/leeds-20261022-104530.overview.json"
        # An overview without totals is archived but not listed in the history
        assert client.calls == [("upload", f"{stem}.pmtiles"), ("upload", f"{stem}.overview.json"),
                                ("upload", archive), ("put", f"{self.PREFIX}/index.html")]
        assert client.objects[f"{stem}.overview.json"]["ContentType"] == "application/json"
        body = client.objects[f"{self.PREFIX}/index.html"]["Body"]
        assert b'DEFAULT_OVERVIEW = "tiles/leeds-20261022-104530.overview.json"' in body

    def test_without_overview_page_gets_none(self, tmp_path):
        client = FakeR2()
        publish.publish_site(client, "site", self.PREFIX, "leeds", _site(tmp_path), now=self.NOW)
        assert b'DEFAULT_OVERVIEW = "";' in client.objects[f"{self.PREFIX}/index.html"]["Body"]

    def test_prunes_whole_versions(self, tmp_path):
        p = self.PREFIX
        old = {f"{p}/tiles/leeds-20260801-000000.{ext}": {"Body": b""} for ext in ("pmtiles", "overview.json")}
        mid = {f"{p}/tiles/leeds-20260901-000000.pmtiles": {"Body": b""}}
        client = FakeR2({**old, **mid})
        publish.publish_site(client, "site", p, "leeds", _site(tmp_path), now=self.NOW)
        assert not any(k in client.objects for k in old)
        assert all(k in client.objects for k in mid)


class TestHistory:
    PREFIX = "k7f3q9x2v8m1w4z6"
    OCT = datetime(2026, 10, 24, 13, 3, 0, tzinfo=timezone.utc)
    NOV = datetime(2026, 11, 2, 7, 15, 0, tzinfo=timezone.utc)

    def _built_site(self, tmp_path, built, updated=True):
        stats = overview.hex_stats([(53.8, -1.55, 4000.0, "2026-09"), (53.8, -1.55, 1000.0, NO_COVERAGE),
                                    (51.5, -0.12, 5000.0, "2016-03")])
        if updated:
            stats = overview.merge_stats([stats, overview.hex_stats([(53.8, -1.55, 4000.0, overview.UPDATED)])])
        tmp_path.mkdir(exist_ok=True)
        site = _site(tmp_path)
        overview.save_json(overview.build_overview(stats, built), str(tmp_path / "leeds.overview.json"))
        return site

    def _history(self, client):
        return json.loads(client.objects[f"{self.PREFIX}/history/leeds.json"]["Body"])

    def test_overview_carries_totals_and_top_areas(self):
        stats = overview.merge_stats([
            overview.hex_stats([(53.8, -1.55, 4000.0, "2026-09"), (51.5, -0.12, 5000.0, "2016-03")]),
            overview.hex_stats([(53.8, -1.55, 3000.0, overview.UPDATED),
                                (51.5, -0.12, 500.0, overview.UPDATED)])])
        fc = overview.build_overview(stats, "2026-10")
        assert fc["totals"]["k"] == 9.0 and fc["totals"]["u"] == 3.5
        assert fc["totals"]["b0"] == 4.0 and fc["totals"]["b5"] == 5.0
        leeds, london = fc["top"]
        assert leeds["km"] == 3.0 and london["km"] == 0.5   # most new imagery first
        assert leeds["y"] == pytest.approx(53.8, abs=0.3) and leeds["x"] == pytest.approx(-1.55, abs=0.3)

    def test_no_comparison_means_no_new_total(self):
        fc = overview.build_overview(overview.hex_stats([(53.8, -1.55, 100.0, "2024-01")]), "2026-10")
        assert "u" not in fc["totals"] and fc["top"] == []

    def test_publish_archives_overview_and_appends_to_history(self, tmp_path):
        client = FakeR2()
        oct_site = self._built_site(tmp_path / "oct", "2026-10", updated=False)
        publish.publish_site(client, "site", self.PREFIX, "leeds", oct_site, now=self.OCT)
        publish.publish_site(client, "site", self.PREFIX, "leeds",
                             self._built_site(tmp_path / "nov", "2026-11"), now=self.NOV)
        first, second = self._history(client)["builds"]
        assert first["stamp"] == "20261024-130300" and first["built"] == "2026-10" and "u" not in first
        assert second["overview"] == "history/leeds-20261102-071500.overview.json"
        assert second["k"] == 10.0 and second["u"] == 4.0 and second["top"][0]["km"] == 4.0
        # Archived overviews are never pruned, unlike the copies beside the tiles
        for stamp in ("20261024-130300", "20261102-071500"):
            assert f"{self.PREFIX}/history/leeds-{stamp}.overview.json" in client.objects
        history = client.objects[f"{self.PREFIX}/history/leeds.json"]
        assert history["CacheControl"] == "no-cache" and history["ContentType"] == "application/json"
        page = client.objects[f"{self.PREFIX}/index.html"]["Body"]
        assert b'DEFAULT_HISTORY = "history/leeds.json"' in page

    def test_republishing_a_stamp_replaces_its_entry(self, tmp_path):
        client = FakeR2()
        for _ in range(2):
            publish.publish_site(client, "site", self.PREFIX, "leeds",
                                 self._built_site(tmp_path, "2026-11"), now=self.NOV)
        assert len(self._history(client)["builds"]) == 1

    def test_publish_page_points_at_history(self, tmp_path):
        p = self.PREFIX
        client = FakeR2({f"{p}/tiles/uk-20261024-130300.pmtiles": {"Body": b"x"}})
        index = tmp_path / "index.html"
        index.write_text(PAGE)
        page, _, _ = publish.publish_page(client, "site", p, "uk", str(index))
        assert b'DEFAULT_HISTORY = "history/uk.json"' in client.objects[page]["Body"]


class TestPrivateWays:
    @pytest.mark.parametrize("tags, private", [
        ({"highway": "residential"}, False),
        ({"highway": "service", "access": "private"}, True),
        ({"highway": "service", "service": "driveway"}, True),
        ({"highway": "unclassified", "access": "no", "bus": "yes"}, True),           # bus gate
        ({"highway": "residential", "access": "private", "motor_vehicle": "yes"}, False),
        ({"highway": "residential", "motor_vehicle": "destination"}, False),
        ({"highway": "tertiary", "motor_vehicle": "no"}, True),
        ({"highway": "footway", "motor_vehicle": "no"}, False),                       # normal for a path
        ({"highway": "footway", "access": "private"}, True),
        ({"highway": "footway", "foot": "private"}, True),
        ({"highway": "cycleway", "access": "no", "bicycle": "designated"}, False),
        ({"highway": "footway", "access": "no", "foot": "designated"}, False),
    ])
    def test_access_rules(self, tags, private):
        assert is_private(tags) is private

    def test_marked_in_tiles(self):
        seg = Segment(id="d", highway="service", name=None, coords=[START, END], private=True)
        assert piece_properties(seg, NO_COVERAGE)["pv"] == 1
        assert "pv" not in piece_properties(_road(), NO_COVERAGE)

    def test_private_gaps_tallied_apart_from_coverage_gaps(self):
        public, private = _road("a"), Segment(id="b", highway="service", name=None,
                                              coords=[START, END], private=True)
        pieces = [Piece("a", 0, public.length, NO_COVERAGE), Piece("b", 0, 500, NO_COVERAGE),
                  Piece("b", 500, private.length, "2024-01")]   # imagery on a private road still counts
        combined = {}
        for _, _, length, value in overview.piece_points(pieces, [public, private]):
            combined[value] = combined.get(value, 0) + length
        summary = overview.summarise(combined, month_index("2026-10"))
        assert summary["p"] == 0.5
        assert summary["n"] == pytest.approx(public.length / 1000, abs=0.06)
        assert summary["k"] == pytest.approx((public.length + private.length) / 1000, abs=0.06)
        assert summary["b2"] == pytest.approx((private.length - 500) / 1000, abs=0.06)


class TestTopTens:
    def _seg(self, seg_id, row, highway="residential", name=None, ref=None, private=False):
        # About 985 m east-west, one row per road so they don't overlap
        lat = 53.8 + row * 0.002
        return Segment(id=seg_id, highway=highway, name=name, coords=[(lat, -1.55), (lat, -1.535)],
                       private=private, ref=ref)

    def _shard(self):
        segs = [self._seg("mill", 0, name="Mill Lane"), self._seg("a64a", 1, "trunk", ref="A64"),
                self._seg("a64b", 2, "trunk", "York Road", ref="A64"), self._seg("new", 3, name="New Road"),
                self._seg("path", 4, "footway"), self._seg("frag", 5), self._seg("gated", 6, private=True)]
        pieces = [Piece("mill", 0, 300, "2008-06"), Piece("mill", 300, 985, "2020-01"),
                  Piece("a64a", 0, 400, "2026-09"), Piece("a64a", 400, 985, "2015-01"),
                  Piece("a64b", 0, 985, "2026-09"),
                  Piece("new", 0, 600, "2026-09"), Piece("new", 600, 985, NO_COVERAGE),
                  Piece("path", 0, 985, "2005-01"),                     # paths aren't listed
                  Piece("frag", 0, 100, "2007-01"), Piece("frag", 100, 985, "2019-01"),  # too short
                  Piece("gated", 0, 985, "2006-01")]                    # private
        updates = [None, None, month_index("2009-03"), None, month_index("2012-01"),
                   0, None, None, None, None, None]
        panos = ["pano-mill", None, "pano-a64", None, None, None, None, None, None, None, None]
        return toplists.shard_lists(pieces, segs, updates, panos)

    def test_oldest_waiting_skips_paths_private_and_fragments(self):
        oldest = self._shard()["oldest"]
        assert [(r["name"], r["m"]) for r in oldest[:2]] == [
            ("Mill Lane", month_index("2008-06")), ("A64", month_index("2015-01"))]
        assert oldest[0]["pano"] == "pano-mill" and oldest[0]["km"] == 0.3
        assert len(oldest[0]["line"]) >= 2 and oldest[0]["line"][0][0] == pytest.approx(-1.55, abs=0.001)
        names = {r["name"] for r in oldest}
        assert not names & {"Unnamed footway road"} and all(r["m"] > month_index("2007-01") for r in oldest)

    def test_road_numbers_group_across_ways(self):
        lists = self._shard()
        a64 = next(r for r in lists["updated"] if r["id"] == "ref:A64")
        assert a64["km"] == pytest.approx(1.385, abs=0.01)    # both ways' new stretches
        assert lists["leaps"][0]["id"] == "ref:A64"
        assert (lists["leaps"][0]["fr"], lists["leaps"][0]["to"]) == (month_index("2009-03"), month_index("2026-09"))
        assert [r["name"] for r in lists["covered"]] == ["New Road"]

    def test_merge_adds_shards_and_ranks_areas(self):
        part = self._shard()
        stats = overview.hex_stats([(53.8, -1.55, 4000.0, overview.UPDATED), (51.5, -0.12, 900.0, overview.UPDATED)])
        merged = toplists.merge([part, part], stats)
        a64 = next(r for r in merged["updated"] if r["id"] == "ref:A64")
        assert a64["km"] == pytest.approx(2.77, abs=0.02)     # the same road in two shards adds up
        assert len(merged["oldest"]) <= toplists.TOP_N
        assert [a["km"] for a in merged["areas"]] == [4.0, 0.9]
        assert merged["areas"][0]["id"] and len(merged["areas"][0]["ring"]) == 7

    def test_without_a_previous_result_only_oldest(self):
        segs = [self._seg("mill", 0, name="Mill Lane")]
        lists = toplists.shard_lists([Piece("mill", 0, 985, "2010-01")], segs)
        assert len(lists["oldest"]) == 1 and lists["leaps"] == lists["updated"] == lists["covered"] == []

    def test_overview_step_merges_shard_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(run, "SITE_DIR", str(tmp_path / "site"))
        for shard in ("uk-1", "uk-2"):
            overview.save_json(overview.hex_stats([(53.8, -1.55, 1000.0, "2020-01")]), run.hexstats_path(shard))
        toplists.save(self._shard(), run.toplists_path("uk-1"))   # uk-2's is missing: left out, not fatal
        run.write_overview("uk", ["uk-1", "uk-2"])
        fc = overview.load_json(run.overview_path("uk"))
        assert fc["totals"]["k"] == 2.0 and fc["toplists"]["oldest"][0]["name"] == "Mill Lane"

    def test_history_keeps_a_compact_copy(self, tmp_path):
        fc = {"built": "2026-11", "totals": {"k": 1.0}, "top": [],
              "toplists": toplists.merge([self._shard()], None)}
        entry = publish.history_entry(fc, "history/uk-x.overview.json", datetime(2026, 11, 2, tzinfo=timezone.utc))
        assert entry["lists"]["oldest"][0] == {"id": "name:Mill Lane|" + entry["lists"]["oldest"][0]["id"].split("|")[1],
                                              "name": "Mill Lane", "km": 0.3, "m": month_index("2008-06")}
        assert "line" not in json.dumps(entry)


class TestAudit:
    # Side Street runs 450 m north of Main Road, in cells of its own
    SIDE_LAT = 53.804

    def _roads(self, side_lat=SIDE_LAT):
        main = Segment(id="main", highway="primary", name="Main Road", coords=[START, END])
        side = Segment(id="side", highway="residential", name="Side Street",
                       coords=[(side_lat, -1.55), (side_lat, -1.535)])
        pieces = [Piece("main", 0, main.length, "2015-01"), Piece("side", 0, side.length, "2012-06")]
        return [main, side], pieces

    def _run(self, resolver, probes=200, side_lat=SIDE_LAT):
        segs, pieces = self._roads(side_lat)
        return asyncio.run(audit.audit(segs, pieces, resolver, probes=probes, seed=7,
                                       now_index=month_index("2026-10")))

    def test_new_imagery_away_from_the_sentinel_is_missed(self):
        segs, pieces = self._roads()
        sentinel_keys = {s.key for s in refresh_plan(segs, pieces).sentinels.values()}

        async def resolve(keys):
            # Google re-drove Side Street, but its sentinel point still gives the old date
            return {k: "2026-05" if k[0] > 53_801_000 and k not in sentinel_keys
                    else ("2012-06" if k[0] > 53_801_000 else "2015-01") for k in keys}

        result = self._run(resolve)
        assert result.probes == 200 and result.outcomes["newer, caught"] == 0
        missed = result.outcomes["newer, missed"]
        assert missed == result.by_group["minor road"]["newer, missed"] > 50
        assert result.outcomes["same"] == result.by_group["major road"]["same"] == 200 - missed
        assert result.by_age["over 10 years"]["newer, missed"] == missed
        assert not result.sentinel_changes

    def test_new_imagery_at_the_sentinel_is_caught(self):
        async def resolve(keys):
            return {k: "2026-05" if k[0] > 53_801_000 else "2015-01" for k in keys}

        result = self._run(resolve)
        assert result.outcomes["newer, caught"] > 50 and result.outcomes["newer, missed"] == 0
        assert result.sentinel_changes == {"newer date": 1}

    def test_side_street_sharing_a_cell_with_a_main_road_is_missed(self):
        # 200 m apart, so one cell's sentinel goes to the main road; Google
        # re-drives only the side street, and the refresh never looks at it
        async def resolve(keys):
            return {k: "2026-05" if k[0] > 53_801_000 else "2015-01" for k in keys}

        result = self._run(resolve, side_lat=53.8018)
        assert result.outcomes["newer, missed"] > 50 and result.outcomes["newer, caught"] == 0

    def test_same_seed_same_points(self):
        seen = []

        async def resolve(keys):
            seen.append(sorted(keys))
            return {k: "2015-01" for k in keys}

        self._run(resolve, probes=20)
        self._run(resolve, probes=20)
        assert seen[0] == seen[1]

    def test_command_writes_reports_and_leaves_state_alone(self, tmp_path, monkeypatch):
        segs, pieces = self._roads()
        monkeypatch.setattr(run, "DATA_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        save_pieces(pieces, run.pieces_path("uk-1", "bisect"))
        save_pieces(pieces, run.pieces_path("uk-2", "bisect"))
        monkeypatch.setattr(areas, "AREAS", {**areas.AREAS, "uk": {"shards": ["uk-1", "uk-2"]}})
        monkeypatch.setattr(run, "load_area_segments", lambda area: segs)
        monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test")

        class FakeClient:
            def __init__(self, key, cache, **kwargs):
                self.cache, self.api_calls = cache, 0

            async def lookup_many(self, keys):
                self.api_calls += len(keys)
                return {k: Lookup("OK", "p", "2026-05" if k[0] > 53_801_000 else "2015-01",
                                  copyright="© Google") for k in keys}

        monkeypatch.setattr(run, "StreetViewClient", FakeClient)
        run.main(["audit", "--area", "uk", "--probes", "50"])
        report = json.loads((tmp_path / "audit.json").read_text())
        assert report["total"]["probes"] == 100 and set(report["areas"]) == {"uk-1", "uk-2"}
        assert "Refresh audit: uk" in (tmp_path / "audit.md").read_text()
        assert not (tmp_path / "audit.cache.sqlite").exists()
        assert not (tmp_path / "uk-1.cache.sqlite").exists()   # the pipeline's cache is never opened

    def test_pull_state_pieces_only(self, tmp_path, monkeypatch):
        seen = {}
        monkeypatch.setattr(run, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(publish, "r2_client", lambda bucket: None)
        monkeypatch.setattr(publish, "pull_state", lambda c, b, paths, force=False: seen.setdefault("p", paths))
        run.main(["pull-state", "--area", "uk-3", "--pieces-only"])
        assert [os.path.basename(p) for p in seen["p"]] == ["uk-3-bisect.pieces.jsonl"]

    def test_comparisons(self):
        assert audit.compare("2015-01", "2015-01") == "same"
        assert audit.compare("2015-01", "2020-03") == "newer"
        assert audit.compare(NO_COVERAGE, "2020-03") == "newer"
        assert audit.compare("2020-03", "2015-01") == "other"
        assert audit.compare("2020-03", NON_GOOGLE) == "other"
        assert audit.sentinel_change("2020-03", NON_GOOGLE) == "dated → user photos"
        assert audit.sentinel_change(NO_COVERAGE, "2020-03") == "no imagery → dated"
        assert audit.sentinel_change("2020-03", "2019-01") == "older date"

    def test_report_and_totals(self):
        async def resolve(keys):
            return {k: "2026-05" if k[0] > 53_801_000 else "2015-01" for k in keys}

        total = self._run(resolve).add(self._run(resolve))
        assert total.probes == 400
        md = audit.report_markdown(total)
        assert "Newer imagery than the map shows" in md and "| minor road |" in md
        json.dumps(total.to_json())


class TestFullRebuild:
    def test_fresh_cutoff(self):
        now = datetime(2026, 11, 2, 4, 23, 0, tzinfo=timezone.utc)
        assert run.fresh_cutoff(None, now) is None
        assert run.fresh_cutoff(25, now) == "2026-10-08 04:23:00"

    def test_sample_accepts_max_age(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(run, "cmd_sample", lambda args: seen.update(vars(args)))
        run.main(["sample", "--area", "uk-3", "--max-age-days", "25"])
        assert seen["max_age_days"] == 25
        run.main(["sample", "--area", "uk-3"])
        assert seen["max_age_days"] is None


class TestPublishPage:
    PREFIX = "k7f3q9x2v8m1w4z6"

    def _index(self, tmp_path):
        path = tmp_path / "index.html"
        path.write_text(PAGE)
        return str(path)

    def test_points_page_at_newest_version_and_uploads_nothing_else(self, tmp_path):
        p = self.PREFIX
        client = FakeR2({k: {"Body": b"x"} for k in (
            f"{p}/tiles/uk-20261023-205701.pmtiles",
            f"{p}/tiles/uk-20261024-003008.pmtiles",
            f"{p}/tiles/uk-20261024-003008.overview.json",
            f"{p}/tiles/leeds-20261101-000000.pmtiles",
        )})
        page, tiles, ov = publish.publish_page(client, "site", p, "uk", self._index(tmp_path))
        assert (tiles, ov) == ("tiles/uk-20261024-003008.pmtiles", "tiles/uk-20261024-003008.overview.json")
        assert client.calls == [("put", f"{p}/index.html")]
        body = client.objects[page]["Body"]
        assert b'DEFAULT_TILES = "tiles/uk-20261024-003008.pmtiles"' in body
        assert b'DEFAULT_OVERVIEW = "tiles/uk-20261024-003008.overview.json"' in body

    def test_version_without_overview(self, tmp_path):
        p = self.PREFIX
        client = FakeR2({f"{p}/tiles/uk-20261023-205701.pmtiles": {"Body": b"x"}})
        _, tiles, ov = publish.publish_page(client, "site", p, "uk", self._index(tmp_path))
        assert ov == "" and tiles.endswith("205701.pmtiles")

    def test_nothing_published_yet(self, tmp_path):
        client = FakeR2()
        with pytest.raises(SystemExit):
            publish.publish_page(client, "site", self.PREFIX, "uk", self._index(tmp_path))
        assert client.calls == []


class TestPanoramaLinks:
    def _cache(self, tmp_path, entries):
        cache = MetadataCache(str(tmp_path / "c.sqlite"))
        cache.put_many([(key, Lookup("OK", pano, date, 53.8, -1.55, "© 2026 Google"))
                        for key, pano, date in entries])
        return cache

    def test_takes_the_pano_at_a_sample_point_with_the_pieces_date(self, tmp_path):
        seg = _road()
        start, end = key_at(seg, 10), key_at(seg, seg.length - 10)
        cache = self._cache(tmp_path, [(start, "OTHER", "2019-08"), (end, "WANTED", "2023-05")])
        piece = Piece(seg.id, 0, seg.length, "2023-05")  # ends extended to the segment ends
        assert piece_panos([piece], [seg], cache) == ["WANTED"]

    def test_no_imagery_and_no_match_give_none(self, tmp_path):
        seg = _road()
        cache = self._cache(tmp_path, [(key_at(seg, 10), "P", "2019-08")])
        pieces = [Piece(seg.id, 0, 500, "2023-05"), Piece(seg.id, 500, seg.length, NO_COVERAGE)]
        assert piece_panos(pieces, [seg], cache) == [None, None]

    def test_short_segment_uses_its_middle(self, tmp_path):
        seg = _road(coords=(START, (53.8, -1.5497)))  # about 20 m
        cache = self._cache(tmp_path, [(key_at(seg, seg.length / 2), "MID", "2024-01")])
        assert piece_panos([Piece(seg.id, 0, seg.length, "2024-01")], [seg], cache) == ["MID"]

    def test_pano_goes_into_the_tile_properties(self, tmp_path):
        seg = _road()
        out = tmp_path / "x.geojsonl"
        pieces = [Piece(seg.id, 0, 500, "2023-05"), Piece(seg.id, 500, seg.length, NO_COVERAGE)]
        write_geojsonseq(pieces, [seg], str(out), ["PANO1", None])
        lines = [json.loads(line) for line in out.read_text().splitlines()]
        assert lines[0]["properties"]["pano"] == "PANO1"
        assert "pano" not in lines[1]["properties"]


class TestNewImagery:
    def test_newer_date_and_new_coverage_are_marked(self):
        prev = [Piece("r", 0, 500, "2019-03"), Piece("r", 500, 1000, NO_COVERAGE),
                Piece("s", 0, 300, "2021-06")]
        now = [Piece("r", 0, 400, "2026-04"),       # newer than 2019-03
               Piece("r", 400, 600, "2019-03"),     # middle (500) on the old boundary: unchanged value
               Piece("r", 600, 1000, "2026-04"),    # had none before
               Piece("s", 0, 300, "2021-06"),       # unchanged
               Piece("t", 0, 200, "2026-04")]       # new road: not new imagery
        assert updates_since(now, prev) == [month_index("2019-03"), None, 0, None, None]

    def test_older_or_lost_imagery_is_not_new(self):
        prev = [Piece("r", 0, 500, "2024-01"), Piece("r", 500, 1000, "2024-01")]
        now = [Piece("r", 0, 500, "2022-05"), Piece("r", 500, 1000, NO_COVERAGE)]
        assert updates_since(now, prev) == [None, None]

    def test_marked_in_tiles_and_tallied_per_hexagon(self, tmp_path):
        seg = _road()
        pieces = [Piece(seg.id, 0, 500, "2026-04"), Piece(seg.id, 500, seg.length, "2019-03")]
        updates = [month_index("2019-03"), None]
        out = tmp_path / "x.geojsonl"
        write_geojsonseq(pieces, [seg], str(out), None, updates)
        props = [json.loads(line)["properties"] for line in out.read_text().splitlines()]
        assert props[0]["u"] == month_index("2019-03") and "u" not in props[1]

        stats = overview.hex_stats(overview.with_updates(overview.piece_points(pieces, [seg]), updates))
        combined = {}  # the road may cross a hexagon boundary: add its cells together
        for cell in stats.values():
            for value, metres in cell.items():
                combined[value] = combined.get(value, 0) + metres
        summary = overview.summarise(combined, month_index("2026-10"))
        # The new stretch counts once in the total, and again only in "u"
        assert summary["u"] == pytest.approx(0.5, abs=0.06)
        assert summary["k"] == pytest.approx(seg.length / 1000, abs=0.06)
