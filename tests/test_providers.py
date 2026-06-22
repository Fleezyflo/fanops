"""M1 — the provider registry. ONE home for 'who publishes a channel': each backend is a Provider entry
(metadata + lazy poster/uploader factories) and get_poster/get_media_uploader resolve through it. This
milestone is BYTE-IDENTICAL — the adapters return the exact same poster classes / uploader functions the
old hand-written dispatch did; only the wiring location moves. Adding a provider later (YouTube-direct) is a
new registry entry, not edits across the publish path."""
from pathlib import Path
from fanops.config import Config
from fanops.post import get_poster, get_media_uploader
from fanops.post.providers import PROVIDERS, get_provider, Provider


def test_registry_has_every_backend():
    assert set(PROVIDERS) == {"dryrun", "postiz", "zernio", "rest", "mcp"}
    assert all(isinstance(p, Provider) for p in PROVIDERS.values())


def test_provider_metadata():
    assert PROVIDERS["postiz"].kind == "hosted" and PROVIDERS["postiz"].creds_env == "POSTIZ_API_KEY"
    assert PROVIDERS["zernio"].kind == "hosted" and PROVIDERS["zernio"].creds_env == "ZERNIO_API_KEY"
    assert PROVIDERS["rest"].creds_env == "BLOTATO_API_KEY" and PROVIDERS["mcp"].creds_env == "BLOTATO_API_KEY"
    assert PROVIDERS["dryrun"].kind == "local" and PROVIDERS["dryrun"].creds_env == ""
    assert all(p.available for p in PROVIDERS.values())                # nothing stubbed yet (youtube lands in M5)


def test_get_poster_delegates_to_same_classes(tmp_path, monkeypatch):
    # posters construct against their creds (same as the old get_poster — PostizPoster needs POSTIZ_URL/key,
    # etc.); set them so construction succeeds and we can assert the CLASS the registry resolves.
    monkeypatch.setenv("POSTIZ_URL", "https://p.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk"); monkeypatch.setenv("BLOTATO_API_KEY", "bk")
    cfg = Config(root=tmp_path)
    from fanops.post.postiz import PostizPoster
    from fanops.post.zernio import ZernioPoster
    from fanops.post.blotato_rest import BlotatoRestPoster
    from fanops.post.blotato_mcp import BlotatoMcpPoster
    from fanops.post.dryrun import DryRunPoster
    assert isinstance(get_poster(cfg, "postiz"), PostizPoster)
    assert isinstance(get_poster(cfg, "zernio"), ZernioPoster)
    assert isinstance(get_poster(cfg, "rest"), BlotatoRestPoster)
    assert isinstance(get_poster(cfg, "mcp"), BlotatoMcpPoster)
    assert isinstance(get_poster(cfg, "dryrun"), DryRunPoster)


def test_get_poster_unknown_backend_falls_back_to_dryrun(tmp_path):
    # byte-identical: the OLD get_poster returned DryRunPoster for any unrecognized backend.
    from fanops.post.dryrun import DryRunPoster
    assert isinstance(get_poster(Config(root=tmp_path), "nonsense"), DryRunPoster)


def test_get_media_uploader_delegates(tmp_path):
    cfg = Config(root=tmp_path)
    from fanops.post.postiz import postiz_upload_media
    from fanops.post.zernio import zernio_upload_media
    from fanops.post.media import upload_media
    assert get_media_uploader(cfg, "postiz") is postiz_upload_media
    assert get_media_uploader(cfg, "zernio") is zernio_upload_media
    assert get_media_uploader(cfg, "rest") is upload_media          # blotato presign
    assert get_media_uploader(cfg, "mcp") is upload_media
    assert callable(get_media_uploader(cfg, "dryrun"))             # the file:// lambda


def test_get_media_uploader_unknown_falls_back_to_blotato(tmp_path):
    # byte-identical: the OLD get_media_uploader's else-branch was upload_media (Blotato), NOT dryrun.
    from fanops.post.media import upload_media
    assert get_media_uploader(Config(root=tmp_path), "nonsense") is upload_media


def test_dryrun_uploader_is_offline(tmp_path):
    up = get_media_uploader(Config(root=tmp_path), "dryrun")
    assert up(Config(root=tmp_path), Path("/x/y.mp4")).startswith("file://")   # no network


def test_provider_has_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    cfg = Config(root=tmp_path)
    assert PROVIDERS["postiz"].has_creds(cfg) is True
    assert PROVIDERS["zernio"].has_creds(cfg) is False
    assert PROVIDERS["dryrun"].has_creds(cfg) is False              # no creds_env -> never live


def test_get_provider_lookup_and_fallback(tmp_path):
    cfg = Config(root=tmp_path)
    assert get_provider(cfg, "zernio").name == "zernio"
    assert get_provider(cfg, "nonsense") is None                   # caller decides the fallback (poster vs uploader differ)
