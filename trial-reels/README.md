# trial-reels

Isolated trial lane for a constrained Arabic/English hook writer.

## What it does

`trial-reels/lib/desk.py` turns an attested transcript into on-screen hook cards without inventing facts:

- contiguous source-order spans only (no reorder, no permutations)
- every card cites a transcript timestamp in spoken order
- second pass rejects any 3+ letter word not present in the transcript
- weak function-word spans like `It's a` are rejected
- Whisper fragments are stitched into sentences; leftover slices are rejected
- credit-only lines such as `ترجمة نانسي قنقر` block
- short transcripts return as many distinct hooks as exist — never five fake anagrams

Hook styles (unchanged list): `result_first`, `mid_action`, `direct_you`, `bold_claim`, `cold_proof`.

## Run tests

```bash
python -m pytest trial-reels/tests/test_desk.py -q
```

## API

```python
from lib.desk import write

write({"language": "ar", "lines": [{"start": 12.4, "text": "لك كفاية عزبتني"}]})
```

Returns `{mode, language, source_line, reason, cites, cards, ear}`.
