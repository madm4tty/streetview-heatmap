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
.venv/bin/python -m pipeline.serve                              # http://localhost:8000/
```

Every lookup is cached in `pipeline/data/metadata_cache.sqlite`, including "no imagery" answers.
An interrupted run picks up where it stopped.

## How sampling works

- **dense**: one lookup at the centre of every 20 m stretch. This is slow but serves as ground truth.
- **bisect**: look up both ends of each stretch, up to 400 m long. If they agree, the whole stretch gets that value. If not, look up the middle and repeat, down to 30 m.

Some details:
- Lookups are inset 10 m from each end of a way, so they don't snap to the crossing road at a junction.
- Footpaths and other path-like ways use a 10 m search radius. Roads use 25 m.
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
.venv/bin/python -m pipeline.run publish --area leeds
```

`publish` uploads the tiles as `<prefix>/tiles/<area>-<YYYYMMDD-HHMMSS>.pmtiles`, then a `<prefix>/index.html` that
points at them,
then deletes all but the two newest tiles files for that area. Each publish gets a new tiles name, so browsers can
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

### Monthly build (GitHub Actions)

`.github/workflows/build-map.yml` runs at 04:23 UTC on the 2nd of each month, and from **Actions → Build map → Run
workflow**. It runs `pull-state`, `download`, then `refresh` (or a full `sample` if there's no previous result),
`push-state`, `tiles` and `publish`. It needs these repository secrets: `GOOGLE_MAPS_API_KEY`, `R2_ACCOUNT_ID`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_SITE_PREFIX`.

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
