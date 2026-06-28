import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, Moment, Source, SourceState, MomentState, ClipState, Platform,
                           CaptionSet, CaptionItem)
from fanops.agentstep import response_path, request_path, latest_request_id
from fanops.caption import brand_risk_flag, request_captions, ingest_captions

def _clip(led, cfg):
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me"))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))

def test_brand_risk_flags_offbrand_english():
    assert brand_risk_flag("sorry pls stream my song 🥺") is not None
    assert brand_risk_flag("link in bio, official drop from the label") is not None
    assert brand_risk_flag("no warning. just impact. 🔥") is None

def test_brand_risk_flags_offbrand_arabic():
    # FIX F33: Arabic begging/please-stream must be caught too.
    assert brand_risk_flag("اسمعوا الأغنية من فضلكم 🥺") is not None      # "please listen"
    assert brand_risk_flag("لينك في البايو") is not None                  # "link in bio"
    assert brand_risk_flag("ما في تحذير. بس تأثير.") is None              # clean bravado

def test_request_captions_writes_surfaces_and_language(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    surfaces = [("@a", Platform.instagram), ("@a", Platform.tiktok)]
    led = request_captions(led, cfg, "clip_1", surfaces)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert {s["surface"] for s in payload["surfaces"]} == {"@a/instagram", "@a/tiktok"}
    assert payload["transcript_excerpt"] == "they slept on me"
    assert payload["language"] == "en"
    assert led.clips["clip_1"].state is ClipState.captions_requested

def test_ingest_captions_clean_advances_and_stores(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact.",
                    hashtags=["#mohflow"])]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    assert led.clips["clip_1"].state is ClipState.captioned
    assert led.clips["clip_1"].held is False
    mc = led.clips["clip_1"].meta_captions["@a/instagram"]
    assert len(mc["hashtags"]) <= 4 and all(t.startswith("#") for t in mc["hashtags"])   # vetted, capped

def test_ingest_captions_records_raw_model_hashtags(tmp_path):
    # finding #3 (surface the RAW output): the model's own tag picks are kept beside the vetted line so
    # Studio can show picked-vs-vetted — even a non-vetted word the vet filter drops must be visible.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact.",
                    hashtags=["#mohflow", "#somerandomword"])]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    mc = led.clips["clip_1"].meta_captions["@a/instagram"]
    assert mc["hashtags_raw"] == ["#mohflow", "#somerandomword"]   # raw picks preserved verbatim
    assert "#somerandomword" not in mc["hashtags"]                 # but the vetted line still drops it

def test_ingest_captions_missing_surface_falls_back_to_seed_not_held(tmp_path):
    # CHANGED from the old F74 hold: for hashtags-ONLY fan captions, a response missing a requested
    # surface (commonly a model SOFT-REFUSAL on edgy lyrics -> items:[]) must NOT permanently bury the
    # clip. Synthesize the reach-vetted SEED tags + NO hook for the missing surface and let the clip
    # through to the operator's Review queue (logged). The approval gate is the real review, so this is
    # not an unreviewed default reaching publish (F74's actual concern).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram), ("@a", Platform.tiktok)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="only IG was answered")]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.held is False and c.state is ClipState.captioned         # NOT buried
    fb = c.meta_captions["@a/tiktok"]                                 # the missing surface got a fallback
    assert fb["hook"] is None and fb.get("fallback") is True
    assert 1 <= len(fb["hashtags"]) <= 4 and all(t.startswith("#") for t in fb["hashtags"])

def test_ingest_captions_empty_items_falls_back_all_surfaces(tmp_path):
    # The exact production failure: the model soft-refuses an edgy clip and returns items:[]. EVERY
    # requested surface must get a seed-tag fallback and the clip must reach Review, not vanish held
    # (83% of music clips were being lost to this silent hold).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(
        CaptionSet(request_id=rid, items=[]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.held is False and c.state is ClipState.captioned
    assert c.meta_captions["@a/instagram"]["hashtags"] and c.meta_captions["@a/instagram"]["hook"] is None

def test_ingest_captions_offbrand_holds(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="pls stream 🥺 sorry")]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.held is True and "bravado" in (c.held_reason or "")
    assert c.state is ClipState.held

def test_ingest_captions_brandrisk_wins_over_missing(tmp_path):
    # When a caption is off-brand AND another surface is missing, the brand-risk reason wins.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram), ("@a", Platform.tiktok)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="pls stream 🥺")]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.held is True
    assert "bravado" in (c.held_reason or "") and "missing caption" not in (c.held_reason or "")

def test_ingest_captions_multi_surface_clean_advances(tmp_path):
    # All requested surfaces answered, none off-brand -> captioned (completeness satisfied).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram), ("@a", Platform.tiktok)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact."),
        CaptionItem(surface="@a/tiktok", caption="they slept. not anymore.")]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.state is ClipState.captioned and c.held is False
    assert set(c.meta_captions) == {"@a/instagram", "@a/tiktok"}

def test_ingest_captions_vets_hashtags_max4_and_drops_random(tmp_path):
    # The operator rule: <=4 hashtags, HARD, and only reach-vetted tags (never random AI words).
    # ingest must filter whatever the model returns through vet_hashtags before storing.
    from fanops.hashtags import VETTED
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="#hiphop #rap #rapper #bars #newmusic #mohflow",
                    hashtags=["#hiphop", "#rap", "#rapper", "#bars", "#newmusic", "#mohflow"])]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    mc = led.clips["clip_1"].meta_captions["@a/instagram"]
    assert len(mc["hashtags"]) <= 4                       # hard cap
    assert "#mohflow" not in mc["hashtags"]               # non-vetted random word dropped
    # every survivor traces to a real signal: reach-vetted OR a per-clip content tag (content membership
    # is the new evidence source — a tag is never a sourceless junk word).
    assert all(t in VETTED or mc["tag_sources"].get(t) == "content" for t in mc["hashtags"])
    assert all(mc["tag_sources"][t] for t in mc["hashtags"])   # no sourceless tag ships
    assert mc["caption"] == " ".join(mc["hashtags"])      # posted caption == the vetted tag line

def test_ingest_captions_noop_without_response(tmp_path):
    # No response on disk -> ledger untouched, not held (stale/pending guard).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    led = ingest_captions(led, cfg, "clip_1")
    c = led.clips["clip_1"]
    assert c.state is ClipState.captions_requested and c.held is False

def _seed_clip_awaiting_captions(tmp_path, src_lang="en"):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", state=SourceState.moments_decided,
                          language=src_lang, transcript=[{"start":0,"end":1,"text":"x"}]))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-4", start=0, end=4,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", state=ClipState.rendered))
    led = request_captions(led, cfg, "c1", [("@a", Platform.instagram)])
    return cfg, led

def test_caption_in_wrong_language_is_held(tmp_path):
    cfg, led = _seed_clip_awaiting_captions(tmp_path, src_lang="en")
    rid = latest_request_id(cfg, "captions", "c1")
    response_path(cfg, "captions", "c1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="bonjour le monde", language="fr")]).model_dump_json())
    led = ingest_captions(led, cfg, "c1")
    assert led.clips["c1"].state is ClipState.held
    assert "language" in (led.clips["c1"].held_reason or "").lower()

def test_caption_with_unknown_surface_key_is_held_with_specific_reason(tmp_path):
    cfg, led = _seed_clip_awaiting_captions(tmp_path, src_lang="en")
    rid = latest_request_id(cfg, "captions", "c1")
    # typo: '@accounts/instagram' instead of the requested '@a/instagram'
    response_path(cfg, "captions", "c1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@accounts/instagram", caption="hi", language="en")]).model_dump_json())
    led = ingest_captions(led, cfg, "c1")
    assert led.clips["c1"].state is ClipState.held
    reason = (led.clips["c1"].held_reason or "")
    assert "@accounts/instagram" in reason     # names the BAD surface, not a generic "missing"

# --- C2 hardening (Phase C adversarial finding): the language match must normalize IETF tags ---
# A skeptic proved the naive exact-string `!=` HELD legitimate same-language captions whose tag
# carried a region subtag or different casing (en-US / EN / "en " vs en). That is a harmful
# false-positive: it blocks correct work and, for an autonomous run, silently wedges the clip.
import pytest

@pytest.mark.parametrize("item_lang", ["en-US", "EN", "en-GB", "en ", " en", "En"])
def test_caption_same_base_language_with_region_or_case_is_not_held(tmp_path, item_lang):
    # en-US / EN / en-GB / "en " are all ENGLISH — they must NOT be held against an `en` source.
    cfg, led = _seed_clip_awaiting_captions(tmp_path, src_lang="en")
    rid = latest_request_id(cfg, "captions", "c1")
    response_path(cfg, "captions", "c1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="no warning. just impact.",
                    language=item_lang)]).model_dump_json())
    led = ingest_captions(led, cfg, "c1")
    assert led.clips["c1"].state is ClipState.captioned   # not a false-positive hold
    assert led.clips["c1"].held is False

def test_caption_genuine_mismatch_still_held_after_normalization(tmp_path):
    # Normalization must NOT weaken the real control: fr vs en still holds (regression guard).
    cfg, led = _seed_clip_awaiting_captions(tmp_path, src_lang="en")
    rid = latest_request_id(cfg, "captions", "c1")
    response_path(cfg, "captions", "c1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="bonjour le monde",
                    language="fr-FR")]).model_dump_json())   # region tag on a TRUE mismatch
    led = ingest_captions(led, cfg, "c1")
    assert led.clips["c1"].state is ClipState.held
    assert "language" in (led.clips["c1"].held_reason or "").lower()

# --- T2 (audit b): the off-brand HOLD lists are operator-tunable via 00_control/tuning.json ---
# Contract: when an override KEY is present it REPLACES the in-code default for that list (the
# clearest, most predictable contract — the operator sees exactly the set they wrote). Absent
# file or absent key -> the in-code DEFAULT list is used, so existing behavior is unchanged.

def _write_tuning(cfg, obj):
    cfg.control.mkdir(parents=True, exist_ok=True)
    cfg.tuning_path.write_text(json.dumps(obj))

def test_offbrand_lists_overridable_from_tuning_json(tmp_path):
    cfg = Config(root=tmp_path)
    # Custom EN list contains a benign word ("bananas") and does NOT contain the default "sorry".
    _write_tuning(cfg, {"offbrand_en": [r"\bbananas\b"]})
    # the override fires on its own pattern...
    assert brand_risk_flag("bananas for breakfast", cfg) is not None
    # ...and the DEFAULT-only pattern no longer fires (proves REPLACE, not merge).
    assert brand_risk_flag("sorry pls stream", cfg) is None

def test_defaults_unchanged_without_tuning_json(tmp_path):
    # No tuning.json on disk -> brand_risk_flag(cfg) behaves exactly like the no-cfg default path.
    cfg = Config(root=tmp_path)
    assert not cfg.tuning_path.exists()
    assert brand_risk_flag("sorry pls stream 🥺", cfg) is not None     # default EN still catches
    assert brand_risk_flag("لينك في البايو", cfg) is not None          # default AR still catches
    assert brand_risk_flag("no warning. just impact. 🔥", cfg) is None  # clean stays clean
    # and the legacy no-cfg call is untouched (existing callers/tests keep working).
    assert brand_risk_flag("sorry pls stream 🥺") is not None
    assert brand_risk_flag("no warning. just impact. 🔥") is None

# --- variation v2 (Task 4): request_captions injects the GATED learned-hook hint per surface ---
# This is where the A/B loop CLOSES: a hook that earned a trustworthy win (>= MIN_POSTS analyzed
# posts AND beating the runner-up by >= MIN_GAP) is fed back into the next caption request payload
# (as `learned_hooks`, which caption_prompt renders — variation v2 Task 3). Gated OFF by default,
# fail-open: any error building the hint -> no hint, and the clip STILL advances (request written).
from fanops.models import Post, PostState

def _seed_variant_posts_for_at_a(led):
    # 3 "WIN" posts at lift 90 + 3 "LOSE" posts at lift 10 on @a/instagram -> best_hooks -> ["WIN"]
    # (90 mean - 10 mean = 80 gap, well over the default MIN_GAP 10; 3 >= default MIN_POSTS 3).
    for i, (hook, lift) in enumerate(
        [("WIN", 90.0), ("WIN", 90.0), ("WIN", 90.0), ("LOSE", 10.0), ("LOSE", 10.0), ("LOSE", 10.0)]
    ):
        led.add_post(Post(id=f"p{i}", parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"vk_p{i}", variant_hook=hook, metrics={"lift_score": lift}))

def test_request_captions_injects_learned_hint_when_gate_met(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    _seed_variant_posts_for_at_a(led)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    # the learned hint reached the agent request ON DISK -> the loop is closed.
    assert "WIN" in payload["learned_hooks"]
    assert led.clips["clip_1"].state is ClipState.captions_requested

def test_request_captions_no_hint_when_learning_off(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_VARIANT_LEARNING", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    _seed_variant_posts_for_at_a(led)                       # same past-gate ledger
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    # OFF -> today's behavior: no learned_hooks key at all (byte-identical to pre-v2).
    assert "learned_hooks" not in payload

def test_request_captions_below_gate_emits_no_hint(monkeypatch, tmp_path):
    # Learning ON but the surface has too few analyzed posts -> gate not met -> no hint (loop stays
    # open for this surface until data accrues). The noise guard, exercised through request_captions.
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led.add_post(Post(id="p0", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk_p0", variant_hook="WIN", metrics={"lift_score": 90.0}))  # only 1
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "learned_hooks" not in payload

def test_request_captions_dedups_hint_across_surfaces(monkeypatch, tmp_path):
    # Two surfaces whose winning hook is the same must yield a single, de-duplicated learned hint.
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    _seed_variant_posts_for_at_a(led)                      # @a/instagram -> WIN
    # same WIN winner on @a/tiktok
    for i, (hook, lift) in enumerate(
        [("WIN", 90.0), ("WIN", 90.0), ("WIN", 90.0), ("LOSE", 10.0), ("LOSE", 10.0), ("LOSE", 10.0)]
    ):
        led.add_post(Post(id=f"t{i}", parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.tiktok, caption="x", state=PostState.analyzed,
                          variant_key=f"vk_t{i}", variant_hook=hook, metrics={"lift_score": lift}))
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram), ("@a", Platform.tiktok)])
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert payload["learned_hooks"] == ["WIN"]             # one entry, not ["WIN", "WIN"]

def test_request_captions_failopen_on_learning_error(monkeypatch, tmp_path):
    # A raising best_hooks must NOT propagate: the request is still written, no hint, clip advances.
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    _seed_variant_posts_for_at_a(led)
    monkeypatch.setattr("fanops.caption.best_hooks",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])   # must NOT raise
    p = request_path(cfg, "captions", "clip_1")
    assert p.exists()                                      # request still written -> clip advances
    payload = json.loads(p.read_text())
    assert "learned_hooks" not in payload                  # error -> no hint
    assert led.clips["clip_1"].state is ClipState.captions_requested


# --- transfer: request_captions injects the cross-surface prior for a COLD recipient ----------
from fanops.accounts import Account, Accounts, AccountStatus

def _transfer_accounts(cfg, handles_personas, platform=Platform.instagram):
    a = Accounts(cfg)
    a.accounts = [Account(handle=h, account_id=h.strip("@") or h, platforms=[platform],
                          status=AccountStatus.active, persona=persona)
                  for (h, persona) in handles_personas]
    return a

def _win_surface_for(led, account, platform, hook, *, n=3):
    rows = [(hook, 90.0)] * n + [("LOSE", 10.0)] * n
    for i, (h, lift) in enumerate(rows):
        led.add_post(Post(id=f"{account}_{platform.value}_{i}", parent_id="clip_1", account=account,
                          account_id="x", platform=platform, caption="x", state=PostState.analyzed,
                          variant_key=f"vk_{account}_{i}", variant_hook=h,
                          metrics={"lift_score": lift}))

def test_request_captions_injects_transferred_prior_for_cold_surface(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    accts = _transfer_accounts(cfg, [("@a", "hype"), ("@b", "hype"), ("@c", "hype")])
    _win_surface_for(led, "@a", Platform.instagram, "STYLE")
    _win_surface_for(led, "@b", Platform.instagram, "STYLE")   # 2 donors -> STYLE qualifies
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True})      # transfer is VALIDATION-FROZEN — open the gate
    # request captions for the COLD recipient @c.
    led = request_captions(led, cfg, "clip_1", [("@c", Platform.instagram)], accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert payload["learned_hooks_transferred"] == ["STYLE"]
    assert "learned_hooks" not in payload                      # @c has no OWN winner

def test_request_captions_no_transfer_when_flag_off(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_VARIANT_TRANSFER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    accts = _transfer_accounts(cfg, [("@a", "hype"), ("@b", "hype"), ("@c", "hype")])
    _win_surface_for(led, "@a", Platform.instagram, "STYLE")
    _win_surface_for(led, "@b", Platform.instagram, "STYLE")
    led = request_captions(led, cfg, "clip_1", [("@c", Platform.instagram)], accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "learned_hooks_transferred" not in payload          # OFF -> byte-identical to today

def test_request_captions_no_accounts_means_no_transfer(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    _seed_variant_posts_for_at_a(led)
    # no accounts arg -> backward-compatible default None -> transfer inert (no key).
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "learned_hooks_transferred" not in payload

def test_request_captions_own_winner_takes_precedence_over_transfer(monkeypatch, tmp_path):
    # The recipient has its OWN winner -> it gets learned_hooks (v2) and NO transferred prior
    # (own-wins rule, the anti-homogenization guarantee proven through the request payload).
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    accts = _transfer_accounts(cfg, [("@a", "hype"), ("@b", "hype"), ("@c", "hype")])
    _win_surface_for(led, "@a", Platform.instagram, "STYLE")
    _win_surface_for(led, "@b", Platform.instagram, "STYLE")
    _win_surface_for(led, "@c", Platform.instagram, "OWN")     # @c has its OWN winner
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True})      # open the validation gate so transfer COULD fire —
    led = request_captions(led, cfg, "clip_1", [("@c", Platform.instagram)], accounts=accts)   # the OWN-WINS rule is what suppresses it
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert payload["learned_hooks"] == ["OWN"]                 # own signal present
    assert "learned_hooks_transferred" not in payload          # borrowed signal suppressed (own-wins, not the freeze)

def test_request_captions_failopen_on_transfer_error(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    accts = _transfer_accounts(cfg, [("@a", "hype"), ("@b", "hype"), ("@c", "hype")])
    _win_surface_for(led, "@a", Platform.instagram, "STYLE")
    _win_surface_for(led, "@b", Platform.instagram, "STYLE")
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True})      # open the validation gate so the raising
    #                                                           scorer is actually REACHED (else the freeze
    #                                                           short-circuits and the fail-open path is untested)
    monkeypatch.setattr("fanops.caption.transferred_hooks",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    led = request_captions(led, cfg, "clip_1", [("@c", Platform.instagram)], accounts=accts)  # no raise
    p = request_path(cfg, "captions", "clip_1")
    assert p.exists()
    payload = json.loads(p.read_text())
    assert "learned_hooks_transferred" not in payload          # error -> no prior
    assert led.clips["clip_1"].state is ClipState.captions_requested

def test_ingest_captions_ignores_legacy_caption_hook(tmp_path):
    # ROOT FIX: the caption gate no longer authors a hook (the frame-seeing moment gate does). Even if a
    # (legacy) response still carries a hook, ingest_captions IGNORES it and stores None.
    from fanops.config import Config
    from fanops.ledger import Ledger
    from fanops.models import Source, Moment, Clip, MomentState, ClipState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-5", start=0, end=5,
                          reason="r", state=MomentState.decided))
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c.mp4", state=ClipState.captions_requested))
    from fanops import caption as capmod
    led = capmod.request_captions(led, cfg, "c1", [("@a", Platform.instagram)])
    # write a response carrying a hook (raw dict — CaptionItem.hook is optional)
    rid = json.loads(request_path(cfg, "captions", "c1").read_text())["request_id"]
    resp = {"request_id": rid, "items": [{"surface": "@a/instagram", "caption": "they slept on me, watch",
            "hashtags": ["#x"], "language": "en", "hook": "THEY SLEPT ON ME"}]}
    response_path(cfg, "captions", "c1").write_text(json.dumps(resp))
    led = capmod.ingest_captions(led, cfg, "c1")
    assert led.clips["c1"].meta_captions["@a/instagram"]["hook"] is None   # caption hook ignored (moment gate owns hooks)

# ---- variation v3 (UCB bandit): the flag selects ucb_rank over best_hooks for the OWN-surface bias.
# A surface engineered so UCB's pick DIFFERS from greedy's: 8x LEAD@60 + 1x NEW@59. Greedy's gap
# (1.0) < MIN_GAP 10 -> best_hooks returns [] (no hint). UCB explores the under-sampled NEW (its
# optimism bonus beats LEAD's thin mean lead) -> picks NEW. So UCB-on yields "NEW", UCB-off yields none.
def _seed_thinlead_for_at_a(led):
    for i in range(1, 9):
        led.add_post(Post(id=f"L{i}", parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.analyzed,
                          variant_key=f"vk_L{i}", variant_hook="LEAD", metrics={"lift_score": 60.0}))
    led.add_post(Post(id="N1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.analyzed,
                      variant_key="vk_N1", variant_hook="NEW", metrics={"lift_score": 59.0}))

def test_request_captions_ucb_picks_challenger_when_flag_on(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    _seed_thinlead_for_at_a(led)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "NEW" in payload.get("learned_hooks", [])      # UCB exploration pick reached the request
    assert "LEAD" not in payload.get("learned_hooks", []) # greedy's would-be leader did NOT

def test_request_captions_greedy_when_ucb_off(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.delenv("FANOPS_VARIANT_UCB", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    _seed_thinlead_for_at_a(led)                          # same ledger
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    # greedy: gap 1.0 < MIN_GAP 10 -> best_hooks [] -> no hint at all (UCB-off = v2 behavior)
    assert "learned_hooks" not in payload

def test_request_captions_no_hint_when_learning_off_even_with_ucb(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_VARIANT_LEARNING", raising=False)   # master gate OFF
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    _seed_thinlead_for_at_a(led)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "learned_hooks" not in payload                 # learning off -> neither scorer runs

def test_request_captions_failopen_on_ucb_error(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_VARIANT_LEARNING", "1")
    monkeypatch.setenv("FANOPS_VARIANT_UCB", "1")
    monkeypatch.setattr("fanops.caption.ucb_rank",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    _seed_thinlead_for_at_a(led)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])  # must NOT raise
    assert request_path(cfg, "captions", "clip_1").exists()                   # written anyway
    assert led.clips["clip_1"].state is ClipState.captions_requested          # clip advanced


# --- persona injection: the UI-set per-account fan voice must reach the caption request ----------
# Persona exists on Account and is shown in the Studio, but was never injected into the caption
# prompt (display-only). request_captions now carries each surface's persona into the payload so the
# model writes in that fan voice. Absent persona stays byte-identical to the pre-persona payload.

def test_request_captions_injects_persona_per_surface(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    accts = _transfer_accounts(cfg, [("@a", "hype superfan")])
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)], accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    sfc = payload["surfaces"][0]
    assert sfc["surface"] == "@a/instagram"
    assert sfc["persona"] == "hype superfan"

def test_request_captions_no_persona_key_when_absent(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    accts = _transfer_accounts(cfg, [("@a", None)])                 # account with no persona
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)], accounts=accts)
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "persona" not in payload["surfaces"][0]                  # None persona -> no key

def test_request_captions_no_persona_key_without_accounts(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])  # no accounts arg
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "persona" not in payload["surfaces"][0]                  # backward-compatible default


# --- M2: em-dash / overlong-hook sanitation at caption ingest ------------------------------------
# REMOVED with the root fix: the caption gate no longer authors a hook, so the caption-hook em-dash
# sanitize AND the 7-word truncation (the mid-sentence chop bug) no longer exist. Hook sanitization now
# lives on the frame-seeing moment gate (tests/test_hook_authorship.py::test_ingest_sanitizes_persona_hooks).


# --- persona differentiation: per-account tag_lean threaded request -> ingest ---
# (Account/Accounts already imported above for the transfer tests)

def _accts(cfg, rows):
    a = Accounts(cfg); a.accounts = [Account(**r) for r in rows]; return a

def test_ingest_captions_no_accounts_is_byte_identical(tmp_path):
    # no accounts -> no lean/corpus; content is a CLIP-level signal so it still applies (the clip's own
    # transcript drives its tags regardless of accounts). The expected line carries that same content.
    from fanops.hashtags import vet_hashtags, content_tag_candidates
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])   # no accounts -> no lean
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "tag_lean" not in payload["surfaces"][0]           # absent key, byte-identical request
    rid = latest_request_id(cfg, "captions", "clip_1")
    tags = ["#hiphop", "#bars", "#viral", "#rap"]
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="c", hashtags=tags)]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    mc = led.clips["clip_1"].meta_captions["@a/instagram"]["hashtags"]
    content = content_tag_candidates("they slept on me")      # the _clip helper's transcript
    assert mc == vet_hashtags(tags, Platform.instagram, "en", content=content)  # no lean; content still rides


# ---- AGENT-6: the vetting platform comes from the REQUEST record, not a re-parse of the model's surface ----
def test_platform_for_surface_prefers_request_over_parse():
    from fanops.caption import _platform_for_surface
    # the surface KEY tail says instagram, but the request recorded tiktok -> the request wins (vet truth)
    assert _platform_for_surface("@a/instagram", {"@a/instagram": "tiktok"}) == Platform.tiktok
    assert _platform_for_surface("@a/tiktok", {}) == Platform.tiktok          # request omits it -> legacy parse fallback
    assert _platform_for_surface("@a/tiktok", {"@a/tiktok": "garbage"}) == Platform.tiktok   # bad value -> parse, never crash

def test_platform_derived_from_request_not_model_string(tmp_path, mocker):
    # End to end: a request whose surface KEY tail diverges from its recorded platform must vet under the
    # RECORDED platform. (Synthetic divergence: the normal request path keys surface==handle/platform.value,
    # so we hand-diverge the on-disk request to prove the request is authoritative, not the parsed string.)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg); _clip(led, cfg)
    led = request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)])
    req = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    req["surfaces"][0]["platform"] = "tiktok"                 # diverge: key stays @a/instagram, platform now tiktok
    request_path(cfg, "captions", "clip_1").write_text(json.dumps(req))   # preserves request_id -> response still matches
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(
        request_id=rid, items=[CaptionItem(surface="@a/instagram", caption="#hiphop")]).model_dump_json())
    import fanops.caption as capmod
    real = capmod.vet_hashtags_traced; captured = {}
    def spy(tags, plat, *a, **k): captured["plat"] = plat; return real(tags, plat, *a, **k)
    mocker.patch("fanops.caption.vet_hashtags_traced", side_effect=spy)
    ingest_captions(led, cfg, "clip_1")
    assert captured["plat"] == Platform.tiktok               # vetted under the REQUESTED platform, not the parsed @a/instagram
