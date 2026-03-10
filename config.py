"""IntegriRef configuration — API keys, endpoints, and defaults."""

import os

# GROBID PDF parser endpoint
GROBID_URL = os.getenv("GROBID_URL", "http://localhost:8070")

# API keys (all optional — improve rate limits when provided)
CROSSREF_MAILTO = os.getenv("INTEGRIREF_CROSSREF_MAILTO", "")
S2_API_KEY = os.getenv("INTEGRIREF_S2_API_KEY", "")
CORE_API_KEY = os.getenv("INTEGRIREF_CORE_API_KEY", "")
NASA_ADS_TOKEN = os.getenv("INTEGRIREF_NASA_ADS_TOKEN", "")
FRED_API_KEY = os.getenv("INTEGRIREF_FRED_API_KEY", "")
COURTLISTENER_TOKEN = os.getenv("INTEGRIREF_COURTLISTENER_TOKEN", "")
LENS_TOKEN = os.getenv("INTEGRIREF_LENS_TOKEN", "")

# Default settings
DEFAULT_TIMEOUT = 20
DEFAULT_SEARCH_LIMIT = 10
