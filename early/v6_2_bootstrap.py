#!/usr/bin/env python3
"""Bootstrap V6.2 with Cloudflare-capable HTTP session for Nifty Indices."""
import os, runpy
import requests
import cloudscraper

# v6_2_validation.py creates requests.Session() inside official_index_history().
# Replace that factory before executing the script so the same validation logic
# uses a Cloudflare-capable session without changing frozen research rules.
def _session():
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

requests.Session = _session
HERE = os.path.dirname(os.path.abspath(__file__))
runpy.run_path(os.path.join(HERE, "v6_2_validation.py"), run_name="__main__")
