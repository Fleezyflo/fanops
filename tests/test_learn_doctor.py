# tests/test_learn_doctor.py
# F2 — read-only learning-loop field-shape doctor. Answers ONE question: does the LIVE Postiz
# analytics field shape carry the `reach` signal lift_score/M4 reach-attribution need? The verdict is
# per-key on `reach` (mapped from the live `reach` label) — NOT all of _W: `retention` is genuinely
# absent from the live label set (reported, never gated). `saves` now maps (the 2026-06-21 label fix).
# Tri-state: PASS / FAIL / NO-DATA, so 0 posts is never a vacuous PASS. Persisted so M4 can gate on it.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.learn_doctor import field_shape_report, cmd_learn_doctor


def _led_with_shipped(tmp_path, *, sub="s_A", state=PostState.published):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=state, submission_id=sub, public_url="dryrun://p1"))
    return cfg, led


def test_verdict_pass_when_reach_present(tmp_path):
    cfg, led = _led_with_shipped(tmp_path)
    rows = [{"postSubmissionId": "s_A", "metrics": {"reach": 5000, "likes": 10},
             "_raw_labels": ["Reach", "Likes"]}]
    rep = field_shape_report(led, cfg, list_posts=lambda w: rows)
    assert rep["verdict"] == "PASS"
    assert rep["reach_present"] is True
    assert rep["posts_sampled"] == 1
    assert "Reach" in rep["labels_seen"]


def test_verdict_fail_when_labels_present_but_no_reach(tmp_path):
    # rows carry real analytics (shares/likes) but NO reach-bearing label -> FAIL, not NO-DATA.
    cfg, led = _led_with_shipped(tmp_path)
    rows = [{"postSubmissionId": "s_A", "metrics": {"likes": 10, "shares": 2},
             "_raw_labels": ["Likes", "Shares", "saved"]}]
    rep = field_shape_report(led, cfg, list_posts=lambda w: rows)
    assert rep["verdict"] == "FAIL"
    assert rep["reach_present"] is False


def test_verdict_no_data_on_zero_posts(tmp_path):
    # No shipped posts -> empty fetch -> NO-DATA (NEVER a vacuous PASS that would let M4 attribute
    # against unvalidated labels).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    rep = field_shape_report(led, cfg, list_posts=lambda w: [])
    assert rep["verdict"] == "NO-DATA"
    assert rep["posts_sampled"] == 0


def test_verdict_no_data_when_rows_have_no_usable_analytics(tmp_path):
    # A row present but empty metrics+labels (Postiz analytics lag / isolated fetch failure) is NOT a
    # FAIL and NOT a PASS — it is NO-DATA (no signal to judge yet).
    cfg, led = _led_with_shipped(tmp_path)
    rows = [{"postSubmissionId": "s_A", "metrics": {}, "_raw_labels": []}]
    rep = field_shape_report(led, cfg, list_posts=lambda w: rows)
    assert rep["verdict"] == "NO-DATA"
    assert rep["posts_sampled"] == 1


def test_reach_pass_with_retention_unmapped(tmp_path):
    # THE C3 correction: the doctor gates ONLY on `reach`, not all of _W. `retention` is genuinely absent
    # from the live Postiz label set, so an all-_W verdict would permanently FAIL; a reach-bearing row
    # PASSes and surfaces retention as a known gap. (`saves` now maps — the 2026-06-21 label fix.)
    cfg, led = _led_with_shipped(tmp_path)
    rows = [{"postSubmissionId": "s_A", "metrics": {"reach": 9000}, "_raw_labels": ["Reach"]}]
    rep = field_shape_report(led, cfg, list_posts=lambda w: rows)
    assert rep["verdict"] == "PASS"
    assert rep["unmapped_weight_keys"] == ["retention"]   # only retention now; saves maps -> reported, never gated
    assert rep["gating_key"] == "reach"


def test_cmd_non_postiz_backend_exits_zero_with_guidance(tmp_path):
    # Default (dryrun) backend: no network, no crash, exit 0, clear guidance — never reaches the client.
    import json
    cfg = Config(root=tmp_path)
    rc = cmd_learn_doctor(cfg)
    assert rc == 0
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines()]
    hints = [r.get("hint", "") for r in recs if r["outcome"] == "missing_backend"]
    assert hints and "postiz" in hints[0].lower()
    assert "FANOPS_POSTER" not in hints[0]


def test_cmd_persists_verdict_for_m4_to_gate(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "sk-test-doctor")
    cfg, led = _led_with_shipped(tmp_path)
    led.save()                                          # cmd reloads the ledger from disk
    rows = [{"postSubmissionId": "s_A", "metrics": {"reach": 4242}, "_raw_labels": ["impressions"]}]
    rc = cmd_learn_doctor(cfg, list_posts=lambda w: rows)
    assert rc == 0
    assert cfg.learn_doctor_path.exists()
    persisted = json.loads(cfg.learn_doctor_path.read_text())
    assert persisted["verdict"] == "PASS"               # the persisted sidecar M4 gates on


def test_cmd_never_logs_the_postiz_key(tmp_path, monkeypatch):
    # Sentinel discipline (mirror PostizMetricsClient): the key value must never reach stdout.
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "sk-SECRET-VALUE-zzz")
    cfg, led = _led_with_shipped(tmp_path)
    led.save()
    rows = [{"postSubmissionId": "s_A", "metrics": {"reach": 1}, "_raw_labels": ["impressions"]}]
    cmd_learn_doctor(cfg, list_posts=lambda w: rows)
    blob = cfg.log_path.read_text()
    assert "sk-SECRET-VALUE-zzz" not in blob


def test_report_is_read_only_no_ledger_mutation(tmp_path):
    # Genuinely read-only: pulling analytics must NOT advance the sampled post to `analyzed` or write
    # metrics (that is record_metrics' job, never the doctor's).
    cfg, led = _led_with_shipped(tmp_path)
    rows = [{"postSubmissionId": "s_A", "metrics": {"reach": 5000}, "_raw_labels": ["impressions"]}]
    field_shape_report(led, cfg, list_posts=lambda w: rows)
    assert led.posts["p1"].state is PostState.published       # NOT analyzed
    assert led.posts["p1"].metrics == {}                       # no metrics written


def test_cmd_propagates_a_real_code_bug(tmp_path, monkeypatch):
    # The catch-all must NOT mask genuine bugs as "analytics fetch failed". A KeyError/TypeError from
    # inside the report logic is a code defect and must surface as a traceback, not a silent exit 0.
    import pytest
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "sk-x")
    cfg, led = _led_with_shipped(tmp_path); led.save()
    def boom(w): raise KeyError("a real bug, not a network failure")
    with pytest.raises(KeyError):
        cmd_learn_doctor(cfg, list_posts=boom)


def test_cmd_swallows_a_transport_failure(tmp_path, monkeypatch):
    # A documented transport failure (the Postiz client raises RuntimeError on a 5xx/non-JSON body, or
    # requests raises) is transient — swallow it, print retry guidance, exit 0 (never crash a pipeline).
    import json
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "sk-x")
    cfg, led = _led_with_shipped(tmp_path); led.save()
    def neterr(w): raise RuntimeError("postiz analytics 503: upstream down")
    rc = cmd_learn_doctor(cfg, list_posts=neterr)
    assert rc == 0
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines()]
    assert any(r["outcome"] == "fetch_failed" for r in recs)


# ---- WS-R1 XC-3: learn_doctor.json written atomically (no torn sidecar re-freezes M4) -----------
def test_persist_verdict_is_atomic_no_torn_file_on_crash(tmp_path, monkeypatch):
    # XC-3: a crash mid-write leaves the PRIOR valid learn_doctor.json, never a half-file.
    import pytest
    from fanops import learn_doctor, controlio
    cfg = Config(root=tmp_path)
    learn_doctor._persist_verdict(cfg, {"verdict": "PASS", "posts_sampled": 1})       # valid file
    good = json.loads(cfg.learn_doctor_path.read_text())
    def boom(src, dst): raise OSError("simulated crash during replace")
    monkeypatch.setattr(controlio.os, "replace", boom)
    with pytest.raises(OSError):
        learn_doctor._persist_verdict(cfg, {"verdict": "FAIL"})
    assert json.loads(cfg.learn_doctor_path.read_text()) == good     # prior verdict intact
    assert not list(cfg.learn_doctor_path.parent.glob(cfg.learn_doctor_path.name + ".*tmp"))
