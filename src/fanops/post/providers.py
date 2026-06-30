"""Provider registry — the SINGLE home for 'who publishes a channel'. Each backend is a `Provider`
entry: metadata (name, kind, creds_env, available) + lazy factories for its poster and media-uploader.
`get_poster`/`get_media_uploader` (post/__init__.py) resolve through here, so adding a provider later
(YouTube-direct in M5) is a NEW ENTRY here — not edits scattered across the publish path.

M1 is BYTE-IDENTICAL: these factories return the exact poster classes / uploader functions the old
hand-written dispatch returned, and the lazy imports are preserved (building the registry imports no
optional dep). `kind`: 'hosted' = the scheduler holds the social OAuth, FanOps sends a key + remote id
(Postiz/Zernio/Blotato); 'local' = dryrun; 'direct' (future) = FanOps holds the OAuth + does the native
upload (YouTube). `available=False` (future) = registered-but-not-built."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from fanops.config import Config


# ── lazy factories (import the optional dep only when the provider is actually used) ──────────────────
def _postiz_poster(cfg: Config): from fanops.post.postiz import PostizPoster; return PostizPoster(cfg)
def _zernio_poster(cfg: Config): from fanops.post.zernio import ZernioPoster; return ZernioPoster(cfg)
def _rest_poster(cfg: Config): from fanops.post.blotato_rest import BlotatoRestPoster; return BlotatoRestPoster(cfg)
def _mcp_poster(cfg: Config): from fanops.post.blotato_mcp import BlotatoMcpPoster; return BlotatoMcpPoster(cfg)
def _dryrun_poster(cfg: Config): from fanops.post.dryrun import DryRunPoster; return DryRunPoster(cfg)

def _postiz_uploader(cfg: Config): from fanops.post.postiz import postiz_upload_media; return postiz_upload_media
def _zernio_uploader(cfg: Config): from fanops.post.zernio import zernio_upload_media; return zernio_upload_media
def _blotato_uploader(cfg: Config): from fanops.post.media import upload_media; return upload_media
def _dryrun_uploader(cfg: Config):
    from fanops.post.media import dryrun_media_url
    return lambda c, p, **_kw: dryrun_media_url(p)          # file:// — **kw absorbs account_id from ensure_*


@dataclass(frozen=True)
class Provider:
    name: str
    kind: str                          # 'hosted' | 'local' | 'direct' (future)
    creds_env: str                     # the credential env var ('' = none, e.g. dryrun)
    available: bool                    # False = registered but not built (M5 youtube stub)
    make_poster: Callable[[Config], object]                           # cfg -> Poster (has .publish)
    make_uploader: Callable[[Config], Callable[[Config, Path], str]]  # cfg -> (cfg, Path) -> hosted URL

    def has_creds(self, cfg: Config) -> bool:
        # live-capable == this provider has its credential. dryrun (no creds_env) is never live. Delegates
        # to the one cfg home so the postiz/zernio/blotato key checks stay single-sourced.
        return bool(self.creds_env) and cfg.backend_has_creds(self.name)


PROVIDERS: dict[str, Provider] = {
    "postiz": Provider("postiz", "hosted", "POSTIZ_API_KEY", True, _postiz_poster, _postiz_uploader),
    "zernio": Provider("zernio", "hosted", "ZERNIO_API_KEY", True, _zernio_poster, _zernio_uploader),
    "rest":   Provider("rest", "hosted", "BLOTATO_API_KEY", True, _rest_poster, _blotato_uploader),
    "mcp":    Provider("mcp", "hosted", "BLOTATO_API_KEY", True, _mcp_poster, _blotato_uploader),
    "dryrun": Provider("dryrun", "local", "", True, _dryrun_poster, _dryrun_uploader),
}


def get_provider(cfg: Config, name: str) -> Optional[Provider]:
    """The Provider for `name`, or None when unrecognized — the CALLER picks the fallback because the old
    poster/uploader fallbacks DIFFER (an unknown backend posted via DryRun but uploaded via Blotato). Keeping
    that exact asymmetry is what makes M1 byte-identical."""
    return PROVIDERS.get(name)
