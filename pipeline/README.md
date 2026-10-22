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
.venv/bin/python -m pipeline.serve        # http://localhost:8000/?tiles=leeds.pmtiles (the page defaults to uk.pmtiles)
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

**Limitation:** a new capture that skips a cell's sentinel road isn't detected until a later refresh or a full re-sample.
A full re-sample once a year is a cheap safety net, at about 115k free calls for Leeds.

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
   `refresh` (or a full `sample` on the first run), `push-state`, `tiles`. Lookups stop after 300 minutes, so
   the job saves its progress before the 6-hour limit.
3. **publish** merges the shard tiles with `tile-join` and runs `publish --area uk`.

A shard that runs out of time saves what it has and fails. That skips publish, so the current map stays up, and
running the workflow again resumes where it stopped. The first full run takes 10–12 hours; a monthly refresh about
2. It needs these repository secrets: `GOOGLE_MAPS_API_KEY`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY` and `R2_SITE_PREFIX`.

## Emergency stop

R2 has no hard spending cap. A budget alert ($1, under **Manage Account → Billing → Billable Usage → Create budget
alert**) only sends an email, and only the day after the threshold is crossed. Realistic exposure is tiny: egress is
free, the free tier is 10 GB of storage and 10M reads a month, and `publish` keeps only the two newest tiles files,
so storage stays flat. The real risks are a leaked R2 API token, `r2.dev` access being switched on, or the map link
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
