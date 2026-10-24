"""
E-Invoicing (ZUGFeRD / XRechnung) Settings.
Kept separate from the core Settings so the e-invoice module can be configured (and disabled) independently.
All variables use the EINVOICE_ prefix, e.g. EINVOICE_MAX_UPLOAD_SIZE_MB=10.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EInvoiceSettings(BaseSettings):
    """Configuration for the /api/einvoice endpoints."""
    model_config = SettingsConfigDict(
        env_prefix="EINVOICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Upload limits
    max_upload_size_mb: int = Field(default=10)

    # Tamper-evident archive of originals (GoBD). Must NOT live under STORAGE_DIR, which has retention cleanup.
    archive_dir: str = Field(default="./storage/einvoice_archive")
    archive_enabled: bool = Field(default=True)

    # Optional KoSIT validator (https://github.com/itplr-kosit/validator) for authoritative XRechnung checks.
    # Example: EINVOICE_KOSIT_JAR=/opt/kosit/validator-1.5.0-standalone.jar
    #          EINVOICE_KOSIT_SCENARIOS=/opt/kosit/xrechnung/scenarios.xml
    #          EINVOICE_KOSIT_REPOSITORY=/opt/kosit/xrechnung
    kosit_jar: Optional[str] = Field(default=None)
    kosit_scenarios: Optional[str] = Field(default=None)
    kosit_repository: Optional[str] = Field(default=None)
    java_bin: str = Field(default="java")
    kosit_timeout_seconds: int = Field(default=120)
    # When true, XRechnung creation/validation fails if the KoSIT validator is not configured.
    kosit_required: bool = Field(default=False)

    # Optional Ghostscript binary used to normalise uploaded PDFs to PDF/A-3b before embedding the XML.
    ghostscript_bin: Optional[str] = Field(default=None)
    ghostscript_timeout_seconds: int = Field(default=120)

    # XRechnung version written into the specification identifier (BT-24)
    xrechnung_version: str = Field(default="3.0")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def kosit_configured(self) -> bool:
        return bool(self.kosit_jar and self.kosit_scenarios)


@lru_cache
def get_einvoice_settings() -> EInvoiceSettings:
    """Returns cached e-invoice settings instance."""
    return EInvoiceSettings()
