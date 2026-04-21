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


def analyze_content(html: str, domain: str) -> dict:
    """
    Full content analysis pass.

    Returns a dict with boolean/float signal values.
    """
    soup = parse_html(html)
    text = soup.get_text(separator=" ", strip=True)

    return {
        "has_login_form": detect_login_form(soup),
        "has_external_form_action": detect_external_form_action(soup, domain),
        "has_urgency_language": detect_urgency_language(text),
        "has_impersonation_language": detect_impersonation_language(text),
        "js_obfuscation_detected": detect_js_obfuscation(html),
    }
