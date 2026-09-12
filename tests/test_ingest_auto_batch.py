# tests/test_ingest_auto_batch.py — pin the contract: every catalogued Source carries a real batch_id.
# Root fix at the chokepoint: ingest_drops auto-resolves/mints a daily drop-batch when the caller did not
# pass one, so a Source.batch_id=None is unconstructable from any path (Studio, CLI, daemon, third-party).
import datetime as _dt
import pytest
from fanops.config import Config
from fanops.ingest import ingest_drops
from fanops.ledger import Ledger


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    monkeypatch.setenv("FANOPS_QUEUE_GATE", "0")


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc; self.stdout = stdout; self.stderr = stderr


def _put_video(p, mocker, body=b"V"):
    """Land a fake video file + stub ffprobe at the OS edge so ingest catalogues it."""
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(body)
    def run(cmd, **kw):
        joined = " ".join(str(c) for c in cmd)
        if "codec_type" in joined:
            return _Proc(stdout="video\n")
        return _Proc(stdout="1920\n1080\n12.0\n")
    mocker.patch("fanops.media_probe.subprocess.run", side_effect=run)


def test_unbatched_ingest_mints_drop_batch_and_stamps_source(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put_video(cfg.inbox / "a.mp4", mocker, b"A")
    led, counts = ingest_drops(Ledger.load(cfg), cfg)
    assert counts.added == 1
    src = next(iter(led.sources.values()))
    assert src.batch_id is not None, "unbatched ingest left Source.batch_id None — root contract violated"
    b = led.get_batch(src.batch_id)
    assert b is not None and b.name.startswith("drop-")
    assert b.target_accounts == []


def test_unbatched_ingest_same_day_reuses_drop_batch(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put_video(cfg.inbox / "a.mp4", mocker, b"A")
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    _put_video(cfg.inbox / "b.mp4", mocker, b"B")
    led, _ = ingest_drops(led, cfg)
    bids = {s.batch_id for s in led.sources.values()}
    assert len(led.sources) == 2
    assert len(bids) == 1, f"second-pass ingest minted a new batch instead of reusing the day's drop-batch: {bids}"
    assert len(led.batches) == 1


def test_caller_supplied_batch_is_honoured(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put_video(cfg.inbox / "a.mp4", mocker, b"A")
    led = Ledger.load(cfg)
    from fanops.batches import create_batch
    b = create_batch(led, name="Launch week", target_accounts=["markmakmouly"],
                     now_iso="2026-06-28T00:00:00.000001Z")
    led, _ = ingest_drops(led, cfg, batch_id=b.id)
    src = next(iter(led.sources.values()))
    assert src.batch_id == b.id


def test_drop_batch_date_is_resolver_clock_not_fixed(tmp_path, mocker):
    cfg = Config(root=tmp_path); _put_video(cfg.inbox / "a.mp4", mocker, b"A")
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    src = next(iter(led.sources.values()))
    b = led.get_batch(src.batch_id)
    day = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    assert b.name == f"drop-{day}"


def test_empty_inbox_does_not_mint_a_batch(tmp_path):
    cfg = Config(root=tmp_path); cfg.inbox.mkdir(parents=True, exist_ok=True)
    led, counts = ingest_drops(Ledger.load(cfg), cfg)
    assert counts.added == 0 and len(led.batches) == 0
