"""ExperienceReader — read-only access to the experience library.

Scans ``success_cases/`` and ``failure_cases/`` subdirectories under
``EXPERIENCE_DIR / cycle_position /``, sorts files by the timestamp
embedded in their filenames (descending, newest first), and returns
the top 5 entries across both directories combined.

File naming convention (timestamp portion):
    YYYY-MM-DD_HH-mm-ss   (minutes use '-', not ':')

Example filename:
    2026-05-18_14-30-45_XAUUSD_1h.json

This module is strictly read-only — it never writes or deletes files.
"""
from __future__ import annotations

import json
import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pa_agent.records.schema import ExperienceEntry

# Regex to extract the timestamp portion from a filename.
_TS_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})")
_TS_FORMAT = "%Y-%m-%d_%H-%M-%S"


def experience_pool_binding(metadata: dict) -> dict:
    """History identity, separate from market sample identity."""
    pool = metadata.get("experience_pool")
    if pool is None:
        return {}
    as_of = metadata.get("analysis_as_of_ms")
    if type(as_of) is not int or not isinstance(pool, dict) or pool.get("protocol_version") != "experience_pool_v1" or pool.get("digest") != ExperienceReader._pool_digest(pool.get("entries")):
        raise ValueError("invalid frozen experience context")
    return {"experience_pool_digest": pool["digest"], "experience_as_of_ms": as_of}


def _default_logger() -> logging.Logger:
    return logging.getLogger(__name__)


def _parse_timestamp_ms(filename: str) -> Optional[int]:
    """Extract and parse the timestamp from a filename.

    Returns the timestamp in milliseconds, or ``None`` if the filename
    does not contain a parseable timestamp.
    """
    match = _TS_PATTERN.search(filename)
    if not match:
        return None
    ts_str = match.group(1)
    try:
        dt = datetime.strptime(ts_str, _TS_FORMAT).replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


class ExperienceReader:
    """Read experience entries from the experience library (read-only).

    Parameters
    ----------
    experience_dir:
        Root directory of the experience library.  Defaults to
        ``pa_agent.config.paths.EXPERIENCE_DIR`` when ``None``.
    logger:
        Optional logger instance.  A module-level logger is used when
        ``None``.
    """

    def __init__(
        self,
        experience_dir: Optional[Path] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if experience_dir is None:
            from pa_agent.config.paths import EXPERIENCE_DIR
            experience_dir = EXPERIENCE_DIR

        self._experience_dir = experience_dir
        self._logger = logger or _default_logger()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_top5(self, cycle_position: str) -> list[ExperienceEntry]:
        """Return the 5 most-recent experience entries for *cycle_position*.

        Scans both ``success_cases/`` and ``failure_cases/`` subdirectories
        under ``EXPERIENCE_DIR / cycle_position /``.  Files are sorted by
        the timestamp embedded in their filenames (descending).  The top 5
        entries across both directories combined are returned.

        Parameters
        ----------
        cycle_position:
            The cycle position label (e.g. ``"micro_channel"``).

        Returns
        -------
        list[ExperienceEntry]
            Up to 5 entries, newest first.  Returns an empty list when no
            readable entries are found.
        """
        base_dir = self._experience_dir / cycle_position
        candidates: list[tuple[int, str, Path]] = []  # (timestamp_ms, case_type, path)

        for case_type, subdir_name in (("success", "success_cases"), ("failure", "failure_cases")):
            subdir = base_dir / subdir_name
            if not subdir.exists():
                self._logger.debug(
                    "ExperienceReader: directory does not exist, skipping: %s", subdir
                )
                continue

            for file_path in subdir.iterdir():
                if not file_path.is_file():
                    continue
                # Skip non-JSON files and hidden files like .gitkeep
                if file_path.suffix.lower() != ".json":
                    continue

                ts_ms = _parse_timestamp_ms(file_path.name)
                if ts_ms is None:
                    self._logger.warning(
                        "ExperienceReader: cannot parse timestamp from filename, skipping: %s",
                        file_path.name,
                    )
                    continue

                candidates.append((ts_ms, case_type, file_path))

        # Sort by timestamp descending (newest first), then take top 5.
        candidates.sort(key=lambda t: t[0], reverse=True)
        top5 = candidates[:5]

        entries: list[ExperienceEntry] = []
        for ts_ms, case_type, file_path in top5:
            content = self._read_json(file_path)
            if content is None:
                continue
            entry = ExperienceEntry(
                filename=file_path.name,
                case_type=case_type,
                cycle_position=cycle_position,
                timestamp_ms=ts_ms,
                content=content,
            )
            entries.append(entry)

        return entries

    def read_for_stage2(
        self,
        cycle_position: str,
        *,
        direction: str = "",
        patterns: list[str] | None = None,
        max_entries: int = 3,
        max_chars_per_entry: int = 400,
        as_of_ms: int | None = None,
        source_identity: dict | None = None,
        timeframe: str = "",
        frozen_pool: dict | None = None,
    ) -> list[ExperienceEntry]:
        """Select only same-market cases whose entire revision was known as-of.

        Filename time is a legacy display field, never causal eligibility.
        No frozen pool or identity means no experience, not a live-directory fallback.
        """
        from pa_agent.data.contracts import ResolvedInstrumentIdentity

        identity = ResolvedInstrumentIdentity.from_value(source_identity)
        if identity is None or not identity.venue or not identity.instrument_type or not timeframe or type(as_of_ms) is not int or frozen_pool is None:
            return []
        if frozen_pool.get("protocol_version") != "experience_pool_v1" or frozen_pool.get("digest") != self._pool_digest(frozen_pool.get("entries")):
            raise ValueError("frozen experience pool identity mismatch")
        entries = []
        for row in frozen_pool["entries"]:
            if row.get("cycle_position") != cycle_position:
                continue
            content = row["content"]
            causal = content.get("causal_context", {})
            if not isinstance(causal, dict):
                continue
            timestamps = [causal.get(key) for key in ("event_at_ms", "outcome_known_at_ms", "revision_at_ms")]
            if any(type(value) is not int or value < 0 for value in timestamps):
                continue
            if not (timestamps[0] <= timestamps[1] <= timestamps[2] <= as_of_ms):
                continue
            candidate = ResolvedInstrumentIdentity.from_value(causal.get("source_identity"))
            if candidate is None or not identity.same_instrument(candidate) or causal.get("timeframe") != timeframe:
                continue
            entries.append(ExperienceEntry(
                filename=row["filename"], case_type=row["case_type"],
                cycle_position=cycle_position, timestamp_ms=timestamps[2], content=content,
            ))
        if not entries:
            return []

        dir_norm = str(direction or "").strip().lower()
        pattern_set = {
            str(p).strip().lower() for p in (patterns or []) if str(p).strip()
        }

        def _score(entry: ExperienceEntry) -> int:
            content = entry.content if isinstance(entry.content, dict) else {}
            score = 0
            ent_dir = str(content.get("direction", "") or "").strip().lower()
            if dir_norm and ent_dir == dir_norm:
                score += 2
            ent_patterns = content.get("detected_patterns") or []
            if pattern_set and isinstance(ent_patterns, list):
                overlap = pattern_set.intersection(
                    {str(p).strip().lower() for p in ent_patterns}
                )
                score += len(overlap)
            return score

        ranked = sorted(entries, key=lambda e: (_score(e), e.timestamp_ms), reverse=True)
        cap = max(0, min(max_entries, 10))
        return ranked[:cap]

    @staticmethod
    def _pool_digest(entries: object) -> str:
        return hashlib.sha256(json.dumps(entries, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()

    def freeze_pool(self) -> dict:
        """Capture source bytes once; all roles/arms consume this detached artifact."""
        entries = []
        for path in sorted(self._experience_dir.glob("*/*/*.json")):
            case_type = {"success_cases": "success", "failure_cases": "failure"}.get(path.parent.name)
            if case_type is None:
                continue
            content = self._read_json(path)
            if isinstance(content, dict):
                entries.append({"filename": path.name, "case_type": case_type,
                                "cycle_position": path.parent.parent.name, "content": content})
        return {"protocol_version": "experience_pool_v1", "entries": entries,
                "digest": self._pool_digest(entries)}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_json(self, path: Path) -> Optional[dict]:
        """Read and parse a JSON file.

        Returns the parsed dict, or ``None`` on any error (with a warning
        logged).
        """
        try:
            text = path.read_text(encoding="utf-8")
            return json.loads(text)
        except OSError as exc:
            self._logger.warning(
                "ExperienceReader: cannot read file %s: %s", path, exc
            )
            return None
        except json.JSONDecodeError as exc:
            self._logger.warning(
                "ExperienceReader: invalid JSON in file %s: %s", path, exc
            )
            return None
