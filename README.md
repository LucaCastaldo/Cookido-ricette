# Bimby Bridge

Il server ponte riceve una ricetta dal **Ricettario TM6** e la crea in **Cookidoo → Le mie creazioni** con passaggi guidati. Da lì la ricetta si sincronizza sul Bimby TM6 via Wi-Fi.

Sul display del TM6 ottieni:

| Cosa | Come appare sul Bimby |
|---|---|
| Pesatura ingredienti | Passaggio dedicato "Pesa e aggiungi" con collegamento alla bilancia |
| Tempo / temperatura / velocità / antiorario | Impostazioni pronte da avviare con un tocco |
| Rosolare, Impastare, Turbo | Modalità guidate |
| Altre modalità (Cottura lenta, Sottovuoto, Fermentazione…) | Testo con l'indicazione di impostarle a mano |

> **Nota.** Il server usa la libreria open source `cookidoo-api`, non ufficiale. Vorwerk non offre API pubbliche e potrebbe cambiare il servizio in qualsiasi momento.

---

## 1. Pubblicazione gratuita su Render

1. Crea un repository GitHub **privato**, ad esempio `bimby-bridge`, e carica tutti i file di questa cartella. Dal sito: **Add file → Upload files**, trascina i file e fai Commit.
2. Genera il segreto, una stringa casuale di 32 o più caratteri composta solo da lettere, numeri, `-` e `_`. Puoi farlo con `python3 -c "import secrets;print(secrets.token_urlsafe(32))"` oppure con un generatore di password online senza simboli.
3. Su [dashboard.render.com](https://dashboard.render.com) scegli **New → Blueprint**, collega il repository e conferma. Render legge `render.yaml` e crea il servizio `bimby-bridge` sul piano Free.
4. Quando richiesto, compila le tre variabili segrete:
   - `COOKIDOO_EMAIL`: email del tuo account Cookidoo
   - `COOKIDOO_PASSWORD`: password Cookidoo
   - `BRIDGE_SECRET`: il segreto del punto 2
5. Al termine della build trovi l'indirizzo, ad esempio `https://bimby-bridge-xxxx.onrender.com`. Aprendo `…/health` deve comparire `ok`.
6. L'URL del connettore è `https://bimby-bridge-xxxx.onrender.com/<BRIDGE_SECRET>/mcp`.

**Pausa del piano Free.** Dopo 15 minuti senza richieste il server si spegne e la riaccensione richiede circa un minuto. Il Ricettario lo risveglia da solo quando apri una ricetta, dopo il primo invio riuscito, così quando premi *Invia al Bimby* è già pronto.

Se vuoi che il server resti sempre acceso, crea un controllo gratuito su [cron-job.org](https://cron-job.org) che chiami `https://…onrender.com/health` ogni 14 minuti. Un servizio acceso tutto il mese usa circa 744 delle 750 ore gratuite mensili di Render: va bene solo se è l'unico servizio Free che tieni sempre attivo.

## 1A. Alternativa: Google Cloud Run

Per un uso personale (qualche ricetta al giorno) resti ampiamente nel piano gratuito di Cloud Run: 2 milioni di richieste al mese. Il server si spegne quando non lo usi e si riaccende in pochi secondi alla prima richiesta.

1. Vai su [console.cloud.google.com](https://console.cloud.google.com) e crea un progetto, ad esempio `bimby-bridge`. Google chiede di collegare un account di fatturazione con carta: serve per l'identità, ma entro i limiti gratuiti non paghi nulla.
2. In **Fatturazione → Budget e avvisi** crea un budget di 1 € con avviso via email. È una sicurezza in più.
3. Apri **Cloud Shell** (icona `>_` in alto a destra). È un terminale nel browser e non devi installare niente.
4. Dal menu ⋮ di Cloud Shell scegli **Carica** e seleziona `bimby-bridge.zip`. Poi esegui:
   ```bash
   unzip bimby-bridge.zip && cd bimby-bridge
   gcloud config set project <ID-DEL-PROGETTO>
   bash deploy-cloudrun.sh
   ```
5. Lo script chiede email e password Cookidoo e genera il segreto. Alla fine stampa l'**URL da incollare nel connettore**: salvalo.

Per aggiornare il server in futuro basta rieseguire `bash deploy-cloudrun.sh`. Viene generato un nuovo segreto, quindi aggiorna anche l'URL del connettore.

## 1B. Alternativa a pagamento: Railway (~5 $/mese)

1. Crea un repository GitHub **privato** e carica tutti i file di questa cartella.
2. Su [railway.app](https://railway.app) scegli **New Project → Deploy from GitHub repo** e seleziona il repository. Railway rileva il `Dockerfile` da solo.
3. Nella scheda **Variables** aggiungi:

| Variabile | Valore |
|---|---|
| `COOKIDOO_EMAIL` | email del tuo account Cookidoo |
| `COOKIDOO_PASSWORD` | password Cookidoo |
| `BRIDGE_SECRET` | stringa casuale di almeno 32 caratteri (vedi sotto) |
| `COOKIDOO_COUNTRY` | `it` |
| `COOKIDOO_LANGUAGE` | `it-IT` |

   Per generare il segreto: `python3 -c "import secrets;print(secrets.token_urlsafe(32))"`

4. Apri **Settings → Networking → Generate Domain**. Ottieni un indirizzo come `https://bimby-bridge-production.up.railway.app`.
5. Verifica che risponda: `https://<dominio>/health` deve mostrare `ok`.

Funziona allo stesso modo su qualsiasi servizio che esegue un Dockerfile (Render, Fly.io, Google Cloud Run, un tuo VPS). Evita i piani gratuiti che mettono in pausa il server: la prima chiamata dopo una pausa può superare il tempo di attesa di Claude.

## 2. Collegamento a Claude (una volta sola)

1. Apri claude.ai → **Impostazioni → Connettori → Aggiungi connettore personalizzato**.
2. **Nome:** `Bimby Bridge`. Il nome deve essere esatto, perché il Ricettario cerca il connettore con questo nome.
3. **URL:** `https://<dominio>/<BRIDGE_SECRET>/mcp`
4. Salva. Il connettore vale anche nell'app Claude sul telefono.

## 3. Uso

Nel Ricettario TM6 apri una ricetta e premi **Invia al Bimby**. La prima volta claude.ai chiede il permesso di usare il connettore da quella pagina. Dopo qualche secondo la ricetta compare sul TM6 in **Le mie creazioni**.

Anche Claude in chat può usare gli strumenti:
- `verifica_connessione`: login, abbonamento e dispositivi
- `invia_ricetta`: crea la ricetta
- `ricette_inviate`: elenca le ricette create

## Sicurezza

- L'URL con il segreto è l'unica protezione del server: non condividerlo. Se pensi che sia stato esposto, cambia `BRIDGE_SECRET` su Railway e aggiorna l'URL del connettore.
- La password Cookidoo resta solo nelle variabili del server. Non compare nel codice né nel Ricettario.
- Le ricette create sono **private** (`workStatus: PRIVATE`).

## Test locale

```bash
pip install -r requirements.txt
python3 test_converter.py          # verifica la conversione, senza rete
COOKIDOO_EMAIL=... COOKIDOO_PASSWORD=... BRIDGE_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))") python3 server.py
```
