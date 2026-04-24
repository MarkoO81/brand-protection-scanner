"""
Content analyzer: login-form detection, NLP urgency/impersonation patterns,
JS obfuscation heuristics.
"""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# NLP word lists
# ---------------------------------------------------------------------------

URGENCY_PATTERNS = re.compile(
    r"\b(urgent|immediately|account.?suspended|verify.?now|action.?required|"
    r"limited.?time|expires?.?soon|within.?24.?hours?|login.?now|confirm.?now|"
    r"update.?now|your.?account.?will.?be.?closed|suspended)\b",
    re.IGNORECASE,
)

IMPERSONATION_PATTERNS = re.compile(
    r"\b(official|authorized|secure|protected.?by|verified|microsoft|google|"
    r"amazon|paypal|apple|facebook|instagram|bank.?of|wells.?fargo|chase|"
    r"citibank|barclays|natwest|hsbc|support.?team|help.?desk)\b",
    re.IGNORECASE,
)

# Common JS obfuscation tells
JS_OBFUSCATION_PATTERNS = [
    re.compile(r"eval\(atob\("),           # base64 eval
    re.compile(r"eval\(unescape\("),
    re.compile(r"\\x[0-9a-fA-F]{2}"),     # hex encoded strings
    re.compile(r"String\.fromCharCode"),
    re.compile(r"\bpacke?r?\b.*function\(.*\breturn\b", re.DOTALL),  # Dean Edwards packer
    re.compile(r"(?:var |let |const )\w{1,3}=\["),  # short variable obfuscation
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def detect_login_form(soup: BeautifulSoup) -> bool:
    """Return True if the page contains a form with a password field."""
    for form in soup.find_all("form"):
        if form.find("input", {"type": "password"}):
            return True
    # Also check for loose password inputs outside a form tag
    return bool(soup.find("input", {"type": "password"}))


def detect_external_form_action(soup: BeautifulSoup, domain: str) -> bool:
    """Return True if any form posts to a domain other than *domain*."""
    for form in soup.find_all("form"):
        action: str = form.get("action", "")
        if action.startswith("http") and domain not in action:
            return True
    return False


def detect_urgency_language(text: str) -> bool:
    return bool(URGENCY_PATTERNS.search(text))


def detect_impersonation_language(text: str) -> bool:
    return bool(IMPERSONATION_PATTERNS.search(text))


def detect_js_obfuscation(html: str) -> bool:
    for pattern in JS_OBFUSCATION_PATTERNS:
        if pattern.search(html):
            return True
    return False


# ---------------------------------------------------------------------------
# Parking / for-sale page detection
# ---------------------------------------------------------------------------

PARKING_TEXT_PATTERNS = re.compile(
    r"\b(this domain is for sale|buy this domain|domain for sale|"
    r"make an offer|purchase this domain|own this domain|"
    r"domain may be for sale|inquire about this domain|"
    r"domain is available|listed for sale|acquire this domain|"
    r"this web page is parked|parked free|powered by sedo|"
    r"godaddy\.com|hugedomains|afternic|dan\.com|"
    r"turncommerce|namecheap marketplace|undeveloped\.com)\b",
    re.IGNORECASE,
)

PARKING_TITLE_PATTERNS = re.compile(
    r"\b(domain for sale|buy this domain|parked domain|"
    r"coming soon|under construction|sedo|hugedomains|"
    r"this domain|domain name)\b",
    re.IGNORECASE,
)

# Known parking/registrar page body fingerprints
PARKING_META_NAMES = {"parking", "sedo", "dan.com", "afternic", "hugedomains", "turncommerce"}


def detect_parked_page(soup: BeautifulSoup, text: str) -> bool:
    """
    Return True if the page is a domain parking / for-sale page
    with no real content.
    """
    # Check visible text
    if PARKING_TEXT_PATTERNS.search(text):
        return True

    # Check <title>
    title_tag = soup.find("title")
    if title_tag and PARKING_TITLE_PATTERNS.search(title_tag.get_text()):
        return True

    # Check meta generator / description
    for meta in soup.find_all("meta"):
        name    = (meta.get("name", "") or "").lower()
        content = (meta.get("content", "") or "").lower()
        if any(p in name or p in content for p in PARKING_META_NAMES):
            return True

    # Very thin page: fewer than 80 words almost always means parked/error
    if len(text.split()) < 80:
        # Only flag thin pages if they also lack any real interactive elements
        if not soup.find(["form", "input", "button", "a"]):
            return True

    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_content(html: str, domain: str) -> dict:
    """
    Full content analysis pass.

    Returns a dict with boolean/float signal values.
    """
    soup = parse_html(html)
    text = soup.get_text(separator=" ", strip=True)

    return {
        "is_parked": detect_parked_page(soup, text),
        "has_login_form": detect_login_form(soup),
        "has_external_form_action": detect_external_form_action(soup, domain),
        "has_urgency_language": detect_urgency_language(text),
        "has_impersonation_language": detect_impersonation_language(text),
        "js_obfuscation_detected": detect_js_obfuscation(html),
    }
