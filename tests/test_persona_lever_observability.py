# tests/test_persona_lever_observability.py — M4: every produced output fragment traces to its lever, and a
# developer/operator-facing MANIFEST renders each lever's current value + what it produces + a health flag —
# all DERIVED from the M1 registry + the pipeline's own resolvers (compose_breakdown), so the operator view,
# the manifest, and the live output cannot disagree. The no-drift assertion is the standing instrument: mutate
# a lever and the manifest moves with it; a hand-maintained copy would red.
from fanops.config import Config
from fanops.personas import Persona, compose_breakdown, manifest
import fanops.persona_levers as pl


# ---- Task 1: complete fragment provenance — caption + cut now carry their lever, not just casting/hook ----
def test_caption_dimension_carries_a_voice_fragment(tmp_path):
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", voice="a devoted fan"))
    srcs = {f["source"] for f in d["caption"]["fragments"]}
    assert "voice" in srcs                                  # the caption text traces to the voice lever
    assert all(f["text"] in d["caption"]["text"] for f in d["caption"]["fragments"])   # parity with the compiler

def test_caption_fragments_empty_when_no_voice(tmp_path):
    cfg = Config(root=tmp_path)
    d = compose_breakdown(cfg, Persona(id="p", content_focus=["punchlines"]))
    assert d["caption"]["fragments"] == []                 # no voice -> no caption fragment (and text is empty)

def test_cut_dimension_names_the_lever_that_produced_it(tmp_path):
    cfg = Config(root=tmp_path)
    # content_focus DERIVES the length, energy DERIVES the framing — the cut fragments name those levers.
    d = compose_breakdown(cfg, Persona(id="p", content_focus=["storytelling"], energy="low"))
    srcs = {f["source"] for f in d["cut"]["fragments"]}
    assert "content_focus" in srcs and "energy" in srcs    # length<-content_focus, framing<-energy
    d2 = compose_breakdown(cfg, Persona(id="q", voice="v"))
    assert d2["cut"]["fragments"] == []                    # global cut -> no per-lever fragment

def test_no_produced_fragment_is_sourceless(tmp_path):
    cfg = Config(root=tmp_path)
    p = Persona(id="p", voice="v", content_focus=["punchlines"], energy="high", hook_angle="curiosity",
                hashtag_corpus=["#a"])
    d = compose_breakdown(cfg, p)
    for dim in ("casting", "hook", "caption", "cut"):
        for f in d[dim]["fragments"]:
            assert f.get("source"), f"{dim} has a sourceless fragment: {f}"


# ---- registry channel map: channels()/owner_of() expose the lever<->channel map from the single source ----
def test_channels_and_owner_of_are_consistent():
    chans = pl.channels()
    assert chans == pl.all_channels()
    for ch in chans:
        owner = pl.owner_of(ch)
        assert owner in pl.editable_fields() and ch in pl.channels_of(owner)   # round-trips
    assert pl.owner_of("nonexistent-channel") is None


# ---- Task 2: the manifest — every editable lever, derived from the resolvers (no-drift) ----
def test_manifest_covers_every_editable_lever(tmp_path):
    cfg = Config(root=tmp_path)
    m = manifest(cfg, Persona(id="p", voice="v", content_focus=["punchlines"], energy="high",
                              hook_angle="curiosity", hashtag_corpus=["#a"]))
    keys = {row["key"] for row in m}
    assert keys == set(pl.editable_fields())               # one manifest row per editable lever, no orphans
    for row in m:
        assert row["channels"] == list(pl.channels_of(row["key"]))
        assert row["health"] == "ok"                       # post-M3 every lever is coherent

def test_manifest_is_derived_no_drift(tmp_path):
    # the manifest's produced values EQUAL compose_breakdown's (same resolver) — a hand-copy would drift.
    cfg = Config(root=tmp_path)
    p = Persona(id="p", voice="v", content_focus=["storytelling"], energy="low", hook_angle="fomo",
                hashtag_corpus=["#myscene"])
    d = compose_breakdown(cfg, p)
    m = {row["key"]: row for row in manifest(cfg, p)}
    assert m["hashtag_corpus"]["produces"] == d["tags"]["lead"]        # hashtags == the breakdown's lead tags
    assert d["cut"]["band"] in m["content_focus"]["produces"]          # the derived length band
    assert m["hook_angle"]["produces"] == d["hook"]["text"]            # the compiled hook directive

def test_manifest_moves_when_a_lever_changes(tmp_path):
    # the standing instrument: change a lever, the manifest's produced value moves with it (proves derivation).
    cfg = Config(root=tmp_path)
    base = Persona(id="p", voice="v", content_focus=["punchlines"])    # short
    longer = base.model_copy(update={"content_focus": ["storytelling"]})  # long
    m0 = {r["key"]: r for r in manifest(cfg, base)}
    m1 = {r["key"]: r for r in manifest(cfg, longer)}
    assert m0["content_focus"]["produces"] != m1["content_focus"]["produces"]   # the cut band moved


# ---- Task 4: the health flag exactly tracks coherence (empty incoherent set post-M3) ----
def test_health_flags_no_incoherent_lever(tmp_path):
    cfg = Config(root=tmp_path)
    m = manifest(cfg, Persona(id="p", voice="v", content_focus=["punchlines"]))
    assert all(row["health"] == "ok" for row in m)          # no ⚠ — coherence holds


# ---- the drawer surfaces the provenance + health (smoke) ----
def test_drawer_renders_cut_and_caption_provenance(tmp_path):
    from fanops.studio.app import create_app
    from fanops.personas import add_persona
    cfg = Config(root=tmp_path)
    add_persona(cfg, name="P", voice="a devoted fan", content_focus=["storytelling"], energy="low")
    app = create_app(cfg); app.config.update(TESTING=True)
    html = app.test_client().post("/personas/compose", data={
        "voice": "a devoted fan", "content_focus": "storytelling", "energy": "low"}).get_data(as_text=True)
    assert "content_focus" in html and "energy" in html     # the cut provenance names the levers
