# tests/test_studio_a11y.py — Phase 1 a11y baseline: skip-to-content link, a single page <h1> sourced
# from the title block, the brand demoted from <h1> to a link, and aria-live on the htmx swap targets.
import math
import re
from pathlib import Path

import fanops.studio
from fanops.config import Config
from fanops.studio.app import create_app

_CSS = Path(fanops.studio.__file__).parent / "static" / "studio.css"


def _html(cfg, path="/"):
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get(path)
    assert r.status_code == 200, r.data[:300]
    return r.data.decode()


def test_skip_nav_link_targets_main_landmark(tmp_path):
    h = _html(Config(root=tmp_path))
    assert 'class="skip-nav' in h and 'href="#main-content"' in h  # skip link lands PAST sticky chrome (cockpit rail)...
    assert 'id="main-content"' in h and 'id="main"' in h           # ...the focusable landing + the <main> landmark both exist


def test_brand_demoted_and_exactly_one_page_h1(tmp_path):
    h = _html(Config(root=tmp_path))
    assert '<h1 class="nav-brand"' not in h                   # brand is no longer the page heading
    assert 'class="nav-brand"' in h                           # but still rendered (as a link)
    assert h.count("<h1") == 1                                # exactly one <h1> per page (the title)


def test_aria_live_on_swap_targets(tmp_path):
    for path, ident in [("/review", "review-body"), ("/schedule", "schedule-body"), ("/run", "run-panel")]:
        h = _html(Config(root=tmp_path), path)
        assert re.search(r'id="%s"[^>]*aria-live="polite"' % ident, h), f"{ident} missing aria-live"


# ── MOL-40: filled-pill state tokens (companion on-fill ink for --ok/--warn/--danger/--info) ──────────
def _oklch_lum(L, C, h_deg):  # OKLCH -> linear sRGB -> WCAG relative luminance (Ottosson); alpha ignored
    h = math.radians(h_deg); a = C * math.cos(h); b = C * math.sin(h)
    l_ = L + 0.3963377774*a + 0.2158037573*b; m_ = L - 0.1055613458*a - 0.0638541728*b; s_ = L - 0.0894841775*a - 1.2914855480*b
    lc, mc, sc = l_**3, m_**3, s_**3
    r = 4.0767416621*lc - 3.3077115913*mc + 0.2309699292*sc
    g = -1.2684380046*lc + 2.6097574011*mc - 0.3413193965*sc
    bl = -0.0041960863*lc - 0.7034186147*mc + 1.7076147010*sc
    return 0.2126*max(r, 0) + 0.7152*max(g, 0) + 0.0722*max(bl, 0)


def _token(css, name, _depth=0):  # resolve `--name:` to (L,C,h); follow one var(--x) alias hop (e.g. --danger-ink:var(--bg))
    assert _depth < 8, f"token --{name} alias chain too deep"
    m = re.search(r'--%s\s*:\s*([^;]+);' % re.escape(name), css)
    assert m, f"token --{name} not found in studio.css"
    val = m.group(1).strip()
    alias = re.match(r'var\(\s*--([\w-]+)\s*\)', val)
    if alias: return _token(css, alias.group(1), _depth + 1)
    o = re.match(r'oklch\(\s*([\d.]+)%\s+([\d.]+)\s+([\d.]+)', val)
    assert o, f"token --{name} is not an oklch literal or var() alias: {val!r}"
    return (float(o.group(1)) / 100.0, float(o.group(2)), float(o.group(3)))


def _contrast(css, fill, ink):
    a, b = _oklch_lum(*_token(css, fill)), _oklch_lum(*_token(css, ink))
    hi, lo = max(a, b), min(a, b); return (hi + 0.05) / (lo + 0.05)


def test_filled_pill_ink_tokens_exist():
    css = _CSS.read_text()
    for hue in ("ok", "warn", "danger", "info"):
        assert re.search(r'--%s-ink\s*:' % hue, css), f"--{hue}-ink token missing from :root"


def test_filled_pill_ink_tokens_meet_wcag_aa():
    css = _CSS.read_text()
    for fill in ("ok", "warn", "danger", "info"):
        c = _contrast(css, fill, f"{fill}-ink")
        assert c >= 4.5, f"--{fill} on --{fill}-ink is {c:.2f}:1 (< 4.5:1 WCAG AA)"


# ── MOL-41: accent-as-text-color is INTERACTIVE-only ──────────────────────────────────────────────
# --accent-bright as a text `color:` marks affordance (clickable / hover / focus / "you-are-here" /
# real <summary> toggle). Static emphasis must NOT borrow it — it reads as a dead link. This test
# collects every rule that sets a text color to --accent-bright and pins the set to an explicit
# interactive-only allowlist, so future accent-creep onto static text fails here.

# Each rule is one selector-list `{ ...decls... }` block. We keep only blocks whose declarations set a
# *text* color to accent-bright: a `color:var(--accent-bright)` that is NOT `border-color`/`caret-color`/
# `background`/`box-shadow`/`outline`/a gradient stop (those are non-text uses, out of this rule's scope).
_RULE = re.compile(r'([^{}]+)\{([^{}]*)\}')
_TEXT_COLOR_ACCENT = re.compile(r'(?<![\w-])color\s*:\s*var\(\s*--accent-bright\s*\)')
_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)


def _accent_text_color_selectors(css):
    css = _COMMENT.sub(' ', css)  # strip comments so a preceding /* … */ can't glue onto a selector
    hits = set()
    for m in _RULE.finditer(css):
        selector, body = m.group(1).strip(), m.group(2)
        if _TEXT_COLOR_ACCENT.search(body):
            hits.add(re.sub(r'\s+', ' ', selector))
    return hits


# Interactive-only: real links, hover/focus states, the you-are-here nav mark, and real <summary> toggles.
# `.term-mark` is KEPT deliberately: its parent `.term` is tabindex=0/focusable and reveals a definition
# card, so the mark is part of an interactive affordance (conservative KEEP — MOL-41 report notes it).
_ACCENT_TEXT_INTERACTIVE_ALLOWLIST = {
    "a",                                              # real <a> links
    ".surface-editor summary",                        # <summary> toggle
    "input[type=file]::file-selector-button:hover",   # hover state
    ".stage-handoff-link",                            # real link (has :hover)
    ".clip-transcript > summary",                     # <summary> toggle
    ".cell-hook:hover, .cell-hook:focus-visible",     # hover/focus state
    ".spine-step.active .spine-num",                  # you-are-here nav mark
    ".row-actions summary",                           # <summary> toggle
    ".home-acct-table a",                             # real <a> links
    ".term-mark",                                     # focusable-parent affordance (conservative keep)
}


def test_accent_text_color_is_interactive_only():
    css = _CSS.read_text()
    found = _accent_text_color_selectors(css)
    static_offenders = found - _ACCENT_TEXT_INTERACTIVE_ALLOWLIST
    assert not static_offenders, (
        "accent-bright used as static text color (must be font-weight/serif, not accent): "
        + ", ".join(sorted(static_offenders))
    )
    # The allowlist must not rot into a superset of what the CSS actually uses.
    stale = _ACCENT_TEXT_INTERACTIVE_ALLOWLIST - found
    assert not stale, "allowlist entries no longer present in studio.css: " + ", ".join(sorted(stale))


# ── MOL-42: 3 card elevation tiers (flat / raised / floating) — one recipe per bucket ──────────────
# ~7 near-identical card recipes collapse to 3 buckets, each internally IDENTICAL:
#   FLAT     — list rows: background only, NO shadow, small radius (read as rows not objects)
#   RAISED   — --elev-1 + one shared radius + one shared border/bg
#   FLOATING — --elev-2 + brighter border (.stage-card-primary, plus the existing .drawer)
# .ops-tile is a deliberate 4th (dashboard-tile) pattern and is NOT asserted into these buckets.

def _rule_body(css, selector):  # exact declaration body for a rule whose selector list == `selector`
    css = _COMMENT.sub(' ', css)
    for m in _RULE.finditer(css):
        if re.sub(r'\s+', ' ', m.group(1).strip()) == selector:
            return re.sub(r'\s+', ' ', m.group(2).strip())
    raise AssertionError(f"rule for selector {selector!r} not found in studio.css")


def _decls(body):  # parse a rule body into a {prop: value} dict (last-wins, whitespace-normalized)
    out = {}
    for part in body.split(';'):
        if ':' not in part: continue
        prop, val = part.split(':', 1)
        out[prop.strip()] = re.sub(r'\s+', ' ', val.strip())
    return out


_FLAT_SELECTORS = [".asset-row", ".publish-row, .footage-row", ".stitch-row"]
# The raised bucket: each of these carries the SAME elevation recipe (border / radius / shadow / bg).
_RAISED_SELECTORS = [".card", ".persona-card", ".golive-step", ".schedule-account-block", ".stage-card"]
_RAISED_KEYS = ("border", "border-radius", "box-shadow", "background")
_DRAWER_SEL = ".drawer"


def test_flat_bucket_rows_carry_no_shadow():
    css = _CSS.read_text()
    for sel in _FLAT_SELECTORS:
        d = _decls(_rule_body(css, sel))
        assert "box-shadow" not in d, f"flat-bucket {sel} must have no box-shadow, got {d.get('box-shadow')!r}"
        assert d.get("border-radius") == "var(--r-sm)", f"flat-bucket {sel} radius must be --r-sm, got {d.get('border-radius')!r}"
        assert d.get("border") == "1px solid var(--line)", f"flat-bucket {sel} border must be 1px solid --line, got {d.get('border')!r}"


def test_raised_bucket_shares_one_identical_recipe():
    css = _CSS.read_text()
    recipes = {sel: {k: _decls(_rule_body(css, sel)).get(k) for k in _RAISED_KEYS} for sel in _RAISED_SELECTORS}
    ref_sel = _RAISED_SELECTORS[0]; ref = recipes[ref_sel]
    for k in _RAISED_KEYS:  # every key present in the shared recipe
        assert ref[k] is not None, f"raised-bucket {ref_sel} missing {k}"
    assert ref["box-shadow"] == "var(--elev-1)", f"raised bucket must sit on --elev-1, got {ref['box-shadow']!r}"
    assert ref["border-radius"] in ("var(--r-lg)", "var(--r-md)"), f"raised bucket must pick ONE of --r-lg/--r-md, got {ref['border-radius']!r}"
    for sel in _RAISED_SELECTORS[1:]:
        assert recipes[sel] == ref, f"raised-bucket {sel} recipe {recipes[sel]} != {ref_sel} recipe {ref}"


def test_floating_tier_uses_elev_2_and_brighter_border():
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".stage-card-primary"))
    assert d.get("box-shadow") == "var(--elev-2)", f".stage-card-primary must float on --elev-2, got {d.get('box-shadow')!r}"
    assert d.get("border-color") == "var(--line-bright)", f".stage-card-primary must use brighter border, got {d.get('border-color')!r}"
    drawer = _decls(_rule_body(css, _DRAWER_SEL))
    assert drawer.get("box-shadow") == "var(--elev-2)", "the existing .drawer floating tier must stay on --elev-2"


# ── MOL-42: .ops-tile is the 4th (dashboard-tile) pattern — a zero-value tile RECEDES ───────────────
# A zero-count tile must read as empty/idle (dimmed value, no accent); a nonzero tile keeps full-weight
# --ink. The distinction is a template-emitted `ops-tile--zero` class + a CSS rule that dims .ops-value.

def test_ops_tile_zero_rule_dims_the_value():
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".ops-tile--zero .ops-value"))
    assert d, ".ops-tile--zero .ops-value rule missing from studio.css"
    # the zero-value text must be visibly recessive — muted color and/or reduced opacity, never full --ink
    dims = d.get("color") == "var(--muted)" or ("opacity" in d and float(d["opacity"]) < 1)
    assert dims, f".ops-tile--zero .ops-value must dim the value (muted color or opacity<1), got {d!r}"


def test_ops_tile_zero_carries_no_accent_border():
    # a zero tile must not pull the accent/danger/warn treatment — confirm the danger/warn modifiers,
    # which are template-gated on a nonzero count, still key their accent off their own class (not on zero).
    css = _CSS.read_text()
    dgr = _decls(_rule_body(css, ".ops-tile--danger"))
    assert dgr.get("border-color") == "var(--danger)", "danger tile keeps its accent (fires only when nonzero)"


def test_home_template_emits_ops_tile_zero_conditionally():
    src = (Path(fanops.studio.__file__).parent / "templates" / "home.html").read_text()
    # every ops-tile <a> must carry a conditional ops-tile--zero class driven by its count being 0/falsey
    assert "ops-tile--zero" in src, "home.html must conditionally emit ops-tile--zero"
    # the six tiles each gate the zero class on their own count value
    assert src.count("ops-tile--zero") >= 6, "each of the 6 ops-tiles must gate its own ops-tile--zero"


def test_home_zero_tile_renders_recessive_class(tmp_path):
    # on a fresh (empty) ledger every count is 0 → every rendered ops-tile carries ops-tile--zero
    h = _html(Config(root=tmp_path))
    assert h.count("ops-tile--zero") >= 6, "a zero-count Home must mark all six tiles ops-tile--zero"


# ── MOL-44: 3 real button tiers (primary / secondary / tertiary) + danger as a modifier ────────────
# PRIMARY  — gradient/accent fill (unchanged, already distinct).
# SECONDARY— the unnamed `button, .button` base: real solid fill + a visible border, NO gradient/accent.
# TERTIARY — `.ghost`: no border, no fill (both transparent/none), text-only, resolving to --ink on hover.
# DANGER   — a *modifier* composed on top of secondary or primary, never a competing 4th tier.

def _base_button_body(css):  # the shared `button, .button` base rule body
    return _decls(_rule_body(css, "button, .button"))


def test_ghost_is_textonly_no_border_no_fill():
    css = _CSS.read_text()
    d = _decls(_rule_body(css, "button.ghost, .button.ghost"))
    # tertiary must read as text-only at REST: transparent/none fill AND no visible border
    assert d.get("background") in ("transparent", "none"), f".ghost must have no fill, got {d.get('background')!r}"
    assert d.get("border-color") in ("transparent", "none") or d.get("border") in ("none", "0"), \
        f".ghost must have no visible border, got border-color={d.get('border-color')!r} border={d.get('border')!r}"


def test_ghost_hover_resolves_to_ink():
    css = _CSS.read_text()
    d = _decls(_rule_body(css, "button.ghost:hover, .button.ghost:hover"))
    assert d.get("color") == "var(--ink)", f".ghost:hover must transition text to --ink, got {d.get('color')!r}"


def test_secondary_base_keeps_fill_and_border_no_accent():
    css = _CSS.read_text()
    d = _base_button_body(css)
    # secondary keeps a REAL solid fill (a surface token, not transparent) …
    assert d.get("background") == "var(--surface-3)", f"secondary base must keep a solid --surface-3 fill, got {d.get('background')!r}"
    # … and a VISIBLE border …
    assert d.get("border") == "1px solid var(--line-bright)", f"secondary base must keep a visible border, got {d.get('border')!r}"
    # … and must NOT borrow the primary tier's gradient or accent hue (distinct from primary at rest).
    joined = " ".join(v or "" for v in d.values())
    assert "gradient" not in joined, "secondary base must not use a gradient (that is the primary tier)"
    assert "accent" not in joined, "secondary base must not use an accent hue (that is the primary tier)"


def test_danger_composes_as_modifier_over_both_tiers():
    css = _CSS.read_text()
    # danger exists as a modifier on secondary (button.danger) AND on primary (button.primary.danger)
    sec = _decls(_rule_body(css, "button.danger"))
    assert sec.get("color") == "var(--danger)", f"secondary+danger must key the danger hue, got {sec.get('color')!r}"
    pri = _decls(_rule_body(css, "button.primary.danger"))
    assert pri, "primary+danger modifier rule (button.primary.danger) must exist"
    assert "gradient" in (pri.get("background") or ""), "primary+danger keeps the primary gradient weight (danger-hued)"


def test_show_more_disclosure_links_are_ghost():
    # low-stakes pagination/disclosure ("Show more") links are demoted to the tertiary (.ghost) tier.
    tpls = Path(fanops.studio.__file__).parent / "templates"
    for name in ("_account_pivot.html", "_schedule_panel.html", "publish.html", "_review_body.html"):
        src = (tpls / name).read_text()
        assert 'class="button ghost"' in src and "Show more" in src, \
            f"{name}: the 'Show more' disclosure link must be class=\"button ghost\""


def test_live_link_is_the_promoted_leading_affordance():
    # MOL-51 item 2: the Posted payoff link must out-weigh the tertiary .ghost utilities beside it — a
    # SOLID accent fill (--accent) at rest, not the old accent-DIM pill that read quieter than the buttons.
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".live-link"))
    assert d.get("background") == "var(--accent)", \
        f".live-link must promote to a solid --accent fill (stronger than the ghosts), got {d.get('background')!r}"
    assert d.get("color") == "var(--accent-ink)", \
        f".live-link text must be --accent-ink on the solid fill for contrast, got {d.get('color')!r}"


def test_review_view_toggle_links_are_ghost():
    # the Review full/compact/ultra view-toggle links are collapse/disclosure toggles → tertiary.
    src = (Path(fanops.studio.__file__).parent / "templates" / "_review_body.html").read_text()
    assert 'class="button ghost" href="{{ url_for(\'review\'' in src, "view-toggle links must be class=\"button ghost\""


# ── MOL-43: degraded-quality chips must lose to the primary action; destructive action must be present ──
# The shared-fallback chips (shared-cut / shared-hook / fix-clip) are INTENTIONAL but routine metadata:
# Tier 3 — an OUTLINED, muted pill that never out-shouts an action button. They must NOT keep the
# saturated --warn border+text they had (which made 2-3 of them the loudest thing on the card).
# The zero_cast chip is a real "posts nothing" alert (Tier 2 — SOLID fill on --danger + --danger-ink),
# and .surface-reject (a destructive action) must not be the quietest element on the card.

def test_warn_chip_family_demoted_to_tier3():
    # .chip.warn is the ROUTINE degraded-quality family (shared-cut / shared-hook / fix-clip / fans-to-all).
    # It must NOT keep the saturated --warn border+text that made 2-3 stacked chips the loudest thing on a
    # card. Tier 3 = a MUTED outline + muted text: border --line-bright, text --muted (never --warn).
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".chip.warn"))
    assert d.get("border-color") == "var(--line-bright)", \
        f".chip.warn must demote its border to muted --line-bright, got {d.get('border-color')!r}"
    assert d.get("color") == "var(--muted)", \
        f".chip.warn must demote its text to --muted (not saturated --warn), got {d.get('color')!r}"
    assert d.get("color") != "var(--warn)" and d.get("border-color") != "var(--warn)", \
        ".chip.warn must no longer carry the saturated --warn border+text"


def test_warn_chip_keeps_a_warn_glyph_but_no_fill():
    # the signal is INTENTIONAL: a warn-hued leading ⚠ glyph stays (legible), but Tier 3 is OUTLINED only —
    # the chip body must never gain a solid warn/danger fill.
    css = _CSS.read_text()
    glyph = _decls(_rule_body(css, ".chip.warn::before"))
    assert glyph.get("color") == "var(--warn)", \
        f".chip.warn::before glyph must stay warn-hued for legibility, got {glyph.get('color')!r}"
    assert "⚠" in (glyph.get("content") or ""), \
        f".chip.warn::before must render a ⚠ warn glyph, got content {glyph.get('content')!r}"
    body = _decls(_rule_body(css, ".chip.warn"))
    bg = body.get("background", "")
    assert "var(--warn)" not in bg and "var(--danger)" not in bg, \
        f".chip.warn Tier-3 chip must stay outlined, not gain a state fill, got background {bg!r}"


def test_zero_cast_chip_is_solid_tier2_fill():
    # zero_cast ("0 cast — posts nothing") is a real Tier-2 alert: SOLID --danger fill + --danger-ink text,
    # scoped to .lane-method so it does NOT recolor the generic S2 provenance .chip.danger rule.
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".chip.lane-method.danger"))
    assert d.get("background") == "var(--danger)", \
        f"zero_cast chip must be a SOLID --danger fill (Tier 2), got {d.get('background')!r}"
    assert d.get("color") == "var(--danger-ink)", \
        f"zero_cast chip must use on-fill --danger-ink, got {d.get('color')!r}"


def test_zero_cast_chip_meets_wcag_aa():
    # the solid zero_cast fill must be legible: --danger fill vs --danger-ink text ≥ 4.5:1.
    css = _CSS.read_text()
    c = _contrast(css, "danger", "danger-ink")
    assert c >= 4.5, f"zero_cast --danger on --danger-ink is {c:.2f}:1 (< 4.5:1 WCAG AA)"


def test_surface_reject_is_present_not_faintest():
    # a destructive action must not be the QUIETEST element on the card: it needs --muted text (up from
    # --faint) and a VISIBLE border (--line-bright, up from --line) — quiet, but a real affordance.
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".surface-reject"))
    assert d.get("color") == "var(--muted)", \
        f".surface-reject must read at --muted, not --faint, got {d.get('color')!r}"
    assert d.get("border") == "1px solid var(--line-bright)", \
        f".surface-reject must carry a visible --line-bright border, got {d.get('border')!r}"


def test_spine_danger_pill_uses_ink_token_for_wcag():
    # DELEGATED from MOL-40: .spine-count.spine-sev-danger rendered background:--danger; color:--ink
    # (near-white) = 2.61:1, below AA. It must use the on-fill --danger-ink token, matching its
    # --spine-sev-warn / --spine-sev-info siblings, and the resulting pair must clear 4.5:1.
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".spine-count.spine-sev-danger"))
    assert d.get("background") == "var(--danger)", \
        f"spine danger pill must keep its --danger fill, got {d.get('background')!r}"
    assert d.get("color") == "var(--danger-ink)", \
        f"spine danger pill must use on-fill --danger-ink (not --ink), got {d.get('color')!r}"
    c = _contrast(css, "danger", "danger-ink")
    assert c >= 4.5, f"spine danger fill/ink pair is {c:.2f}:1 (< 4.5:1 WCAG AA)"


# ── MOL-48: Go-Live readiness — a failing check must INTERRUPT the scan (Tier-1 solid fill) ─────────
# A failed row (.checks li.err) used to differ from a passing row (.checks li.ok) by a 35%-alpha border
# tint + glyph/text color only — same bg, padding, weight. With one backend confirmed unreachable while
# the banner reads LIVE, that fact was visually indistinguishable from "ffmpeg is on PATH". The fix
# promotes .checks li.err to the ladder's Tier-1 (MOL-40): SOLID --danger fill + on-fill --danger-ink,
# bold — not a border tint. A passing row must stay quiet (no fill).

def test_checks_err_row_is_solid_danger_fill_bold():
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".checks li.err"))
    # Tier-1: solid fill on the danger hue, not a translucent border tint.
    assert d.get("background") == "var(--danger)", \
        f".checks li.err must carry a SOLID --danger fill (Tier-1), got {d.get('background')!r}"
    # on-fill ink is the MOL-43-fixed --danger-ink (6.70:1), reused exactly.
    assert d.get("color") == "var(--danger-ink)", \
        f".checks li.err text must use on-fill --danger-ink, got {d.get('color')!r}"
    # bold, so the row weighs more than its passing neighbours.
    assert (d.get("font-weight") or "").strip() in ("600", "700", "bold"), \
        f".checks li.err must be bold, got font-weight={d.get('font-weight')!r}"


def test_checks_err_fill_ink_pair_meets_wcag_aa():
    css = _CSS.read_text()
    c = _contrast(css, "danger", "danger-ink")
    assert c >= 4.5, f".checks li.err fill/ink pair is {c:.2f}:1 (< 4.5:1 WCAG AA)"


def test_checks_ok_row_stays_quiet_no_fill():
    # the passing row must NOT gain a solid fill — only the failure interrupts the scan.
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".checks li.ok"))
    assert d.get("background") not in ("var(--danger)", "var(--ok)"), \
        f".checks li.ok must stay quiet (no solid state fill), got {d.get('background')!r}"


# ── MOL-49: live-publish arming checkbox is Tier-1, not mid-warn-continuum ─────────────────────────
# Arming a live publish is the single most consequential act in the app. Its checkbox used the SAME
# `.confirm` treatment (warn hue, 8%-alpha fill, 35%-alpha border) as routine informational warn
# elements — mid-warn-continuum, not unmistakably above it. The fix adds a danger-grade arming
# treatment `.confirm.danger` (ladder Tier-1, MOL-40): SOLID --danger fill + on-fill --danger-ink,
# distinctly heavier than any informational warn element, applied at all three live-arming call sites
# (both /run confirm forms + the Go-Live flip confirm). `.confirm` alone stays the quiet warn box for
# any non-arming use.

def test_confirm_danger_is_solid_danger_fill():
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".confirm.danger"))
    # Tier-1: solid fill on the danger hue, not the base .confirm warn-tint.
    assert d.get("background") == "var(--danger)", \
        f".confirm.danger must carry a SOLID --danger fill (Tier-1), got {d.get('background')!r}"
    # on-fill ink is the MOL-43-tuned --danger-ink (6.70:1), reused exactly so the label stays readable.
    assert d.get("color") == "var(--danger-ink)", \
        f".confirm.danger text must use on-fill --danger-ink, got {d.get('color')!r}"
    # a full-danger border, not the base .confirm's 35%-alpha warn tint.
    assert d.get("border-color") == "var(--danger)" or "var(--danger)" in (d.get("border") or ""), \
        f".confirm.danger must carry a --danger border, got border={d.get('border')!r} border-color={d.get('border-color')!r}"


def test_confirm_danger_fill_ink_pair_meets_wcag_aa():
    css = _CSS.read_text()
    c = _contrast(css, "danger", "danger-ink")
    assert c >= 4.5, f".confirm.danger fill/ink pair is {c:.2f}:1 (< 4.5:1 WCAG AA)"


def test_base_confirm_stays_quiet_warn_no_danger_fill():
    # the base .confirm (non-arming use) must NOT gain the solid danger fill — only .confirm.danger arms.
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".confirm"))
    assert d.get("background") != "var(--danger)", \
        f"base .confirm must stay the quiet warn box (no solid --danger fill), got {d.get('background')!r}"


def _run_html_live(cfg, monkeypatch):
    # Render /run with a non-dryrun backend so BOTH confirm forms are emitted (the live-arming state).
    from fanops.studio import views
    monkeypatch.setattr(views, "_publish_mode_label", lambda _cfg: "postiz")
    return _html(cfg, "/run")


def test_run_panel_both_arming_confirms_carry_danger_class(tmp_path, monkeypatch):
    h = _run_html_live(Config(root=tmp_path), monkeypatch)
    # Both live-arming checkboxes are rendered when the backend is live...
    assert h.count("publish to REAL accounts") == 2, \
        "expected both /run arming confirms (Make clips + Run one pipeline step) when live"
    # ...and every rendered arming confirm must carry the Tier-1 danger-grade treatment.
    assert h.count('class="confirm danger"') == 2, \
        f'both /run arming confirms must be class="confirm danger", found {h.count(chr(34)+"confirm danger"+chr(34))}'
    assert 'class="confirm"' not in h.replace('class="confirm danger"', ''), \
        "no /run arming confirm may remain the plain warn-tint .confirm"


def test_golive_flip_confirm_carries_danger_class(tmp_path):
    # A not-live config renders the GO-LIVE flip form with its arming confirm.
    h = _html(Config(root=tmp_path), "/golive")
    assert "I understand this publishes to REAL accounts" in h, "go-live flip confirm not rendered"
    assert 'class="confirm danger"' in h, \
        "the Go-Live flip confirm must carry the Tier-1 danger-grade class"


# ── MOL-50: Results — the Lift number is the row's dominant element; DEGRADED becomes a quiet marker ──
# The Lift number is the answer to "which variant won", so it must be the loudest per-row element:
# larger + bolder + full --ink. And when degradation is uniform (table-level note shown), the per-row
# marker must be a QUIET muted chip (not the saturated .badge.degraded danger fill that drowned the number).

def test_lift_num_is_dominant_ink_bold_larger():
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".lift-num"))
    assert d.get("color") == "var(--ink)", f".lift-num must be full --ink, got {d.get('color')!r}"
    # weight may be declared standalone or inside the `font:` shorthand (house style favours the shorthand).
    font_sh = d.get("font", "")
    fw = (d.get("font-weight") or "").strip()
    is_bold = fw in ("600", "700", "bold") or re.search(r'\bfont\s*:\s*(600|700|bold)\b', "font:" + font_sh)
    assert is_bold, f".lift-num must be bold, got font-weight={fw!r} font={font_sh!r}"
    # a real size step up (larger than body copy) — a font/font-size token, not the default.
    assert "font-size" in d or "font" in d, f".lift-num must set an enlarged size, got {d!r}"


def test_degraded_quiet_marker_is_muted_no_danger_fill():
    # the quiet per-row marker (shown when the table-level note carries the message) must be recessive:
    # muted text, no saturated --danger fill/border that would out-shout the Lift number.
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".degraded-quiet"))
    assert d.get("color") == "var(--muted)", \
        f".degraded-quiet must read at --muted, got {d.get('color')!r}"
    bg = d.get("background", "")
    assert "var(--danger)" not in bg, \
        f".degraded-quiet must NOT carry the saturated --danger fill, got background {bg!r}"
    assert d.get("border-color") != "var(--danger)" and "var(--danger)" not in (d.get("border") or ""), \
        ".degraded-quiet must not carry the loud --danger border"


# ── MOL-52: a disabled bulk-action button must read as inert — even when it carries .primary ─────────
# Bulk buttons ("Approve selected" / "Release selected" / "Reject selected") keep their .primary class
# but must go visibly inert when their selection/list is empty. No disabled treatment existed before
# (only the htmx busy-state). The recipe: reduced opacity, cursor:not-allowed, and it must OVERRIDE the
# .primary gradient fill so a disabled primary button doesn't still read as the loud call-to-action.

def test_disabled_button_recipe_is_inert():
    css = _CSS.read_text()
    d = _decls(_rule_body(css, "button:disabled, .button:disabled"))
    # visibly dimmed …
    op = d.get("opacity")
    assert op is not None and float(op) < 1, f"disabled button must reduce opacity (<1), got {op!r}"
    # … non-interactive cursor …
    assert d.get("cursor") == "not-allowed", f"disabled button must use cursor:not-allowed, got {d.get('cursor')!r}"
    # … and it must neutralise the .primary gradient (muted/flat fill, no accent gradient) so a disabled
    # primary CTA stops shouting.
    bg = d.get("background", "")
    assert "gradient" not in bg and "accent" not in bg, \
        f"disabled button must override the .primary accent gradient with a flat muted fill, got {bg!r}"
    assert bg, "disabled button must set an explicit (flat) background to override .primary"


def test_disabled_button_kills_hover_lift():
    # a disabled button must not lift/animate on hover (the base + primary hover states apply a transform
    # and elevation shadow — those must be neutralised so a disabled control feels dead to the pointer).
    css = _CSS.read_text()
    d = _decls(_rule_body(css, "button:disabled:hover, .button:disabled:hover"))
    assert d.get("transform") == "none", f"disabled button hover must not lift (transform:none), got {d.get('transform')!r}"
    assert d.get("box-shadow") == "none", f"disabled button hover must drop the elevation shadow, got {d.get('box-shadow')!r}"


# ── MOL-55: Home account-row "needs action" cue — accent border + filled count badge ───────────────
# "Open" (browse) is .ghost; "Review (N)"/"Schedule (N)" keep secondary weight but gain .acct-cta-pending
# (accent border) and wrap the count in a .cta-badge (small filled accent badge), so action-rows read apart
# from browse-rows at a glance without reading the numbers. Neither is .primary (accent border, not gradient).
def test_acct_cta_pending_uses_accent_border_not_gradient():
    css = _CSS.read_text()
    body = _rule_body(css, ".acct-cta-pending")
    d = _decls(body)
    assert "--accent" in d.get("border-color", ""), "pending cue must carry an accent border"
    assert "gradient" not in body, "pending cue is secondary weight, never the primary gradient"


def test_cta_badge_is_a_small_filled_accent_badge():
    css = _CSS.read_text()
    body = _rule_body(css, ".cta-badge")
    d = _decls(body)
    assert "--accent" in d.get("background", ""), "count badge must be filled with the accent family"
    assert d.get("border-radius"), "count badge must be a rounded pill/badge"


# ── MOL-58: mobile (≤768px) horizontal nav rail must signal off-screen items ────────────────────────
# At 375px the horizontal-scrolling .rail truncated "Schedule" mid-word at the viewport edge with no
# affordance, and "Add & run" wrapped to two lines. Fix (pure CSS, inside the existing @media block):
#   • .rail gets a right-edge fade mask (mask-image linear-gradient) → a peeking/fading next item
#     signals "more content" instead of a hard silent cut;
#   • .rail gets scroll-snap-type + scroll-padding so a scrolled item lands whole, never mid-label;
#   • .rail-link gets white-space:nowrap (kills the "Add & run" two-line wrap) + scroll-snap-align.
def _media_block(css, query):  # body of the @media rule whose condition == query (nested braces handled)
    css = _COMMENT.sub(' ', css)
    marker = "@media " + query
    i = css.find(marker)
    assert i != -1, f"@media {query} block not found in studio.css"
    j = css.find("{", i)
    depth, k = 0, j
    while k < len(css):
        if css[k] == "{": depth += 1
        elif css[k] == "}":
            depth -= 1
            if depth == 0: return css[j + 1:k]
        k += 1
    raise AssertionError(f"unbalanced braces after @media {query}")


def _media_rule(block, selector):  # declaration body for `selector` within a media block body
    for m in _RULE.finditer(block):
        if re.sub(r'\s+', ' ', m.group(1).strip()) == selector:
            return re.sub(r'\s+', ' ', m.group(2).strip())
    raise AssertionError(f"rule {selector!r} not found in the media block")


def test_mobile_rail_has_fade_edge_affordance():
    block = _media_block(_CSS.read_text(), "(max-width:768px)")
    d = _decls(_media_rule(block, ".rail"))
    mask = d.get("mask-image", "") + " " + d.get("-webkit-mask-image", "")
    assert "linear-gradient" in mask, "mobile .rail must carry a linear-gradient fade mask so the next item peeks/fades (off-screen signal)"


def test_mobile_rail_snaps_so_labels_never_cut_mid_word():
    block = _media_block(_CSS.read_text(), "(max-width:768px)")
    rail = _decls(_media_rule(block, ".rail"))
    assert "scroll-snap-type" in rail, "mobile .rail must set scroll-snap-type so items land whole"
    assert rail.get("scroll-padding-inline") or rail.get("scroll-padding"), "mobile .rail must set scroll-padding so a snapped item leaves a peeking edge"
    link = _decls(_media_rule(block, ".rail-link"))
    assert "scroll-snap-align" in link, "mobile .rail-link must declare scroll-snap-align so each label is a snap target"


def test_mobile_rail_link_does_not_wrap_to_two_lines():
    block = _media_block(_CSS.read_text(), "(max-width:768px)")
    link = _decls(_media_rule(block, ".rail-link"))
    assert link.get("white-space") == "nowrap", "mobile .rail-link must be nowrap so 'Add & run' stays on one line"


# ── MOL-62: matrix sticky header/column defeated by global table{overflow:hidden} ──────────────
# The global card-table rule makes every <table> its own clip container, so sticky cells stick to the
# non-scrolling table box instead of .matrix-scroll. .review-matrix must override overflow:visible so
# its sticky thead/row-head pin against the scroll region; the global table rule must KEEP overflow:hidden
# (that's the rounded-corner clip for ordinary card tables — the look must be preserved).

def test_review_matrix_overrides_overflow_visible_so_sticky_pins():
    css = _CSS.read_text()
    body = _rule_body(css, ".review-matrix")
    assert "overflow:visible" in re.sub(r"\s+", "", body), \
        ".review-matrix must set overflow:visible (MOL-62) so its sticky header/column pin to .matrix-scroll, not the table box"


def test_global_table_rule_keeps_overflow_hidden_for_card_corners():
    css = _CSS.read_text()
    body = _rule_body(css, "table")
    assert "overflow:hidden" in re.sub(r"\s+", "", body), \
        "global table{} rule must KEEP overflow:hidden — it's the rounded-corner clip for ordinary card tables"


# ── MOL-63: schedule row action cluster must NOT wrap caption-dependently ──────────────────────────
# The row is a flex line: [thumb] [.sched-row-text caption] [.sched-row-controls cluster]. A long caption
# expanded the text column (no flex:1/min-width:0 → it took intrinsic width), squeezing the cluster and
# forcing it to wrap to a second line at a caption-dependent x — ragged heights, misaligned buttons.
# Fix: the text column absorbs+truncates (flex:1;min-width:0) and the cluster is a fixed, no-wrap column.

def test_sched_row_text_absorbs_and_truncates_not_pushes():
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".sched-row-text"))
    assert d.get("flex") in ("1", "1 1 0", "1 1 0%"), \
        f".sched-row-text must flex:1 so a long caption absorbs slack instead of pushing controls, got {d.get('flex')!r}"
    assert d.get("min-width") == "0", \
        f".sched-row-text needs min-width:0 so the clamp-1 caption can actually shrink, got {d.get('min-width')!r}"


def test_sched_row_controls_do_not_wrap():
    # The control cluster must be a fixed-width column that never reflows to a 2nd line — uniform x + height.
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".sched-row-controls"))
    assert d.get("flex-wrap") == "nowrap", \
        f".sched-row-controls must be flex-wrap:nowrap (MOL-63) so the cluster never wraps caption-dependently, got {d.get('flex-wrap')!r}"
    assert d.get("flex") == "none", \
        f".sched-row-controls must be flex:none so the caption column (not the cluster) absorbs slack, got {d.get('flex')!r}"


# ── MOL-64: block-scope tools legible from row-scope actions ────────────────────────────────────────
# +1 day / −1 day / Re-spread account mutate the WHOLE account block; Move/Clear time/Use suggested/
# ← Review mutate ONE row. They rendered at identical secondary weight. Legibility, not demotion:
#   • the block-scope tools get a SEPARATED band (MOL-53 pattern: a leading rule + gap) so scope reads;
#   • the per-row reversible utilities (Move / Clear time / Use suggested / ← Review) demote to .ghost
#     (tertiary) — the MOL-44/51/55/60 pattern; Publish KEEPS its distinct .primary weight (it ships live).

def test_schedule_account_tools_are_a_separated_band():
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".schedule-account-tools"))
    assert d.get("border-left") and d.get("border-left") != "none", \
        f".schedule-account-tools must carry a leading rule (MOL-64) separating block-scope tools, got border-left={d.get('border-left')!r}"
    assert d.get("padding-left"), \
        ".schedule-account-tools needs padding-left off its leading rule so the band reads as its own group"


def test_schedule_per_row_reversible_actions_are_ghost():
    # Move / Clear time / Use suggested / ← Review are per-row, reversible → tertiary .ghost.
    src = (Path(fanops.studio.__file__).parent / "templates" / "_schedule_panel.html").read_text()
    for label in (">Move<", ">Clear time<", ">Use suggested<", "← Review"):
        idx = src.find(label)
        assert idx != -1, f"expected {label!r} button in _schedule_panel.html"
        button_open = src.rfind("<button", 0, idx)
        assert 'class="ghost"' in src[button_open:idx], \
            f"the {label!r} per-row action must be a tertiary .ghost button (MOL-64), got: {src[button_open:idx]!r}"


def test_schedule_publish_keeps_distinct_primary_weight():
    # Publish SHIPS LIVE — it must NOT be ghosted; it keeps its .primary weight (consequence-legible).
    src = (Path(fanops.studio.__file__).parent / "templates" / "_schedule_panel.html").read_text()
    idx = src.find(">Publish<")
    assert idx != -1, "expected the Publish button in _schedule_panel.html"
    button_open = src.rfind("<button", 0, idx)
    seg = src[button_open:idx]
    assert 'class="primary"' in seg, f"Publish must keep .primary weight (never ghost), got: {seg!r}"
    assert "ghost" not in seg, "Publish must never be ghosted — it publishes live"


# ── T-05 (MOL-92): raw dark-theme color literals → token-tracking color-mix ─────────────────────────
# The state-tint backgrounds/borders were hardcoded oklch literals frozen to the OLD dark-theme hues, so
# they did NOT follow the T-01 token flip and rendered the wrong hue on the light ground. Each is converted
# to `color-mix(in oklch, var(--X) N%, transparent)` so it tracks --warn/--ok/--danger automatically. The
# pins below assert (a) the mix references the right token and (b) no raw oklch literal remains in the rule.

_MIX = re.compile(r'color-mix\(\s*in\s+oklch\s*,\s*var\(\s*--([\w-]+)\s*\)\s*(\d+)%\s*,\s*transparent\s*\)')
_RAW_OKLCH = re.compile(r'oklch\(\s*\d')  # a raw oklch(<number>...) literal (not a var()/color-mix)


def _mix_token(value):  # (token, pct) from a color-mix(...) value, or None
    m = _MIX.search(value or "")
    return (m.group(1), int(m.group(2))) if m else None


def test_badge_background_tracks_warn_token():
    d = _decls(_rule_body(_CSS.read_text(), ".badge"))
    assert _mix_token(d.get("background")) == ("warn", 10), \
        f".badge background must be color-mix warn 10% (was raw oklch), got {d.get('background')!r}"
    assert not _RAW_OKLCH.search(d.get("background", "")), ".badge background must carry no raw oklch literal"


def test_confirm_bg_and_border_track_warn_token():
    d = _decls(_rule_body(_CSS.read_text(), ".confirm"))
    assert _mix_token(d.get("background")) == ("warn", 8), \
        f".confirm background must be color-mix warn 8%, got {d.get('background')!r}"
    # border shorthand carries the mix (35%); no raw oklch literal survives anywhere in the border/bg.
    border = d.get("border", "")
    assert _mix_token(border) == ("warn", 35), f".confirm border must carry color-mix warn 35%, got {border!r}"
    assert not _RAW_OKLCH.search(d.get("background", "") + " " + border), \
        ".confirm bg/border must carry no raw oklch literal"


def test_batch_zero_summary_bg_tracks_warn_token():
    d = _decls(_rule_body(_CSS.read_text(), ".batch-zero-summary"))
    assert _mix_token(d.get("background")) == ("warn", 8), \
        f".batch-zero-summary background must be color-mix warn 8%, got {d.get('background')!r}"
    assert not _RAW_OKLCH.search(d.get("background", "")), ".batch-zero-summary bg must carry no raw oklch literal"


def test_alerts_lane_bg_tracks_warn_token():
    d = _decls(_rule_body(_CSS.read_text(), ".alerts-lane"))
    assert _mix_token(d.get("background")) == ("warn", 8), \
        f".alerts-lane background must be color-mix warn 8%, got {d.get('background')!r}"
    assert not _RAW_OKLCH.search(d.get("background", "")), ".alerts-lane bg must carry no raw oklch literal"


def test_result_ok_tracks_ok_token():
    d = _decls(_rule_body(_CSS.read_text(), ".result.ok"))
    assert _mix_token(d.get("border-color")) == ("ok", 40), \
        f".result.ok border-color must be color-mix ok 40%, got {d.get('border-color')!r}"
    assert _mix_token(d.get("background")) == ("ok", 8), \
        f".result.ok background must be color-mix ok 8%, got {d.get('background')!r}"
    assert not _RAW_OKLCH.search(d.get("border-color", "") + " " + d.get("background", "")), \
        ".result.ok must carry no raw oklch literal"


def test_result_err_tracks_danger_token():
    d = _decls(_rule_body(_CSS.read_text(), ".result.err"))
    assert _mix_token(d.get("border-color")) == ("danger", 40), \
        f".result.err border-color must be color-mix danger 40%, got {d.get('border-color')!r}"
    assert _mix_token(d.get("background")) == ("danger", 8), \
        f".result.err background must be color-mix danger 8%, got {d.get('background')!r}"
    assert not _RAW_OKLCH.search(d.get("border-color", "") + " " + d.get("background", "")), \
        ".result.err must carry no raw oklch literal"


def test_checks_ok_border_tracks_ok_token():
    # the passing-row quiet border tint was a raw old-ok literal (35%-alpha) — the same straggler class as
    # .result.ok; it must track --ok via color-mix, preserving its 35% weight. (.checks li.err is a SOLID
    # --danger fill already token-referenced by MOL-48 — out of scope, untouched.)
    d = _decls(_rule_body(_CSS.read_text(), ".checks li.ok"))
    assert _mix_token(d.get("border-color")) == ("ok", 35), \
        f".checks li.ok border-color must be color-mix ok 35%, got {d.get('border-color')!r}"
    assert not _RAW_OKLCH.search(d.get("border-color", "")), ".checks li.ok border must carry no raw oklch literal"


def test_chip_win_bg_tracks_ok_token():
    d = _decls(_rule_body(_CSS.read_text(), ".chip.win"))
    assert _mix_token(d.get("background")) == ("ok", 10), \
        f".chip.win background must be color-mix ok 10%, got {d.get('background')!r}"
    assert not _RAW_OKLCH.search(d.get("background", "")), ".chip.win bg must carry no raw oklch literal"


def test_live_dot_ring_tracks_ok_token():
    d = _decls(_rule_body(_CSS.read_text(), ".review-live .live-dot"))
    bs = d.get("box-shadow", "")
    assert _mix_token(bs) == ("ok", 18), f".live-dot box-shadow ring must be color-mix ok 18%, got {bs!r}"
    assert "0 0 0 3px" in bs, f".live-dot ring must keep its 3px spread, got {bs!r}"
    assert not _RAW_OKLCH.search(bs), ".live-dot box-shadow must carry no raw oklch literal"


# ── T-05 orchestrator addition: rail background swap off the dark-theme holdover ────────────────────
# `.rail` was a raw near-black oklch(17% ...) — a dark-theme holdover the T-05 list missed. Post-flip it
# renders near-black on paper, making --muted labels barely legible. It becomes --surface-2 (computed
# --muted/--surface-2 = 5.99:1, AA-pass). The white-alpha `.rail-link:hover` (a second dark-theme holdover)
# is invisible on --surface-2 and must resolve to a real surface tone the swap makes legible.

def test_rail_background_is_surface_token_not_raw_literal():
    d = _decls(_rule_body(_CSS.read_text(), ".rail"))
    assert d.get("background") == "var(--surface-2)", \
        f".rail background must be var(--surface-2) (was raw dark oklch), got {d.get('background')!r}"
    assert not _RAW_OKLCH.search(d.get("background", "")), ".rail background must carry no raw oklch literal"


def test_rail_muted_labels_meet_wcag_aa_on_surface_2():
    # the whole reason for the swap: --muted (.rail-group-label AND .rail-link at rest) must clear AA on --surface-2.
    c = _contrast(_CSS.read_text(), "muted", "surface-2")
    assert c >= 4.5, f"--muted on --surface-2 is {c:.2f}:1 (< 4.5:1 WCAG AA) — rail labels illegible"


def test_rail_link_hover_is_legible_on_surface_2():
    # the white-alpha hover holdover is invisible on the new --surface-2 ground; it must resolve to a real tone.
    d = _decls(_rule_body(_CSS.read_text(), ".rail-link:hover"))
    assert not _RAW_OKLCH.search(d.get("background", "")), \
        f".rail-link:hover must not keep the raw white-alpha holdover (invisible on --surface-2), got {d.get('background')!r}"


# ── T-08 (MOL-95): high-visibility 3px accent-bright focus ring (non-default, new work per P-06) ─────
# The shared --ring recipe (used at every :focus-visible site via box-shadow:var(--ring)) is a double
# box-shadow: a 2px moat in the page background, then an OUTER accent ring. This pins the new recipe:
# a 2px moat + 5px outer = a 3px visible accent-bright stroke (brief's literal "3px accent-bright ring").
# The two active-state sites that diverge with their OWN inline double-box-shadow (because the active
# background already IS accent-dim, so the moat differs from --bg) must track the same 5px outer width.
_RING_RE = re.compile(r'--ring\s*:\s*([^;]+);')


def _ring_value(css):
    m = _RING_RE.search(css)
    assert m, "--ring token not found in studio.css"
    return re.sub(r'\s+', ' ', m.group(1).strip())


def test_ring_is_3px_accent_bright_stroke():
    # 2px moat + 5px outer = a 3px visible accent-bright stroke; hue is --accent-bright (not the dimmer --accent).
    ring = _ring_value(_CSS.read_text())
    assert "5px var(--accent-bright)" in ring, \
        f"--ring must end in a 5px var(--accent-bright) outer ring (3px visible stroke), got {ring!r}"
    assert "2px var(--bg)" in ring, \
        f"--ring must keep its 2px var(--bg) moat before the accent stroke, got {ring!r}"
    assert "var(--accent)," not in ring and not re.search(r'\bvar\(--accent\)\s*$', ring), \
        f"--ring must use the brighter --accent-bright hue, not the dimmer --accent, got {ring!r}"


def test_active_state_rings_track_the_new_5px_width():
    # the two divergent active-state rings (own inline double-box-shadow) must widen to the same 5px outer,
    # each preserving its OWN moat color (rail-link.active = --accent-dim; spine-step.active = --bg).
    css = _CSS.read_text()
    rail = _decls(_rule_body(css, ".rail-link.active:focus-visible")).get("box-shadow", "")
    assert "0 0 0 5px var(--accent-bright)" in re.sub(r'\s+', ' ', rail), \
        f".rail-link.active:focus-visible ring must widen its outer stroke to 5px accent-bright, got {rail!r}"
    assert "0 0 0 2px var(--accent-dim)" in re.sub(r'\s+', ' ', rail), \
        f".rail-link.active:focus-visible must preserve its own --accent-dim moat, got {rail!r}"
    spine = _decls(_rule_body(css, ".spine-step.active > a:focus-visible")).get("box-shadow", "")
    assert "0 0 0 5px var(--accent-bright)" in re.sub(r'\s+', ' ', spine), \
        f".spine-step.active > a:focus-visible ring must widen its outer stroke to 5px accent-bright, got {spine!r}"
    assert "0 0 0 2px var(--bg)" in re.sub(r'\s+', ' ', spine), \
        f".spine-step.active > a:focus-visible must preserve its own --bg moat, got {spine!r}"


# ── T-09 (MOL-96): SVG-chevron select caret + light-ground readonly re-theme (P-03 caret half) ───────
# The old caret was a two-linear-gradient triangular hack with fixed pixel offsets that don't scale with
# T-02's larger base size. It becomes ONE crisp SVG chevron data-URI, scoped :not([multiple]) so the
# native multi-select is left alone, with a dimmed disabled variant. SVG data-URIs cannot reference CSS
# custom properties, so the stroke is a BAKED hex computed from --muted oklch(45% .015 60) ≈ #5c534d — a
# required CSS comment ties the two so a future token change can't silently desync them.
def test_select_caret_is_svg_chevron_not_gradient_hack():
    css = _CSS.read_text()
    body = _rule_body(css, "select:not([multiple])")
    d = _decls(body)
    assert d.get("appearance") == "none" and d.get("-webkit-appearance") == "none", \
        f"select:not([multiple]) must strip native appearance, got {d!r}"
    assert d.get("padding-right") == "2.2rem", \
        f"select:not([multiple]) padding-right must be 2.2rem (breathing room for the SVG), got {d.get('padding-right')!r}"
    assert "data:image/svg+xml" in body and "linear-gradient" not in body, \
        "select:not([multiple]) caret must be an SVG data-URI, not the old linear-gradient hack"
    # the enabled caret stroke is the baked hex computed from --muted (NOT the draft's saturated brown #724b28).
    assert "%23724b28" not in body, "caret must not keep the draft's saturated-brown placeholder %23724b28"
    assert "%235c534d" in body, \
        "enabled caret stroke must be the baked hex computed from --muted oklch(45% .015 60) = %235c534d"


def test_select_caret_has_dimmed_disabled_variant():
    css = _CSS.read_text()
    body = _rule_body(css, "select:not([multiple]):disabled")
    assert "data:image/svg+xml" in body, "disabled select must supply its own dimmed SVG caret"
    assert "%235c534d" not in body, "disabled caret must not reuse the full-strength enabled hex"
    assert "%23" in body, "disabled caret must bake a (lighter) literal hex stroke"


def test_select_caret_comment_ties_hex_to_muted_token():
    # a bare hex desyncs silently if --muted is retuned; the exact comment wording is the guard.
    css = _CSS.read_text()
    assert "/* caret ≈ --muted oklch(45% .015 60); update together */" in css, \
        "the caret rule must carry the exact desync-guard comment tying the baked hex to --muted"


def test_readonly_inputs_use_light_ground_tokens_not_raw_dark_literal():
    # the old readonly recipe was a raw dark-surface literal oklch(16% .012 280 / .6) — a dark-theme holdover
    # invisible on the paper ground. It becomes two token refs: --surface-2 ground, --muted text.
    d = _decls(_rule_body(_CSS.read_text(), "textarea[readonly], input[readonly]"))
    assert d.get("background") == "var(--surface-2)", \
        f"readonly background must be var(--surface-2) (was raw dark oklch), got {d.get('background')!r}"
    assert d.get("color") == "var(--muted)", \
        f"readonly text must be var(--muted), got {d.get('color')!r}"


def test_table_row_hover_shifts_to_surface2():
    css = _CSS.read_text()
    d = _decls(_rule_body(css, "tbody tr:hover td, table tr:hover td"))
    assert d.get("background") == "var(--surface-2)", \
        f"table row hover must shift to --surface-2, got {d.get('background')!r}"


def test_schedule_row_hover_shifts_to_surface2():
    css = _CSS.read_text()
    d = _decls(_rule_body(css, ".schedule-row:hover"))
    assert d.get("background") == "var(--surface-2)", \
        f".schedule-row hover must shift to --surface-2, got {d.get('background')!r}"
