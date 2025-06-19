# streetview-heatmap


This project experiments with visualising the age of street-level imagery. The
included Python script queries the Google Street View metadata API and colours
OpenStreetMap road segments according to the capture date of nearby imagery.
The default bounding box covers Farsley, West Yorkshire.

## Requirements

- Python 3.8+
- A Google Maps API key (`GOOGLE_MAPS_API_KEY` environment variable)
- Optional SQLite database path (`HEATMAP_DB` environment variable)

Install dependencies:

```bash
pip install -r requirements.txt
```


Create a `.env` file containing your Google Maps API key:

```bash
echo "GOOGLE_MAPS_API_KEY=YOUR_KEY" > .env
```

The script uses `python-dotenv` to load this file automatically when running.
If `HEATMAP_DB` is set, it will be used as the path for a SQLite database
cache. You can also specify this using the `--db` command-line option.

## Usage

`generate_heatmap.py` downloads roads from the Overpass API, queries Street View
metadata for each road and writes `heatmap.html` by default. You can adjust the
bounding box, sampling step, the number of samples per road and the request
concurrency using command-line options. The step value determines the spacing of
the grid of points used to query Street View. It must be a positive number.

```bash
export GOOGLE_MAPS_API_KEY=YOUR_KEY
python generate_heatmap.py \
  --bbox -1.70 53.79 -1.65 53.82 \
  --step 0.005 \
  --samples 5 \
  --concurrency 5 \
  --output heatmap.html \
  --csv results.csv \
  --db metadata.db
```

Open `heatmap.html` in a browser to view the map. Road segments are coloured
from green (recent imagery) to red (older imagery). The bounding box,
sampling step, sample count and concurrency can be edited in the script if you
wish to target different areas or query more detail.

The map includes a small legend that explains what each colour represents, so
you can quickly interpret how recent the imagery is.


## Version control

Temporary files such as Python bytecode caches and test artifacts are listed in `.gitignore` so they are not committed to the repository.

