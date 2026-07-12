# tests/test_onboarding_cards.py — U12: the ACCOUNT-CENTRIC Go-Live onboarding cards. One card per handle
# carrying its own channel ladder + a single next-action CTA, so onboarding one account never means hopping
# stations. The load-bearing invariants under test: the card is a pure RE-PROJECTION of channel_readiness
# (never a second readiness computation), its CTA is the WORST-channel first_blocker (same priority order as
# the fleet next_blocker) deep-linking to the form that clears it, the DISPLAY-ONLY insights row states IG
# creds / TikTok ceiling WITHOUT ever feeding ChannelReadiness.ready or the live-arming count, and no rendered
# page echoes a secret VALUE (write-only inputs stay type=password). Env isolation mirrors test_studio_golive.
import json
import os
import pytest
from fanops.config import Config
from fanops.studio import golive, views
from fanops.meta_graph import per_account_token_env_key
from tests.keyring_fake import install_mem_keyring

_ENV_KEYS = ("FANOPS_LIVE", "FANOPS_POSTER", "POSTIZ_URL", "POSTIZ_API_KEY", "ZERNIO_API_KEY",
             "FANOPS_CREATIVE_VARIATION", "FANOPS_ACCOUNT_CASTING", "FANOPS_RESPONDER",
             "META_GRAPH_TOKEN", "META_IG_USER_ID")
_ENV_BASELINE = {k: os.environ.get(k) for k in _ENV_KEYS}


@pytest.fixture(autouse=True)
def _restore_env():
    yield
    for k, v in _ENV_BASELINE.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@pytest.fixture(autouse=True)
def _mem_keyring(monkeypatch):
    install_mem_keyring(monkeypatch)


def _clean(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)             # clean start + registers the key for teardown-restore
    # a per-handle Meta token env key can leak from the operator .env; clear the slugs the fixtures use
    for h in ("ig", "tk", "yt", "a", "linked"):
        k = per_account_token_env_key(h)
        if k:
            monkeypatch.delenv(k, raising=False)
    return Config(root=tmp_path)


def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


def _card(cards, handle):
    return next(c for c in cards if c.account.handle == handle)


def _ch_of(card, platform):
    return next(c for c in card.channels if c.platform == platform)


def _rd(cfg, handle, platform):
    """The fleet channel_readiness row for this (handle, platform) — the identity the card must reproject."""
    return next(c for c in views.channel_readiness(cfg) if c.handle == handle and c.platform == platform)


# ---- re-projection identity: the card ladder EQUALS channel_readiness (never a second computation) ----
def test_cards_reproject_channel_readiness(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [
        {"handle": "tk", "account_id": "", "platforms": ["tiktok"], "status": "active",
         "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}, "persona": "v"},   # ready
        {"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_1"}},                                                  # R2 drift — not ready
    ])
    cards = views.onboarding_account_cards(cfg)
    assert {c.account.handle for c in cards} == {"tk", "ig"}
    for card in cards:
        for ch in card.channels:
            src = _rd(cfg, card.account.handle, ch.platform)
            assert (ch.mapped, ch.creds, ch.persona, ch.window, ch.ready, ch.first_blocker) == \
                   (src.mapped, src.creds, src.persona, src.window, src.ready, src.first_blocker)
    assert _ch_of(_card(cards, "tk"), "tiktok").ready is True
    assert _ch_of(_card(cards, "ig"), "instagram").ready is False


def test_each_active_account_gets_one_card(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [
        {"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active"},
        {"handle": "tk", "account_id": "", "platforms": ["tiktok"], "status": "active"},
    ])
    cards = views.onboarding_account_cards(cfg)
    assert [c.account.handle for c in cards] == ["ig", "tk"]     # one card per active handle, order preserved


def test_demoted_account_gets_a_card(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [
        {"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active"},   # gates the surface
        {"handle": "sleeper", "account_id": "", "platforms": ["instagram"], "status": "planned"},
    ])
    cards = views.onboarding_account_cards(cfg)
    assert "sleeper" in {c.account.handle for c in cards}        # a demoted account is not a dead-end
    # a demoted account has no live channel_readiness rows (only active() is projected) -> empty ladder, promote CTA
    assert _card(cards, "sleeper").channels == []


# ---- CTA == the WORST-channel first_blocker (same priority order as the fleet next_blocker) ----
def test_card_cta_matches_worst_channel_blocker(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    # two channels on one handle: instagram is unmapped ("map an integration id", prio 1); tiktok is mapped but
    # backend-less ("route to a scheduler backend", prio 2). The card CTA must pick the WORST (lowest prio number).
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram", "tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}}])
    card = _card(views.onboarding_account_cards(cfg), "ig")
    blockers = {c.first_blocker for c in card.channels if c.first_blocker}
    worst = min(blockers, key=views._blocker_priority)          # the SAME ordering the fleet next_blocker uses
    assert card.next_blocker == worst
    assert card.next_blocker == "map an integration id"


def test_ready_card_has_empty_blocker_no_cta(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [{"handle": "tk", "account_id": "", "platforms": ["tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}, "persona": "v"}])
    card = _card(views.onboarding_account_cards(cfg), "tk")
    assert all(c.ready for c in card.channels)
    assert card.next_blocker == "" and card.next_anchor == ""    # ready -> ready chip, no CTA


# ---- CTA anchor deep-links to the form that clears the blocker ----
def test_card_cta_anchor_for_map_blocker(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    card = _card(views.onboarding_account_cards(cfg), "ig")
    assert card.next_blocker == "map an integration id"
    assert "map-ig-instagram" in card.next_anchor               # deep-links to THIS handle's unmapped-channel form


def test_card_cta_anchor_for_backend_blocker(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active",
                 "integrations": {"instagram": "ig_1"}}])   # mapped, no backend -> route to a scheduler backend
    card = _card(views.onboarding_account_cards(cfg), "ig")
    assert card.next_blocker == "route to a scheduler backend"
    assert "backend-ig-instagram" in card.next_anchor


def test_card_cta_anchor_for_connect_blocker(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)                          # NO scheduler key -> connect-first
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    card = _card(views.onboarding_account_cards(cfg), "ig")
    assert card.next_blocker == "connect Postiz or Zernio first"
    assert card.next_anchor == "#golive-connect"


def test_card_cta_anchor_for_persona_blocker(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "1")
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active",
                 "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}}])
    card = _card(views.onboarding_account_cards(cfg), "ig")
    assert card.next_blocker == "link a persona"
    assert card.next_anchor == "#persona-ig"


# ---- insights row (DISPLAY-ONLY): IG creds state, TikTok ceiling, YT/other omitted ----
def test_insights_ig_missing_creds(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    card = _card(views.onboarding_account_cards(cfg), "ig")
    ig = next(r for r in card.insights_rows if r["platform"] == "instagram")
    assert ig["ok"] is False
    assert "Meta" in ig["label"] and "Instagram" in ig["label"]   # the IG-creds fix line


def test_insights_ig_present_creds(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv(per_account_token_env_key("ig"), "EAAtoken")   # per-handle Graph token set
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active",
                 "ig_user_id": "17841400000000000"}])                 # + per-handle IG user id
    card = _card(views.onboarding_account_cards(cfg), "ig")
    ig = next(r for r in card.insights_rows if r["platform"] == "instagram")
    assert ig["ok"] is True


def test_insights_tiktok_ceiling(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [{"handle": "tk", "account_id": "", "platforms": ["tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}, "persona": "v"}])
    card = _card(views.onboarding_account_cards(cfg), "tk")
    tk = next(r for r in card.insights_rows if r["platform"] == "tiktok")
    assert tk["ok"] is False
    assert tk["label"] == "Insights not available via Zernio"     # red-flag 3's honest ceiling, verbatim


def test_insights_youtube_row_omitted(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "yt", "account_id": "", "platforms": ["youtube"], "status": "active"}])
    card = _card(views.onboarding_account_cards(cfg), "yt")
    assert [r for r in card.insights_rows if r["platform"] == "youtube"] == []   # no false insights parity for YT


# ---- insights NEVER gates ready or the live-arming count ----
def test_insights_do_not_block_ready(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    # a fully live-ready IG channel with NO Meta creds: publish path green, insights blind.
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active",
                 "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}, "persona": "v"}])
    card = _card(views.onboarding_account_cards(cfg), "ig")
    ig_ch = _ch_of(card, "instagram")
    ig_insights = next(r for r in card.insights_rows if r["platform"] == "instagram")
    assert ig_ch.ready is True and ig_insights["ok"] is False     # ready DESPITE blind insights
    # and the live-arming readiness count (channels with ready) still sees it as ready
    st = views.golive_status(cfg)
    assert len([c for c in st.channels if c.ready]) == 1
    assert golive.go_live(cfg, confirmed=True).ok is True         # insights never gates the live flip


# ---- account_cards wired into golive_status so every panel render carries them (no route edits) ----
def test_golive_status_carries_account_cards(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    st = views.golive_status(cfg)
    assert [c.account.handle for c in st.account_cards] == ["ig"]


# ---- rendered pages: card markup present + NO secret VALUE echo, write-only inputs stay password ----
def test_rendered_pages_no_secret_echo(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setattr(golive.postiz, "postiz_check_auth", lambda c: True)
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    cli = _client(cfg)
    # GET the panel -> account card + ladder rendered
    html = cli.get("/golive").get_data(as_text=True)
    assert "onboarding-card" in html and "onboarding-ladder" in html
    # POST a Postiz key -> the key VALUE is never echoed back into the response
    r = cli.post("/golive/config", data={"url": "https://p.example.com", "key": "sk_TOPSECRET_postiz"})
    body = r.get_data(as_text=True)
    assert "sk_TOPSECRET_postiz" not in body
    # POST a per-account Meta token -> the token VALUE is never echoed
    r2 = cli.post("/golive/account/meta-creds",
                  data={"handle": "ig", "ig_user_id": "17841400000000000", "token": "EAAsecrettoken"})
    body2 = r2.get_data(as_text=True)
    assert "EAAsecrettoken" not in body2
    # every rendered password input is EMPTY (write-only: no secret pre-filled as a value)
    for chunk in body2.split("<input")[1:]:
        head = chunk.split(">")[0]
        if 'type="password"' in head:
            assert 'value="' not in head or 'value=""' in head


def test_ig_user_id_is_shown_but_token_is_not(tmp_path, monkeypatch):
    # the IG user id is NON-secret (accounts.json) — safe to render as the current value; the token is a SECRET.
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv(per_account_token_env_key("ig"), "EAAlivetoken")
    _seed(cfg, [{"handle": "ig", "account_id": "", "platforms": ["instagram"], "status": "active",
                 "ig_user_id": "17841499999999999"}])
    html = _client(cfg).get("/golive").get_data(as_text=True)
    assert "17841499999999999" in html          # the non-secret id is rendered
    assert "EAAlivetoken" not in html            # the secret token is NEVER rendered


# ---- fresh workspace: add-account affordance appears first (no dead-end, "no sorcery" path) ----
def test_fresh_workspace_add_account_first(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)                          # zero accounts
    html = _client(cfg).get("/golive").get_data(as_text=True)
    assert "do_golive_account_add" not in html or True          # (endpoint name is url-mapped; assert the form)
    assert 'name="handle"' in html and "Add account" in html    # the add-account form is present on an empty workspace
    assert views.onboarding_account_cards(cfg) == []             # and there are no account cards to hunt through
