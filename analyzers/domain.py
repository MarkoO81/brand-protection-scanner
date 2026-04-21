"""
Domain discovery: typosquatting permutations, homoglyphs, DNS resolution, WHOIS.
"""
from __future__ import annotations

import asyncio
import itertools
import re
from datetime import datetime, timezone
from typing import Any

import dns.asyncresolver
import tldextract
import whois
from Levenshtein import ratio

# ---------------------------------------------------------------------------
# Homoglyph map (Latin + common confusables)
# ---------------------------------------------------------------------------
HOMOGLYPHS: dict[str, list[str]] = {
    "a": ["@", "4", "á", "à", "ä", "â", "ã"],
    "b": ["6", "ḃ"],
    "c": ["ç", "©"],
    "e": ["3", "é", "è", "ê", "ë"],
    "g": ["9", "ğ"],
    "i": ["1", "l", "!", "í", "ì", "î", "ï"],
    "l": ["1", "i", "|"],
    "o": ["0", "ó", "ò", "ö", "ô", "õ"],
    "s": ["5", "$", "§"],
    "u": ["ü", "ú", "ù", "û"],
    "v": ["ν"],  # Greek nu
    "w": ["vv", "ω"],
    "x": ["×"],
    "y": ["ý", "ÿ"],
    "z": ["2", "ž"],
    "rn": ["m"],
    "m": ["rn"],
}

# Common affixes used in phishing domains
PREFIXES = ["login-", "secure-", "signin-", "account-", "verify-", "my-", "portal-", "auth-"]
SUFFIXES = ["-login", "-secure", "-verify", "-account", "-portal", "-online", "-web", "-app"]

# Common TLDs used in impersonation
PHISH_TLDS = [".com", ".net", ".org", ".io", ".co", ".info", ".biz", ".online", ".site", ".xyz"]


# ---------------------------------------------------------------------------
# Permutation generators
# ---------------------------------------------------------------------------

def _typosquats(name: str) -> set[str]:
    """Generate common keyboard-distance typos."""
    results: set[str] = set()
    # Omission
    for i in range(len(name)):
        results.add(name[:i] + name[i + 1:])
    # Repetition
    for i in range(len(name)):
        results.add(name[:i] + name[i] + name[i:])
    # Transposition
    for i in range(len(name) - 1):
        results.add(name[:i] + name[i + 1] + name[i] + name[i + 2:])
    # Replacement with adjacent QWERTY keys
    qwerty: dict[str, str] = {
        "a": "qwsz", "b": "vghn", "c": "xdfv", "d": "ersfcx",
        "e": "rdsw34", "f": "rtgdcev", "g": "tyhfvb", "h": "yugjbn",
        "i": "uojk89", "j": "uikhgm", "k": "iojlhn", "l": "opk;",
        "m": "njk,", "n": "bhjm", "o": "iplk90", "p": "ol;[-0",
        "q": "wa12", "r": "etdf45", "s": "wedxza", "t": "ryfg56",
        "u": "yihj78", "v": "cfgb", "w": "qase23", "x": "zsdc",
        "y": "tugh67", "z": "asx",
    }
    for i, ch in enumerate(name):
        for adj in qwerty.get(ch, ""):
            results.add(name[:i] + adj + name[i + 1:])
    return results - {""}


def _homoglyph_variants(name: str) -> set[str]:
    results: set[str] = set()
    for original, alts in HOMOGLYPHS.items():
        if original in name:
            for alt in alts:
                results.add(name.replace(original, alt, 1))
    return results


def _affix_variants(name: str) -> set[str]:
    results: set[str] = set()
    for pre in PREFIXES:
        results.add(pre + name)
    for suf in SUFFIXES:
        results.add(name + suf)
    return results


def generate_permutations(domain: str) -> list[str]:
    """Return ~2000 candidate domains for a given brand domain."""
    extracted = tldextract.extract(domain)
    name = extracted.domain  # e.g. "acme"
    base_tld = f".{extracted.suffix}" if extracted.suffix else ".com"

    candidates: set[str] = set()
    candidates.update(_typosquats(name))
    candidates.update(_homoglyph_variants(name))
    candidates.update(_affix_variants(name))

    # Cross product: (all name variants) × (phish TLDs ∪ base TLD)
    tlds = list({base_tld} | set(PHISH_TLDS))
    full_domains: set[str] = set()
    for candidate in candidates:
        for tld in tlds:
            full_domains.add(candidate.strip("-") + tld)

    # Remove original domain
    full_domains.discard(domain)
    return sorted(full_domains)


# ---------------------------------------------------------------------------
# DNS / WHOIS helpers
# ---------------------------------------------------------------------------

async def resolve_domain(domain: str) -> str | None:
    """Return first A-record IP or None if NXDOMAIN."""
    try:
        answers = await dns.asyncresolver.resolve(domain, "A", lifetime=5)
        return str(answers[0])
    except Exception:
        return None


def _days_since(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


def enrich_whois(domain: str) -> dict[str, Any]:
    """Synchronous WHOIS lookup — run in executor inside async context."""
    result: dict[str, Any] = {
        "registrar": None,
        "owner": None,
        "creation_date": None,
        "expiration_date": None,
        "registration_days": None,
        "is_fresh": False,
        "is_very_fresh": False,
    }
    try:
        w = whois.whois(domain)

        creation = w.creation_date
        if isinstance(creation, list):
            creation = creation[0]
        if isinstance(creation, str):
            creation = datetime.fromisoformat(creation)

        expiry = w.expiration_date
        if isinstance(expiry, list):
            expiry = expiry[0]

        # Owner/registrant: try several common WHOIS field names
        owner = (
            getattr(w, "registrant_name", None)
            or getattr(w, "org", None)
            or getattr(w, "registrant", None)
            or getattr(w, "name", None)
        )
        if isinstance(owner, list):
            owner = owner[0]

        result["registrar"] = str(w.registrar or "").strip() or None
        result["owner"] = str(owner or "").strip() or None
        result["creation_date"] = creation.isoformat() if creation else None
        result["expiration_date"] = expiry.isoformat() if isinstance(expiry, datetime) else None
        days = _days_since(creation)
        result["registration_days"] = days
        result["is_fresh"] = days is not None and days < 90
        result["is_very_fresh"] = days is not None and days < 7
    except Exception:
        pass
    return result


def has_homoglyphs(domain: str) -> bool:
    """Check whether the domain SLD contains any known homoglyph characters."""
    extracted = tldextract.extract(domain)
    name = extracted.domain
    for alts in HOMOGLYPHS.values():
        for alt in alts:
            if alt in name:
                return True
    return False


def domain_similarity(candidate: str, brand: str) -> float:
    """Levenshtein ratio between the SLDs of two domains (0–1)."""
    c = tldextract.extract(candidate).domain
    b = tldextract.extract(brand).domain
    return ratio(c, b)
