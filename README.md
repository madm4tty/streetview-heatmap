# streetview-heatmap

This project experiments with visualising the age of street-level imagery. A
Python script is provided that queries the Google Street View metadata API for
image capture dates around Farsley, West Yorkshire and produces a simple heat
map overlay using Folium.

## Requirements

- Python 3.8+
- A Google Maps API key (`GOOGLE_MAPS_API_KEY` environment variable)

Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

`generate_heatmap.py` fetches metadata for a grid of points and writes
`heatmap.html` by default. You can adjust the bounding box or step size using
command-line options.

```bash
export GOOGLE_MAPS_API_KEY=YOUR_KEY
python generate_heatmap.py \
  --bbox -1.70 53.79 -1.65 53.82 \
  --step 0.005 \
  --output heatmap.html \
  --csv results.csv
```

Open `heatmap.html` in a browser to view the map. Points are colored from green
(new imagery) to red (older imagery). The bounding box and grid spacing can be
edited in the script if you wish to target different areas or sample more
points.
