# tests/test_ingest_auto_batch.py — pin the contract: every catalogued Source carries a real batch_id.
# Root fix at the chokepoint: ingest_drops auto-resolves/mints a daily drop-batch when the caller did not
# pass one, so a Source.batch_id=None is unconstructable from any path (Studio, CLI, daemon, third-party).
import datetime as _dt
from fanops.config import Config
from fanops.ingest import ingest_drops
from fanops.ledger import Ledger


def _put_video(p, mocker):
    """Land a fake video file + stub the ffprobe gates so ingest catalogues it."""
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"V")
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1920, 1080, 12.0))


def test_unbatched_ingest_mints_drop_batch_and_stamps_source(tmp_path, mocker):
    # The pre-fix bug: ingest_drops(led, cfg) — no batch_id — left every Source.batch_id None, and every
    # downstream Post inherited that None and rendered under Studio Review's "Ungrouped" group. The fix:
    # the SAME call now mints a deterministic per-day drop-batch and stamps it onto the new Source.
    cfg = Config(root=tmp_path); _put_video(cfg.inbox / "a.mp4", mocker)
    led, counts = ingest_drops(Ledger.load(cfg), cfg)
    assert counts.added == 1
    src = next(iter(led.sources.values()))
    assert src.batch_id is not None, "unbatched ingest left Source.batch_id None — root contract violated"
    b = led.get_batch(src.batch_id)
    assert b is not None and b.name.startswith("drop-")
    assert b.target_accounts == []        # ALL-sentinel: byte-identical fan-out to today's behaviour


def test_unbatched_ingest_same_day_reuses_drop_batch(tmp_path, mocker):
    # A second pass on the same day must REUSE the day's drop-batch (idempotent on date) — otherwise
    # every pass would spawn a new batch and the Review grouper would shatter the day into many groups.
    cfg = Config(root=tmp_path); _put_video(cfg.inbox / "a.mp4", mocker)
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    _put_video(cfg.inbox / "b.mp4", mocker)
    led, _ = ingest_drops(led, cfg)
    bids = {s.batch_id for s in led.sources.values()}
    assert len(bids) == 1, f"second-pass ingest minted a new batch instead of reusing the day's drop-batch: {bids}"
    assert len(led.batches) == 1


def test_caller_supplied_batch_is_honoured(tmp_path, mocker):
    # The Studio "Add video" form (named-batch path) still wins: an explicit batch_id is passed through
    # verbatim — the auto-resolver only runs when the caller passed None.
    cfg = Config(root=tmp_path); _put_video(cfg.inbox / "a.mp4", mocker)
    led = Ledger.load(cfg)
    from fanops.batches import create_batch
    b = create_batch(led, name="Launch week", target_accounts=["markmakmouly"],
                     now_iso="2026-06-28T00:00:00.000001Z")
    led, _ = ingest_drops(led, cfg, batch_id=b.id)
    src = next(iter(led.sources.values()))
    assert src.batch_id == b.id


def test_drop_batch_date_is_resolver_clock_not_fixed(tmp_path, mocker):
    # Pin "deterministic per-day": the drop-batch name is `drop-{YYYY-MM-DD}` derived from the resolver's
    # clock. We stub the resolver clock to verify the date appears in the batch name verbatim.
    cfg = Config(root=tmp_path); _put_video(cfg.inbox / "a.mp4", mocker)
    fixed = _dt.datetime(2026, 6, 28, 12, 0, 0, tzinfo=_dt.timezone.utc)
    mocker.patch("fanops.batches._resolver_now_utc", return_value=fixed)
    led, _ = ingest_drops(Ledger.load(cfg), cfg)
    src = next(iter(led.sources.values()))
    b = led.get_batch(src.batch_id)
    assert b.name == "drop-2026-06-28"


def test_empty_inbox_does_not_mint_a_batch(tmp_path):
    # No new files → no batch needed. Don't litter the ledger with empty daily batches just because the
    # daemon ticked — the resolver fires inside the per-file path, after the inbox-walk has found work.
    cfg = Config(root=tmp_path); cfg.inbox.mkdir(parents=True, exist_ok=True)
    led, counts = ingest_drops(Ledger.load(cfg), cfg)
    assert counts.added == 0 and len(led.batches) == 0
