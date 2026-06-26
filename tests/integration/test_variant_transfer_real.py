"""Cross-surface transfer — proven END-TO-END ON DISK (the project's Integrate bar). A real
ledger.json where a hook STYLE clearly out-lifts on TWO distinct same-platform donor surfaces, a
THIRD cold recipient surface with no own winner, reloaded FROM DISK, then real request_captions with
transfer ON — asserting the borrowed STYLE reached the on-disk caption request under
learned_hooks_transferred (NOT learned_hooks), and the committed caption_prompt rendered it. A
companion case raises TRANSFER_MIN_DONORS above the donor count and asserts NO transfer (the
stricter gate, proven on disk)."""
from __future__ import annotations
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, MomentState, ClipState, PostState, Platform
from fanops.accounts import Account, Accounts, AccountStatus
from fanops.caption import request_captions
from fanops.prompts import caption_prompt
from fanops.agentstep import request_path

pytestmark = pytest.mark.integration


def _accounts(cfg):
    a = Accounts(cfg)
    a.accounts = [Account(handle=h, account_id=h.strip("@"), platforms=[Platform.instagram],
                          status=AccountStatus.active, persona="hype cinematic")
                  for h in ("@a", "@b", "@c")]
    return a


def _seed_on_disk(cfg: Config) -> None:
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))
    # @a and @b: STYLE (90 x3) vs LOSE (10 x3) -> each a trustworthy gated winner of STYLE.
    for acct in ("@a", "@b"):
        rows = [("STYLE", 90.0)] * 3 + [("LOSE", 10.0)] * 3
        for i, (hook, lift) in enumerate(rows):
            led.add_post(Post(id=f"{acct}{i}", parent_id="clip_1", account=acct, account_id=acct.strip("@"),
                              platform=Platform.instagram, caption="x", state=PostState.analyzed,
                              variant_key=f"vk_{acct}{i}", variant_hook=hook, metrics={"lift_score": lift}))
    # @c: COLD recipient — no analyzed variant posts of its own.
    led.save()


def test_transferred_prior_reaches_caption_request_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    cfg = Config(root=tmp_path)
    _seed_on_disk(cfg)
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True})    # B2: transfer is VALIDATION-FROZEN — open the gate
    #                                                          so the borrowed prior actually reaches the caption
    led = Ledger.load(cfg)                                   # round-trip from disk
    assert led.posts and led.clips["clip_1"].state is ClipState.rendered
    led = request_captions(led, cfg, "clip_1", [("@c", Platform.instagram)], accounts=_accounts(cfg))
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert payload.get("learned_hooks_transferred") == ["STYLE"], \
        f"the borrowed style must reach the on-disk request; got {payload.get('learned_hooks_transferred')!r}"
    assert "learned_hooks" not in payload                    # @c is cold — no OWN winner
    prompt = caption_prompt(payload)
    assert "STYLE" in prompt and "verbatim" in prompt.lower()


def test_stricter_min_donors_blocks_transfer_on_disk(tmp_path, monkeypatch):
    # Raise the gate above the donor count: 2 donors, MIN_DONORS=3 -> NO transfer reaches disk.
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER", "1")
    monkeypatch.setenv("FANOPS_VARIANT_TRANSFER_MIN_DONORS", "3")
    cfg = Config(root=tmp_path)
    _seed_on_disk(cfg)
    from fanops import cutover
    cutover._save_state(cfg, {"metrics_confirmed": True})    # validate so the MIN_DONORS gate (not the freeze) is what blocks
    led = Ledger.load(cfg)
    led = request_captions(led, cfg, "clip_1", [("@c", Platform.instagram)], accounts=_accounts(cfg))
    payload = json.loads(request_path(cfg, "captions", "clip_1").read_text())
    assert "learned_hooks_transferred" not in payload        # stricter gate held
    assert "STYLE" not in json.dumps(payload)
