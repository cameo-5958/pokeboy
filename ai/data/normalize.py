"""Canonical-name lookup for foreign corpora (metamon/pokechamp lowercase ids)."""

from __future__ import annotations

import re

from sim.gen1data import MOVES, SPECIES


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


NORM_SPECIES = {norm(s): s for s in SPECIES}
NORM_MOVES = {norm(m): m for m in MOVES}

_STATUS = {
    "slp": "SLP",
    "par": "PAR",
    "brn": "BRN",
    "frz": "FRZ",
    "psn": "PSN",
    "tox": "TOX",
    "nostatus": None,
    "fnt": "FNT",
}


def canon_species(name: str) -> str | None:
    return NORM_SPECIES.get(norm(name))


def canon_move(name: str) -> str | None:
    return NORM_MOVES.get(norm(name))


def canon_status(status: str | None) -> str | None:
    if status is None:
        return None
    return _STATUS.get(status.lower(), None)
