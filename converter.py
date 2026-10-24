"""Converte una ricetta del Ricettario TM6 nel formato esatto che l'editor di
Cookidoo (Le mie creazioni) salva per una ricetta TM6 guidata.

Formato verificato sull'output reale dell'editor (vedi progetto mcp-cookidoo):

  {"type": "STEP",
   "text": "Cuocere 12 min/100°C/\\ue003/vel. \\ue002.",
   "annotations": [
     {"type": "INGREDIENT", "data": {"description": "1 cipolla"}, "position": {...}},
     {"type": "TTS", "data": {"speed": "soft", "direction": "CCW", "time": 720,
                               "temperature": {"value": "100", "unit": "C"}},
      "position": {"offset": .., "length": ..}}]}

- le etichette (min, sec, vel., Varoma) e i titoli delle modalità vengono letti
  dalla pagina dell'editor Cookidoo dell'account (catalogo), così il testo è
  identico a quello che produrrebbe l'editor in italiano;
- antiorario e velocità soft sono glifi del font icone Cookidoo (\\ue003, \\ue002);
- modalità: nome minuscolo (dough, turbo, warm_up) + glifo; Rosolare e Cottura a
  vapore guidate esistono solo per TM7, quindi su TM6 restano testo;
- posizioni in unità UTF-16 come nel browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

REVERSE_GLYPH = ""
SOFT_GLYPH = ""
MODE_GLYPHS = {"dough": "", "turbo": "", "warm_up": ""}
TTS_TEMPS = ["37", "40", "45", "50", "55", "60", "65", "70", "75", "80", "85", "90",
             "95", "98", "100", "105", "110", "115", "120"]
SPEEDS = ["0.5", "1", "1.5", "2", "2.5", "3", "3.5", "4", "4.5", "5", "5.5", "6",
          "6.5", "7", "7.5", "8", "8.5", "9", "9.5", "10"]
MAX_TTS = 5940
SEMICOLON = "；"  # l'editor sostituisce ";" con il punto e virgola a tutta larghezza


@dataclass
class Catalog:
    """Etichette e modalità dell'editor Cookidoo per la lingua dell'account."""
    minutes: str = "min"
    seconds: str = "sec"
    speed: str = "vel."
    varoma: str = "Varoma"
    modes: dict[str, str] = field(default_factory=dict)  # dough -> "Impastare"
    tm7_only: set[str] = field(default_factory=set)
    turbo_times: tuple[str, ...] = ("0.5", "1", "2")
    source: str = "builtin"


@dataclass
class Conversion:
    payload: dict
    warnings: list[str] = field(default_factory=list)
    guided_steps: int = 0
    weigh_steps: int = 0


# ---------------------------------------------------------------- utilità

def _s(v) -> str:
    return "" if v is None else str(v).strip()


def u16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def norm_text(t: str) -> str:
    return _s(t).replace(";", SEMICOLON)


def parse_seconds(t: str) -> int:
    t = _s(t).lower().replace(",", ".")
    if not t:
        return 0
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(h|ore|ora|min|m|sec|s)\b", t):
        v = float(num)
        total += v * (3600 if unit in ("h", "ora", "ore") else 60 if unit in ("min", "m") else 1)
    if total == 0 and re.fullmatch(r"\d+(\.\d+)?", t):
        total = float(t)
    return int(round(total))


def snap_time(sec: int) -> int:
    """Griglia dell'editor: fino a 5 min passi di 10 s, fino a 20 min di 30 s, poi minuti."""
    if sec <= 60:
        return max(1, sec)
    step = 10 if sec <= 300 else 30 if sec <= 1200 else 60
    return min(MAX_TTS, int(round(sec / step) * step))


def fmt_time(sec: float, cat: Catalog) -> str:
    m, s = divmod(sec, 60)
    parts = []
    if m:
        parts.append(f"{int(m)} {cat.minutes}")
    if s:
        sv = int(s) if float(s).is_integer() else s
        parts.append(f"{sv} {cat.seconds}")
    return " ".join(parts) or f"0 {cat.seconds}"


def parse_speed(v: str) -> tuple[str | None, str | None]:
    v = _s(v).lower().replace(",", ".").replace("vel.", "").replace("velocità", "").strip()
    if not v:
        return None, None
    if "spiga" in v or "impast" in v:
        return None, "dough"
    if "turbo" in v:
        return None, "turbo"
    if "soft" in v or "lenta" in v or "delicat" in v:
        return "soft", None
    m = re.search(r"\d+(?:\.\d+)?", v)
    if not m:
        return None, None
    num = max(0.5, min(10.0, round(float(m.group()) * 2) / 2))
    return f"{num:g}", None


def parse_temp(v: str) -> tuple[str | None, int | None]:
    v = _s(v).lower()
    if not v:
        return None, None
    if "varoma" in v:
        return "varoma", None
    m = re.search(r"\d+", v)
    if not m:
        return None, None
    deg = int(m.group())
    if deg > 120:
        return None, deg
    return min(TTS_TEMPS, key=lambda x: abs(int(x) - deg)), deg


def speed_text(speed: str, cat: Catalog) -> str:
    return f"{cat.speed} {SOFT_GLYPH}" if speed == "soft" else f"{cat.speed} {speed.replace('.', ',')}"


def _ing_line(i: dict) -> str:
    return norm_text(re.sub(r"\s+", " ", f"{_s(i.get('qty'))} {_s(i.get('unit'))} {_s(i.get('name'))}"))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9àèéìòù ]", "", s.lower()).strip()


def _match(names, ingredients, lines) -> list[str]:
    out = []
    for n in names or []:
        nn = _norm(_s(n))
        if not nn:
            continue
        for ing, line in zip(ingredients, lines):
            iname = _norm(_s(ing.get("name")))
            if iname and (nn == iname or nn in iname or iname in nn):
                if line not in out:
                    out.append(line)
                break
    return out


def _ann(kind: str, data: dict, text: str, start: int, length_text: str, name: str | None = None) -> dict:
    a = {"type": kind, "data": data,
         "position": {"offset": u16(text[:start]), "length": u16(length_text)}}
    if name:
        a["name"] = name
    return a


def _step(prefix: str, slot: str, suffix: str = ".") -> tuple[str, int]:
    text = f"{prefix}{slot}{suffix}"
    return text, len(prefix)


# ---------------------------------------------------------------- conversione

def convert(r: dict, cat: Catalog | None = None) -> Conversion:
    cat = cat or Catalog()
    warnings: list[str] = []
    title = norm_text(r.get("title"))[:120] or "Ricetta TM6"
    sv = _s(r.get("servings"))
    servings = int(sv) if sv.isdigit() and int(sv) > 0 else 4
    ingredients = [i for i in (r.get("ingredients") or []) if isinstance(i, dict) and _s(i.get("name"))]
    lines = [_ing_line(i) for i in ingredients]
    if not lines:
        raise ValueError("La ricetta non ha ingredienti.")

    out: list[dict] = []
    guided = weighed = machine = 0

    for idx, st in enumerate(r.get("steps") or [], start=1):
        if isinstance(st, str):
            st = {"text": st}
        text = norm_text(_s(st.get("text")).rstrip(" .;:"))
        if not text:
            continue

        # 1) pesatura: "Aggiungi 40 g cipolla, 30 g olio." con link agli ingredienti
        to_add = _match(st.get("add"), ingredients, lines)
        if to_add:
            wt = "Aggiungi " + ", ".join(to_add) + "."
            anns, pos = [], 0
            for line in to_add:
                start = wt.index(line, pos)
                anns.append(_ann("INGREDIENT", {"description": line}, wt, start, line))
                pos = start + len(line)
            out.append({"type": "STEP", "text": wt, "annotations": anns})
            weighed += 1

        # 2) impostazione macchina
        sec = parse_seconds(st.get("time"))
        speed, speed_mode = parse_speed(st.get("speed"))
        temp, raw_deg = parse_temp(st.get("temp"))
        reverse = st.get("reverse") is True or _s(st.get("reverse")).lower() == "true"
        mode_txt = _s(st.get("mode")).lower()
        mode = speed_mode
        if "impast" in mode_txt:
            mode = "dough"
        elif "turbo" in mode_txt:
            mode = "turbo"
        elif "riscald" in mode_txt:
            mode = "warm_up"
        wants_tm7 = "rosol" in mode_txt or (raw_deg and raw_deg > 120) or "vapore" in mode_txt and not temp

        prefix = text + " "
        step = None
        if mode and mode in cat.modes and mode not in cat.tm7_only:
            title_m, glyph = cat.modes[mode], MODE_GLYPHS[mode]
            if mode == "dough" and sec:
                t = min(max(sec, 1), 1200)
                slot = f"{title_m} {glyph}/{fmt_time(t, cat)}"
                txt, start = _step(prefix, slot)
                step = {"type": "STEP", "text": txt,
                        "annotations": [_ann("MODE", {"time": t}, txt, start, slot, "dough")]}
                machine += t
            elif mode == "turbo":
                allowed = [float(x) for x in cat.turbo_times] or [0.5, 1, 2]
                t = min(allowed, key=lambda x: abs(x - (sec or 1)))
                tv = int(t) if t.is_integer() else t
                slot = f"{title_m} {glyph}/{fmt_time(tv, cat)}"
                txt, start = _step(prefix, slot)
                step = {"type": "STEP", "text": txt,
                        "annotations": [_ann("MODE", {"time": tv}, txt, start, slot, "turbo")]}
            elif mode == "warm_up" and temp and temp != "varoma" and int(temp) <= 90:
                sp = speed if speed in ("soft", "1", "2") else "soft"
                slot = f"{title_m} {glyph}/{temp}°C"
                txt, start = _step(prefix, slot)
                step = {"type": "STEP", "text": txt, "annotations": [_ann(
                    "MODE", {"temperature": {"value": temp, "unit": "C"}, "speed": sp},
                    txt, start, slot, "warm_up")]}
        elif mode:
            warnings.append(f"Passaggio {idx}: modalità non disponibile nell'editor per TM6, lasciata come testo.")

        if step is None and not mode and speed and sec:
            if sec > MAX_TTS:
                warnings.append(f"Passaggio {idx}: oltre 99 min, lasciato come testo.")
            elif temp and speed != "soft" and float(speed) > 6:
                warnings.append(f"Passaggio {idx}: vel. {speed} non ammessa con il calore, lasciato come testo.")
            elif wants_tm7:
                pass
            else:
                t = snap_time(sec)
                if t != sec:
                    warnings.append(f"Passaggio {idx}: tempo arrotondato a {fmt_time(t, cat)} (griglia Cookidoo).")
                segs = [fmt_time(t, cat)]
                if temp == "varoma":
                    segs.append(cat.varoma)
                elif temp:
                    segs.append(f"{temp}°C")
                if reverse:
                    segs.append(REVERSE_GLYPH)
                segs.append(speed_text(speed, cat))
                slot = "/".join(segs)
                data = {"speed": speed, "time": t}
                if reverse:
                    data["direction"] = "CCW"
                if temp:
                    data["temperature"] = {"value": temp, "unit": "C"}  # anche "varoma": come l'editor
                txt, start = _step(prefix, slot)
                step = {"type": "STEP", "text": txt, "annotations": [_ann("TTS", data, txt, start, slot)]}
                machine += t

        if step is not None:
            acc = _s(st.get("accessory"))
            if acc and acc.lower() not in text.lower():
                # l'accessorio va prima delle impostazioni, ricalcolo la posizione
                ann = step["annotations"][0]
                slot_len = ann["position"]["length"]
                slot = step["text"][len(text) + 1:-1]
                new_prefix = f"{text} ({norm_text(acc)}) "
                step["text"] = f"{new_prefix}{slot}."
                ann["position"] = {"offset": u16(new_prefix), "length": slot_len}
            out.append(step)
            guided += 1
        else:
            extra = []
            if wants_tm7 or (mode and step is None):
                label = _s(st.get("mode")) or ("Rosolare" if wants_tm7 else "")
                if label:
                    extra.append(f"imposta {label} dal Bimby")
            vals = [v for v in (_s(st.get("time")), _s(st.get("temp")),
                                f"vel. {_s(st.get('speed'))}" if _s(st.get("speed")) else "",
                                "antiorario" if reverse else "") if v]
            if vals and any(c.isdigit() for c in "".join(vals)):
                extra.append("/".join(vals))
            if _s(st.get("accessory")):
                extra.append(_s(st.get("accessory")))
            plain = text + (" (" + "; ".join(extra) + ")" if extra else "") + "."
            out.append({"type": "STEP", "text": norm_text(plain)})

    if not out:
        raise ValueError("La ricetta non ha passaggi.")

    tv = _s(r.get("time"))
    total = max((int(tv) if tv.isdigit() else 0) * 60, machine, 60)
    prep = max(0, total - machine)
    hints = [norm_text(t) for t in (r.get("tips") or []) if _s(t)]
    hints.append("Ricetta generata con il Ricettario TM6 (Claude), non verificata da Vorwerk.")

    payload = {
        "name": title,
        "image": None,
        "isImageOwnedByUser": False,
        "tools": ["TM6"],
        "yield": {"value": servings, "unitText": "portion"},
        "prepTime": prep,
        "cookTime": total - prep,
        "totalTime": total,
        "ingredients": [{"type": "INGREDIENT", "text": l} for l in lines],
        "instructions": out,
        "hints": "\n".join(hints),
        "workStatus": "PRIVATE",
        "recipeMetadata": {"requiresAnnotationsCheck": False},
    }
    return Conversion(payload=payload, warnings=warnings, guided_steps=guided, weigh_steps=weighed)


def without_modes(payload: dict) -> dict:
    """Variante prudente: le modalità diventano testo, restano pesature e TTS."""
    steps = []
    for s in payload["instructions"]:
        if any(a.get("type") == "MODE" for a in s.get("annotations", [])):
            steps.append({"type": "STEP", "text": s["text"]})
        else:
            steps.append(s)
    return {**payload, "instructions": steps}


# ---------------------------------------------------------------- catalogo editor

_SECTIONS = {"cr-tts-mode", "cr-tts-speed", "cr-tts-temperature", "cr-tts-time"}


def parse_editor_html(html: str) -> Catalog:
    """Legge etichette e modalità dalla pagina editor Cookidoo (come mcp-cookidoo, MIT)."""
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.section = None
            self.attrs_by = {}
            self.radios = {s: [] for s in _SECTIONS}
            self.tm7 = set()
            self.last = ""

        def handle_starttag(self, tag, attrs):
            a = {k: v or "" for k, v in attrs}
            if tag in _SECTIONS:
                self.section = tag
                self.attrs_by.setdefault(tag, a)
            elif tag == "input" and self.section and a.get("type") == "radio":
                self.radios[self.section].append(
                    (a.get("value", ""), a.get("header-title") or self.last.strip(), a.get("id", "")))
            elif tag == "label" and "tm7-mode" in a:
                self.tm7.add(a.get("for", ""))
            if tag == "span":
                self.last = ""

        def handle_endtag(self, tag):
            if tag == self.section:
                self.section = None

        def handle_data(self, data):
            if data.strip():
                self.last = data

    p = P()
    p.feed(html)
    if not p.radios["cr-tts-mode"]:
        raise ValueError("La pagina editor non contiene le modalità (login non valido?).")
    t = p.attrs_by.get("cr-tts-time", {})
    s = p.attrs_by.get("cr-tts-speed", {})
    tp = p.attrs_by.get("cr-tts-temperature", {})
    return Catalog(
        minutes=t.get("minutes-translation", "min"),
        seconds=t.get("seconds-translation", "sec"),
        speed=s.get("speed-translation", "vel."),
        varoma=tp.get("varoma-translation", "Varoma"),
        modes={v: title for v, title, _ in p.radios["cr-tts-mode"]},
        tm7_only={v for v, _, i in p.radios["cr-tts-mode"] if i in p.tm7},
        turbo_times=tuple(v for v, _, _ in p.radios["cr-tts-time"]) or ("0.5", "1", "2"),
        source="cookidoo",
    )
