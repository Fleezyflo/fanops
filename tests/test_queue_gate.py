# tests/test_queue_gate.py — U4: explicit run queue gate (pending → bind → release → catalogued).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.pipeline import advance
from fanops.studio import actions


def _put_video(cfg, mocker, name="a.mp4", data=None):
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    (cfg.inbox / name).write_bytes(data if data is not None else b"V" + name.encode())
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 12.0))


def _seed_accounts(cfg, handles):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "x", "platforms": ["instagram"], "status": "active"} for h in handles]}))


def test_advance_holds_unbound_pending(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put_video(cfg, mocker)
    mocker.patch("fanops.produce.run_all")
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
    mocker.patch("fanops.studio.actions_run.kick_prepare")
    mocker.patch("fanops.produce.run_all")
    mocker.patch("fanops.transcribe._transcribe_toolchain_present", return_value=True)
    actions.release_batch(cfg, bid_a, confirmed=True)
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    assert led.sources[sids[0]].state is SourceState.catalogued
    assert led.sources[sids[1]].state is SourceState.pending


def test_release_all_held(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put_video(cfg, mocker, "a.mp4"); _put_video(cfg, mocker, "b.mp4")
    _seed_accounts(cfg, ["a", "b"])
    actions.catalogue_inbox(cfg)
    led = Ledger.load(cfg)
    sids = sorted(led.sources)
    actions.bind_queue(cfg, source_ids=[sids[0]], batch_name="A", target_accounts=["a"])
    actions.bind_queue(cfg, source_ids=[sids[1]], batch_name="B", target_accounts=["b"])
    mocker.patch("fanops.studio.actions_run.kick_prepare")
    actions.release_all_held(cfg, confirmed=True)
    led = Ledger.load(cfg)
    assert all(s.state is SourceState.catalogued for s in led.sources.values())


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
    mocker.patch("fanops.produce.run_all")
    mocker.patch("fanops.transcribe._transcribe_toolchain_present", return_value=True)
    advance(cfg, base_time="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    assert led.sources["legacy"].state is SourceState.catalogued and led.sources["legacy"].batch_id == "b-old"
