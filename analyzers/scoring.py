"""
Scoring engine — weighted 0-100 score + verdict.

Signal weights mirror the table in the README.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Signals:
    # Domain
    similarity_score: float = 0.0          # 0–1  → max 20 pts
    is_fresh_registration: bool = False     # → 18 pts
    is_very_fresh: bool = False             # → +8 pts (additive)
    has_homoglyphs: bool = False            # → 12 pts

    # Visual
    logo_similarity: float = 0.0           # 0–1  → max 22 pts
    favicon_match: bool = False             # → 15 pts
    color_similarity: float = 0.0          # 0–1  → max 12 pts

    # Content
    is_parked: bool = False                 # → caps final score at 30
    has_login_form: bool = False            # → 18 pts
    has_external_form_action: bool = False  # → 14 pts
    has_urgency_language: bool = False      # → 10 pts
    has_impersonation_language: bool = False# → 12 pts

    # Infra
    ip_reputation_score: float = 0.0       # 0–1  → max 18 pts
    bad_hosting_asn: bool = False           # → 15 pts


def compute_score(signals: Signals) -> tuple[float, str, dict]:
    """
    Returns (total_score, verdict, breakdown_dict).
    verdict is one of: CLEAN / SUSPICIOUS / CRITICAL
    """
    breakdown: dict[str, float] = {}

    def add(key: str, pts: float) -> None:
        breakdown[key] = round(pts, 2)

    # Domain signals
    add("similarity", signals.similarity_score * 20)
    add("fresh_registration", 18.0 if signals.is_fresh_registration else 0.0)
    add("very_fresh", 8.0 if signals.is_very_fresh else 0.0)
    add("homoglyphs", 12.0 if signals.has_homoglyphs else 0.0)

    # Visual signals
    add("logo_phash", signals.logo_similarity * 22)
    add("favicon_match", 15.0 if signals.favicon_match else 0.0)
    add("color_palette", signals.color_similarity * 12)

    # Content signals
    add("login_form", 18.0 if signals.has_login_form else 0.0)
    add("external_form_action", 14.0 if signals.has_external_form_action else 0.0)
    add("urgency_language", 10.0 if signals.has_urgency_language else 0.0)
    add("impersonation_language", 12.0 if signals.has_impersonation_language else 0.0)

    # Infra signals
    add("ip_reputation", signals.ip_reputation_score * 18)
    add("bad_hosting_asn", 15.0 if signals.bad_hosting_asn else 0.0)

    total = min(100.0, sum(breakdown.values()))

    # Parked/for-sale pages have no active content — cap at 30 and
    # zero out visual/content signals since there's nothing real to match.
    if signals.is_parked:
        parked_only = {
            k: v for k, v in breakdown.items()
            if k in ("similarity", "fresh_registration", "very_fresh", "homoglyphs")
        }
        total = min(30.0, sum(parked_only.values()))
        breakdown["parked_page_cap"] = 0.0  # marker in breakdown

    if total >= 70:
        verdict = "CRITICAL"
    elif total >= 40:
        verdict = "SUSPICIOUS"
    else:
        verdict = "CLEAN"

    return round(total, 2), verdict, breakdown
