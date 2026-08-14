# tests/test_recaption.py — `fanops posts recaption`: the backlog re-caption DRIVER over the
# ORIGINAL pipeline (request_captions -> responder -> ingest_captions -> post sync). Pins: dry-run
# is a pure read; apply syncs posts from the freshly-vetted meta_captions and RESTORES the clip's
# prior state (crosspost re-mints rejected surfaces of a `captioned` clip — the restore is load-
# bearing); a pipeline HOLD leaves posts untouched; imminent queued posts are skipped; the journal
# makes a re-run skip done clips (resume).
import json
from datetime import datetime, timedelta, timezone
from fanops.agentstep import pending, request_path, response_path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (CaptionItem, CaptionSet, Clip, ClipState, Moment, MomentState,
                           Platform, Post, PostState, Source)
from fanops.recaption import _journal_path, run_recaption
from fanops.timeutil import iso_z

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
FUTURE = "2099-01-01T00:00:00Z"
_OLD_ENTRY = {"caption": "#old", "hashtags": ["#old"], "hashtags_raw": [], "hook": None,
              "axis": None, "rationale": None, "tag_sources": {}}


def _seed(cfg, *, clip_state=ClipState.queued, post_state=PostState.awaiting_approval,
          lang="en", sched=FUTURE):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active",
         "hashtag_corpus": ["#alpha", "#beta", "#gamma"]}]}))
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language=lang))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.decided))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=clip_state,
                      meta_captions={"a/instagram": dict(_OLD_ENTRY)}))
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="#old", hashtags=["#old"],
                      state=post_state, scheduled_time=sched, created_at="2026-07-01T12:00:00+00:00"))
    led.save()
    return led


class _FakeResponder:
    """Answers pending caption gates like the real sequential responder — but from canned tags."""
    def __init__(self, hashtags=("#alpha", "#hiphop"), language="en"):
        self.hashtags = list(hashtags); self.language = language; self.calls = 0
        self.last_kinds = None; self.last_parallel = None

    def answer_pending(self, cfg, *, kinds=None, parallel=None):
        self.last_kinds = kinds; self.last_parallel = parallel
        n = 0
        for key in pending(cfg, kind="captions"):
            req = json.loads(request_path(cfg, "captions", key).read_text())
            items = [CaptionItem(surface=s["surface"], caption="fresh", language=self.language,
                                 hashtags=list(self.hashtags)) for s in req.get("surfaces", [])]
            response_path(cfg, "captions", key).write_text(
                CaptionSet(request_id=req.get("request_id"), items=items).model_dump_json())
            n += 1
        self.calls += n
        return n


def test_apply_answers_captions_only_in_parallel(tmp_path):
    # Recaption must NOT drain moments/hooks, and must force a parallel captions fan-out.
    cfg = Config(root=tmp_path); _seed(cfg)
    fake = _FakeResponder()
    run_recaption(cfg, apply=True, responder=fake, now=NOW)
    assert fake.last_kinds == ("captions",)
    assert fake.last_parallel is True
    assert fake.calls == 1


def test_dry_run_lists_targets_and_writes_nothing(tmp_path):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.add_post(Post(id="p_pub", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.published,
                      public_url="dryrun://p_pub",   # R1: a published Post requires a public_url at construction
                      created_at="2026-07-01T12:00:00+00:00"))
    led.save()
    s = run_recaption(cfg, apply=False, now=NOW)
    assert s["clips"] == 1 and s["posts"] == 1                      # published post is NOT a target
    assert s["rows"][0]["clip"] == "clip_1"
    assert not request_path(cfg, "captions", "clip_1").exists()     # a dry-run opens no gate
    assert Ledger.load(cfg).posts["p1"].caption == "#old"           # and writes nothing


def test_apply_syncs_posts_and_restores_clip_state(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#slept": {"graph_id": "g1", "play_count": 100, "media_count": 5000.0,
                   "measured_at": "2026-07-01T00:00:00+00:00"},
        "#hiphop": {"graph_id": "g2", "play_count": 50, "media_count": 100.0,
                    "measured_at": "2026-07-01T00:00:00+00:00"},
    }))
    fake = _FakeResponder(hashtags=["#slept", "#junkjunkjunk", "#hiphop"])
    s = run_recaption(cfg, apply=True, responder=fake, now=NOW)
    assert s["done"] == 1 and s["synced"] == 1 and fake.calls == 1
    led = Ledger.load(cfg)
    p = led.posts["p1"]
    assert p.caption == " ".join(p.hashtags) and 0 < len(p.hashtags) <= 4   # the vetted tag line
    assert "#slept" in p.hashtags                                  # source-measured lead survives the vet
    assert "#junkjunkjunk" not in p.hashtags                        # junk cannot reach a post
    assert p.state is PostState.awaiting_approval                   # approval lifecycle untouched
    assert p.edited_at
    clip = led.clips["clip_1"]
    assert clip.state is ClipState.queued                           # RESTORED — never left `captioned`
    assert clip.meta_captions["a/instagram"]["tag_sources"]         # provenance stamped by ingest
    assert "clip_1" in json.loads(_journal_path(cfg).read_text())["done"]


def test_rerun_skips_done_clips(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    run_recaption(cfg, apply=True, responder=_FakeResponder(), now=NOW)
    first = Ledger.load(cfg).posts["p1"].caption
    again = _FakeResponder()
    s2 = run_recaption(cfg, apply=True, responder=again, now=NOW)
    assert again.calls == 0 and s2["done"] == 0                     # resume: nothing re-requested
    assert Ledger.load(cfg).posts["p1"].caption == first


def test_pipeline_hold_leaves_posts_untouched(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, lang="en")
    s = run_recaption(cfg, apply=True, responder=_FakeResponder(language="ar"), now=NOW)
    assert s["held"] == 1 and s["synced"] == 0
    led = Ledger.load(cfg)
    assert led.clips["clip_1"].state is ClipState.held              # the pipeline spoke — hold stands
    assert led.posts["p1"].caption == "#old"                        # posts untouched on hold
    j = json.loads(_journal_path(cfg).read_text())
    assert "clip_1" in j["done"] and j["notes"]["clip_1"].startswith("held:")


def test_imminent_queued_post_is_skipped(tmp_path):
    cfg = Config(root=tmp_path)
    led = _seed(cfg, post_state=PostState.queued, sched=iso_z(NOW + timedelta(minutes=2)))
    led.add_post(Post(id="p2", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="#old", hashtags=["#old"],
                      state=PostState.awaiting_approval, scheduled_time=FUTURE,
                      created_at="2026-07-01T12:00:00+00:00"))
    led.save()
    s = run_recaption(cfg, apply=True, responder=_FakeResponder(), now=NOW)
    assert s["skipped_imminent"] == 1 and s["synced"] == 1
    led = Ledger.load(cfg)
    assert led.posts["p1"].caption == "#old"                        # imminent queued: shipping, untouched
    assert led.posts["p2"].caption == " ".join(led.posts["p2"].hashtags) != "#old"
