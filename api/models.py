"""
Pydantic request/response models for the REST API.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, HttpUrl


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

class BrandConfig(BaseModel):
    domain: str = Field(..., examples=["acme.com"])
    keywords: list[str] = Field(default_factory=list, examples=[["acme"]])


# ---------------------------------------------------------------------------
# /brands/fingerprint
# ---------------------------------------------------------------------------

class FingerprintRequest(BaseModel):
    brand_url: str = Field(..., examples=["https://acme.com"])


class FingerprintResponse(BaseModel):
    domain: str
    logo_phash: Optional[str]
    favicon_hash: Optional[str]
    color_palette: Optional[list]
    screenshot_url: Optional[str]
    message: str = "Fingerprint saved."


# ---------------------------------------------------------------------------
# /scan
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    domain: str = Field(..., examples=["acm3-login.com"])
    brand_config: BrandConfig


class ScanResponse(BaseModel):
    task_id: str
    domain: str
    brand_domain: str
    status: str = "queued"


# ---------------------------------------------------------------------------
# /sweep
# ---------------------------------------------------------------------------

class SweepRequest(BaseModel):
    domain: str = Field(..., examples=["acme.com"])
    keywords: list[str] = Field(default_factory=list)


class SweepResponse(BaseModel):
    brand_domain: str
    task_id: str
    candidates_estimated: int
    status: str = "queued"


# ---------------------------------------------------------------------------
# /monitor/certstream
# ---------------------------------------------------------------------------

class MonitorRequest(BaseModel):
    domain: str
    keywords: list[str] = Field(default_factory=list)


class MonitorResponse(BaseModel):
    message: str
    domain: str


# ---------------------------------------------------------------------------
# /results
# ---------------------------------------------------------------------------

class SignalBreakdown(BaseModel):
    similarity: float = 0
    fresh_registration: float = 0
    very_fresh: float = 0
    homoglyphs: float = 0
    logo_phash: float = 0
    favicon_match: float = 0
    color_palette: float = 0
    login_form: float = 0
    external_form_action: float = 0
    urgency_language: float = 0
    impersonation_language: float = 0
    ip_reputation: float = 0
    bad_hosting_asn: float = 0


class ScanResultOut(BaseModel):
    id: int
    domain: str
    brand_domain: str
    score: float
    verdict: str
    signals: dict[str, Any]
    similarity_score: float
    is_fresh_registration: bool
    registration_days: Optional[int]
    has_homoglyphs: bool
    logo_similarity: float
    favicon_match: bool
    color_similarity: float
    has_login_form: bool
    has_external_form_action: bool
    has_urgency_language: bool
    has_impersonation_language: bool
    ip_address: Optional[str]
    ip_reputation_score: float
    bad_hosting_asn: bool
    source: str
    scanned_at: datetime
    screenshot_path: Optional[str]

    model_config = {"from_attributes": True}
