"""
Certstream monitor — subscribe to live SSL certificate transparency feed
and enqueue scans for any certificate that matches brand keywords/domain.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Callable

import websockets
from structlog import get_logger

from core.config import settings
from workers.tasks import scan_domain

logger = get_logger(__name__)


def _domain_matches(domain: str, brand_domain: str, keywords: list[str]) -> bool:
    """
    Return True if the certificate domain is worth scanning:
    - Contains a brand keyword (case-insensitive)
    - Is similar to the brand domain (naive substring check — fast)
    - Excludes the brand domain itself
    """
    domain = domain.lower().strip("*. ")
    brand_domain = brand_domain.lower()

    if domain == brand_domain or domain.endswith(f".{brand_domain}"):
        return False  # Legitimate sub-domain or the brand itself

    for kw in keywords:
        if kw.lower() in domain:
            return True

    brand_name = brand_domain.split(".")[0]
    if brand_name in domain and domain != brand_domain:
        return True

    return False


async def run_certstream_monitor(
    brand_domain: str,
    keywords: list[str],
    on_match: Callable[[str], None] | None = None,
) -> None:
    """
    Long-running coroutine — connects to Certstream WebSocket and dispatches
    scan tasks for matching domains.

    Call on_match(domain) if provided (useful for testing / hooks).
    """
    url = settings.certstream_url
    logger.info("Connecting to Certstream", url=url, brand=brand_domain)

    reconnect_delay = 5

    while True:
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                reconnect_delay = 5  # reset on successful connect
                logger.info("Certstream connected", brand=brand_domain)

                async for raw_message in ws:
                    try:
                        message = json.loads(raw_message)
                    except json.JSONDecodeError:
                        continue

                    if message.get("message_type") != "certificate_update":
                        continue

                    leaf = message.get("data", {}).get("leaf_cert", {})
                    all_domains: list[str] = leaf.get("all_domains", [])

                    for domain in all_domains:
                        if _domain_matches(domain, brand_domain, keywords):
                            logger.info(
                                "Certstream match — queuing scan",
                                domain=domain,
                                brand=brand_domain,
                            )
                            scan_domain.apply_async(
                                kwargs={
                                    "domain": domain,
                                    "brand_domain": brand_domain,
                                    "brand_keywords": keywords,
                                    "source": "certstream",
                                }
                            )
                            if on_match:
                                on_match(domain)

        except (websockets.exceptions.ConnectionClosed, OSError) as exc:
            logger.warning(
                "Certstream connection lost — reconnecting",
                exc=str(exc),
                delay=reconnect_delay,
            )
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
        except asyncio.CancelledError:
            logger.info("Certstream monitor cancelled", brand=brand_domain)
            return
