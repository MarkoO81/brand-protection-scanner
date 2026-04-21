"""
Visual analyzer: screenshot capture, perceptual hashing, favicon comparison,
color palette extraction.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
from pathlib import Path
from typing import Optional

import aiohttp
import imagehash
from PIL import Image

from core.config import settings


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

async def take_screenshot(url: str, domain: str) -> Optional[str]:
    """
    Capture a full-page screenshot with Playwright.
    Returns the local file path or None on failure.
    """
    try:
        from playwright.async_api import async_playwright

        Path(settings.screenshot_dir).mkdir(parents=True, exist_ok=True)
        filename = f"{domain.replace('.', '_')}.png"
        filepath = os.path.join(settings.screenshot_dir, filename)

        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox"])
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.screenshot(path=filepath, full_page=False)
            await browser.close()
        return filepath
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Perceptual hashing
# ---------------------------------------------------------------------------

def compute_phash(image_path: str) -> Optional[str]:
    try:
        img = Image.open(image_path).convert("RGB")
        return str(imagehash.phash(img))
    except Exception:
        return None


def compute_dhash(image_path: str) -> Optional[str]:
    try:
        img = Image.open(image_path).convert("RGB")
        return str(imagehash.dhash(img))
    except Exception:
        return None


def phash_similarity(hash1: str, hash2: str) -> float:
    """Return 0–1 similarity (1 = identical) between two pHash strings."""
    try:
        h1 = imagehash.hex_to_hash(hash1)
        h2 = imagehash.hex_to_hash(hash2)
        distance = h1 - h2  # Hamming distance (0–64 for 8x8 hash)
        return max(0.0, 1.0 - distance / 64.0)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Favicon
# ---------------------------------------------------------------------------

async def fetch_favicon(domain: str) -> Optional[bytes]:
    """Try /favicon.ico and common paths; return raw bytes or None."""
    urls = [
        f"https://{domain}/favicon.ico",
        f"http://{domain}/favicon.ico",
        f"https://{domain}/favicon.png",
    ]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
        for url in urls:
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if len(data) > 100:  # skip empty/placeholder
                            return data
            except Exception:
                continue
    return None


def hash_favicon(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def favicon_match(domain1: str, domain2: str) -> bool:
    """Return True if both domains serve the same favicon (by SHA-256)."""
    d1, d2 = await asyncio.gather(fetch_favicon(domain1), fetch_favicon(domain2))
    if d1 is None or d2 is None:
        return False
    return hash_favicon(d1) == hash_favicon(d2)


# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

def extract_palette(image_path: str, color_count: int = 5) -> list[tuple[int, int, int]]:
    """Return top N dominant colors as RGB tuples."""
    try:
        from colorthief import ColorThief

        ct = ColorThief(image_path)
        if color_count == 1:
            return [ct.get_color(quality=1)]
        return ct.get_palette(color_count=color_count, quality=1)
    except Exception:
        return []


def palette_similarity(palette1: list, palette2: list) -> float:
    """
    Coarse color-palette similarity: fraction of palette1 colors that have
    a close match in palette2 (within Euclidean distance 50).
    """
    if not palette1 or not palette2:
        return 0.0

    def dist(c1: tuple, c2: tuple) -> float:
        return sum((a - b) ** 2 for a, b in zip(c1, c2)) ** 0.5

    threshold = 50.0
    matches = sum(
        1 for c1 in palette1 if any(dist(c1, c2) < threshold for c2 in palette2)
    )
    return matches / len(palette1)
