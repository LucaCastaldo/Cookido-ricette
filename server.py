"""Bimby Bridge — server MCP che invia le ricette del Ricettario TM6
a Cookidoo (Le mie creazioni), da dove si sincronizzano sul Bimby TM6.

Variabili d'ambiente:
  COOKIDOO_EMAIL, COOKIDOO_PASSWORD   credenziali Cookidoo (obbligatorie)
  BRIDGE_SECRET                       segreto lungo e casuale nell'URL (obbligatorio)
  COOKIDOO_COUNTRY=it  COOKIDOO_LANGUAGE=it-IT
  PORT=8000

Endpoint MCP: https://<host>/<BRIDGE_SECRET>/mcp
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time

import uvicorn
from aiohttp import ClientSession, CookieJar
from cookidoo_api import Cookidoo, CookidooConfig, get_localization_options
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from converter import Catalog, convert, parse_editor_html, without_modes

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
        "invia_ricetta accetta: title, servings, time (minuti), ingredients [{qty, unit, name}], "
        "steps [{text, time, temp, speed, reverse, mode, accessory, add}], tips. "
        "'add' elenca gli ingredienti da pesare prima di quel passaggio. "
        "leggi_ricetta_grezza mostra una ricetta esattamente come Cookidoo l'ha salvata (diagnostica)."
    ),
)


class CookidooError(RuntimeError):
    pass


class Session:
    """Login con cookidoo-api, poi chiamate dirette all'API delle ricette create
    (le stesse che usa l'editor web di Cookidoo)."""

    def __init__(self):
        self.http: ClientSession | None = None
        self.api: Cookidoo | None = None
        self.catalog: Catalog | None = None
        self.catalog_at = 0.0
        self.lock = asyncio.Lock()

    async def login(self):
        if not EMAIL or not PASSWORD:
            raise CookidooError("Credenziali Cookidoo mancanti sul server (COOKIDOO_EMAIL / COOKIDOO_PASSWORD).")
        if self.http is None or self.http.closed:
            self.http = ClientSession(cookie_jar=CookieJar(unsafe=True))
        loc = await get_localization_options(country=COUNTRY, language=LANGUAGE)
        if not loc:
            raise CookidooError(f"Localizzazione Cookidoo non trovata: {COUNTRY}/{LANGUAGE}")
        self.api = Cookidoo(self.http, CookidooConfig(localization=loc[0], email=EMAIL, password=PASSWORD))
        await self.api.login()
        log.info("Login Cookidoo eseguito")

    async def ensure(self):
        if self.api is None:
            await self.login()
        else:
            try:
                await self.api._ensure_token()  # rinnova il token solo se sta per scadere
            except Exception:
                await self.login()

    def ctx(self):
        loc = self.api.localization
        parts = loc.url.split("/")
        base = f"{parts[0]}//{parts[2]}"
        token = self.api.auth_data.access_token
        headers = {"Accept": "application/json", "Content-Type": "application/json",
                   "Authorization": f"Bearer {token}"}
        return base, loc.language, headers

    async def request(self, method: str, path: str, *, body=None, html=False, retry=True):
        base, _, headers = self.ctx()
        if html:
            headers = {**headers, "Accept": "text/html"}
        async with self.http.request(method, base + path, headers=headers,
                                     data=None if body is None else json.dumps(body)) as resp:
            text = await resp.text()
            if resp.status == 401 and retry:
                await self.login()
                return await self.request(method, path, body=body, html=html, retry=False)
            if resp.status not in (200, 201, 204):
                raise CookidooError(f"Cookidoo {method} {path.split('?')[0]} -> {resp.status}: {text[:400]}")
            if html or not text:
                return text
            try:
                return json.loads(text)
            except ValueError:
                return text

    async def load_catalog(self, recipe_id: str) -> tuple[Catalog, str]:
        if self.catalog and time.monotonic() - self.catalog_at < 3600:
            return self.catalog, ""
        _, lang, _ = self.ctx()
        try:
            html = await self.request(
                "GET", f"/created-recipes/{lang}/{recipe_id}/edit/ingredients-and-preparation-steps", html=True)
            self.catalog = parse_editor_html(html)
            self.catalog_at = time.monotonic()
            return self.catalog, ""
        except Exception as e:
            log.warning("Catalogo editor non letto: %s", e)
            return Catalog(), "Etichette dell'editor non lette: modalità lasciate come testo."


S = Session()


def _recipe_id(value: str) -> str:
    value = str(value).strip()
    m = re.search(r"created-recipes/[^/]+/([A-Za-z0-9_-]+)", value)
    return m.group(1) if m else value


@mcp.tool
async def verifica_connessione() -> dict:
    """Verifica login Cookidoo, abbonamento attivo e dispositivi collegati. Non modifica nulla."""
    async with S.lock:
        await S.ensure()
        sub = await S.api.get_active_subscription()
        try:
            devices = [f"{d.type}" for d in await S.api.get_devices()]
        except Exception:
            devices = []
        return {"ok": True, "abbonamento_attivo": bool(sub and sub.active),
                "scadenza": sub.expires if sub else None, "dispositivi": devices}


@mcp.tool
async def invia_ricetta(ricetta: dict | str) -> dict:
    """Crea la ricetta in Cookidoo > Le mie creazioni nel formato dell'editor Cookidoo:
    passaggi guidati TM6 (tempo/temperatura/velocità/antiorario, Impastare, Turbo,
    Riscaldare) e ingredienti collegati per la pesatura. Restituisce id, link e riepilogo."""
    if isinstance(ricetta, str):
        ricetta = json.loads(ricetta)
    convert(ricetta)  # validazione prima di creare qualsiasi cosa su Cookidoo

    async with S.lock:
        await S.ensure()
        base, lang, _ = S.ctx()
        created = await S.request("POST", f"/created-recipes/{lang}",
                                  body={"recipeName": str(ricetta.get("title") or "Ricetta TM6")[:120]})
        rid = created.get("recipeId") if isinstance(created, dict) else None
        if not rid:
            raise CookidooError("Cookidoo non ha restituito l'id della ricetta.")
        try:
            catalog, note = await S.load_catalog(rid)
            conv = convert(ricetta, catalog)
            notes = list(conv.warnings) + ([note] if note else [])
            await asyncio.sleep(3)  # come mcp-cookidoo: la bozza appena creata va lasciata assestare
            try:
                await S.request("PATCH", f"/created-recipes/{lang}/{rid}", body=conv.payload)
            except CookidooError as e:
                log.warning("PATCH completo rifiutato (%s): riprovo senza modalità", e)
                await S.request("PATCH", f"/created-recipes/{lang}/{rid}", body=without_modes(conv.payload))
                notes.append("Cookidoo ha rifiutato una modalità: quei passaggi sono diventati testo.")
        except Exception:
            try:
                await S.request("DELETE", f"/created-recipes/{lang}/{rid}")
            except Exception:
                log.warning("Bozza %s non rimossa", rid)
            raise

    return {
        "ok": True,
        "id": rid,
        "nome": conv.payload["name"],
        "link": f"{base}/created-recipes/{lang}/{rid}",
        "passaggi_guidati": conv.guided_steps,
        "pesature_bilancia": conv.weigh_steps,
        "etichette": catalog.source,
        "avvisi": notes,
        "sul_bimby": "Sul TM6: Cookidoo > Le mie creazioni (sincronizzazione via Wi-Fi).",
    }


@mcp.tool
async def leggi_ricetta_grezza(id_o_link: str) -> dict:
    """Diagnostica: restituisce ingredienti e passaggi di una ricetta di Le mie creazioni
    esattamente come Cookidoo li ha salvati (testo, annotazioni, posizioni). Accetta id o link."""
    rid = _recipe_id(id_o_link)
    async with S.lock:
        await S.ensure()
        _, lang, _ = S.ctx()
        raw = await S.request("GET", f"/created-recipes/{lang}/{rid}")
    if not isinstance(raw, dict):
        return {"grezzo": str(raw)[:20000]}
    content = raw.get("recipeContent", raw)
    keep = {k: content.get(k) for k in ("name", "tools", "ingredients", "instructions", "recipeMetadata")
            if k in content}
    return {"id": rid, "ricetta": keep}


@mcp.tool
async def ricette_inviate() -> list[dict]:
    """Elenca le ricette presenti in Le mie creazioni (nome, id, link)."""
    async with S.lock:
        await S.ensure()
        return [{"id": r.id, "nome": r.name, "link": r.url} for r in await S.api.list_custom_recipes()]


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
