"""Test offline: conversione + costruzione del payload Cookidoo (nessuna rete)."""
import json

from cookidoo_api import Cookidoo

from converter import convert, parse_seconds, parse_speed, parse_temp, strip_modes

RISOTTO = {
    "title": "Risotto zucca e provola", "servings": 4, "time": 35,
    "ingredients": [
        {"qty": "40", "unit": "g", "name": "cipolla dorata"},
        {"qty": "30", "unit": "g", "name": "olio extravergine"},
        {"qty": "400", "unit": "g", "name": "zucca a cubetti"},
        {"qty": "320", "unit": "g", "name": "riso Carnaroli"},
        {"qty": "60", "unit": "g", "name": "vino bianco"},
        {"qty": "900", "unit": "g", "name": "acqua calda"},
        {"qty": "120", "unit": "g", "name": "provola affumicata a dadini"},
    ],
    "steps": [
        {"text": "Trita la cipolla.", "time": "5 sec", "speed": "5", "add": ["cipolla dorata"]},
        {"text": "Soffriggi.", "time": "3 min", "temp": "120°C", "speed": "1", "add": ["olio"]},
        {"text": "Rosola la zucca.", "time": "4 min", "temp": "160°C", "mode": "Rosolare", "add": ["zucca a cubetti"]},
        {"text": "Tosta il riso.", "time": "3 min", "temp": "120°C", "speed": "1", "reverse": True,
         "accessory": "Farfalla", "add": ["riso carnaroli"]},
        {"text": "Cuoci.", "time": "15 min", "temp": "100", "speed": "Soft", "reverse": True,
         "add": ["vino bianco", "acqua calda"]},
        {"text": "Impasta.", "speed": "Spiga", "time": "2 min"},
        {"text": "Frulla col calore.", "time": "1 min", "temp": "90°C", "speed": "8"},
        {"text": "Riposa 2 minuti.", "time": "", "speed": ""},
        {"text": "Cottura lenta.", "mode": "Cottura lenta", "time": "4 h", "temp": "98°C", "speed": "Soft"},
    ],
    "tips": ["Servi subito."],
}


def test_parsers():
    assert parse_seconds("1 min 30 sec") == 90
    assert parse_seconds("5 sec") == 5
    assert parse_seconds("2 ore") == 7200
    assert parse_speed("vel. 0,5") == ("0.5", None)
    assert parse_speed("Spiga") == (None, "DOUGH")
    assert parse_temp("Varoma") == ("varoma", None)
    assert parse_temp("99°C")[0] == "98"


def test_payload():
    conv = convert(RISOTTO)
    payload = Cookidoo._build_custom_recipe_payload(
        name=conv.recipe.name, ingredients=conv.recipe.ingredients, steps=conv.recipe.instructions,
        servings=conv.recipe.serving_size, active_time=conv.recipe.active_time,
        total_time=conv.recipe.total_time, hints=conv.recipe.hints, machine_types=conv.recipe.tools,
        unit_text=conv.recipe.unit_text, image=None, image_owned_by_user=False,
        work_status="PRIVATE", requires_annotations_check=False)
    instr = payload["instructions"]
    for s in instr:  # regola Cookidoo: pesatura separata dalle impostazioni
        types = {a["type"] for a in s.get("annotations", [])}
        assert not ("INGREDIENT" in types and types & {"TTS", "MODE"}), s
        for a in s.get("annotations", []):
            seg = s["text"].encode("utf-16-le")[a["position"]["offset"] * 2:(a["position"]["offset"] + a["position"]["length"]) * 2].decode("utf-16-le")
            assert seg.strip(), a
    assert conv.weigh_steps == 5
    assert conv.guided_steps == 6  # 4 TTS + rosolare + impastare
    assert len(conv.warnings) == 2  # vel. 8 con calore + 4 h
    assert payload["tools"] == ["TM6"]
    strip_modes(conv.recipe)
    print(json.dumps(instr, ensure_ascii=False, indent=1)[:3000])
    print("avvisi:", conv.warnings)


if __name__ == "__main__":
    test_parsers()
    test_payload()
    print("OK")
