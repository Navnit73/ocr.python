"""
KoSIT validator integration (https://github.com/itplr-kosit/validator).
Runs the standalone validator JAR with the XRechnung validator configuration and maps its VARL report
messages to ValidationIssue objects. Optional: when not configured the check is reported as skipped.
"""

import asyncio
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from lxml import etree

from app.core.einvoice_config import get_einvoice_settings
from app.schemas.einvoice import ValidationIssue

logger = logging.getLogger("app.einvoice.kosit")


@dataclass
class KositResult:
    status: str  # passed | failed | skipped | error
    errors: List[ValidationIssue] = field(default_factory=list)
    warnings: List[ValidationIssue] = field(default_factory=list)
    detail: Optional[str] = None


def parse_kosit_report(report_xml: bytes) -> KositResult:
    """Parses a KoSIT VARL report (rep:report). Acceptance comes from rep:assessment/rep:accept|rep:reject."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    root = etree.fromstring(report_xml, parser=parser)
    if root.xpath("//*[local-name()='noScenarioMatched']"):
        # e.g. XRechnung 2.x or ZUGFeRD BASIC with an XRechnung-3 configuration: not a verdict on the document
        return KositResult(status="skipped", detail="No KoSIT validation scenario matches this document type/version")
    accepted = bool(root.xpath("//*[local-name()='assessment']/*[local-name()='accept']"))
    if not accepted and root.get("valid") == "true":
        accepted = True
    errors, warnings = [], []
    for message in root.xpath("//*[local-name()='message']"):
        level = (message.get("level") or "error").lower()
        if level not in ("error", "warning"):
            continue
        line = message.get("lineNumber")
        issue = ValidationIssue(
            rule_id=message.get("code") or message.get("id") or "KOSIT",
            field=message.get("xpathLocation"),
            message=" ".join((message.text or "").split()),
            severity=level,
            source="kosit",
            line=int(line) if line and line.isdigit() else None,
        )
        (errors if level == "error" else warnings).append(issue)
    return KositResult(status="passed" if accepted and not errors else "failed", errors=errors, warnings=warnings)


async def run_kosit(xml_bytes: bytes) -> KositResult:
    settings = get_einvoice_settings()
    if not settings.kosit_configured:
        return KositResult(status="skipped", detail="KoSIT validator not configured (EINVOICE_KOSIT_JAR / EINVOICE_KOSIT_SCENARIOS)")

    with tempfile.TemporaryDirectory(prefix="kosit_") as workdir:
        work = Path(workdir)
        source = work / "invoice.xml"
        source.write_bytes(xml_bytes)
        out_dir = work / "out"
        out_dir.mkdir()
        cmd = [settings.java_bin, "-jar", settings.kosit_jar, "-s", settings.kosit_scenarios, "-o", str(out_dir)]
        if settings.kosit_repository:
            cmd += ["-r", settings.kosit_repository]
        cmd.append(str(source))
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=settings.kosit_timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            return KositResult(status="error", detail="KoSIT validator timed out")
        except OSError as exc:
            return KositResult(status="error", detail=f"KoSIT validator could not be started: {exc}")

        # The validator exits non-zero for rejected documents, so the report is the source of truth.
        reports = sorted(out_dir.glob("*-report.xml")) or sorted(out_dir.glob("*.xml"))
        if not reports:
            logger.warning("KoSIT produced no report (exit %s): %s", process.returncode, stderr.decode(errors="replace")[-500:])
            return KositResult(status="error", detail=f"KoSIT validator produced no report (exit code {process.returncode})")
        try:
            return parse_kosit_report(reports[0].read_bytes())
        except etree.XMLSyntaxError as exc:
            return KositResult(status="error", detail=f"Unreadable KoSIT report: {exc}")
