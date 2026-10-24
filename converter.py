"""Converte una ricetta del Ricettario TM6 in una ricetta creata Cookidoo
con passaggi guidati per il Bimby TM6.

Regole Cookidoo rispettate:
- i passaggi di pesatura (annotazione INGREDIENT, bilancia) sono separati dai
  passaggi con impostazioni macchina (TTS / MODE);
- TTS: tempo 1-5940 s, velocità ammesse, temperatura solo con velocità <= 6;
- modalità supportate: Rosolare (BROWNING), Impastare (DOUGH), Turbo (TURBO).
Tutto ciò che non è convertibile resta come testo leggibile, mai scartato.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from cookidoo_api import (
    CookidooCreateCustomRecipe,
    CookidooIngredientAnnotation,
    CookidooInstruction,
    CookidooModeAnnotation,
    CookidooTemperatureSetting,
    CookidooTTSAnnotation,
)

SPEEDS = {"soft", "0.5", "1", "1.5", "2", "2.5", "3", "3.5", "4", "4.5", "5", "5.5",
          "6", "6.5", "7", "7.5", "8", "8.5", "9", "9.5", "10"}
TEMPS = [37, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 98, 100, 105, 110, 115, 120]
MAX_TTS = 5940


@dataclass
class Conversion:
    recipe: CookidooCreateCustomRecipe
    warnings: list[str] = field(default_factory=list)
    guided_steps: int = 0
    weigh_steps: int = 0


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def parse_seconds(t: str) -> int:
    """'3 min', '1 min 30 sec', '30 sec', '1 h', '2 ore', '90 s' -> secondi."""
    t = _s(t).lower().replace(",", ".")
    if not t:
        return 0
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(h|ore|ora|min|m|sec|s)\b", t):
        v = float(num)
        if unit in ("h", "ora", "ore"):
            total += v * 3600
        elif unit in ("min", "m"):
            total += v * 60
        else:
            total += v
    if total == 0 and re.fullmatch(r"\d+(\.\d+)?", t):
        total = float(t)  # numero nudo: secondi
    return int(round(total))


def fmt_time(sec: int) -> str:
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    parts = []
    if h:
        parts.append(f"{h} h")
    if m:
        parts.append(f"{m} min")
    if s or not parts:
        parts.append(f"{s} sec")
    return " ".join(parts)


def parse_speed(v: str) -> tuple[str | None, str | None]:
    """-> (speed, mode) dove mode è 'DOUGH' o 'TURBO' se indicato."""
    v = _s(v).lower().replace(",", ".").replace("vel.", "").replace("velocità", "").strip()
    if not v:
        return None, None
    if "spiga" in v or "impast" in v:
        return None, "DOUGH"
    if "turbo" in v:
        return None, "TURBO"
    if "soft" in v or "lenta" in v:
        return "soft", None
    m = re.search(r"\d+(?:\.\d+)?", v)
    if not m:
        return None, None
    num = float(m.group())
    num = max(0.5, min(10.0, round(num * 2) / 2))
    s = f"{num:g}"
    return (s if s in SPEEDS else None), None


def parse_temp(v: str) -> tuple[str | None, int | None]:
    """-> ('varoma' | '100', gradi grezzi) ; None se assente."""
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
        return None, deg  # sopra 120 °C -> modalità Rosolare
    nearest = min(TEMPS, key=lambda x: abs(x - deg))
    return str(nearest), deg


def _ing_line(i: dict) -> str:
    return re.sub(r"\s+", " ", f"{_s(i.get('qty'))} {_s(i.get('unit'))} {_s(i.get('name'))}").strip()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9àèéìòù ]", "", s.lower()).strip()


def _match_ingredients(names: list[str], ingredients: list[dict], lines: list[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        nn = _norm(_s(n))
        if not nn:
            continue
        best = None
        for ing, line in zip(ingredients, lines):
            iname = _norm(_s(ing.get("name")))
            if not iname:
                continue
            if nn == iname or nn in iname or iname in nn:
                best = line
                break
        if best and best not in out:
            out.append(best)
    return out


def _clean_text(text: str) -> str:
    return _s(text).rstrip(" .;:")


def convert(r: dict) -> Conversion:
    warnings: list[str] = []
    title = _s(r.get("title"))[:120] or "Ricetta TM6"
    servings = int(_s(r.get("servings")) or 4) if _s(r.get("servings")).isdigit() else 4
    ingredients = [i for i in (r.get("ingredients") or []) if isinstance(i, dict) and _s(i.get("name"))]
    lines = [_ing_line(i) for i in ingredients]
    if not lines:
        raise ValueError("La ricetta non ha ingredienti.")

    steps_out: list[CookidooInstruction | str] = []
    guided = weighed = 0
    machine_seconds = 0

    for idx, st in enumerate(r.get("steps") or [], start=1):
        if isinstance(st, str):
            st = {"text": st}
        text = _clean_text(st.get("text"))
        if not text:
            continue

        # 1) passaggio di pesatura separato (bilancia del TM6)
        to_add = _match_ingredients(list(st.get("add") or []), ingredients, lines)
        if to_add:
            weigh_text = "Pesa e aggiungi: " + ", ".join(to_add) + "."
            steps_out.append(CookidooInstruction(
                text=weigh_text,
                annotations=[CookidooIngredientAnnotation(slot=l, description=l) for l in to_add],
            ))
            weighed += 1

        # 2) passaggio macchina
        sec = parse_seconds(st.get("time"))
        speed, speed_mode = parse_speed(st.get("speed"))
        temp, raw_deg = parse_temp(st.get("temp"))
        reverse = st.get("reverse") is True or _s(st.get("reverse")).lower() == "true"
        mode_txt = _s(st.get("mode")).lower()

        mode = speed_mode
        if "rosol" in mode_txt or (raw_deg and raw_deg > 120):
            mode = "BROWNING"
        elif "impast" in mode_txt:
            mode = "DOUGH"
        elif "turbo" in mode_txt:
            mode = "TURBO"
        manual_mode = _s(st.get("mode")) if mode_txt and mode is None else ""

        annotation = None
        slot = ""
        if mode == "BROWNING" and sec:
            sec_b = min(sec, 1800)
            deg = raw_deg if raw_deg and raw_deg > 120 else 160
            deg = min(160, max(130, deg))
            slot = f"Rosolare/{fmt_time(sec_b)}/{deg}°C"
            annotation = CookidooModeAnnotation(
                slot=slot, mode="browning", name="BROWNING", time=sec_b,
                temperature=CookidooTemperatureSetting(value=str(deg), unit="C"), power="Intense")
            machine_seconds += sec_b
        elif mode == "DOUGH" and sec:
            sec_d = min(max(sec, 1), 1200)
            slot = f"Impastare/{fmt_time(sec_d)}"
            annotation = CookidooModeAnnotation(slot=slot, mode="dough", name="DOUGH", time=sec_d)
            machine_seconds += sec_d
        elif mode == "TURBO":
            t = 2 if sec >= 2 else (1 if sec >= 1 else 0.5)
            slot = f"Turbo/{t:g} sec"
            annotation = CookidooModeAnnotation(slot=slot, mode="turbo", name="TURBO", time=t)
        elif speed and sec:
            if sec > MAX_TTS:
                warnings.append(f"Passaggio {idx}: tempo oltre 99 min, lasciato come testo.")
            elif temp and speed != "soft" and float(speed) > 6:
                warnings.append(f"Passaggio {idx}: velocità {speed} non ammessa con il calore, lasciato come testo.")
            else:
                parts = [fmt_time(sec)]
                if temp:
                    parts.append("Varoma" if temp == "varoma" else f"{temp}°C")
                if reverse:
                    parts.append("antiorario")
                parts.append(f"vel. {speed}")
                slot = "/".join(parts)
                annotation = CookidooTTSAnnotation(
                    slot=slot, time=sec,
                    temperature=CookidooTemperatureSetting(value=temp, unit=None if temp == "varoma" else "C") if temp else None,
                    speed=speed, direction="CCW" if reverse else None)
                machine_seconds += sec

        accessory = _s(st.get("accessory"))
        extra = []
        if accessory and accessory.lower() not in text.lower():
            extra.append(f"({accessory})")
        if manual_mode:
            extra.append(f"[imposta la modalità {manual_mode} dal Bimby]")

        if annotation is not None:
            full = f"{text} {' '.join(extra)}: {slot}.".replace("  ", " ").replace(" :", ":").strip()
            steps_out.append(CookidooInstruction(text=full, annotations=[annotation]))
            guided += 1
        else:
            plain = text
            fallback = [p for p in (_s(st.get("time")), _s(st.get("temp")),
                                    f"vel. {_s(st.get('speed'))}" if _s(st.get("speed")) else "",
                                    "antiorario" if reverse else "") if p]
            if fallback and any(ch.isdigit() for ch in "".join(fallback)):
                plain += " " + "/".join(fallback)
            if extra:
                plain += " " + " ".join(extra)
            steps_out.append(plain.strip() + ".")

    if not steps_out:
        raise ValueError("La ricetta non ha passaggi.")

    total_min = int(_s(r.get("time")) or 0) if _s(r.get("time")).isdigit() else 0
    total = max(total_min * 60, machine_seconds, 60)
    active = max(0, total - machine_seconds)

    hints = [_s(t) for t in (r.get("tips") or []) if _s(t)]
    hints.append("Ricetta generata con il Ricettario TM6 (Claude), non verificata da Vorwerk.")

    recipe = CookidooCreateCustomRecipe(
        name=title, ingredients=lines, instructions=steps_out, serving_size=servings,
        total_time=total, active_time=active, tools=["TM6"], unit_text="portion",
        hints=hints, work_status="PRIVATE", requires_annotations_check=False,
    )
    return Conversion(recipe=recipe, warnings=warnings, guided_steps=guided, weigh_steps=weighed)


def strip_modes(recipe: CookidooCreateCustomRecipe) -> CookidooCreateCustomRecipe:
    """Variante prudente: tiene pesature e TTS, trasforma le modalità in testo."""
    out: list[CookidooInstruction | str] = []
    for st in recipe.instructions:
        if isinstance(st, CookidooInstruction) and any(isinstance(a, CookidooModeAnnotation) for a in st.annotations):
            out.append(st.text)
        else:
            out.append(st)
    return replace(recipe, instructions=out)
