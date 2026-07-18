"""R1–R4: the declaration/lifecycle split, the digest `D`, and the closed metadata grammar.

THE ONLY MODULE THAT HANDLES UNTRUSTED BYTES. One failure family (syntax), one next actor (the
author). Everything downstream receives typed records or nothing at all — parsing never returns
partial results, because a half-parsed contract is a contract whose approved extent is unknown.

ADR-0105 §11 calls the head "YAML front matter" because it is SHAPED like the existing ADR heads.
This parser implements the closed contract subset defined by operator decision D-1 and NOT the YAML
language. A document a YAML library would accept but this grammar rejects is invalid, deliberately.
Twelve constructs are rejected by name (`_UNSUPPORTED` below), each because it lets the effective
meaning of an approved document be decided somewhere other than in its own visible bytes — an
anchor defined elsewhere, a merge key pulling in another mapping, a block scalar whose chomping
rules change the bytes that approval binds to.

No third-party parser is used, and none is needed: this grammar is smaller than any library's.
"""
from __future__ import annotations

import hashlib
import re

from .model import (ALL_FIELDS, FIELD_TYPES, FRONTMATTER_FIELDS, MALFORMED, MISSING, PROSE_FIELDS,
                    TABLE_COLUMNS, TABLE_FIELDS, UNKNOWN, UNSUPPORTED, Declaration, Diagnostic,
                    Field, LifecycleEvent)

BOUNDARY = b"\n## Lifecycle\n"

_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_HEADING = re.compile(r"^###\s+(?P<name>[a-z][a-z0-9_]*)\s*$")
_EVENT_COLUMNS = ("timestamp", "event", "values")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Every construct outside the grammar, with the code the diagnostic carries. Order matters only for
# reporting: the first match on a line wins, so a line carrying two problems names one of them
# rather than emitting two diagnostics for one defect.
_UNSUPPORTED = (
    ("UNSUP-TAB", "a tab character"),
    ("UNSUP-MERGE", "a merge key `<<:`"),
    ("UNSUP-ANCHOR", "an anchor `&name`"),
    ("UNSUP-ALIAS", "an alias `*name`"),
    ("UNSUP-TAG", "a type tag `!tag` / `!!type`"),
    ("UNSUP-BLOCK-SCALAR", "a block scalar `|` / `>`"),
    ("UNSUP-FLOW-MAP", "a flow mapping `{ … }`"),
    ("UNSUP-COMMENT", "a comment `#`"),
    ("UNSUP-NESTED", "a nested mapping"),
)


def digest(decl: bytes) -> str:
    return "sha256:" + hashlib.sha256(decl).hexdigest()


def split(raw: bytes) -> tuple[bytes, bytes, int]:
    """Return (declaration bytes, lifecycle bytes, boundary count).

    The byte semantics are ADR-0105 §3's REFERENCE IMPLEMENTATION — `raw.split(b"\\n## Lifecycle\\n",
    1)[0]` — not the prose gloss beside it, which reads "up to, and excluding, the first line that is
    exactly `## Lifecycle`" and is ambiguous about the newline that terminates the preceding line.
    The two readings differ by exactly one byte and therefore by the whole digest. Constitution C2.1
    ranks executable source above prose, and the reference implementation is executable.
    """
    n = raw.count(BOUNDARY) + (1 if raw.startswith(BOUNDARY[1:]) else 0)
    if not n:
        return raw, b"", 0
    if raw.startswith(BOUNDARY[1:]):
        return b"", raw, n
    decl, life = raw.split(BOUNDARY, 1)
    return decl, BOUNDARY[1:] + life, n


def parse(raw: bytes, path: str = "") -> Declaration:
    """Parse a whole contract file. Never raises on bad input; it returns diagnostics."""
    diags: list[Diagnostic] = []
    decl, life, boundaries = split(raw)
    d = Declaration(path=path, digest=digest(decl), raw=raw, decl_bytes=decl,
                    boundary_count=boundaries)

    if b"\r\n" in raw:
        i = raw.index(b"\r\n")
        diags.append(Diagnostic(UNSUPPORTED, "UNSUP-CRLF", "the file uses CRLF line endings",
                                line=raw[:i].count(b"\n") + 1, got="\\r\\n", expected="\\n",
                                remediation="rewrite the file with LF endings; the `## Lifecycle` "
                                            "separator and the digest are both byte-literal"))
        return _with(d, diagnostics=tuple(diags))

    if boundaries == 0:
        diags.append(Diagnostic(MISSING, "NO-BOUNDARY", "no `## Lifecycle` boundary line",
                                expected="a line exactly `## Lifecycle`",
                                remediation="add the boundary; without it the declaration extent, "
                                            "and therefore `D`, is undefined"))
        return _with(d, diagnostics=tuple(diags))
    if boundaries > 1:
        diags.append(Diagnostic(MALFORMED, "MULTI-BOUNDARY",
                                f"{boundaries} `## Lifecycle` boundary lines; the declaration "
                                f"extent is ambiguous", got=str(boundaries), expected="1",
                                remediation="exactly one boundary line may appear in the file"))
        return _with(d, diagnostics=tuple(diags))

    text = decl.decode("utf-8", errors="replace")
    fields, fdiags = _front_matter(text)
    sfields, sdiags = _sections(text, _body_offset(text))
    fields += sfields
    events, ediags = _lifecycle(life.decode("utf-8", errors="replace"))
    return _with(d, fields=tuple(fields), events=tuple(events),
                 diagnostics=tuple(fdiags + sdiags + ediags))


def _with(d: Declaration, **kw) -> Declaration:
    base = {"path": d.path, "digest": d.digest, "raw": d.raw, "decl_bytes": d.decl_bytes,
            "fields": d.fields, "events": d.events, "diagnostics": d.diagnostics,
            "boundary_count": d.boundary_count}
    return Declaration(**{**base, **kw})


# ── front matter ────────────────────────────────────────────────────────────────────────────
def _body_offset(text: str) -> int:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i + 1
    return 0


def _unsupported_on(line: str) -> tuple[str, str] | None:
    """The first grammar violation on one front-matter line, or None.

    `#` is the delicate one, because `#` is IN the SAFE character set (a bare value may contain
    `PR#702`) while a comment must be rejected. Both are true at once under exactly one reading: a
    `#` that would START a comment — at line start, or preceded by whitespace — is a comment; a `#`
    inside a token is part of the value. Rejecting rather than stripping is deliberate: a stripped
    comment is semantically inert but byte-relevant, and the bytes are what approval binds to.
    """
    if "\t" in line: return "UNSUP-TAB", "a tab character"
    s = line.strip()
    if s.startswith("<<:") or s.startswith("<<"): return "UNSUP-MERGE", "a merge key `<<:`"
    body = s.split(":", 1)[1].strip() if ":" in s else s
    if body.startswith("&"): return "UNSUP-ANCHOR", "an anchor `&name`"
    if body.startswith("*"): return "UNSUP-ALIAS", "an alias `*name`"
    if body.startswith("!"): return "UNSUP-TAG", "a type tag `!tag` / `!!type`"
    if body in ("|", ">", "|-", ">-", "|+", ">+"): return "UNSUP-BLOCK-SCALAR", "a block scalar"
    if body.startswith("{"): return "UNSUP-FLOW-MAP", "a flow mapping `{ … }`"
    if _comment_at(line) is not None: return "UNSUP-COMMENT", "a comment `#`"
    return None


def _comment_at(line: str) -> int | None:
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"': quoted = not quoted
        elif ch == "#" and not quoted and (i == 0 or line[i - 1] in " \t"): return i
    return None


def _front_matter(text: str) -> tuple[list[Field], list[Diagnostic]]:
    fields: list[Field] = []
    diags: list[Diagnostic] = []
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        diags.append(Diagnostic(MISSING, "NO-FRONTMATTER", "the file has no `---` front matter",
                                line=1, expected="a first line exactly `---`"))
        return fields, diags

    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        diags.append(Diagnostic(MALFORMED, "UNCLOSED-FRONTMATTER",
                                "the front matter is never closed by a `---` line", line=1))
        return fields, diags

    nxt = next((line.strip() for line in lines[close + 1:] if line.strip()), "")
    if nxt in ("---", "..."):
        diags.append(Diagnostic(UNSUPPORTED, "UNSUP-MULTIDOC",
                                "a second YAML document begins after the front matter",
                                line=close + 2, got=nxt, expected="one document per file"))
        return fields, diags

    offset = len(lines[0]) + 1
    pending: tuple[str, int, int] | None = None       # an open block list: (key, line, start)
    items: list[str] = []
    seen: dict[str, int] = {}

    for n in range(1, close):
        line, start = lines[n], offset
        offset += len(line) + 1
        if not line.strip():
            continue
        bad = _unsupported_on(line) if not line.startswith("  - ") else _unsupported_on(line.strip())
        if line.startswith("  - "):
            if pending is None:
                diags.append(Diagnostic(MALFORMED, "ORPHAN-ITEM", "a list item with no key above it",
                                        line=n + 1, got=line.strip()))
                continue
            if bad:
                diags.append(_unsup(bad, n + 1, line))
                continue
            items.append(_scalar(line[4:].strip()))
            continue
        if bad:
            diags.append(_unsup(bad, n + 1, line))
            continue
        if line[:1] in (" ",):
            diags.append(Diagnostic(UNSUPPORTED, "UNSUP-NESTED",
                                    "an indented mapping — only three location kinds exist "
                                    "(front matter, prose section, table)", line=n + 1,
                                    got=line.strip(), expected="a top-level `key: value`"))
            continue

        if pending is not None:
            key, kline, kstart = pending
            fields.append(Field(key, items, kline, kstart, start))
            pending, items = None, []

        if ":" not in line:
            diags.append(Diagnostic(MALFORMED, "NO-COLON", "a front-matter line with no `key:`",
                                    line=n + 1, got=line.strip()))
            continue
        key, rest = line.split(":", 1)
        if not _KEY.match(key):
            diags.append(Diagnostic(MALFORMED, "BAD-KEY", f"{key.strip()!r} is not a valid key",
                                    line=n + 1, got=key.strip(), expected="^[a-z][a-z0-9_]*$"))
            continue
        if key in seen:
            diags.append(Diagnostic(MALFORMED, "DUP-KEY",
                                    f"key {key!r} appears twice (first at line {seen[key]}) — "
                                    f"last-wins would make the approved text ambiguous",
                                    line=n + 1, got=key, expected="each key at most once"))
            continue
        seen[key] = n + 1
        if key not in FRONTMATTER_FIELDS:
            diags.append(Diagnostic(UNKNOWN, "UNKNOWN-KEY",
                                    f"{key!r} is not a declaration field", line=n + 1, got=key,
                                    expected="one of " + ", ".join(FRONTMATTER_FIELDS),
                                    remediation="the field set is CLOSED: a contract cannot invent "
                                                "a field, which is what keeps it from granting "
                                                "authority ADR-0105 §2 makes narrowing-only"))
            continue

        rest = rest.strip()
        if not rest:
            pending, items = (key, n + 1, start), []
            continue
        fields.append(Field(key, _value(rest, key), n + 1, start, offset))

    if pending is not None:
        key, kline, kstart = pending
        fields.append(Field(key, items, kline, kstart, offset))
    return fields, diags


def _unsup(bad: tuple[str, str], line: int, got: str) -> Diagnostic:
    code, what = bad
    return Diagnostic(UNSUPPORTED, code, f"{what} is outside the contract metadata grammar",
                      line=line, got=got.strip()[:60], expected="the closed subset (ADR-0105 §6)",
                      remediation="rewrite the value as a plain scalar, a `[a, b]` inline list, or "
                                  "a two-space `  - item` block list")


def _scalar(raw: str) -> str:
    """A bare scalar is ALWAYS its literal text. There is no implicit typing anywhere.

    `true`, `null`, `~`, `yes`, `no`, `on`, `off`, `2026-07-18`, `0x10`, `1_000`, `.inf`, `NaN` all
    parse to exactly those strings. Coercion happens in ONE place — the per-field type table in
    `model.py` — so a value can never quietly change type because of how it happened to be spelled.
    """
    return raw[1:-1] if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"' else raw


def _value(raw: str, key: str) -> object:
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [_scalar(p.strip()) for p in inner.split(",")] if inner else []
    v = _scalar(raw)
    return [v] if FIELD_TYPES.get(key) == "list" else v


# ── prose sections and tables ───────────────────────────────────────────────────────────────
def _sections(text: str, start_line_offset: int) -> tuple[list[Field], list[Diagnostic]]:
    fields: list[Field] = []
    diags: list[Diagnostic] = []
    lines = text.split("\n")
    heads = [(i, m.group("name")) for i, line in enumerate(lines)
             if i >= start_line_offset and (m := _HEADING.match(line))]

    for idx, (i, name) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        block = lines[i + 1:end]
        offset = sum(len(x) + 1 for x in lines[:i])
        if name not in ALL_FIELDS:
            diags.append(Diagnostic(UNKNOWN, "UNKNOWN-SECTION", f"`### {name}` is not a field",
                                    line=i + 1, got=name,
                                    expected="one of " + ", ".join(PROSE_FIELDS + TABLE_FIELDS)))
            continue
        if name in TABLE_FIELDS:
            rows, rdiags = _table(name, block, i + 1)
            diags += rdiags
            fields.append(Field(name, rows, i + 1, offset, offset + sum(len(x) + 1 for x in block)))
        elif name in PROSE_FIELDS:
            body = "\n".join(block).strip()
            if not body:
                diags.append(Diagnostic(MISSING, "EMPTY-SECTION", f"`### {name}` has no text",
                                        line=i + 1, got="", expected="prose"))
            fields.append(Field(name, body, i + 1, offset,
                                offset + sum(len(x) + 1 for x in block)))
        else:
            diags.append(Diagnostic(MALFORMED, "WRONG-LOCATION",
                                    f"`{name}` is a front-matter field, not a `###` section",
                                    line=i + 1, got=f"### {name}", expected="front matter"))
    return fields, diags


def _cells(line: str) -> list[str]:
    """Split one table row. `\\|` is a literal pipe inside a cell, never a column boundary."""
    out, cur, i = [], [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            cur.append("|"); i += 2; continue
        if ch == "|":
            out.append("".join(cur).strip()); cur = []; i += 1; continue
        cur.append(ch); i += 1
    out.append("".join(cur).strip())
    return out[1:-1] if len(out) >= 2 and not out[0] and not out[-1] else out


def _table(name: str, block: list[str], line0: int) -> tuple[list[dict], list[Diagnostic]]:
    diags: list[Diagnostic] = []
    rows = [(n, line) for n, line in enumerate(block) if line.strip().startswith("|")]
    want = TABLE_COLUMNS[name]
    if len(rows) < 2:
        return [], [Diagnostic(MALFORMED, "NO-TABLE", f"`### {name}` carries no table",
                               line=line0, expected="| " + " | ".join(want) + " |")]
    header = _cells(rows[0][1])
    if tuple(header) != want:
        return [], [Diagnostic(MALFORMED, "BAD-COLUMNS",
                               f"`{name}` column header does not match", line=line0 + rows[0][0] + 1,
                               got=" | ".join(header), expected=" | ".join(want),
                               remediation="columns are fixed and ordered; a reordered header would "
                                           "silently transpose values")]
    out: list[dict] = []
    for n, line in rows[2:]:
        cells = _cells(line)
        if len(cells) != len(want):
            diags.append(Diagnostic(MALFORMED, "BAD-ROW",
                                    f"row has {len(cells)} cell(s), expected {len(want)}",
                                    line=line0 + n + 1, got=line.strip()[:70],
                                    expected=" | ".join(want)))
            continue
        out.append(dict(zip(want, cells)))
    return out, diags


# ── lifecycle ───────────────────────────────────────────────────────────────────────────────
def _lifecycle(text: str) -> tuple[list[LifecycleEvent], list[Diagnostic]]:
    """Events are one table row each: append is a line append, which is what makes it monotone.

    The parser accepts any `kind` and any timestamp shape; deciding which kinds are legal, and that
    timestamps do not go backwards, is `validate.py`'s job. Keeping the two apart is what lets a
    lifecycle with an unknown event kind still be COMPARED against `main`'s copy — a parser that
    rejected it would make the append-only check impossible exactly when it matters.
    """
    diags: list[Diagnostic] = []
    lines = text.split("\n")
    rows = [(n, line) for n, line in enumerate(lines) if line.strip().startswith("|")]
    if not rows:
        return [], diags
    header = _cells(rows[0][1])
    if tuple(header) != _EVENT_COLUMNS:
        return [], [Diagnostic(MALFORMED, "BAD-EVENT-COLUMNS",
                               "the lifecycle table header does not match", line=rows[0][0] + 1,
                               got=" | ".join(header), expected=" | ".join(_EVENT_COLUMNS))]
    events: list[LifecycleEvent] = []
    for n, line in rows[2:]:
        cells = _cells(line)
        if len(cells) != 3:
            diags.append(Diagnostic(MALFORMED, "BAD-EVENT-ROW",
                                    f"lifecycle row has {len(cells)} cell(s), expected 3",
                                    line=n + 1, got=line.strip()[:70]))
            continue
        ts, kind, values = cells
        pairs = tuple((k.strip(), v.strip()) for part in values.split(";") if part.strip()
                      for k, _, v in [part.partition("=")] if k.strip())
        events.append(LifecycleEvent(kind.strip(), ts.strip(), n + 1, pairs))
    return events, diags


def is_utc(ts: str) -> bool:
    return bool(_TS.match(ts))
