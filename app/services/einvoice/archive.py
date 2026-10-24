"""
Tamper-evident archive for e-invoice originals (GoBD / §147 AO: unaltered, retrievable storage).

Each operation writes the received originals and generated documents byte-for-byte into a new directory
(files created write-once with mode 0444), plus a manifest with SHA-256 hashes and a UTC timestamp.
Every manifest is appended to a hash-chained ledger (ledger.jsonl), so modifying, deleting or reordering a record
or ledger entry is detectable. Truncating the newest ledger entries is only detectable against an externally kept
copy of the latest entry hash (anchor it, e.g. in a daily export). For legally robust immutability, mount archive_dir
on WORM storage (e.g. S3 Object Lock in compliance mode); this module provides the integrity evidence on top.
"""

import asyncio
import fcntl
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.einvoice_config import get_einvoice_settings
from app.schemas.einvoice import ArchiveFileInfo, ArchiveInfo, ArchiveVerification

ARCHIVE_ID_PATTERN = re.compile(r"^ea_(\d{8})T\d{6}Z_[a-f0-9]{16}$")
GENESIS_HASH = "0" * 64


class ArchiveUnavailableError(Exception):
    """The archive cannot be written (I/O failure or a damaged ledger); nothing must be issued."""


@dataclass
class ArchiveFile:
    role: str
    filename: str
    content: bytes
    content_type: str


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj: Dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _safe_name(filename: str) -> str:
    name = os.path.basename(filename or "file")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._") or "file"
    return name[:120]


def _write_once(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _last_line(handle) -> Optional[bytes]:
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    if size == 0:
        return None
    block = min(size, 4096)
    while True:
        handle.seek(size - block)
        chunk = handle.read(block)
        lines = chunk.rstrip(b"\n").split(b"\n")
        if len(lines) > 1 or block == size:
            return lines[-1]
        block = min(size, block * 2)


class EInvoiceArchive:
    """Filesystem archive with hash-chained ledger."""

    @staticmethod
    def _root() -> Path:
        root = Path(get_einvoice_settings().archive_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    @classmethod
    def _dir_for(cls, archive_id: str) -> Optional[Path]:
        match = ARCHIVE_ID_PATTERN.match(archive_id)
        if not match:
            return None
        day = match.group(1)
        return cls._root() / day[:4] / day[4:6] / day[6:] / archive_id

    @classmethod
    def _store_sync(cls, files: List[ArchiveFile], operation: str, invoice_number: Optional[str], owner_id: Optional[str]) -> ArchiveInfo:
        now = datetime.now(timezone.utc)
        archive_id = f"ea_{now:%Y%m%dT%H%M%S}Z_{uuid.uuid4().hex[:16]}"
        target = cls._dir_for(archive_id)
        target.mkdir(parents=True, exist_ok=False)

        file_infos: List[ArchiveFileInfo] = []
        for index, item in enumerate(files):
            stored_name = f"{index:02d}_{item.role}__{_safe_name(item.filename)}"
            _write_once(target / stored_name, item.content)
            file_infos.append(ArchiveFileInfo(
                role=item.role, filename=stored_name, sha256=sha256_hex(item.content),
                size=len(item.content), content_type=item.content_type,
            ))

        manifest_core = {
            "archive_id": archive_id,
            "archived_at": now.isoformat(),
            "operation": operation,
            "invoice_number": invoice_number,
            "owner_id": owner_id,
            "files": [info.model_dump() for info in file_infos],
        }
        manifest_hash = sha256_hex(_canonical(manifest_core))

        ledger_path = cls._root() / "ledger.jsonl"
        with open(ledger_path, "a+b") as ledger:
            fcntl.flock(ledger, fcntl.LOCK_EX)
            try:
                last = _last_line(ledger)
                try:
                    prev_hash = json.loads(last)["entry_hash"] if last else GENESIS_HASH
                except (ValueError, KeyError, TypeError) as exc:
                    raise ArchiveUnavailableError(f"Archive ledger is damaged (last entry unreadable): {exc}") from exc
                entry = {
                    "archive_id": archive_id, "archived_at": now.isoformat(), "owner_id": owner_id,
                    "manifest_sha256": manifest_hash, "prev_hash": prev_hash,
                }
                entry["entry_hash"] = sha256_hex(_canonical(entry))
                ledger.seek(0, os.SEEK_END)
                ledger.write(_canonical(entry) + b"\n")
                ledger.flush()
                os.fsync(ledger.fileno())
            finally:
                fcntl.flock(ledger, fcntl.LOCK_UN)

        manifest = {**manifest_core, "manifest_sha256": manifest_hash, "prev_hash": prev_hash, "entry_hash": entry["entry_hash"]}
        _write_once(target / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))
        return ArchiveInfo(archive_id=archive_id, archived_at=now, entry_hash=entry["entry_hash"], files=file_infos)

    @classmethod
    async def store(cls, files: List[ArchiveFile], operation: str, invoice_number: Optional[str] = None,
                    owner_id: Optional[str] = None) -> Optional[ArchiveInfo]:
        if not get_einvoice_settings().archive_enabled:
            return None
        try:
            return await asyncio.to_thread(cls._store_sync, files, operation, invoice_number, owner_id)
        except OSError as exc:
            raise ArchiveUnavailableError(exc.strerror or str(exc)) from exc

    @classmethod
    def _scan_ledger(cls, archive_id: str) -> tuple:
        """Walks the whole ledger, re-checking every entry hash and link. Returns (entry, chain_problems)."""
        problems: List[str] = []
        found = None
        previous_hash = GENESIS_HASH
        ledger_path = cls._root() / "ledger.jsonl"
        if not ledger_path.is_file():
            return None, ["Ledger file is missing"]
        with open(ledger_path, "rb") as ledger:
            for number, raw in enumerate(ledger, start=1):
                try:
                    entry = json.loads(raw)
                    recorded = entry.pop("entry_hash")
                except (ValueError, KeyError):
                    problems.append(f"Ledger line {number} is unreadable")
                    previous_hash = None
                    continue
                if sha256_hex(_canonical(entry)) != recorded:
                    problems.append(f"Ledger entry {number} was modified")
                if previous_hash is not None and entry.get("prev_hash") != previous_hash:
                    problems.append(f"Ledger chain is broken at entry {number} (entry deleted, inserted or reordered)")
                if entry.get("archive_id") == archive_id:
                    found = {**entry, "entry_hash": recorded}
                previous_hash = recorded
        return found, problems

    @classmethod
    def _verify_sync(cls, archive_id: str, owner_id: Optional[str]) -> Optional[ArchiveVerification]:
        target = cls._dir_for(archive_id)
        if target is None:
            return None
        ledger_entry, problems = cls._scan_ledger(archive_id)
        if not (target / "manifest.json").is_file():
            # A ledger entry without its directory means the record was deleted.
            if ledger_entry and ledger_entry.get("owner_id") == owner_id:
                return ArchiveVerification(
                    archive_id=archive_id, archived_at=datetime.fromisoformat(ledger_entry["archived_at"]),
                    entry_hash=ledger_entry["entry_hash"], files=[], operation="unknown", intact=False,
                    problems=["Archive directory or manifest was deleted", *problems],
                )
            return None
        manifest = json.loads((target / "manifest.json").read_bytes())
        if manifest.get("owner_id") != owner_id:
            return None

        for info in manifest["files"]:
            path = target / info["filename"]
            if not path.is_file():
                problems.append(f"File missing: {info['filename']}")
            elif sha256_hex(path.read_bytes()) != info["sha256"]:
                problems.append(f"Hash mismatch (file modified): {info['filename']}")

        core = {key: manifest[key] for key in ("archive_id", "archived_at", "operation", "invoice_number", "owner_id", "files")}
        if sha256_hex(_canonical(core)) != manifest.get("manifest_sha256"):
            problems.append("Manifest content does not match its recorded hash")

        if ledger_entry is None:
            problems.append("No ledger entry found for this archive")
        elif ledger_entry["manifest_sha256"] != manifest.get("manifest_sha256") or ledger_entry["entry_hash"] != manifest.get("entry_hash"):
            problems.append("Manifest does not match the ledger entry")

        return ArchiveVerification(
            archive_id=archive_id,
            archived_at=datetime.fromisoformat(manifest["archived_at"]),
            entry_hash=manifest.get("entry_hash", ""),
            files=[ArchiveFileInfo(**info) for info in manifest["files"]],
            operation=manifest["operation"],
            invoice_number=manifest.get("invoice_number"),
            intact=not problems,
            problems=problems,
        )

    @classmethod
    async def verify(cls, archive_id: str, owner_id: Optional[str]) -> Optional[ArchiveVerification]:
        return await asyncio.to_thread(cls._verify_sync, archive_id, owner_id)
