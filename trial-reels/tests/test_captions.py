from lib.captions import (
    DEFAULT_ALIGNMENT,
    DEFAULT_FONT,
    DEFAULT_FONTSIZE,
    DEFAULT_MARGIN_V,
    write_ass,
)


def test_write_ass_hook_style_contract():
  ass = write_ass(
      [{"start": 0.0, "end": 2.5, "text": "عزبتني"}],
      DEFAULT_FONT,
  )
  assert "Noto Naskh Arabic" in ass
  assert f"Style: HOOK,{DEFAULT_FONT},{DEFAULT_FONTSIZE}" in ass
  assert f",{DEFAULT_ALIGNMENT}," in ass.split("Style: HOOK,")[1]
  assert f",{DEFAULT_MARGIN_V},1" in ass.split("Style: HOOK,")[1]
  assert "Dialogue:" in ass
  assert "عزبتني" in ass
  assert "{\\fad(0," in ass


def test_write_ass_empty_events_returns_empty():
  assert write_ass([], DEFAULT_FONT) == ""
  assert write_ass([{"start": 0, "end": 1, "text": "  "}], DEFAULT_FONT) == ""


def test_write_ass_escapes_ass_specials():
  ass = write_ass([{"start": 0, "end": 1, "text": "a{b}\nc"}], DEFAULT_FONT)
  assert "a{b}" not in ass
  assert "ab\\Nc" in ass
