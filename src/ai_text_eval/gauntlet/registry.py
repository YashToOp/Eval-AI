"""Field registry (R-01): metadata fields exist only via governed data.

CAS §4.1: every field has a stated purpose, an allowed-value definition,
validation rules, and a since-version. Vocabularies are closed and versioned;
values are added through governance, never invented inline. Records must not
contain fields absent from the registry *as of their declared schema
version*; consumers must ignore fields added after their own version.

The version logic in `unknown_fields` implements both halves of that rule:

- A record at or below the registry's current schema version is validated
  strictly: every field it carries must exist with `since` <= the record's
  version. A v1 record carrying a v2 field is claiming a schema it does not
  have.
- A record *newer* than the registry is tolerated: this consumer cannot know
  the newer schema, so unknown fields are ignored and a warning marks the
  version gap (forward compatibility, CAS §4.1).

Fields are deprecated, never removed (CAS §4.1), so old records remain
readable forever.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ai_text_eval.gauntlet.findings import Report
from ai_text_eval.gauntlet.sample import Sample
from ai_text_eval.gauntlet.spec import BENCHMARK_DIR

REGISTRY_FILE = "field_registry.json"


@dataclass
class FieldRegistry:
    raw: dict = field(default_factory=dict)
    path: Path | None = None

    # -- accessors -------------------------------------------------------

    @property
    def registry_version(self) -> str:
        return self.raw.get("registry_version", "1")

    @property
    def current_schema_version(self) -> str:
        return self.raw.get("current_schema_version", "1")

    @property
    def fields(self) -> dict[str, dict]:
        return self.raw.get("fields", {})

    @property
    def relationship_types(self) -> dict[str, str]:
        return self.raw.get("relationship_types", {})

    @property
    def mutual_relationship_types(self) -> frozenset[str]:
        return frozenset(self.raw.get("mutual_relationship_types", []))

    @property
    def roles(self) -> frozenset[str]:
        return frozenset(self.raw.get("roles", []))

    def field_order(self, schema_version: str) -> list[str]:
        orders = self.raw.get("field_order", {})
        if schema_version in orders:
            return list(orders[schema_version])
        # Unknown version: newest known order (forward-compat reading).
        newest = max(orders, key=lambda v: int(v)) if orders else None
        return list(orders.get(newest, []))

    def vocabulary(self, name: str) -> list[str] | None:
        v = self.raw.get("vocabularies", {}).get(name)
        return list(v) if isinstance(v, list) else None

    # -- version arithmetic ----------------------------------------------

    @staticmethod
    def _v(version: str | None) -> int:
        try:
            return int(str(version))
        except (TypeError, ValueError):
            return -1

    def known_at(self, schema_version: str) -> set[str]:
        """Field names that exist as of `schema_version`."""
        v = self._v(schema_version)
        return {
            name for name, meta in self.fields.items()
            if self._v(meta.get("since", "1")) <= v
        }

    def required_at(self, schema_version: str) -> set[str]:
        v = self._v(schema_version)
        out = set()
        for name, meta in self.fields.items():
            if self._v(meta.get("since", "1")) > v:
                continue
            dep = meta.get("deprecated_since")
            if dep is not None and self._v(dep) <= v:
                continue
            if meta.get("required", True):
                out.add(name)
        return out

    # -- validation ------------------------------------------------------

    def validate_fields(self, sample: Sample) -> Report:
        """Registry-level field checks for one record (CAS §4.1)."""
        r = Report(checked=1)
        sid = sample.id or f"{sample.source_file}:{sample.source_line}"
        declared = sample.get("schema_version")
        rv = self._v(declared)
        current = self._v(self.current_schema_version)

        if rv < 0:
            r.error("CAS 4.1", "BAD_SCHEMA_VERSION",
                    f"schema_version {declared!r} is not a known version", sid)
            return r

        if rv > current:
            # Forward compatibility: a newer record is tolerated, not
            # validated field-by-field, and the gap is made visible.
            r.warn("CAS 4.1", "NEWER_SCHEMA_TOLERATED",
                   f"record declares schema {declared}; this registry knows "
                   f"{self.current_schema_version}. Unknown fields ignored.", sid)
            return r

        known = self.known_at(declared)
        for name in sample.raw:
            if name not in known:
                if name in self.fields:
                    r.error("CAS 4.1", "FIELD_FROM_FUTURE_SCHEMA",
                            f"field {name!r} exists since schema "
                            f"{self.fields[name].get('since')}, but the record "
                            f"declares schema {declared}", sid)
                else:
                    r.error("CAS 4.1", "UNREGISTERED_FIELD",
                            f"field {name!r} is absent from the field registry",
                            sid)

        for name in self.required_at(declared):
            if name not in sample.raw:
                r.error("CAS 4.1", "MISSING_FIELD",
                        f"required field {name!r} absent (schema {declared})", sid)

        for name, meta in self.fields.items():
            dep = meta.get("deprecated_since")
            if dep is not None and name in sample.raw and self._v(dep) <= rv:
                r.warn("CAS 4.1", "DEPRECATED_FIELD",
                       f"field {name!r} is deprecated since schema {dep}", sid)

        # Lineage entries against the closed relationship vocabulary (P10).
        lineage = sample.get("lineage")
        if isinstance(lineage, list):
            for entry in lineage:
                if (not isinstance(entry, dict)
                        or not isinstance(entry.get("relation"), str)
                        or not isinstance(entry.get("target"), str)):
                    r.error("CAS 4.2", "BAD_LINEAGE_ENTRY",
                            f"lineage entry {entry!r} must be "
                            "{{'relation': ..., 'target': ...}}", sid)
                    continue
                if entry["relation"] not in self.relationship_types:
                    r.error("CAS 4.2", "BAD_RELATION",
                            f"relation {entry['relation']!r} is not in the closed "
                            f"vocabulary {sorted(self.relationship_types)}", sid)
        elif lineage is not None:
            r.error("CAS 4.2", "BAD_LINEAGE_ENTRY",
                    "lineage must be an array of relation entries", sid)
        return r


_cached: FieldRegistry | None = None


def load_field_registry(benchmark_dir: Path | None = None,
                        use_cache: bool = True) -> FieldRegistry:
    global _cached
    if benchmark_dir is None and use_cache and _cached is not None:
        return _cached
    d = Path(benchmark_dir or BENCHMARK_DIR)
    path = d / REGISTRY_FILE
    if not path.is_file():
        raise FileNotFoundError(
            f"field registry missing: {path}. Fields exist only via the "
            "registry (CAS 4.1); there is no code fallback."
        )
    reg = FieldRegistry(raw=json.loads(path.read_text(encoding="utf-8")), path=path)
    if benchmark_dir is None and use_cache:
        _cached = reg
    return reg
