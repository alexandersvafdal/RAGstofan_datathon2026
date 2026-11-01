"""Persist source metadata for discovery and warm live definitions outside the answer path.

Profiles are retrieval hints until the source adapter rechecks their identity,
version and age. Snapshot definitions cannot certify a live table's definitions.
"""

import hashlib
import json
import logging
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from .sources import TableMeta

log = logging.getLogger(__name__)


def core_signature(meta: TableMeta) -> str:
    core = {"id": meta.id, "path": meta.path, "title": meta.title,
            "variables": [{"code": v.code, "text": v.text, "values": v.values, "labels": v.value_texts,
                           "time": v.time} for v in meta.variables]}
    return hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=4)
def _read(path: str, stamp: int) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict) or data.get("version") != 1:
            return {}
        return {key: TableMeta.model_validate(value) for key, value in data.get("profiles", {}).items()}
    except (OSError, ValueError, TypeError):
        log.warning("Source profiles unavailable or invalid: %s", path)
        return {}


def load_profiles(path: Path, lang: str = "en") -> dict[str, TableMeta]:
    if not path.exists():
        return {}
    return {key.removeprefix(lang + ":"): meta.model_copy(deep=True)
            for key, meta in _read(str(path), path.stat().st_mtime_ns).items() if key.startswith(lang + ":")}


def save_profiles(path: Path, profiles: dict[str, TableMeta]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": 1, "profiles": {key: meta.model_dump() for key, meta in profiles.items()}},
                                    ensure_ascii=False))
    temporary.replace(path)


def enrich_live_metadata(meta: TableMeta, path: Path, lang: str, ttl: float) -> TableMeta:
    saved = _read(str(path), path.stat().st_mtime_ns).get(f"{lang}:{meta.id}") if path.exists() else None
    if (saved is None or saved.source_kind != "live" or core_signature(saved) != core_signature(meta)
            or (meta.updated and meta.updated != saved.updated)):
        return meta
    try:
        age = time.time() - datetime.fromisoformat(saved.retrieved_at).timestamp()
    except (ValueError, TypeError):
        return meta
    if not 0 <= age < ttl:
        return meta
    # Preserve the API's current schema, labels and checked-at timestamp.
    meta.description = meta.description or saved.description
    meta.source = meta.source or saved.source
    meta.updated = meta.updated or saved.updated
    meta.notes = list(dict.fromkeys([*meta.notes, *saved.notes]))
    if meta.unit_source != "source" and saved.unit_source == "source":
        meta.unit, meta.unit_source = saved.unit, "source"
    for var in meta.variables:
        old = saved.var(var.code)
        var.notes = list(dict.fromkeys([*var.notes, *old.notes]))
        var.value_notes = {**{code: list(notes) for code, notes in old.value_notes.items()}, **var.value_notes}
    return meta


def discovery_text(meta: TableMeta) -> str:
    return " ".join([meta.title, meta.description, meta.source, meta.unit, *meta.notes,
                     *(n for v in meta.variables for n in v.notes),
                     *(n for v in meta.variables for notes in v.value_notes.values() for n in notes)])
