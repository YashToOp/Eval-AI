"""Evidence packages and chain of custody (R-05, CAS §5).

Provenance is the evidence-backed account of how a text came to exist, and it
is the *sole* source of ground truth (P1). This module models the evidence
package, checks that it actually supports the tier a sample claims, verifies
integrity by checksum, and enforces the derivation rule of §5.5.

Three design commitments:

1. **Tier is earned, not asserted.** A record claiming T1 with no process
   capture is not "T1 with a paperwork gap"; it is not T1. `validate_package`
   reports the tier the evidence supports and flags any overclaim.

2. **Inadmissible evidence is named, not ignored.** §5.4 lists what MUST NOT
   support a label — stylistic judgment, detector output, platform signals,
   recollection, partial records, contributor reputation. The vocabulary is
   modelled explicitly so a package containing such an item is rejected
   rather than silently carrying it. Detector output in particular would make
   the benchmark circular (P3).

3. **Derived provenance can only weaken.** §5.5: a derived sample's
   provenance is exactly (base evidence + transform record) and can never
   exceed its base's tier. `derived_tier` implements the ceiling.

This module validates *packages*. It does not read the evidence contents —
replaying a diff chain or recomputing a token share is R-06's job, and
comparing a package against the corpus is R-08's.

The package manifest format below is an implementation choice; CAS §5
deliberately specifies no storage format. Any layout preserving the same
normative facts is conformant.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path

from ai_text_eval.gauntlet.findings import Report

#: §5.2: T0 requires capture by an independent archive *before* this date.
#: Generative writing assistance after this point is widespread enough that
#: publication on the open web stops being evidence of purely human production
#: (CAS §3.2).
ARCHIVE_CUTOFF = date(2020, 1, 1)

#: The evidence package manifest filename inside a package directory.
PACKAGE_MANIFEST = "evidence.json"


class EvidenceKind(str, Enum):
    """Acceptable evidence classes (CAS §5.3)."""

    ARCHIVE_RECORD = "archive_record"
    PROCESS_CAPTURE = "process_capture"
    GENERATION_SESSION = "generation_session"
    INTERMEDIATE_CHAIN = "intermediate_chain"
    ATTESTATION = "attestation"
    TRANSFORM_RECORD = "transform_record"


class InadmissibleKind(str, Enum):
    """Evidence that MUST NOT support a label, alone or combined (CAS §5.4).

    Modelled explicitly so a package carrying one is rejected with a reason,
    rather than the item passing unrecognised.
    """

    STYLISTIC_JUDGMENT = "stylistic_judgment"
    DETECTOR_OUTPUT = "detector_output"
    PLATFORM_SIGNAL = "platform_signal"
    RECOLLECTION = "recollection"
    PARTIAL_RECORD = "partial_record"
    REPUTATION = "reputation"


ACCEPTABLE_KINDS = frozenset(k.value for k in EvidenceKind)
INADMISSIBLE_KINDS = frozenset(k.value for k in InadmissibleKind)

#: Which evidence kinds can support each tier (CAS §5.2).
#: T3 is provenance *inferred* — it is supported by nothing, which is why it
#: is admissible in DEV only and never gates a headline metric.
TIER_SUPPORTING_KINDS: dict[str, frozenset[str]] = {
    "T0": frozenset({EvidenceKind.ARCHIVE_RECORD.value}),
    "T1": frozenset({
        EvidenceKind.PROCESS_CAPTURE.value,
        EvidenceKind.GENERATION_SESSION.value,
        EvidenceKind.INTERMEDIATE_CHAIN.value,
    }),
    "T2": frozenset({EvidenceKind.ATTESTATION.value}),
    "T3": frozenset(),
}

#: Tier strength order, weakest last. Used by the §5.5 derivation ceiling.
TIER_ORDER = ("T0", "T1", "T2", "T3")

#: Per-label T1 evidence requirement (CAS §5.2): process capture for human
#: writing, complete session transcripts for generation, stored intermediate
#: chains for hybrids.
T1_KIND_FOR_LABEL: dict[str, str] = {
    "HUMAN": EvidenceKind.PROCESS_CAPTURE.value,
    "AI": EvidenceKind.GENERATION_SESSION.value,
    "AI_HUMAN_EDITED": EvidenceKind.INTERMEDIATE_CHAIN.value,
    "HUMAN_AI_EDITED": EvidenceKind.INTERMEDIATE_CHAIN.value,
    "COLLAB_MIXED": EvidenceKind.INTERMEDIATE_CHAIN.value,
}


def file_checksum(path: Path) -> str:
    """SHA-256 of a file's bytes, prefixed with its algorithm."""
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class EvidenceItem:
    """One artefact inside an evidence package."""

    kind: str
    path: str = ""
    checksum: str = ""
    recorded_at: str = ""
    attributes: dict = field(default_factory=dict)

    @property
    def is_acceptable_kind(self) -> bool:
        return self.kind in ACCEPTABLE_KINDS

    @property
    def is_inadmissible_kind(self) -> bool:
        return self.kind in INADMISSIBLE_KINDS

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "path": self.path, "checksum": self.checksum,
            "recorded_at": self.recorded_at, "attributes": self.attributes,
        }

    @staticmethod
    def from_dict(raw: dict) -> "EvidenceItem":
        return EvidenceItem(
            kind=str(raw.get("kind", "")),
            path=str(raw.get("path", "")),
            checksum=str(raw.get("checksum", "")),
            recorded_at=str(raw.get("recorded_at", "")),
            attributes=raw.get("attributes") or {},
        )


@dataclass
class EvidencePackage:
    """The retained evidence behind one sample's label (CAS §5)."""

    sample_id: str
    tier: str
    items: list[EvidenceItem] = field(default_factory=list)
    intake_checksummed_at: str = ""
    base_package_ref: str = ""   # §5.5: derived packages name their base
    root: Path | None = None
    notes: str = ""

    def kinds(self) -> set[str]:
        return {i.kind for i in self.items}

    def of_kind(self, kind: str | EvidenceKind) -> list[EvidenceItem]:
        want = kind.value if isinstance(kind, EvidenceKind) else kind
        return [i for i in self.items if i.kind == want]

    def to_dict(self) -> dict:
        return {
            "sample_id": self.sample_id,
            "tier": self.tier,
            "items": [i.to_dict() for i in self.items],
            "intake_checksummed_at": self.intake_checksummed_at,
            "base_package_ref": self.base_package_ref,
            "notes": self.notes,
        }

    @staticmethod
    def from_dict(raw: dict, root: Path | None = None) -> "EvidencePackage":
        return EvidencePackage(
            sample_id=str(raw.get("sample_id", "")),
            tier=str(raw.get("tier", "")),
            items=[EvidenceItem.from_dict(i) for i in raw.get("items", [])
                   if isinstance(i, dict)],
            intake_checksummed_at=str(raw.get("intake_checksummed_at", "")),
            base_package_ref=str(raw.get("base_package_ref", "")),
            root=root,
            notes=str(raw.get("notes", "")),
        )


def load_package(package_dir: Path) -> EvidencePackage:
    """Read an evidence package from a directory containing evidence.json."""
    d = Path(package_dir)
    manifest = d / PACKAGE_MANIFEST
    if not manifest.is_file():
        raise FileNotFoundError(
            f"evidence package manifest not found: {manifest}. A candidate "
            "without a complete evidence package does not enter validation "
            "(CAS §3.1)."
        )
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ValueError(f"{manifest}: invalid JSON: {err}") from err
    return EvidencePackage.from_dict(raw, root=d)


def supported_tier(package: EvidencePackage) -> str | None:
    """Strongest tier the package's evidence kinds can support.

    Returns None when nothing in the package supports any tier above T3.
    This is the *ceiling from evidence kinds alone*; per-tier detail checks
    (archive dates, attestation completeness) run in `validate_package`.
    """
    kinds = package.kinds()
    for tier in TIER_ORDER:          # strongest first
        if tier == "T3":
            continue
        if kinds & TIER_SUPPORTING_KINDS[tier]:
            return tier
    return None


def derived_tier(base_tier: str, transform_recorded: bool) -> str:
    """Tier for a sample derived from a base by transform (CAS §5.5).

    A derived sample's provenance is exactly (base evidence + transform
    record) and can never exceed its base's tier. Without a transform record
    the derivation is undocumented and the result is heuristic (T3).
    """
    if base_tier not in TIER_ORDER:
        raise ValueError(f"unknown base tier {base_tier!r}")
    if not transform_recorded:
        return "T3"
    return base_tier


def validate_package(package: EvidencePackage, label: str | None = None,
                     claimed_tier: str | None = None,
                     root: Path | None = None) -> Report:
    """Check that a package supports its tier and contains only admissible
    evidence (CAS §5.2–5.4). Integrity is checked by `verify_integrity`.
    """
    r = Report(checked=1)
    sid = package.sample_id or "<unknown>"
    tier = claimed_tier or package.tier

    if tier not in TIER_ORDER:
        r.error("CAS 5.2", "EVIDENCE_BAD_TIER",
                f"tier {tier!r} is not one of {TIER_ORDER}", sid)
        return r

    if not package.items and tier != "T3":
        r.error("CAS 3.1", "EVIDENCE_PACKAGE_EMPTY",
                f"tier {tier} requires evidence; the package is empty", sid)

    # §5.4: inadmissible evidence, alone or in combination.
    for item in package.items:
        if item.is_inadmissible_kind:
            r.error("CAS 5.4", "INADMISSIBLE_EVIDENCE",
                    f"evidence of kind {item.kind!r} MUST NOT support a label; "
                    "it is listed as unacceptable", sid)
        elif not item.is_acceptable_kind:
            r.error("CAS 5.3", "UNKNOWN_EVIDENCE_KIND",
                    f"evidence kind {item.kind!r} is not in the acceptable "
                    f"vocabulary {sorted(ACCEPTABLE_KINDS)}", sid)

    # Every item must be checksummed at intake (§5.5, P4).
    for item in package.items:
        if item.is_acceptable_kind and not item.checksum:
            r.error("CAS 5.5", "EVIDENCE_NOT_CHECKSUMMED",
                    f"{item.kind} item {item.path or '<no path>'} has no "
                    "checksum; packages are checksummed at intake", sid)

    # Does the evidence support the claimed tier?
    if tier != "T3":
        available = supported_tier(package)
        if available is None:
            r.error("CAS 5.2", "TIER_UNSUPPORTED",
                    f"tier {tier} is claimed but no evidence kind in the package "
                    f"supports any tier; kinds present: "
                    f"{sorted(package.kinds()) or 'none'}", sid)
        elif TIER_ORDER.index(available) > TIER_ORDER.index(tier):
            r.error("CAS 5.2", "TIER_OVERCLAIM",
                    f"tier {tier} is claimed but the evidence supports only "
                    f"{available}; tier is earned by evidence, not asserted", sid)

    # Per-tier detail checks.
    if tier == "T0":
        r.extend(_check_archive(package, sid))
    elif tier == "T1":
        r.extend(_check_process(package, sid, label))
    elif tier == "T2":
        r.extend(_check_attestation(package, sid))
    elif tier == "T3":
        # Heuristic provenance carries no evidence requirement, but it is
        # admissible in DEV only and never gates a metric (§5.2). Split
        # admissibility is enforced by validate_splits.
        r.warn("CAS 5.2", "TIER_HEURISTIC",
               "T3 provenance is inferred; admissible in DEV only and never "
               "used for any headline or gating metric", sid)

    return r


def _check_archive(package: EvidencePackage, sid: str) -> Report:
    """T0: an independent archive captured the text before 2020 and can prove
    when (CAS §5.2, §5.3)."""
    r = Report()
    for item in package.of_kind(EvidenceKind.ARCHIVE_RECORD):
        attrs = item.attributes
        captured = attrs.get("capture_date")
        if not captured:
            r.error("CAS 5.3", "ARCHIVE_NO_CAPTURE_DATE",
                    "archive record must carry a capture timestamp", sid)
        else:
            try:
                captured_date = date.fromisoformat(str(captured)[:10])
            except ValueError:
                r.error("CAS 5.3", "ARCHIVE_BAD_CAPTURE_DATE",
                        f"capture_date {captured!r} is not an ISO date", sid)
            else:
                if captured_date >= ARCHIVE_CUTOFF:
                    r.error("CAS 5.2", "ARCHIVE_TOO_RECENT",
                            f"T0 requires capture before {ARCHIVE_CUTOFF.isoformat()}; "
                            f"this record was captured {captured_date.isoformat()}", sid)
        if not attrs.get("archive"):
            r.error("CAS 5.3", "ARCHIVE_NOT_IDENTIFIED",
                    "archive record must name the archive", sid)
        if attrs.get("independent") is not True:
            r.error("CAS 5.3", "ARCHIVE_NOT_INDEPENDENT",
                    "the archive must predate and not be controlled by the "
                    "contributor; set independent=true with justification", sid)
        if not attrs.get("integrity_record"):
            r.error("CAS 5.3", "ARCHIVE_NO_INTEGRITY_RECORD",
                    "archive record must retain the archive's integrity data", sid)
    return r


def _check_process(package: EvidencePackage, sid: str, label: str | None) -> Report:
    """T1: the production process itself was recorded (CAS §5.2)."""
    r = Report()
    if label:
        required = T1_KIND_FOR_LABEL.get(label)
        if required and required not in package.kinds():
            r.error("CAS 5.2", "T1_WRONG_EVIDENCE_KIND",
                    f"label {label} at T1 requires {required!r} evidence; "
                    f"package has {sorted(package.kinds()) or 'none'}", sid)

    for item in package.of_kind(EvidenceKind.PROCESS_CAPTURE):
        attrs = item.attributes
        if attrs.get("logging_started_before_writing") is not True:
            r.error("CAS 3.2", "LOGGING_NOT_PRE_ARRANGED",
                    "the logging arrangement MUST be in place before writing "
                    "begins; retroactive logging does not exist", sid)
        if attrs.get("complete_session") is not True:
            r.error("CAS 5.4", "PARTIAL_SESSION_RECORD",
                    "excerpts of session records are inadmissible where the "
                    "complete record was not retained", sid)
        if attrs.get("generative_tools_attested_absent") is not True:
            r.error("CAS 3.2", "ENVIRONMENT_NOT_ATTESTED",
                    "the writing environment must be attested free of "
                    "generative writing tools", sid)

    for item in package.of_kind(EvidenceKind.GENERATION_SESSION):
        attrs = item.attributes
        for required_field in ("model_family", "model_version", "provider",
                               "prompt", "decoding", "request_date",
                               "raw_response"):
            if not attrs.get(required_field):
                r.error("CAS 3.3", "GENERATION_RECORD_INCOMPLETE",
                        f"generation session record missing {required_field!r}; "
                        "the complete record is the provenance", sid)

    for item in package.of_kind(EvidenceKind.INTERMEDIATE_CHAIN):
        attrs = item.attributes
        states = attrs.get("states")
        if not isinstance(states, list) or len(states) < 2:
            r.error("CAS 3.4", "CHAIN_TOO_SHORT",
                    "a hybrid chain must retain the base text and at least one "
                    "edit round as stored intermediate states", sid)
        if not attrs.get("instructions"):
            r.error("CAS 3.4", "CHAIN_NO_INSTRUCTIONS",
                    "the instruction given to the editor defines the category "
                    "and must be recorded", sid)
    return r


def _check_attestation(package: EvidencePackage, sid: str) -> Report:
    """T2: a named, contactable author signed a contemporaneous description
    and accepted spot verification (CAS §5.2, §5.3)."""
    r = Report()
    for item in package.of_kind(EvidenceKind.ATTESTATION):
        attrs = item.attributes
        if not attrs.get("author_identified"):
            r.error("CAS 5.3", "ATTESTATION_ANONYMOUS",
                    "attestation must come from a named, contactable author", sid)
        if not attrs.get("signature"):
            r.error("CAS 5.3", "ATTESTATION_UNSIGNED",
                    "attestation must be signed", sid)
        if attrs.get("contemporaneous") is not True:
            r.error("CAS 5.4", "ATTESTATION_RECONSTRUCTED",
                    "process descriptions reconstructed after the fact are "
                    "inadmissible; the attestation must be contemporaneous", sid)
        if attrs.get("spot_verification_acknowledged") is not True:
            r.error("CAS 5.3", "ATTESTATION_NO_VERIFICATION_RIGHT",
                    "attestation must acknowledge the spot-verification right", sid)
        if not attrs.get("tools_described"):
            r.error("CAS 5.3", "ATTESTATION_NO_TOOL_DESCRIPTION",
                    "attestation must describe the tools and process used", sid)
    return r


def verify_integrity(package: EvidencePackage, root: Path | None = None) -> Report:
    """Recompute checksums for every referenced artefact (CAS §5.5, P4).

    Integrity is verified against the retained files. A package whose files
    are missing or altered cannot support any tier, because the evidence no
    longer exists in the form that was checksummed at intake.
    """
    r = Report(checked=len(package.items))
    base = Path(root) if root is not None else package.root
    sid = package.sample_id or "<unknown>"
    if base is None:
        r.warn("CAS 5.5", "INTEGRITY_NOT_CHECKED",
               "no package root available; checksums were not recomputed", sid)
        return r

    for item in package.items:
        if not item.path:
            continue
        path = base / item.path
        if not path.is_file():
            r.error("CAS 5.5", "EVIDENCE_FILE_MISSING",
                    f"referenced artefact {item.path!r} is absent; evidence must "
                    "be retained for the life of the corpus", sid)
            continue
        if not item.checksum:
            continue  # already reported by validate_package
        actual = file_checksum(path)
        if actual != item.checksum:
            r.error("CAS 5.5", "EVIDENCE_CHECKSUM_MISMATCH",
                    f"{item.path}: manifest records {item.checksum[:19]}…, "
                    f"file hashes to {actual[:19]}…", sid)
    return r


def checksum_package(package: EvidencePackage, root: Path | None = None,
                     stamped_at: str = "") -> EvidencePackage:
    """Populate missing checksums from the artefacts on disk (intake, §5.5).

    Used once, at intake. It never *overwrites* an existing checksum: doing so
    would let an altered artefact be re-blessed, which is the failure the
    chain of custody exists to prevent.
    """
    base = Path(root) if root is not None else package.root
    if base is None:
        raise ValueError("checksum_package needs a package root")
    for item in package.items:
        if item.path and not item.checksum:
            path = base / item.path
            if path.is_file():
                item.checksum = file_checksum(path)
    if stamped_at:
        package.intake_checksummed_at = stamped_at
    return package
