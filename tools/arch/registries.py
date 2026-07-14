"""Exception and UNKNOWN governance.

An exception is a *time-boxed, owned, justified* suspension of a rule. An UNKNOWN is tracked
architectural debt. Both are registries, both are reviewable, and neither may grow silently.

The rule that makes this real: an EXPIRED exception stops suppressing. A suppression with no end
date is not an exception — it is a repeal, and repeals go through review, not through a JSON file.
"""
from __future__ import annotations

from .common import GOVERNANCE, load

_REQUIRED_EXC = ("id", "rule", "scope", "justification", "owner", "risk",
                 "mitigation", "expiry", "review_date", "removal_plan")
_REQUIRED_UNK = ("id", "question", "subsystem", "evidence", "owner", "risk",
                 "next_investigation", "status", "review_date")


def _read(name: str, key: str) -> list[dict]:
    p = GOVERNANCE / f"{name}.json"
    if not p.exists():
        return []
    return load(p).get(key, [])


def exceptions() -> list[dict]:
    return _read("exceptions", "exceptions")


def unknowns() -> list[dict]:
    return _read("unknowns", "unknowns")


def _expired(exc: dict, today: str) -> bool:
    """String compare on ISO-8601 dates. Deterministic, no clock import, no timezone argument."""
    return str(exc.get("expiry", "")) < today


def active_exceptions(today: str | None = None) -> list[dict]:
    """Exceptions that are still in force.

    `today` is INJECTED, never read from the wall clock inside the policy engine. A checker whose
    verdict depends on the second it runs is a checker whose verdict is not reproducible — and
    reproducibility is the whole product here. CI passes the commit date; the operator can pass any
    date to ask "what will be expired next month?".
    """
    if today is None:
        from datetime import date
        today = date.today().isoformat()
    return [e for e in exceptions() if not _expired(e, today)]


def validate() -> list[str]:
    """Structural problems in the registries themselves. Returns human-readable errors."""
    errs: list[str] = []
    seen: set[str] = set()

    for e in exceptions():
        eid = e.get("id", "<no id>")
        missing = [k for k in _REQUIRED_EXC if not e.get(k)]
        if missing:
            errs.append(f"exception {eid}: missing required field(s) {missing}. An exception "
                        f"without an owner, an expiry and a removal plan is an undocumented "
                        f"suppression, which is forbidden.")
        if eid in seen:
            errs.append(f"exception {eid}: duplicate id")
        seen.add(eid)
        from .policy import RULES
        if e.get("rule") and e["rule"] not in RULES:
            errs.append(f"exception {eid}: suppresses unknown rule {e['rule']!r}")

    seen.clear()
    for u in unknowns():
        uid = u.get("id", "<no id>")
        missing = [k for k in _REQUIRED_UNK if not u.get(k)]
        if missing:
            errs.append(f"unknown {uid}: missing required field(s) {missing}")
        if uid in seen:
            errs.append(f"unknown {uid}: duplicate id")
        seen.add(uid)
    return errs


def expired(today: str | None = None) -> list[dict]:
    if today is None:
        from datetime import date
        today = date.today().isoformat()
    return [e for e in exceptions() if _expired(e, today)]


def unknown_growth() -> tuple[int, int]:
    """(open unknowns, the approved ceiling). CI blocks growth above the ceiling."""
    p = GOVERNANCE / "unknowns.json"
    if not p.exists():
        return 0, 0
    doc = load(p)
    open_ = [u for u in doc.get("unknowns", []) if u.get("status", "open").lower() == "open"]
    return len(open_), int(doc.get("approved_ceiling", len(open_)))
