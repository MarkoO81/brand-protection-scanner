"""
IP reputation: AbuseIPDB check + known-bad ASN list.
"""
from __future__ import annotations

import aiohttp

from core.config import settings

# Known bad hosting ASNs (bulletproof hosters, frequent abuse sources)
BAD_ASNS: set[str] = {
    "AS9009",    # M247
    "AS60068",   # CDN77 / DataCamp
    "AS202425",  # IP Volume
    "AS209588",  # Flyservers
    "AS44477",   # STARK INDUSTRIES
    "AS174",     # Cogent (frequently abused)
    "AS395954",  # Leaseweb USA
    "AS29073",   # Quasi Networks
    "AS49453",   # Global Layer
    "AS136258",  # Akamai (rare false positive — kept for demo)
}


async def get_ip_reputation(ip: str) -> dict:
    """
    Check AbuseIPDB for the given IP.
    Falls back gracefully if no API key is configured.
    """
    result = {
        "ip": ip,
        "abuse_confidence_score": 0,
        "asn": None,
        "isp": None,
        "country_code": None,
        "is_bad_asn": False,
        "reputation_score": 0.0,
    }

    api_key = settings.abuseipdb_api_key
    if not api_key:
        return result

    url = "https://api.abuseipdb.com/api/v2/check"
    headers = {"Key": api_key, "Accept": "application/json"}
    params = {"ipAddress": ip, "maxAgeInDays": 90}

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = (await resp.json()).get("data", {})
                    result["abuse_confidence_score"] = data.get("abuseConfidenceScore", 0)
                    result["asn"] = data.get("asn")
                    result["isp"] = data.get("isp")
                    result["country_code"] = data.get("countryCode")
                    result["is_bad_asn"] = result["asn"] in BAD_ASNS
                    # Normalize to 0–1
                    result["reputation_score"] = data.get("abuseConfidenceScore", 0) / 100.0
    except Exception:
        pass

    return result
