"""Test offline della conversione nel formato dell'editor Cookidoo (nessuna rete)."""
import json

from converter import (REVERSE_GLYPH, SOFT_GLYPH, Catalog, convert, parse_editor_html,
                       parse_seconds, parse_speed, parse_temp, snap_time, without_modes)

CAT = Catalog(minutes="min", seconds="sec", speed="vel.", varoma="Varoma",
              modes={"dough": "Impastare", "turbo": "Turbo", "warm_up": "Riscaldare",
                     "browning": "Rosolare", "steaming": "Cottura a vapore"},
              tm7_only={"browning", "steaming"}, source="test")

RISOTTO = {
    "title": "Risotto zucca e provola", "servings": 4, "time": 35,
    "ingredients": [
        {"qty": "40", "unit": "g", "name": "cipolla dorata"},
        {"qty": "30", "unit": "g", "name": "olio extravergine"},
        {"qty": "400", "unit": "g", "name": "zucca a cubetti"},
        {"qty": "320", "unit": "g", "name": "riso Carnaroli"},
        {"qty": "900", "unit": "g", "name": "acqua calda"},
        {"qty": "500", "unit": "g", "name": "farina 00"},
    ],
    "steps": [
        {"text": "Trita la cipolla.", "time": "5 sec", "speed": "5", "add": ["cipolla dorata"]},
        {"text": "Soffriggi.", "time": "3 min", "temp": "120°C", "speed": "1", "add": ["olio"]},
        {"text": "Rosola la zucca.", "time": "4 min", "temp": "160°C", "mode": "Rosolare", "add": ["zucca a cubetti"]},
        {"text": "Tosta il riso.", "time": "3 min", "temp": "120°C", "speed": "1", "reverse": True,
         "accessory": "Farfalla", "add": ["riso carnaroli"]},
        {"text": "Cuoci.", "time": "15 min 10 sec", "temp": "100", "speed": "Soft", "reverse": True, "add": ["acqua calda"]},
        {"text": "Impasta.", "speed": "Spiga", "time": "2 min", "add": ["farina 00"]},
        {"text": "Dai un colpo.", "speed": "Turbo", "time": "1 sec"},
        {"text": "Frulla col calore.", "time": "1 min", "temp": "90°C", "speed": "8"},
        {"text": "Cuoci a vapore.", "time": "20 min", "temp": "Varoma", "speed": "1"},
    ],
    "tips": ["Servi subito; caldo."],
}


def seg(text, pos):
    b = text.encode("utf-16-le")
    return b[pos["offset"] * 2:(pos["offset"] + pos["length"]) * 2].decode("utf-16-le")


def test_parsers():
    assert parse_seconds("1 min 30 sec") == 90
    assert parse_speed("vel. 0,5") == ("0.5", None)
    assert parse_speed("Spiga") == (None, "dough")
    assert parse_temp("99°C")[0] == "98"
    assert snap_time(913) == 900 and snap_time(45) == 45 and snap_time(126) == 130


def test_payload():
    conv = convert(RISOTTO, CAT)
    p = conv.payload
    ins = p["instructions"]
    texts = [s["text"] for s in ins]
    for s in ins:
        for a in s.get("annotations", []):
            assert seg(s["text"], a["position"]).strip(), a
            if a["type"] == "INGREDIENT":
                assert seg(s["text"], a["position"]) == a["data"]["description"]
                assert a["data"]["description"] in [i["text"] for i in p["ingredients"]]
    assert "Trita la cipolla 5 sec/vel. 5." in texts
    assert f"Cuoci 15 min/100°C/{REVERSE_GLYPH}/vel. {SOFT_GLYPH}." in texts
    assert "Impasta Impastare /2 min." in texts
    assert "Dai un colpo Turbo /1 sec." in texts
    assert "Cuoci a vapore 20 min/Varoma/vel. 1." in texts
    assert any(t.startswith("Rosola la zucca (") for t in texts)  # Rosolare = solo TM7 -> testo
    mode = [a for s in ins for a in s.get("annotations", []) if a["type"] == "MODE"]
    assert {m["name"] for m in mode} == {"dough", "turbo"}
    tostare = next(s for s in ins if s["text"].startswith("Tosta il riso"))
    assert seg(tostare["text"], tostare["annotations"][0]["position"]) == f"3 min/120°C/{REVERSE_GLYPH}/vel. 1"
    assert conv.weigh_steps == 6 and conv.guided_steps == 7, (conv.weigh_steps, conv.guided_steps)
    assert "；" in p["hints"]
    assert all("annotations" not in s or all(a["type"] != "MODE" for a in s["annotations"])
               for s in without_modes(p)["instructions"])
    print(json.dumps(ins[:4], ensure_ascii=True, indent=1))
    print("avvisi:", conv.warnings)


def test_builtin_catalog_has_no_modes():
    conv = convert(RISOTTO)  # catalogo non letto: niente modalità guidate, solo TTS
    assert not any(a["type"] == "MODE" for s in conv.payload["instructions"] for a in s.get("annotations", []))


def test_editor_html():
    html = ('<cr-tts-time minutes-translation="min" seconds-translation="sec">'
            '<input type="radio" value="0.5"><input type="radio" value="1"></cr-tts-time>'
            '<cr-tts-speed speed-translation="vel."></cr-tts-speed>'
            '<cr-tts-mode><span>Impastare</span><input type="radio" value="dough" id="m1">'
            '<span>Rosolare</span><input type="radio" value="browning" id="m2"><label for="m2" tm7-mode></label></cr-tts-mode>')
    c = parse_editor_html(html)
    assert c.modes == {"dough": "Impastare", "browning": "Rosolare"} and c.tm7_only == {"browning"}
    assert c.speed == "vel." and c.turbo_times == ("0.5", "1")


if __name__ == "__main__":
    test_parsers(); test_payload(); test_builtin_catalog_has_no_modes(); test_editor_html()
    print("OK")
