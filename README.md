# streetview-heatmap

## Requirements

- Python 3.8+
- A Google Maps API key (`GOOGLE_MAPS_API_KEY` environment variable)

Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage


```bash
export GOOGLE_MAPS_API_KEY=YOUR_KEY
python generate_heatmap.py \
  --bbox -1.70 53.79 -1.65 53.82 \
  --step 0.005 \
  --output heatmap.html \
  --csv results.csv
```

