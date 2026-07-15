"""RC-9 (S11) — model validators run at CONSTRUCTION; the mutation APIs bypass them.

The R1 invariant (a terminal-positive Post — published/analyzed — carries a real public_url) is enforced by
Post's @model_validator, which runs at construction. `model_copy(update=...)` (every ledger state transition)
and plain setattr both bypass it, so a FUTURE path could build the ghost row Post(state=published,
public_url='') in memory without raising (RC-9).

Today this is LATENT — zero reachability (Cycle 4). Four manual call-site guards ensure a url before any
terminal transition (cli.cmd_resolve refuses `--status published` without `--url`; run._publish_one gates the
promotion on public_url; track only moves a post to `analyzed` after it was `published`). And it is BACKSTOPPED:
the ledger reconstructs every Post at load (`Post(**v)`, ledger.py:453), so a ghost row that ever reached disk
is REJECTED on the next load, never silently trusted.

This test locks BOTH enforced doors — the construction invariant and the load backstop. A full type-level fix
(validate_assignment on Post, or a save-time guard) is deliberately NOT taken here: it re-validates on every
mutation and would break the many fixtures that build terminal-state posts via the bypass (test_audit_trail,
test_compress, test_cli, ...) — disproportionate to a hazard with no production path. RC-9 was sequenced last
for exactly this reason; the mutation door remains the documented, accepted-latent residual.
"""
import pytest
from pydantic import ValidationError
from fanops.config import Config
from fanops.errors import ControlFileError
from fanops.ledger import Ledger, SCHEMA_VERSION
from fanops.ledger_sqlite import SqliteLedgerStore
from fanops.models import Post, PostState, Platform

_EMPTY_MAPS = {"sources": {}, "moments": {}, "clips": {}, "tag_log": {}, "variant_streaks": {},
               "stitch_plans": {}, "batches": {}, "renders": {}, "imported_media": {}}


def _post(**over) -> dict:
    return {"id": "p", "parent_id": "c", "account": "a", "account_id": "1",
            "platform": Platform.instagram, "caption": "c", **over}


@pytest.mark.parametrize("state", [PostState.published, PostState.analyzed])
def test_ctor_refuses_terminal_positive_without_url(state):
    # R1 at CONSTRUCTION: a terminal-positive state demands a real permalink; empty is refused.
    with pytest.raises(ValidationError):
        Post(**_post(state=state, public_url=""))
    assert Post(**_post(state=state, public_url="https://ig/reel/A/")).state is state   # a url -> fine


def test_load_rejects_a_persisted_ghost_row(tmp_path):
    # The BACKSTOP: even if a mutation bypass wrote a ghost row to disk, the ledger reconstructs every Post
    # at load (Post(**v)), so the row is REJECTED (ControlFileError) — never silently trusted as published.
    cfg = Config(root=tmp_path)
    ghost = Post(**_post(state=PostState.needs_reconcile)).model_dump()   # a valid row on disk...
    ghost["state"] = PostState.published.value; ghost["public_url"] = ""  # ...poisoned past the ctor (bypass)
    store = SqliteLedgerStore(cfg)
    with store.lock():
        store.write_raw({"schema_version": SCHEMA_VERSION, "posts": {"ghost": ghost}, **_EMPTY_MAPS})
    with pytest.raises(ControlFileError):
        Ledger.load(cfg)
