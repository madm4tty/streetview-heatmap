#!/usr/bin/env python3
"""
Simple test script to verify the Google Maps API key is working.
"""

import os
import sys
import requests
from dotenv import load_dotenv


def test_api_key():
    """Test the Google Maps Street View Metadata API with the configured API key."""

    # Load environment variables
    load_dotenv()

    # Try to get API key from environment
    api_key_env = os.getenv("GMAPS_APIKEY")
    if api_key_env:
        print(f"✓ Found GMAPS_APIKEY in environment")
        os.environ.setdefault("GOOGLE_MAPS_API_KEY", api_key_env)

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")

    if not api_key:
        print("✗ ERROR: No API key found!")
        print("  Set GOOGLE_MAPS_API_KEY or GMAPS_APIKEY environment variable")
        return False

    print(f"✓ API key loaded (length: {len(api_key)})")

    # Test with a known location (Trafalgar Square, London)
    test_lat = 51.5080
    test_lon = -0.1281

    print(f"\nTesting API with location: {test_lat}, {test_lon}")
    print("Making request to Street View Metadata API...")

    url = "https://maps.googleapis.com/maps/api/streetview/metadata"
    params = {
        "location": f"{test_lat},{test_lon}",
        "key": api_key
    }

    try:
        resp = requests.get(url, params=params, timeout=10)

        # Check HTTP status
        print(f"\nHTTP Status Code: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            print(f"Response: {data}")

            status = data.get("status")
            print(f"\nAPI Status: {status}")

            if status == "OK":
                print("✓ SUCCESS! API key is working correctly!")

                # Show some details if available
                if "date" in data:
                    print(f"  - Image date: {data['date']}")
                if "location" in data:
                    loc = data["location"]
                    print(f"  - Image location: {loc.get('lat')}, {loc.get('lng')}")
                if "pano_id" in data:
                    print(f"  - Panorama ID: {data['pano_id']}")

                return True
            elif status == "ZERO_RESULTS":
                print("✓ API key is valid, but no imagery at this location")
                print("  (This is normal - not all locations have Street View)")
                return True
            elif status == "REQUEST_DENIED":
                print("✗ FAILED! Request was denied")
                if "error_message" in data:
                    print(f"  Error: {data['error_message']}")
                print("\nPossible issues:")
                print("  - API key is invalid")
                print("  - Street View Static API not enabled for this key")
                print("  - API key restrictions blocking the request")
                return False
            else:
                print(f"✗ Unexpected status: {status}")
                return False
        else:
            print(f"✗ FAILED! HTTP error: {resp.status_code}")
            print(f"Response: {resp.text}")
            return False

    except requests.RequestException as exc:
        print(f"✗ FAILED! Network error: {exc}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Google Maps API Key Test")
    print("=" * 60)

    success = test_api_key()

    print("\n" + "=" * 60)
    if success:
        print("RESULT: ✓ API key test PASSED")
        sys.exit(0)
    else:
        print("RESULT: ✗ API key test FAILED")
        sys.exit(1)
