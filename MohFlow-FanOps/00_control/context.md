# context.md — Moh Flow Fan-Ops creative brief

> **This file is functional, not decorative.** Its entire contents are read by
> `_guidance(cfg)` (in `src/fanops/moments.py` and `src/fanops/caption.py`) and
> injected verbatim as the `guidance` field of every moment-decision request and
> every caption request sent to the agent. Treat the moment- and caption-guidance
> sections below as **direct instructions to the model that picks clips and writes
> captions.** Editing them changes what the system produces on the next `advance`.
> Keep the prose actionable; do not bury the instructions in commentary.

---

## Who this is for

The accounts in `accounts.json` are independent fan / enthusiast accounts that
amplify **Moh Flow** — a bilingual (English / Arabic) rapper. The content posted is
Moh Flow's own catalogue, clipped into short vertical moments and cross-posted across
those accounts (Instagram, TikTok). These read as genuine fans sharing what they love,
not as a label feed.

## Voice

The through-line is **bravado** — confident, unbothered, the clip speaks for itself.
Never beg, never explain, never apologise for the artist.

Per-account persona varies on top of that through-line — read each account's `persona`
field in `accounts.json` and let it color tone and edit feel (e.g. one account runs
"fast cinematic edits, hype energy", another "raw studio + lyric-forward"). Same
bravado spine, different voice per account so the network does not read as one hand.

---

## Moment guidance

> Injected as `guidance` into every **moment-decision** request alongside the
> source's `duration`, `transcript` (word-/segment-adjacent timing), `signal_peaks`
> (scene-cut and silence-onset signals), and `language`. Return a `MomentDecision`
> with a list of `picks`, each `{start, end, reason, transcript_excerpt, signal_score}`.

Pick the moments that would make someone stop scrolling. Prize, in rough order:

- **The bar / the punchline** — the cleverest, hardest, most quotable line.
- **The line right before the beat drop** — the tension beat that pays off on the drop.
- **A quotable phrase in EITHER language** — a sharp **English OR Arabic** line is
  equally valid. Do not privilege English; an Arabic hook for an Arabic source is the
  strong pick. Match the source's `language`.
- **A hard visual cut** — a moment where a `signal_peaks` scene cut lands on a strong
  line or beat, so the clip has a clean visual hit, not just audio.

Rules that the code enforces — respect them so your picks survive validation:

- **Use the provided `language` and the word-adjacent `transcript` timing to place
  cuts**, but **widen every pick by about ±0.3s** at each edge. Whisper timestamps are
  segment-level, not frame-accurate; a too-tight window clips the first or last
  syllable. Err on the side of catching the whole line.
- **Return as many genuinely-strong moments as actually exist — there is NO quota and
  there are no tiers.** Three great moments beat eight padded ones. If only one moment
  is worth posting, return one. Do not invent filler to hit a number.
- **Every pick needs a real `reason`** (one line: why this stops the scroll) and a
  `transcript_excerpt` (the line itself, so captioning downstream has the words).
- **Bounds must be valid:** `start < end`, `start >= 0`, the window at least ~0.5s long,
  and `end` within the source `duration`. An out-of-bounds or zero-length pick is
  rejected; a decision where *every* pick is invalid quarantines the source instead of
  reconciling, so getting the bounds right matters.

---

## Caption guidance

> Injected as `guidance` into every **caption** request alongside the clip's
> `transcript_excerpt`, the source `language`, and the list of `surfaces` to write for
> (each is an `account/platform` string, e.g. `@somefan/instagram`). Return a
> `CaptionSet` whose `items` answer **every** surface, each
> `{surface, caption, hashtags}`.

Write **a different caption for every surface** — do not reuse one string across
surfaces. Distinct wording per surface is both platform fit and opsec (identical
captions across "independent" accounts is a fingerprint). Match the source `language`:
**write Arabic captions for Arabic sources, English for English sources.**

Per platform:

- **Instagram (`.../instagram`):** Lead with a **hook in the first ~125 characters** —
  that is what shows before the "more" fold, so the bar or the payoff has to be inside
  it. Include a **save / share call to action** (the algorithm rewards saves and shares
  far more than likes — this is the metric we optimize). Use **3–10 hashtags**.
- **TikTok (`.../tiktok`):** The **first line should extend the on-screen hook** rather
  than repeat it — pick up where the burned-in text leaves off. Keep it
  **conversational**, like a fan talking, not ad copy. Use **3–5 hashtags**.

Hard guardrails — these are **screened in both English and Arabic** and a match puts the
clip on a **brand-risk HOLD** (it will not post until a human reviews it). Avoid:

- **No begging** ("please stream", "pls", 🥺, "sorry", and the Arabic equivalents
  أرجوكم / من فضلك / رجاء / اسمعوا / آسف / بليز).
- **No "official" / "label" framing** ("official drop/release", "from the label", and
  the Arabic equivalents) — these accounts are fans, not the label.
- **No "link in bio"** in either language ("link in bio" / لينك في البايو / الرابط في
  البايو). Let the clip earn the follow; routing traffic like a brand breaks the cover.

A surface left unanswered does **not** silently default — a **missing surface holds the
whole clip** (FIX F74). So answer all of them, in the right language, distinct per
surface, inside the guardrails.
