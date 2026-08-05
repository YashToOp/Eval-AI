"""Why the demo fixture corpus cannot enter GAUNTLET (TD-B05, P2, CAS §5.4).

TD-B05 scheduled the 36-sample demo fixture for migration into DEV as
T3/noisy, blocked only on intake existing. Intake now exists, so the migration
was attempted — and it does not survive contact with the machinery. This file
is the attempt, kept as a permanent test so the conclusion is verifiable and
so nobody later "fixes" the fixture into the corpus by relaxing a check.

Two independent walls, one per half:

**The human half is model-produced text.** Its own metadata says "excerpt
transcribed from memory" — a model reconstructing public-domain prose, not
anyone transcribing a book. Declared honestly, that is model involvement in a
HUMAN-labelled candidate: P2, and X-1 makes the rejection automatic and "not
curable by editing or re-review". T3 and DEV lower the *provenance tier* bar;
they do not lower the P2 bar, because P2 is a §1.3 inviolable principle. The
original TD-B05 note treated this as a §3.2 sourcing blemish that "T3+DEV
tolerates". That was wrong, and the correction is recorded in the register.

**The AI half has no generation record.** The text is genuinely
model-authored, so the label is right, but BS §4.4 requires every AI-involved
sample to store model identifier and exact version, provider, request date,
the full prompt including system prompt, all decoding parameters, seeds, and
the raw unmodified response. None of that was captured, and BS §4.2 requires
T1 for all AI categories by construction. Producing the record after the fact
would be fabricating benchmark evidence.

The honest conclusion is that this fixture is a *detector* test set, which is
what it was built as, and that GAUNTLET's human and AI cells both need
material sourced under Section 3 from the start.
"""

import json
from pathlib import Path

import pytest

from ai_text_eval.gauntlet.evidence import (
    EvidenceItem,
    EvidenceKind,
    EvidencePackage,
    InadmissibleKind,
    supported_tier,
    validate_package,
)
from ai_text_eval.gauntlet.intake import Candidate, Declaration, check_generation_firewall

TS = "2026-08-05T00:00:00Z"
DATA = Path(__file__).resolve().parents[1] / "src" / "ai_text_eval" / "data"


def load(name):
    rows = [json.loads(line) for line in (DATA / name).read_text().splitlines() if line.strip()]
    assert rows, f"{name} is empty"
    return rows


HUMAN_ROWS = load("demo_human.jsonl")
AI_ROWS = load("demo_ai.jsonl")


def codes(report):
    return {f.code for f in report.findings}


def human_candidate(row, *, declared_involvement=True, evidence_items=None):
    """The fixture row as it would actually be submitted, declared honestly."""
    package = EvidencePackage(
        sample_id="H-01-B250-0001", tier="T3",
        items=evidence_items if evidence_items is not None else [
            EvidenceItem(kind=InadmissibleKind.RECOLLECTION.value,
                         recorded_at=TS,
                         attributes={"note": row["meta"]["note"],
                                     "source_work": row["source"]})])
    return Candidate(
        identifier="H-01-B250-0001", text=row["text"],
        metadata={"label": "HUMAN", "generator": None},
        evidence=package,
        declaration=Declaration(
            contributor="assistant", model_involved=declared_involvement,
            detail="text reconstructed from memory by a generative model"),
        target_label="HUMAN")


# =====================================================================
# The fixture describes itself
# =====================================================================


def test_the_fixture_is_thirty_six_samples():
    assert len(HUMAN_ROWS) == 18
    assert len(AI_ROWS) == 18


def test_every_human_row_declares_it_was_reconstructed_from_memory():
    """Not an inference about the text — the fixture says so itself."""
    for row in HUMAN_ROWS:
        assert "from memory" in row["meta"]["note"]


def test_every_ai_row_declares_it_was_assistant_authored():
    for row in AI_ROWS:
        assert "assistant-authored" in row["source"]


# =====================================================================
# Wall 1: the human half cannot enter the HUMAN class (P2, X-1)
# =====================================================================


@pytest.mark.parametrize("row", HUMAN_ROWS, ids=lambda r: r["source"][:24])
def test_every_human_fixture_row_is_rejected_by_the_generation_firewall(row):
    report = check_generation_firewall(human_candidate(row))
    assert not report.ok
    assert "FIREWALL_DECLARED_MODEL_INVOLVEMENT" in codes(report)


def test_the_rejection_is_not_curable_by_dropping_to_t3_and_dev():
    """T3 and DEV lower the provenance bar. P2 is not a provenance rule — it
    is a §1.3 inviolable principle, and X-1 makes this rejection automatic."""
    candidate = human_candidate(HUMAN_ROWS[0])
    candidate.evidence.tier = "T3"
    candidate.metadata["split"] = "dev"
    candidate.metadata["provenance_tier"] = "T3"
    candidate.metadata["noisy_label"] = True
    assert not check_generation_firewall(candidate).ok


def test_recollection_is_inadmissible_evidence_in_its_own_right():
    """§5.4 lists recollection among the evidence kinds that MUST NOT support
    a label. So even a contributor who declared nothing is stopped — by the
    evidence validator rather than the firewall."""
    report = validate_package(human_candidate(HUMAN_ROWS[0]).evidence,
                              label="HUMAN", claimed_tier="T3")
    assert "INADMISSIBLE_EVIDENCE" in codes(report)


def test_an_undeclared_submission_is_caught_by_the_evidence_not_the_firewall():
    """Documents where the boundary actually sits. The firewall's evidence
    detector looks for a generation session, and reconstruction-from-memory
    leaves none, so a contributor who declared `model_involved=False` would
    clear the firewall. What stops them is §5.4: their only evidence is
    recollection, and recollection supports nothing.
    """
    candidate = human_candidate(HUMAN_ROWS[0], declared_involvement=False)
    assert check_generation_firewall(candidate).ok          # firewall passes
    assert "INADMISSIBLE_EVIDENCE" in codes(                # evidence does not
        validate_package(candidate.evidence, label="HUMAN", claimed_tier="T3"))


def test_at_t3_the_last_wall_is_the_declaration_and_nothing_mechanical():
    """The honest limit of the machinery, stated rather than papered over.

    Declare no involvement, attach no evidence, claim T3: the firewall passes
    (no generation session to find) and the evidence validator passes (T3 is
    the tier that means "unverified" — an empty package is what it *is*). No
    mechanical check stands between the fixture and DEV on that path.

    That is inherent, not a gap to close here. Detecting model involvement
    from records that do not exist is impossible, and detecting it from the
    text is inadmissible under P3. The specification's answer is that the
    declaration binds and §11.6 quarantines everything a contributor touched
    if it turns out to be false; the containment is that T3 implies DEV, whose
    numbers must never be reported as results (BS §2.3).

    So the fixture is not migrated because the truthful declaration blocks it,
    not because a validator would have caught the lie.
    """
    candidate = human_candidate(HUMAN_ROWS[0], declared_involvement=False,
                                evidence_items=[])
    assert check_generation_firewall(candidate).ok
    report = validate_package(candidate.evidence, label="HUMAN", claimed_tier="T3")
    assert report.ok
    assert "TIER_HEURISTIC" in codes(report)   # flagged as unverified, not clean


def test_an_empty_package_still_fails_at_every_tier_above_t3():
    """The escape above is available only at the tier that already means
    "unverified", and T3 is confined to DEV by §4.2."""
    for tier in ("T0", "T1", "T2"):
        package = EvidencePackage(sample_id="H-01-B250-0001", tier=tier, items=[])
        assert "EVIDENCE_PACKAGE_EMPTY" in codes(
            validate_package(package, label="HUMAN", claimed_tier=tier))


def test_the_human_half_could_only_enter_as_t0_with_a_real_archive_record():
    """The remedy, stated mechanically: T0 is supported by an archive record,
    which means the actual archived source, not a reconstruction of it."""
    from ai_text_eval.gauntlet.evidence import TIER_SUPPORTING_KINDS
    assert TIER_SUPPORTING_KINDS["T0"] == frozenset({EvidenceKind.ARCHIVE_RECORD.value})
    assert InadmissibleKind.RECOLLECTION.value not in TIER_SUPPORTING_KINDS["T0"]


# =====================================================================
# Wall 2: the AI half has no BS §4.4 generation record
# =====================================================================


def test_the_ai_half_has_no_generation_session_evidence():
    """Nothing in the fixture carries a model version, prompt, decoding
    parameters, or raw response — the BS §4.4 required set."""
    for row in AI_ROWS:
        assert set(row) == {"text", "label", "source", "meta"}
        assert "model" not in row["meta"]
        assert "prompt" not in row["meta"]


def test_an_ai_candidate_without_a_generation_record_cannot_support_t1():
    package = EvidencePackage(sample_id="A-01-B250-0001", tier="T1", items=[])
    report = validate_package(package, label="AI", claimed_tier="T1")
    assert "T1_WRONG_EVIDENCE_KIND" in codes(report)
    assert "EVIDENCE_PACKAGE_EMPTY" in codes(report)
    assert supported_tier(package) is None


def test_t1_is_required_for_ai_categories_by_construction():
    """BS §4.2: "All AI categories require T1 by construction." Unlike the
    human rule, this one is not scoped to TEST/HIDDEN, so dropping the AI half
    into DEV does not rescue it either."""
    spec = (Path(__file__).resolve().parents[1] / "docs" / "gauntlet-v1.0-spec.txt").read_text()
    assert "All AI categories require T1 by construction" in spec


def test_a_placeholder_generation_record_is_rejected():
    """The obvious shortcut — fill the required fields with "unknown" — does
    not work: §3.3 requires the *complete* record, and an empty decoding block
    is an incomplete one."""
    placeholder = EvidenceItem(
        kind=EvidenceKind.GENERATION_SESSION.value, path="never-existed.json",
        checksum="sha256:" + "0" * 64, recorded_at=TS,
        attributes={"model_family": "unknown", "model_version": "unknown",
                    "provider": "unknown", "prompt": "unknown", "decoding": {},
                    "request_date": "unknown", "raw_response": "unknown"})
    package = EvidencePackage(sample_id="A-01-B250-0001", tier="T1",
                              items=[placeholder])
    report = validate_package(package, label="AI", claimed_tier="T1")
    assert "GENERATION_RECORD_INCOMPLETE" in codes(report)


def test_a_fully_invented_generation_record_would_validate():
    """The limit of what a validator can do, stated plainly.

    A complete-looking record for a session that never happened passes every
    structural check, because structure is all a validator sees. The bar
    against it is conduct — "do not fabricate benchmark evidence" — backed by
    §5.6 spot verification and audit-time regeneration (BS §4.4), not by this
    function. Recording that here is the point: the shortcut is named, so it
    cannot be taken by accident.
    """
    invented = EvidenceItem(
        kind=EvidenceKind.GENERATION_SESSION.value, path="never-existed.json",
        checksum="sha256:" + "0" * 64, recorded_at=TS,
        attributes={"model_family": "some-model", "model_version": "1.0",
                    "provider": "some-provider", "prompt": "write an essay",
                    "decoding": {"temperature": 1.0}, "request_date": "2026-01-01",
                    "raw_response": "..."})
    package = EvidencePackage(sample_id="A-01-B250-0001", tier="T1",
                              items=[invented])
    assert validate_package(package, label="AI", claimed_tier="T1").ok


# =====================================================================
# The corpus stays empty until real material exists
# =====================================================================


def test_no_fixture_sample_has_been_written_into_the_corpus():
    corpus = Path(__file__).resolve().parents[1] / "corpus" / "samples"
    written = [line for path in sorted(corpus.glob("*.jsonl"))
               for line in path.read_text().splitlines() if line.strip()]
    assert written == [], (
        "the corpus contains samples; every one must have entered through the "
        "lifecycle with an admissible evidence package (TD-B05)")
