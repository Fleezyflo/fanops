# src/fanops/casting.py — the crosspost SINGLE-OWNER affinity gate.
# After the P11 casting teardown (MOL-152) the LLM casting stage (request/ingest_moment_casting) and the
# durable AccountSelection are gone: `Moment.affinities` — stamped single-owner at pick (P5), operator-
# overridable via the Studio (P13) — is the SOLE crosspost-gate input. One pure predicate, no I/O, no ledger
# read. (The old token-overlap heuristic `cast_moments` was already DELETED in WS-M1/MOM-7; it stays gone.)
# C1-safe: reads only cfg + moment.affinities — never touches amplify/retire/cascade/track.
from __future__ import annotations


def affinity_admits(cfg, moment, account) -> bool:
    """THE crosspost gate predicate — single-owner Moment.affinities, WITH the missing-attribution DENY branch
    (the RF1/MOM-2 no-silent-fan-to-all guard carried forward):
      - casting OFF        -> admit all (A2 firewall: flag-OFF IGNORES persisted affinities)
      - moment is None     -> DENY (scrutiny — never the old admit-all)
      - affinities == []   -> the moment was persona-blind-picked -> fan-to-all (a legit unattributed moment)
      - affinities != []   -> admit iff `account` is an owner (single-owner: exactly the owner(s))
    There is NO silent fan-to-all for an ATTRIBUTED moment: an account absent from a non-empty affinities set is
    DENIED. (Operator override may deliberately co-own a moment — affinities is then a >1 owner set.)"""
    if not cfg.account_casting: return True
    if moment is None: return False
    if not moment.affinities: return True
    return account in moment.affinities
