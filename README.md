# streetview-heatmap


This project experiments with visualising the age of street-level imagery. The
included Python script queries the Google Street View metadata API and colours
OpenStreetMap road segments according to the capture date of nearby imagery.
The default bounding box covers Farsley, West Yorkshire.

## Requirements

- Python 3.8+
- A Google Maps API key (`GOOGLE_MAPS_API_KEY` environment variable)

Install dependencies:

```bash
pip install -r requirements.txt
```


Create a `.env` file containing your Google Maps API key:

```bash
echo "GOOGLE_MAPS_API_KEY=YOUR_KEY" > .env
```

The script uses `python-dotenv` to load this file automatically when running.

## Usage

`generate_heatmap.py` downloads roads from the Overpass API, queries Street View
metadata for each road and writes `heatmap.html` by default. You can adjust the
bounding box or sampling step using command-line options.

```bash
export GOOGLE_MAPS_API_KEY=YOUR_KEY
python generate_heatmap.py \
  --bbox -1.70 53.79 -1.65 53.82 \
  --step 0.005 \
  --output heatmap.html \
  --csv results.csv
```

Open `heatmap.html` in a browser to view the map. Road segments are coloured
from green (recent imagery) to red (older imagery). The bounding box and
sampling step can be edited in the script if you wish to target different
areas or query more detail.

