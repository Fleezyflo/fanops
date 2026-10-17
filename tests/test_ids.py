# tests/test_ids.py
import subprocess, sys
from fanops.ids import make_id, child_id, surface_key

def test_make_id_deterministic():
    assert make_id("src", "/in/a.mov") == make_id("src", "/in/a.mov")
    assert make_id("src", "/in/a.mov").startswith("src_")

def test_make_id_differs():
    assert make_id("src", "a") != make_id("src", "b")

def test_child_id_is_content_addressed_not_positional():
    p = make_id("src", "x")
    # same content token -> same id; different content token -> different id
    a = child_id("moment", p, "14.00-21.00")
    b = child_id("moment", p, "30.00-37.00")
    assert a != b and a.startswith("moment_")
    assert a == child_id("moment", p, "14.00-21.00")

def test_surface_key_stable_and_distinct():
    assert surface_key("a", "instagram") == surface_key("a", "instagram")
    assert surface_key("a", "instagram") != surface_key("a", "tiktok")
    assert surface_key("a", "instagram") == "a|instagram"

def test_content_id_stable_across_processes():
    # The crux: a child id derived from a surface key must be identical when computed
    # in a brand-new interpreter process (hash() would fail this).
    here = "from fanops.ids import content_id; print(content_id('post','clip_1','@a|instagram'))"
    r1 = subprocess.run([sys.executable, "-c", here], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, "-c", here], capture_output=True, text=True)
    assert r1.stdout.strip() == r2.stdout.strip() != ""

def test_sha1_digests_unchanged_after_usedforsecurity_flag():
    # PKT-2 (MOL-108): four non-security sha1 seeds gained `usedforsecurity=False` for S324 clarity.
    # Call the production seed functions; hash their documented inputs independently so a formula
    # drift (not a copied digest literal) is what this pin catches.
    import hashlib
    from fanops.crosspost import _seed
    from fanops.ids import _hash
    from fanops.studio.views_common import account_color_hue
    from fanops.tagging import should_tag
    assert _hash("render", "x") == hashlib.sha1(b"render\x00x", usedforsecurity=False).hexdigest()[:12]
    assert _seed("acc", "ig", "2026-07-04", "clip_1") == int(
        hashlib.sha1(b"acc|ig|2026-07-04|clip_1", usedforsecurity=False).hexdigest()[:8], 16)
    tag_h = int(hashlib.sha1(b"clip_1|acc", usedforsecurity=False).hexdigest()[:8], 16)
    assert should_tag("clip_1", "acc", rate=0.0) is False
    assert should_tag("clip_1", "acc", rate=1.0) is True
    assert should_tag("clip_1", "acc", rate=(tag_h % 1000) / 1000.0 + 1e-9) is True
    assert account_color_hue("handle") == int(
        hashlib.sha1(b"handle", usedforsecurity=False).hexdigest()[:8], 16) % 360

def test_no_builtin_hash_in_source():
    # Guard: the builtin hash() must never be CALLED in ids.py (it is salted per process
    # — PEP 456 — and reintroduces the duplicate-post bug). We parse the AST and look for a
    # call to a bare name `hash`, so `_hash(...)`, `hashlib`, and the word "hash" in the
    # docstring are correctly ignored (a naive `"hash(" in src` would false-positive on them).
    import ast
    tree = ast.parse(open("src/fanops/ids.py").read())
    builtin_hash_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "hash"
    ]
    assert not builtin_hash_calls, "builtin hash() must never be called in ids.py"
