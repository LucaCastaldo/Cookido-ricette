"""Bimby Bridge — server MCP che invia le ricette del Ricettario TM6
a Cookidoo (Le mie creazioni), da dove si sincronizzano sul Bimby TM6.

Variabili d'ambiente:
  COOKIDOO_EMAIL, COOKIDOO_PASSWORD   credenziali Cookidoo (obbligatorie)
  BRIDGE_SECRET                       segreto lungo e casuale nell'URL (obbligatorio)
  COOKIDOO_COUNTRY=it  COOKIDOO_LANGUAGE=it-IT   localizzazione
  PORT=8000

Endpoint MCP: https://<host>/<BRIDGE_SECRET>/mcp
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import uvicorn
from aiohttp import ClientSession, CookieJar
from cookidoo_api import (
    Cookidoo,
    CookidooAuthException,
    CookidooConfig,
    CookidooException,
    get_localization_options,
)
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from converter import convert, strip_modes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bimby-bridge")

EMAIL = os.environ.get("COOKIDOO_EMAIL", "")
PASSWORD = os.environ.get("COOKIDOO_PASSWORD", "")
SECRET = os.environ.get("BRIDGE_SECRET", "")
COUNTRY = os.environ.get("COOKIDOO_COUNTRY", "it")
LANGUAGE = os.environ.get("COOKIDOO_LANGUAGE", "it-IT")

mcp = FastMCP(
    "Bimby Bridge",
    instructions=(
        "Invia ricette al Bimby TM6 tramite Cookidoo (Le mie creazioni). "
        "invia_ricetta accetta la ricetta nel formato del Ricettario TM6: title, servings, time (minuti), "
        "ingredients [{qty, unit, name}], steps [{text, time, temp, speed, reverse, mode, accessory, add}], tips. "
        "'add' elenca i nomi degli ingredienti da pesare con la bilancia prima di quel passaggio."
    ),
)

_lock = asyncio.Lock()
_session: ClientSession | None = None
_api: Cookidoo | None = None


async def _client(force_login: bool = False) -> Cookidoo:
    global _session, _api
    if not EMAIL or not PASSWORD:
        raise RuntimeError("Credenziali Cookidoo mancanti sul server (COOKIDOO_EMAIL / COOKIDOO_PASSWORD).")
    if _api is None or force_login:
        if _session is None or _session.closed:
            _session = ClientSession(cookie_jar=CookieJar(unsafe=True))
        loc = await get_localization_options(country=COUNTRY, language=LANGUAGE)
        if not loc:
            raise RuntimeError(f"Localizzazione Cookidoo non trovata: {COUNTRY}/{LANGUAGE}")
        _api = Cookidoo(_session, CookidooConfig(localization=loc[0], email=EMAIL, password=PASSWORD))
        await _api.login()
        log.info("Login Cookidoo eseguito")
    return _api


async def _call(fn):
    """Esegue fn(api) rifacendo il login una volta se il token è scaduto."""
    async with _lock:
        api = await _client()
        try:
            return await fn(api)
        except CookidooAuthException:
            api = await _client(force_login=True)
            return await fn(api)


def _orphan_id(err: Exception) -> str | None:
    for note in getattr(err, "__notes__", []) or []:
        m = re.search(r"Orphaned custom recipe stub id: (\S+)", note)
        if m:
            return m.group(1)
    return None


@mcp.tool
async def verifica_connessione() -> dict:
    """Verifica login Cookidoo, abbonamento attivo e dispositivi collegati. Non modifica nulla."""

    async def run(api: Cookidoo):
        sub = await api.get_active_subscription()
        try:
            devices = [f"{d.type} {getattr(d, 'name', '') or ''}".strip() for d in await api.get_devices()]
        except CookidooException:
            devices = []
        return {
            "ok": True,
            "abbonamento_attivo": bool(sub and sub.active),
            "scadenza": sub.expires if sub else None,
            "dispositivi": devices,
        }

    return await _call(run)


@mcp.tool
async def invia_ricetta(ricetta: dict | str) -> dict:
    """Crea la ricetta in Cookidoo > Le mie creazioni con passaggi guidati TM6
    (tempo/temperatura/velocità/antiorario, Rosolare, Impastare, Turbo) e pesatura
    degli ingredienti con la bilancia. Restituisce id, link e riepilogo."""
    if isinstance(ricetta, str):
        ricetta = json.loads(ricetta)
    conv = convert(ricetta)
    notes = list(conv.warnings)

    async def run(api: Cookidoo):
        try:
            return await api.create_custom_recipe(conv.recipe)
        except CookidooAuthException:
            raise
        except CookidooException as e:
            orphan = _orphan_id(e)
            if orphan:
                try:
                    await api.remove_custom_recipe(orphan)
                except CookidooException:
                    log.warning("Bozza orfana %s non rimossa", orphan)
            log.warning("Invio completo rifiutato (%s): riprovo senza modalità speciali", e)
            notes.append("Cookidoo ha rifiutato le modalità speciali: quei passaggi sono diventati testo.")
            return await api.create_custom_recipe(strip_modes(conv.recipe))

    created = await _call(run)
    return {
        "ok": True,
        "id": created.id,
        "nome": created.name,
        "link": created.url,
        "passaggi_guidati": conv.guided_steps,
        "pesature_bilancia": conv.weigh_steps,
        "avvisi": notes,
        "sul_bimby": "Sul TM6: Cookidoo > Le mie creazioni (sincronizzazione via Wi-Fi in pochi secondi).",
    }


@mcp.tool
async def ricette_inviate() -> list[dict]:
    """Elenca le ricette presenti in Le mie creazioni (nome, id, link)."""

    async def run(api: Cookidoo):
        return [{"id": r.id, "nome": r.name, "link": r.url} for r in await api.list_custom_recipes()]

    return await _call(run)


async def health(_: Request):
    return PlainTextResponse("ok")


def build_app():
    if len(SECRET) < 24:
        raise SystemExit("BRIDGE_SECRET mancante o troppo corto (minimo 24 caratteri).")
    app = mcp.http_app(path=f"/{SECRET}/mcp")
    app.add_route("/health", health)
    return app


if __name__ == "__main__":
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
