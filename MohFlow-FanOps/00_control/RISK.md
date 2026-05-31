# RISK.md — recorded acknowledgment (locked product decision)

This file exists so the central risk of this system is **explicit and on the record**,
not buried in code comments. It states what the system does, the risk that creates, and
the operator's decision to accept that risk as a product requirement for this build.

---

## What the system does

The system operates **multiple artist-operated accounts** that **cross-post one
artist's content** with **non-synchronized timing and per-account personas**. The
accounts present as independent fans; the content and the operation are the artist's.

## The risk this creates

- **Platform policy.** The named platforms — **Instagram / Meta, TikTok, YouTube, X** —
  prohibit **coordinated / inauthentic multi-account amplification**. The realistic
  enforcement mechanism is **correlated, network-level takedown** (platforms detect the
  pattern across accounts, not one post in isolation), and that **linkage can reach a
  primary account** associated with the network.
- **Disclosure regimes.** **FTC (US), ASA (UK), and EU UCPD** treat **undisclosed
  artist-operated "fan" accounts** as a **material connection that requires
  disclosure**. Operating them as if independent, without disclosing the connection, is
  the exposure these regimes are written to catch.

These are real, understood risks. Nothing in this build claims to evade detection or to
make the operation policy-compliant.

---

## Operator decision

The **multi-account opsec model is ACCEPTED** as a product requirement for this build.
This file records that acceptance so the decision is **deliberate and explicit**, owned
by the operator, rather than an accident of the implementation.

Specifically acknowledged as **intentional design choices, not bugs**:

- **Non-synchronized timing** (the staggered, spread `scheduled_time` per surface) is a
  deliberate **opsec** choice — lockstep posting is itself a detectable fingerprint.
- **Per-account personas** (the `persona` field driving distinct voice/edit feel) are
  deliberate, for the same reason.
- **The subtle, non-synchronized artist `@mention`** (a buried `@mohflow` on a minority
  of posts, rate-limited so no two accounts tag close together) is a deliberate opsec
  choice in how attribution surfaces — not a coverage gap.

## Guardrails that remain the operator's responsibility

Acceptance of the model does **not** remove these obligations:

- **PII exclusion is filename-only — necessary, but NOT sufficient.** Ingest skips files
  whose **names** match PII/legal/financial patterns (passport, contract, invoice, tax,
  bank, national/Emirates ID, …). A private file that is **misnamed slips through.**
  Therefore **a human reviews held / odd clips** before anything posts. The filename
  filter is a first pass, not a guarantee.
- **Music licensing.** The music in clips is the **artist's own catalogue**. For **any
  third-party audio**, confirm licensing before it goes out — the automation does not
  clear rights.
- **Brand-risk HOLDs must be reviewed by a human** (see RUNTIME.md): captions that trip
  the begging / "official" / "link in bio" guardrails (EN or AR) are held and must be
  cleared by a person, not auto-released.

---

*Recorded as the standing acknowledgment for the Moh Flow Fan-Ops build. If the product
decision changes, update this file rather than letting practice and the record diverge.*
