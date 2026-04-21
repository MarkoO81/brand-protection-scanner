from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    keywords: Mapped[list] = mapped_column(JSON, default=list)

    # Visual fingerprint
    logo_phash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    favicon_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    color_palette: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    screenshot_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScanResult(Base):
    __tablename__ = "scan_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    brand_domain: Mapped[str] = mapped_column(String(255), index=True)

    # Score & verdict
    score: Mapped[float] = mapped_column(Float, default=0.0)
    verdict: Mapped[str] = mapped_column(String(32), default="CLEAN")  # CLEAN / SUSPICIOUS / CRITICAL

    # Raw signal breakdown
    signals: Mapped[dict] = mapped_column(JSON, default=dict)

    # Domain signals
    similarity_score: Mapped[float] = mapped_column(Float, default=0.0)
    is_fresh_registration: Mapped[bool] = mapped_column(default=False)
    registration_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    has_homoglyphs: Mapped[bool] = mapped_column(default=False)
    whois_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Visual signals
    logo_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    favicon_match: Mapped[bool] = mapped_column(default=False)
    color_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    screenshot_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Content signals
    has_login_form: Mapped[bool] = mapped_column(default=False)
    has_external_form_action: Mapped[bool] = mapped_column(default=False)
    has_urgency_language: Mapped[bool] = mapped_column(default=False)
    has_impersonation_language: Mapped[bool] = mapped_column(default=False)
    js_obfuscation_detected: Mapped[bool] = mapped_column(default=False)

    # Infra signals
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    ip_reputation_score: Mapped[float] = mapped_column(Float, default=0.0)
    bad_hosting_asn: Mapped[bool] = mapped_column(default=False)
    raw_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual / sweep / certstream
