"""Logged Writing Environment — evidence by default for T1 human writing.

CAS §3.2 admits commissioned human text only when written "under process
logging … in an environment attested to be free of generative writing tools",
and prohibits text from tools offering generative rewriting "unless the
session log proves it". This package produces that log.

Design: `docs/LWE_DESIGN.md`.

The package is usable as an independent application: `journal` and `session`
import nothing from GAUNTLET, and only `export` crosses the boundary.
"""

from ai_text_eval.lwe.journal import (
    EventKind,
    Journal,
    JournalError,
    Verification,
    canonical_hash,
)

__all__ = [
    "EventKind",
    "Journal",
    "JournalError",
    "Verification",
    "canonical_hash",
]

LWE_VERSION = "0.1.0"
