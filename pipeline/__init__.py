"""Batch pipeline: OSM roads -> Street View metadata sampling -> PMTiles.

A standalone replacement for the Flask/scheduler stack. Each step is a
subcommand of ``python3 -m pipeline.run``; see pipeline/README.md.
"""
