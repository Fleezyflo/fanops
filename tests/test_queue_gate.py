# tests/test_queue_gate.py — U4: explicit run queue gate (pending → bind → release → catalogued).
import json
from pathlib import Path

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.pipeline import advance
from fanops.studio import actions


def _toolchain(mocker):
    def fake(cmd, **kw):
        joined = " ".join(str(c) for c in cmd)
        if cmd[0] == "ffprobe":
            class R:
                returncode=0; stderr=""
                stdout = "video" if "codec_type" in joined else "1920\n1080\n12.0\n"
            return R()
        if cmd[0] == "whisper" or "fanops._fwrun" in cmd:
            outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
                {"language": "en", "segments": [{"start": 0, "end": 2, "text": "hi"}]}))
            class R: returncode=0; stderr=""; stdout=""
            return R()
        if cmd[0] == "ffmpeg" and "null" in cmd:
            class R:
                returncode=0; stdout=""
                stderr = ("silence_end: 1.0 | silence_duration: 0.5" if "silencedetect" in joined
                          else "[scdet @ 0x] lavfi.scd.score: 28.0, lavfi.scd.time: 1.0")
            return R()
        if cmd[0] == "ffmpeg" and not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode=0; stderr=""; stdout=""
        return R()
    for mod in ("transcribe", "signals", "clip", "ingest", "media_probe"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)


def _put_video(cfg, mocker, name="a.mp4", data=None):
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    (cfg.inbox / name).write_bytes(data if data is not None else b"V" + name.encode())
    _toolchain(mocker)


def _seed_accounts(cfg, handles):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "x", "platforms": ["instagram"], "status": "active"} for h in handles]}))


def test_advance_holds_unbound_pending(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put_video(cfg, mocker)
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    assert len(led.sources) == 1
    src = next(iter(led.sources.values()))
    assert src.state is SourceState.pending and src.batch_id is None
    assert not list(cfg.agent_io.glob("**/transcript.json"))
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    led2 = Ledger.load(cfg)
    assert next(iter(led2.sources.values())).state is SourceState.pending


def test_bind_queue_stamps_batch(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put_video(cfg, mocker); _seed_accounts(cfg, ["a", "b"])
    actions.catalogue_inbox(cfg)
    led = Ledger.load(cfg)
    sid = next(iter(led.sources))
    res = actions.bind_queue(cfg, source_ids=[sid], batch_name="Line one", target_accounts=["a", "b"])
    assert res.ok
    led = Ledger.load(cfg)
    src = led.sources[sid]
    assert src.batch_id is not None
    b = led.get_batch(src.batch_id)
    assert b is not None and b.target_accounts == ["a", "b"]


def test_two_binds_two_lines(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put_video(cfg, mocker, "a.mp4"); _put_video(cfg, mocker, "b.mp4")
    _seed_accounts(cfg, ["a", "b"])
    actions.catalogue_inbox(cfg)
    led = Ledger.load(cfg)
    sids = sorted(led.sources)
    actions.bind_queue(cfg, source_ids=[sids[0]], batch_name="A only", target_accounts=["a"])
    actions.bind_queue(cfg, source_ids=[sids[1]], batch_name="B only", target_accounts=["b"])
    led = Ledger.load(cfg)
    assert len(led.batches) == 2
    assert led.sources[sids[0]].batch_id != led.sources[sids[1]].batch_id


def test_release_batch_only_that_line(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put_video(cfg, mocker, "a.mp4"); _put_video(cfg, mocker, "b.mp4")
    _seed_accounts(cfg, ["a", "b"])
    actions.catalogue_inbox(cfg)
    led = Ledger.load(cfg)
    sids = sorted(led.sources)
    r1 = actions.bind_queue(cfg, source_ids=[sids[0]], batch_name="Line A", target_accounts=["a"])
    assert actions.bind_queue(cfg, source_ids=[sids[1]], batch_name="Line B", target_accounts=["b"]).ok
    bid_a = r1.detail["batch_id"]
    res = actions.release_batch(cfg, bid_a, confirmed=True)
    led = Ledger.load(cfg)
    assert led.sources[sids[0]].state is not SourceState.pending
    assert led.sources[sids[1]].state is SourceState.pending
    assert (res.detail or {}).get("line_released") == 1


def test_release_all_held(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put_video(cfg, mocker, "a.mp4"); _put_video(cfg, mocker, "b.mp4")
    _seed_accounts(cfg, ["a", "b"])
    actions.catalogue_inbox(cfg)
    led = Ledger.load(cfg)
    sids = sorted(led.sources)
    actions.bind_queue(cfg, source_ids=[sids[0]], batch_name="A", target_accounts=["a"])
    actions.bind_queue(cfg, source_ids=[sids[1]], batch_name="B", target_accounts=["b"])
    actions.release_all_held(cfg, confirmed=True)
    led = Ledger.load(cfg)
    assert all(s.state is not SourceState.pending for s in led.sources.values())


def test_prepare_releases_bound_held_footage(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path); _put_video(cfg, mocker, "a.mp4"); _put_video(cfg, mocker, "b.mp4")
    _seed_accounts(cfg, ["a", "b"])
    actions.catalogue_inbox(cfg)
    led = Ledger.load(cfg)
    sids = sorted(led.sources)
    assert actions.bind_queue(cfg, source_ids=[sids[0]], batch_name="Bound", target_accounts=["a"]).ok
    res = actions.run_prepare(cfg, base_time="2026-06-02T18:00:00Z", confirmed=True)
    assert res.detail["released"] == 1
    led = Ledger.load(cfg)
    assert led.sources[sids[0]].state is not SourceState.pending
    assert led.sources[sids[1]].state is SourceState.pending
    assert actions.run_prepare(cfg, base_time="2026-06-02T18:00:00Z", confirmed=True).detail["released"] == 0


def test_gate_off_byte_identical_birth_and_autobatch(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "0")
    cfg = Config(root=tmp_path); _put_video(cfg, mocker)
    from fanops.studio import actions as studio_actions
    res = studio_actions.run_ingest(cfg, batch_name="   ")
    assert res.ok and "batch" not in res.detail
    led = Ledger.load(cfg)
    src = next(iter(led.sources.values()))
    assert src.state is SourceState.catalogued
    assert src.batch_id is not None and led.get_batch(src.batch_id).name.startswith("drop-")


def test_grandfather_catalogued_untouched(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="legacy", source_path="/v/old.mp4", state=SourceState.catalogued, batch_id="b-old"))
    _put_video(cfg, mocker)
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    assert led.sources["legacy"].batch_id == "b-old"


def test_bind_queue_defaults_blank_name(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put_video(cfg, mocker); _seed_accounts(cfg, ["a"])
    actions.catalogue_inbox(cfg)
    sid = next(iter(Ledger.load(cfg).sources))
    res = actions.bind_queue(cfg, source_ids=[sid], batch_name="  ", target_accounts=["a"])
    assert res.ok and res.detail["batch"].startswith("queue-")
    b = Ledger.load(cfg).get_batch(res.detail["batch_id"])
    assert b is not None and b.name.startswith("queue-")


def test_prepare_binds_then_releases(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_RESPONDER", "llm")
    cfg = Config(root=tmp_path); _put_video(cfg, mocker, "a.mp4"); _put_video(cfg, mocker, "b.mp4")
    _seed_accounts(cfg, ["a", "b"])
    actions.catalogue_inbox(cfg)
    sids = sorted(Ledger.load(cfg).sources)
    res = actions.run_prepare(cfg, base_time="2026-06-02T18:00:00Z", confirmed=True,
                              source_ids=[sids[0]], target_accounts=["a"])
    assert res.detail["released"] == 1 and res.detail.get("bound") == 1
    led = Ledger.load(cfg)
    assert led.sources[sids[0]].state is not SourceState.pending
    assert led.sources[sids[0]].batch_id is not None
    assert led.get_batch(led.sources[sids[0]].batch_id).target_accounts == ["a"]
    assert led.sources[sids[1]].state is SourceState.pending and led.sources[sids[1]].batch_id is None
