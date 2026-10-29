# Pipeline

Builds a static Street View age map: OSM roads → Street View metadata → PMTiles → one HTML page.
The output is two files that any web server with HTTP range support can host.

## Setup (build machine only)

```bash
python3 -m venv .venv
.venv/bin/pip install -r pipeline/requirements.txt pytest pytest-asyncio
brew install tippecanoe          # only needed for the `tiles` step
```

The API key comes from `GOOGLE_MAPS_API_KEY` (or `GMAPS_APIKEY`) in the environment or in `.env`.
The key needs the **Street View Static API** enabled. Metadata lookups are free and don't use quota.

## Steps

```bash
.venv/bin/python -m pipeline.run download --area leeds        # ~54 MB West Yorkshire extract
.venv/bin/python -m pipeline.run estimate --area leeds        # road km and call estimates, no API calls

# Accuracy check on a small area: dense 20 m ground truth vs bisection
.venv/bin/python -m pipeline.run sample  --area leeds-centre --mode dense --max-calls 20000
.venv/bin/python -m pipeline.run sample  --area leeds-centre --mode bisect
.venv/bin/python -m pipeline.run compare --area leeds-centre

# Full area and map
.venv/bin/python -m pipeline.run sample --area leeds --mode bisect --max-calls 150000
.venv/bin/python -m pipeline.run tiles  --area leeds --mode bisect
.venv/bin/python -m pipeline.serve        # http://localhost:8000/?tiles=leeds.pmtiles&overview=leeds.overview.json
```

Every lookup is cached in `pipeline/data/<area>.cache.sqlite`, including "no imagery" answers.
An interrupted run picks up where it stopped. `--max-minutes` stops a run cleanly after that long, for the same effect.

## How sampling works

- **dense**: one lookup at the centre of every 20 m stretch. This is slow but serves as ground truth.
- **bisect**: look up both ends of each stretch, up to 400 m long. If they agree, the whole stretch gets that value. If not, look up the middle and repeat, down to 30 m.

Some details:
- Lookups are inset 10 m from each end of a way, so they don't snap to the crossing road at a junction.
- Footpaths, other path-like ways and `service` roads (driveways, car parks) use a 10 m search radius, so they
  don't borrow the date of a road running beside them. Other roads use 25 m.
- Only imagery whose copyright says "© Google" counts as dated. Results come from `source=outdoor`, and user photospheres show as "no Google imagery".

## Monthly refresh

The cache keeps lookups forever, so rerunning `sample` never picks up new imagery. Use `refresh` instead:

```bash
.venv/bin/python -m pipeline.run download --area leeds   # latest OSM roads
.venv/bin/python -m pipeline.run refresh  --area leeds
.venv/bin/python -m pipeline.run tiles    --area leeds
```

How it works:
1. It splits the area into 250 m grid cells.
2. In each cell it re-checks one "sentinel" point: the middle of the longest stretch of the most important road there, preferring roads that already have Google imagery.
3. Every road touching a cell whose sentinel changed is re-sampled. So are roads that are new or reshaped in OSM. Everything else keeps its previous dates.

Lookups newer than `--max-age-days` (default 25) count as fresh, so a rerun in the same month resumes rather than starting again.
The previous result is kept as `<area>-bisect.prev.pieces.jsonl`.

Leeds trial (2026-10-21, forced with `--max-age-days 0` on the same day as the full run):
- 16.6k calls in 2.3 minutes, about 14% of a full run;
- 5% of cells were flagged as changed, all of them false alarms from nearest-panorama noise;
- re-sampling them reproduced the original map exactly (100% match).

**Limitation:** a new capture that skips a cell's sentinel road isn't detected until a full re-sample. Sentinels are
chosen the same way every month, so a cell keeps checking the same road: a side street re-driven without its cell's
main road, or a road that had no imagery (sentinels prefer roads that have some), can stay out of date until the
yearly full rebuild.

**Measuring it:** the **Audit refresh** workflow (Actions tab, manual only, about 30 minutes) runs
`python3 -m pipeline.run audit --area uk`. It looks up random points along the roads (5,000 per shard by default,
uniform by length, from a fixed seed) and the sentinels a refresh would check for each road, then reports the share of
road with newer imagery than the map shows, how much of that a refresh run now would catch or miss, broken down by
road type, coverage and age, and what the changed sentinels changed to (many refresh re-samples find nothing newer).
It reads each shard's last result from R2 (`pull-state --pieces-only`) and saves nothing: lookups go to a throwaway
cache. The report is in the run summary and the `audit` artifact.
A full re-sample once a year is a cheap safety net, at about 115k free calls for Leeds. Note that plain `sample`
reuses every cached lookup however old, so a real re-sample needs `--max-age-days` (see *Full rebuild* below).

## The map page

`pipeline/web/index.html` is a single static page (MapLibre plus the PMTiles plugin, no build step):

- **Overview hexagons** at low zoom: H3 cells of about 250 km² (below zoom 6.5) and 36 km² (up to zoom 9.5), coloured
  by the median capture date of their roads. Roads fade in from zoom 8 to 9.5, and no road tiles are fetched below
  zoom 8. `tiles` writes each area's hexagon tallies (`<area>.hexstats.json`); for the UK, `overview --area uk` merges
  the shards' tallies into `uk.overview.json` (about 0.5 MB compressed).
- **"In view" panel**: the legend rows show each age band's share of road length in the current view. Below zoom 11
  the shares come from the hexagons whose centres are on screen (as of the build month, all ways). From zoom 11 they
  come from the roads drawn in the window, each clipped to its edges, under the current road-type filter. Bands with
  nothing in view get a grey background. Clicking a row hides or shows that band. Counting roads costs about 300 ms
  after each move at zoom 11–12 in central London (on a slow test machine) and a few ms at street level; raise
  `ROAD_STATS_ZOOM` in the page if that ever feels sluggish.
- **Filters**: all ways, roads only (no paths, tracks or service roads), or major roads.
- **Private ways**: roads closed to cars (`access`, `vehicle`, `motor_vehicle` or `motorcar` set to `private` or
  `no`, the most specific tag winning, so bus gates count), driveways (`service=driveway`), and paths where every
  tagged mode (`foot`, `bicycle`, falling back to `access`) is shut out. Without imagery they're drawn as dots and
  have their own legend row, "Private, no imagery", instead of counting as gaps in Google's coverage (`pv` in the
  tiles, `p` in the hexagon tallies). With imagery they're dated as usual and the popup notes the restriction.
  Sampling is unchanged. Gates (tagged on nodes) aren't read.
- **Colour-blind safe colours**: the Okabe–Ito set, recent to old: blue, sky blue, bluish green, orange, vermilion,
  wine. Distinct hues chosen to stay apart for colour-blind viewers; passes the categorical palette checks on every
  pair (sky blue and orange are faint on the light base map, which the text labels cover). It replaced a single-hue
  blue ramp that passed the checks but was hard to read on thin lines. The default green-to-red palette fails them.
- **Highlight new imagery since the last update**: `tiles` compares each piece with the previous result
  (`<area>-bisect.prev.pieces.jsonl`, kept by `refresh`, and by `sample` when it replaces a result) and marks pieces
  whose date got newer, or that have imagery where there was none, with `u` (the previous month index, or 0). The
  hexagon tallies count that length too (`u`, km). With the box ticked, everything else fades, new stretches get a
  dark casing, the panel shows the new km in view, and popups give the previous date. The box only appears for data
  built with this.
- **Updates and history**: each `publish` keeps a copy of the build's overview under `history/` for good (2.9 MB
  each, about 35 MB a year) and adds the build to `history/<area>.json`, with the UK totals per age band and the
  five areas (H3 resolution-4 hexagons, about 1,800 km²) with the most new imagery. The panel's "Latest update"
  section shows the new km, names those areas (Photon's reverse geocoder, fetched when the section is opened) and
  flies to one with the highlight on when clicked. From the second build it also charts the share of roads and
  paths with imagery under a year old at each update. Any past update can be shown as hexagons (roads are always
  the latest build's), with ages as of that update; `#build=<stamp>` links to it. Builds published before this
  have no history entry.
- **Top tens**: a button in the panel opens a second panel (a drawer on phones) of lists that change from build to
  build: oldest roads still waiting for an update, biggest leaps (old date → new date), most updated roads and newly
  covered roads in this update, and the busiest areas (H3 resolution-5 hexagons, about 250 km², named with Photon).
  Road lists are roads only (no paths, service roads or private ways) and stretches of 200 m or more; a road is
  grouped by its `ref` (A64) across the UK, else by name within an area hexagon, else is its own OSM way. `tiles`
  writes each shard's best candidates (`<area>.toplists.json`, `pipeline/toplists.py`) and `overview` merges them
  into the overview's `toplists`; a compact copy goes into each history entry, so the oldest list can mark roads new
  to it and name those that left it since the update before. Clicking an entry flies there, outlines the road or
  area, and opens a popup with the exact panorama. `#top=<list>` links to an open list.
- **Theme**: Device (the default, following the device's light or dark setting), Light or Dark, remembered on that
  device (not put in shared links). Dark swaps the base map for OpenFreeMap's `dark` style, which drops the page's
  own layers, so they are added again once it loads. They go just above the base map's last road or building layer,
  under the labels that follow it: not under its first label, since the dark style has water names low down, and not
  after its boundaries, which the dark style draws after its street names. The dark style also switches town,
  village and city names off past zoom 12–15; the page keeps them on, as the light style does. The dark style is near
  black, so the page recolours it as it loads (`DARK_MAP_COLOURS`): charcoal land (`#242426`), blue water, green
  parks, grey roads and light-grey labels, in the spirit of Apple Maps' dark mode, with the panel in matching greys.
  Roads stay grey so the base map never borrows the age colours. On the dark map the new-imagery casing turns light,
  and each palette's oldest colour is lightened to reach 3:1 against the charcoal: `#c9354f` for the classic palette
  (its other checks no worse) and `#B04A84` for the colour-blind one (colour-blind separation 7, just under the
  target of 8; popups give the date as text).
- **Place search** with [Photon](https://photon.komoot.io) (free OpenStreetMap geocoder, limited to the UK), and a
  locate-me button.
- **"Open in Street View"** opens the exact panorama the stretch is dated by: `tiles` looks up each piece's
  panorama ID in the cache (the lookup at one of its sample points that gave its date) and stores it as `pano`.
  Given only a point, Google Maps picks its own panorama nearby, which can be a different or broken one. Stretches
  without an ID fall back to the tapped point. The IDs add roughly 18% to the tiles.
- **Shareable links**: map position, filters and any past update shown live in the URL hash
  (`#map=…&hide=5,none&type=major&palette=cvd&build=20261102-071500`).
- **Phones**: the panel becomes a drawer along the bottom, closed to start with.

Band edges (6, 12, 36, 60, 120 months) are defined in both `pipeline/overview.py` and the page; keep them in step.

## Hosting

Copy `pipeline/data/site/` (`index.html` plus `<area>.pmtiles`) to any static web server. nginx and Caddy both serve
the HTTP range requests that PMTiles needs without extra configuration. Python's built-in `http.server` does not, which is why
`pipeline.serve` exists.

### Cloudflare R2 (current setup)

The map is shared by unlisted link: `https://map.<your-domain>/<R2_SITE_PREFIX>/index.html`. The bucket is public on
that custom domain, but everything lives under a secret folder. R2 never lists a bucket's contents, so without the
link there's nothing to find. Don't rely on the subdomain staying unknown: certificate transparency logs publish it.

Anyone with the link can pass it on. To revoke it, set a new `R2_SITE_PREFIX`, publish, share the new link,
then delete the old folder in the R2 dashboard (it stays public until you do).

```bash
.venv/bin/python -m pipeline.run publish --area uk
```

`publish` uploads the tiles as `<prefix>/tiles/<area>-<YYYYMMDD-HHMMSS>.pmtiles`, then a `<prefix>/index.html` that
points at them, then deletes all but the two newest tiles files for that area. Before uploading it adds up storage in
both buckets and refuses if the result, after pruning, would pass 8 GB (R2's free tier is 10 GB). Each publish gets a new tiles name, so browsers can
cache tiles forever without ever showing an old map. `index.html` is sent with `Cache-Control: no-cache`.

A second, private bucket holds the state the monthly job needs: the lookup cache and the last sampling result.

```bash
.venv/bin/python -m pipeline.run pull-state --area leeds   # skips files you already have; --force replaces them
.venv/bin/python -m pipeline.run push-state --area leeds
```

Settings, from the environment or `.env`:

| Variable | Meaning |
|---|---|
| `R2_ACCOUNT_ID` | Cloudflare account ID (the part before `.r2.cloudflarestorage.com`) |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | R2 API token with Object Read & Write on both buckets |
| `R2_SITE_PREFIX` | secret folder for the site: 16+ random letters, digits, `-` or `_` |
| `R2_BUCKET` | site bucket, default `streetview-heatmap` |
| `R2_STATE_BUCKET` | state bucket, default `streetview-heatmap-state` |

**Never connect a custom domain to the state bucket.** Connecting a domain makes a bucket public, and the state
bucket holds the whole lookup cache. Never upload anything to the site bucket outside the secret folder.

## UK-wide build

The UK is 1.17 million km of OSM ways (2026-10 `measure` run): 13–17 million lookups for a first full run, about
10–12 hours at 400 lookups a second, and roughly 1.75 GB of tiles. That's too much for one GitHub Actions job (6-hour
limit), so it's split into ten shards, `uk-1` to `uk-10` (`pipeline/shards.py`). Each shard is a set of 1° cells,
and a way belongs to the cell holding its middle vertex, so every way is in exactly one shard. Each needs 1.0–1.7
million lookups at minimum.

Shards are areas like any other, with their own cache and result:

```bash
.venv/bin/python -m pipeline.run download --area uk-3                 # the whole UK extract, ~2 GB
.venv/bin/python -m pipeline.run sample   --area uk-3 --rate 400 --concurrency 100
.venv/bin/python -m pipeline.run tiles    --area uk-3
tile-join -o pipeline/data/site/uk.pmtiles --no-tile-size-limit pipeline/data/site/uk-*.pmtiles
```

To size up an extract before sampling it (no API calls), run
`python3 -m pipeline.run measure --pbf <extract>`, or the **Measure UK** workflow. It reports km and lookups per
1° cell, which is what the shard table was packed from.

### Monthly build (GitHub Actions)

`.github/workflows/build-map.yml` runs at 04:23 UTC on the 2nd of each month, and from **Actions → Build map → Run
workflow**:

1. **prepare** downloads the UK extract once, keeps only highways (`osmium tags-filter`) and passes it on.
   It also re-enables the workflow, because GitHub disables scheduled workflows in public repos after 60 days
   without commits.
2. **shard** runs once per shard, one at a time so they share Google's 500 requests/s limit: `pull-state`,
   then `refresh` or `sample` (below), `push-state`, `tiles`. Lookups stop after 300 minutes, so the job saves
   its progress before the 6-hour limit.
3. **publish** merges the shard tiles with `tile-join` and runs `publish --area uk`.

A shard that runs out of time saves what it has and fails. That skips publish, so the current map stays up, and
running the workflow again resumes where it stopped. The first full run takes 10–12 hours; a monthly refresh about
2–3. It needs these repository secrets: `GOOGLE_MAPS_API_KEY`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY` and `R2_SITE_PREFIX`.

**What each shard does** depends only on the state bucket and the run's *Full rebuild* box:

| Full rebuild ticked? | `uk-N-bisect.pieces.jsonl.gz` in R2? | Shard runs |
|---|---|---|
| No | yes | `refresh`: fresh sentinels, re-sample only changed cells and new or reshaped roads |
| No | no (first run) | `sample`, reusing any cached lookups |
| Yes | either | `sample --max-age-days 25`: every road, re-asking lookups older than 25 days |

Scheduled runs never tick the box, so they always refresh once a shard has a result.

### Page-only changes

Changes to `pipeline/web/` (the panel, colours, layout) don't need new data. When one reaches `main`, the
**Deploy page** workflow (`.github/workflows/deploy-page.yml`) runs `publish-page --area uk`: it points the page at the
newest tiles and overview already in R2 and uploads only `index.html`, in about a minute. It can also be started by
hand from the Actions tab.

```bash
.venv/bin/python -m pipeline.run publish-page --area uk
```

Build map's publish step also takes the page from the latest `main` rather than the commit it started from, so a long
build never puts back an older page. A page change that needs data the tiles don't have yet (a new road property)
only works after the next Build map run.

### Full rebuild

**Actions → Build map → Run workflow**, tick **Full rebuild**. The run shows as "Build map (full rebuild)" and takes
about 12 hours. Use it:

- once a year, to catch new captures that missed every sentinel;
- after changing the sampling rules (search radii, end inset, bisection spans, or what counts as dated imagery),
  because a refresh only revisits roads in changed cells;
- if an area you know was recently driven still shows old dates after a couple of refreshes.

It doesn't delete anything first. Each shard's previous result is replaced only when its new sample completes, and
the live map changes only when all ten succeed and publish runs. If a rebuild stops part-way, run it again **with
the box ticked**: lookups from the first attempt are under 25 days old, so they're reused and finished shards take
minutes. Running without the box instead would refresh, leaving the unfinished shards on their old results.

## Emergency stop

R2 has no hard spending cap. A budget alert ($1, under **Manage Account → Billing → Billable Usage → Create budget
alert**) only sends an email, and only the day after the threshold is crossed. Realistic exposure is tiny: egress is
free, the free tier is 10 GB of storage and 10M reads a month, and `publish` keeps only the two newest tiles files,
so storage stays nearly flat (the archived overviews add about 35 MB a year). The real risks are a leaked R2 API token, `r2.dev` access being switched on, or the map link
spreading and being hammered.

If the bill or usage looks wrong, work through these in order:

1. **Find the cause.** **Manage Account → Billing → Billable Usage** splits the cost into storage, Class A
   operations (writes and lists) and Class B operations (reads). Many writes point to the token; many reads point
   to the public link.
2. **Revoke the R2 API token** (**R2 → Manage API tokens**). Do this first. It stops all S3 API access to both
   buckets. The monthly build fails until you create a new token and update the `R2_*` secrets in GitHub (and in
   the cloud environment, if you keep them there).
3. **Cut off public reads.** In the `streetview-heatmap` bucket, go to **Settings → Custom Domains** and remove
   the `map.` custom domain. Check that **Public Development URL** (`r2.dev`) is disabled on both buckets. The map goes offline.
4. **Stop storage charges.** Delete objects in the dashboard, or for large volumes add an object lifecycle rule
   that deletes everything after 1 day (bucket **Settings → Object lifecycle rules**). Nothing in either bucket is
   irreplaceable. Without the state bucket the next build does a full `sample` again (about 115k free lookups for
   Leeds) instead of a refresh.
5. **Afterwards:** look for API tokens you don't recognise (R2 tokens and **My Profile → API Tokens**) and unknown
   account members, confirm two-factor login is on, and contact Cloudflare billing support about charges caused by
   misuse. If the link leaked, publish under a new `R2_SITE_PREFIX` (see [Hosting](#cloudflare-r2-current-setup)).
6. **This is not automated, by design.** A script that could revoke tokens would need a more powerful token, and
   that token would be a new risk.

If the Google key leaks, delete or regenerate it in Google Cloud Console (**APIs & Services → Credentials**) and
update `GOOGLE_MAPS_API_KEY` in GitHub. It is restricted to the Street View Static API, but billed image requests
use that same API, so keep a budget alert on the Google project and a low daily quota cap on the API (for example
100 requests a day). Google's docs say metadata requests are free and use no quota, so the cap doesn't block the
pipeline, which never loads images.
