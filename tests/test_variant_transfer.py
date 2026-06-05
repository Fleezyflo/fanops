"""Cross-account/cross-surface transfer scorer (the v2 follow-up). transferred_hooks returns a
SAME-PLATFORM hook style proven on >= TRANSFER_MIN_DONORS distinct OTHER surfaces, as a weak prior
for a COLD recipient (one with no own gated winner). Pure/read-only/deterministic; reuses v2's
best_hooks gate on every donor. The whole anti-homogenization + stricter-gate argument lives here."""
from __future__ import annotations
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
from fanops.accounts import Account, Accounts, AccountStatus
from fanops.variant_transfer import transferred_hooks


def _accounts(cfg, specs):
    """specs: list of (handle, [platforms], persona). All active so surfaces() yields them."""
    a = Accounts(cfg)
    a.accounts = [Account(handle=h, account_id=h.strip("@") or h, platforms=plats,
                          status=AccountStatus.active, persona=persona)
                  for (h, plats, persona) in specs]
    return a


def _win_surface(led, account, platform, hook="WIN", *, n=3, win=90.0, lose=10.0, idprefix=""):
    """Seed `account/platform` with a comparative gated winner `hook` (n WIN posts vs n LOSE posts).
    Mirrors the v2 best_hooks gate: >= MIN_POSTS (3) and a gap (80) well over MIN_GAP (10)."""
    pid = idprefix or f"{account}_{platform.value}_"
    rows = [(hook, win)] * n + [("LOSE", lose)] * n
    for i, (h, lift) in enumerate(rows):
        led.add_post(Post(id=f"{pid}{i}", parent_id="clip_1", account=account, account_id="x",
                          platform=platform, caption="x", state=PostState.analyzed,
                          variant_key=f"vk_{pid}{i}", variant_hook=h, metrics={"lift_score": lift}))


def test_recipient_with_own_winner_gets_nothing(tmp_path, monkeypatch):
    # own-wins rule: a surface that already has its own gated winner borrows nothing.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype"),
                            ("@b", [Platform.instagram], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    _win_surface(led, "@a", Platform.instagram, "STYLE")     # donor 1
    _win_surface(led, "@b", Platform.instagram, "STYLE")     # donor 2
    _win_surface(led, "@c", Platform.instagram, "OWN")       # recipient HAS its own winner
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == []


def test_single_donor_below_min_donors_returns_empty(tmp_path):
    # one donor wins STYLE but TRANSFER_MIN_DONORS default is 2 -> nothing transfers.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    _win_surface(led, "@a", Platform.instagram, "STYLE")     # only ONE donor
    # @c is cold (no posts) -> recipient. Only 1 donor won STYLE < 2 -> [].
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == []


def test_two_donors_same_style_transfers(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype"),
                            ("@b", [Platform.instagram], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    _win_surface(led, "@a", Platform.instagram, "STYLE")
    _win_surface(led, "@b", Platform.instagram, "STYLE")     # 2 distinct donors won STYLE
    # @c cold -> receives STYLE.
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == ["STYLE"]


def test_other_platform_donor_does_not_contribute(tmp_path):
    # same-platform HARD gate: a tiktok winner must not inform an instagram recipient.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.tiktok], "hype"),
                            ("@b", [Platform.tiktok], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    _win_surface(led, "@a", Platform.tiktok, "STYLE")
    _win_surface(led, "@b", Platform.tiktok, "STYLE")        # both donors are TIKTOK
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == []


def test_donor_below_v2_gate_contributes_nothing(tmp_path):
    # a donor whose surface fails v2's own gate (lone variant, no comparative runner-up) is not a
    # winner -> best_hooks returns [] for it -> it cannot seed transfer.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype"),
                            ("@b", [Platform.instagram], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    # @a and @b each have ONLY a single "STYLE" variant (no runner-up) -> best_hooks -> [].
    for acct in ("@a", "@b"):
        for i in range(3):
            led.add_post(Post(id=f"{acct}{i}", parent_id="clip_1", account=acct, account_id="x",
                              platform=Platform.instagram, caption="x", state=PostState.analyzed,
                              variant_key=f"vk_{acct}{i}", variant_hook="STYLE",
                              metrics={"lift_score": 90.0}))
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == []


def test_cap_limits_returned_styles(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MAX_HOOKS", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    # two distinct winning styles, each on 2 donors -> both qualify, but cap=1 -> only one returned.
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype"),
                            ("@b", [Platform.instagram], "hype"),
                            ("@d", [Platform.instagram], "hype"),
                            ("@e", [Platform.instagram], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    _win_surface(led, "@a", Platform.instagram, "ALPHA")
    _win_surface(led, "@b", Platform.instagram, "ALPHA")
    _win_surface(led, "@d", Platform.instagram, "BETA")
    _win_surface(led, "@e", Platform.instagram, "BETA")
    out = transferred_hooks(led, cfg, accts, "@c", Platform.instagram)
    assert len(out) == 1


def test_persona_ranking_is_deterministic_and_prefers_overlap(tmp_path, monkeypatch):
    # When more styles qualify than the cap, prefer donors whose persona token-overlaps the
    # recipient's. ALPHA donors share the recipient's "hype cinematic" words; BETA donors don't.
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MAX_HOOKS", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype cinematic edits"),
                            ("@b", [Platform.instagram], "hype cinematic energy"),
                            ("@d", [Platform.instagram], "calm lyric reading"),
                            ("@e", [Platform.instagram], "calm lyric reading"),
                            ("@c", [Platform.instagram], "hype cinematic")])   # recipient
    _win_surface(led, "@a", Platform.instagram, "ALPHA")
    _win_surface(led, "@b", Platform.instagram, "ALPHA")
    _win_surface(led, "@d", Platform.instagram, "BETA")
    _win_surface(led, "@e", Platform.instagram, "BETA")
    out = transferred_hooks(led, cfg, accts, "@c", Platform.instagram)
    assert out == ["ALPHA"]                                  # persona-closer style wins the single slot
    # determinism: identical inputs -> identical output.
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == out


def test_no_accounts_or_empty_ledger_returns_empty(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@c", [Platform.instagram], "hype")])
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == []   # cold + no donors


def test_recipient_excluded_from_its_own_donor_pool(tmp_path):
    # The recipient surface must never count itself as a donor. @c has a (losing-runner-up) winner
    # of its own -> own-wins short-circuit returns [] anyway; this asserts no self-donation path.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    accts = _accounts(cfg, [("@a", [Platform.instagram], "hype"),
                            ("@c", [Platform.instagram], "hype")])
    _win_surface(led, "@a", Platform.instagram, "STYLE")     # 1 donor
    _win_surface(led, "@c", Platform.instagram, "STYLE")     # @c also "won" STYLE itself
    # @c has its own winner -> own-wins rule returns []; STYLE is NOT double-counted via @c.
    assert transferred_hooks(led, cfg, accts, "@c", Platform.instagram) == []


def test_none_accounts_returns_empty(tmp_path):
    # accounts=None (backward-compat / no registry) -> [] (nothing to borrow), never a crash.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    assert transferred_hooks(led, cfg, None, "@c", Platform.instagram) == []
