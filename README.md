# Serafini SPX Dashboard v2 — repository PUBBLICO, dati PRIVATI

## Stato attuale verificato (audit 11/11/2026)

Questa sezione prevale sulle NOTE STORICHE più sotto che riportano versioni precedenti.
- **Net Gamma Serafini**: `(OI Call + Volume Call)×Gamma Call − (OI Put + Volume Put)×Gamma Put`; **25 strike reali sopra e 25 sotto** il close SPX precedente. L'estensione fino a 140 livelli è soltanto grafica e non cambia i 50 originali.
- **Pine ufficiali**: cinque pulsanti, `07:30`, `14:46` (slot logico delle **07:46 Chicago**, dal 12/11/2026), `15:50`, `17:00` e `20:30`. L'11/11 soltanto il secondo slot ufficiale storico è `13:30`, che dal 12/11 resta Replay.
- **Pine delle 20:30**: soltanto 0DTE + Weekly correnti; senza precedente.
- **Orari USA** ancorati a `America/Chicago`, non sempre corrispondono agli stessi orari `Europe/Rome` nelle settimane di disallineamento DST; usare gli orari di download dei log.
- **Storage Supabase**: bucket privato `spx-snapshots`; ufficiali dell'ultima seduta mantenuti dalla pulizia notturna, Replay compatti conservati 30 giorni (correzione audit 11/11).
- **Ricerca Gamma Contraction**: separata in `Serafini-SPX-Cloud`, raw ufficiali in archivio OAuth Google Drive, output giornaliero e cumulativo non validato.
- **Sicurezza**: service role solo in Secrets del server. Il login della dashboard usa password condivise con limitazione basilare per sessione, non identità individuali.
- **Workflow e test**: la dashboard è distinta dal downloader principale di `Serafini-SPX-Cloud`; il codice di compatibilità qui presente non va scambiato per l'acquisizione programmata.


Questa cartella è **solo** l'interfaccia di consultazione/esportazione per `spx-snapshots`. Non contiene downloader, GitHub Actions, password o chiavi. Il repository privato `Serafini-SPX-Cloud` gestisce separatamente le acquisizioni e la pulizia automatica dello storico alle 23:30 Europe/Rome.

## Pubblicazione gratuita
1. Crea un nuovo repository GitHub **PUBBLICO** chiamato `Serafini-SPX-Dashboard` (distinto da `Serafini-SPX-Cloud`).
2. Carica `app.py`, `storage.py`, `requirements.txt`, `.gitignore`, `README.md` nella radice. **Non caricare ZIP, file di dati o `secrets.toml`**.
3. In Streamlit Community Cloud → Create app → repository `GrespoSan/Serafini-SPX-Dashboard`, branch `main`, main file path `app.py`.
4. In **Advanced settings → Secrets**, incolla (sostituendo i segnaposto):

```toml
SUPABASE_URL = "https://TUO-PROGETTO.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "LA_TUA_CHIAVE_SB_SECRET"
SUPABASE_BUCKET = "spx-snapshots"
ADMIN_PASSWORD = "PASSWORD_FORTE_SOLO_TUA"
FRIENDS_PASSWORD = "PASSWORD_FORTE_DIVERSA_PER_GLI_AMICI"
```

**Non inviare le password o le chiavi nella chat.** I Secrets di Streamlit sono distinti dai Secrets di GitHub Actions: vanno configurati entrambi nei rispettivi servizi.
5. Deploy. Verifica l'accesso lettore e amministratore: entrambi possono consultare RILEVAMENTI CBOE; soltanto l'amministratore puo' richiedere acquisizioni supplementari.

## Trend Daily SPX · allineamento Multi-Market V2

Il pannello Trend Daily non utilizza più `Yahoo ES=F`. Riceve le
**stesse barre OHLC Massive `ESZ6` (o contratto ES impostato nei Secrets)**
già lette dal contesto volatilità tramite una sola chiamata cached di
75 giorni. Le quattro caselle condivise con Multi-Market V2 sono
**Struttura Daily**, **Chiusura vs baricentro**, **Baricentro Daily**
e **Barre stesso colore**. Range ed espansione/compressione compaiono
soltanto nella scheda di volatilità, non sono duplicati nel Trend.

Il precedente criterio di rilevamento della candela di impulso resta
sperimentale e applicato ora alle medesime candele Massive. Se gli OHLC
non includono Open o sono disponibili meno di cinque sedute, si indica
non disponibile senza un fallback implicito a Yahoo.

Restano specifiche SPX: **regime VX in contango/backwardation** e
**buffer indicativo ES 10/15 punti**, senza applicarlo agli altri futures.
La sezione NET GAMMA, l'archivio SPX e i moduli Replay/GEX non sono modificati.

## Regime di volatilità · buffer Serafini (precisazione 01/11/2026)

Per il buffer indicativo della SPX si usa la seduta ES Daily precedente.
Il buffer è **15 punti** se si verifica **almeno una** delle due condizioni:

- futures VX mensili in **backwardation**; **oppure**
- candela Daily ES **rossa** (`Close < Open`) con **range High–Low
  strettamente superiore all'1%**, calcolato come `(High-Low)/Close × 100`.

Il buffer è **10 punti** soltanto se entrambe le condizioni sono state
verificate e nessuna è vera. Se mancano i dati necessari per escludere
entrambe le condizioni (ad esempio Open ES assente e VX in contango),
il valore rimane **N/D**, senza forzare 10 punti. La backwardation,
da sola, è sufficiente per 15 punti anche se il Daily non è disponibile.

La variazione `Close/Close` è mostrata nella scheda a scopo descrittivo
ma **non** determina più il buffer. Il confronto del Range Daily
percentuale con quello della seduta precedente continua a indicare
espansione/compressione, indipendentemente dal colore della candela.

## Massive Futures · range e andamento della seduta precedente

**I dati Daily ES** (Open, High, Low e Close) provengono dalle stesse
barre Massive `1session` del contratto reale scelto. Il range e la
variazione Close/Close usano il campo `close` dell'ultimo trade,
**non `settlement_price`**. Open e Close determinano il colore della candela.

La scheda ha il titolo **Andamento e volatilità — seduta precedente**,
con una riga chiaramente etichettata "Variazione chiusura della seduta
(Close/Close)" verde/rossa. Il Range Daily percentuale rimane neutro.
La curva VX e i dati Cboe delle opzioni SPX non cambiano; i grafici
e le strategie NET GAMMA rimangono invariati.

Aggiungere ai **Secrets Streamlit della Serafini SPX**:

```toml
MASSIVE_API_KEY = "CHIAVE_API_PERSONALE_DA_MASSIVE"
MASSIVE_ES_CONTRACT = "ESZ6"
```

`ESZ6` è un esempio per ottobre 2026: aggiornare il contratto alla
scadenza realmente visualizzata su TradingView ES1! al cambio di
contratto. Senza chiave e contratto la scheda Daily indica N/D e non
ripiega silenziosamente su Yahoo. I dati VX e la gestione delle
fotografie SPX restano indipendenti.

La connessione Massive in ChatGPT non trasmette automaticamente
una API key alla tua app Streamlit. La tua API key dev'essere
abilitata agli **aggregati Futures**. Non pubblicarla su GitHub,
nel codice o in chat.

## Sicurezza (leggere)
- Il repository dell'interfaccia è **pubblico**, ma gli snapshot restano in un bucket Supabase **privato**. I Secrets sono lato server.
- Password condivise NON identificano individualmente gli amici. La protezione da tentativi ripetuti è **solo basilare** e non efficace contro attacchi distribuiti; per un gruppo ampio o dati sensibili preferire Supabase Auth/login individuali.
- La chiave Supabase `sb_secret_...` è ad alto privilegio. Conservarla **solo** nei Secrets del server; mai in browser, codice, repository o screenshot. Una vulnerabilità nell'app potrebbe compromettere il progetto: usare un progetto Supabase dedicato (già creato) ed evitare dati sensibili nello stesso progetto.
- La Dashboard non include più la preparazione ZIP dell'archivio né pulsanti di cancellazione manuale. La retention dei dati storici è gestita separatamente nel repository Cloud, conservando l'ultima giornata disponibile e i feed MT4.
- I due ruoli usano password differenti.
- La lettura di snapshot SPX da Cboe, la programmazione e il metodo GEX restano invariati.

## Aggiornamento v3 — NET GAMMA in verifica

La dashboard conserva il login amministratore/amici, i grafici NET GAMMA 0DTE/Weekly, il confronto GEX, il Replay e il report AI. Il pannello **RILEVAMENTI CBOE** si trova **all'interno di ANALISI NET GAMMA**: è chiuso inizialmente, accessibile sia all'amministratore sia al lettore, e non scarica i registri finché non viene aperto. Il pulsante per avviare acquisizioni manuali resta riservato all'amministratore.

* `net_gamma.py`: parsing SPX/SPXW OCC, formula slide 132: `(OI_CALL + VOL_CALL)*GAMMA_CALL - (OI_PUT + VOL_PUT)*GAMMA_PUT` per strike.
* `gamma_section.py`: scelta dello snapshot, riferimento alla chiusura SPX precedente, differenziale ES−SPX manuale facoltativo, profili 0DTE e venerdì settimanale separati, tabella e CSV.
* Lo snapshot Cboe originale non viene modificato dai calcoli. La Dashboard non espone azioni di cancellazione; le fotografie storiche vengono gestite dal workflow Cloud indipendente.

### Importante: metodo Serafini

Le slide documentano 50 livelli intorno alla chiusura SPX, ma non il criterio algoritmico inequivocabile. **Al momento il programma prende i 50 strike disponibili con distanza assoluta più bassa dal riferimento**: implementazione tecnica ESPLICITAMENTE PROVVISORIA, NON attribuibile a Serafini finché non venga verificata sul video. Non usare i valori per negoziare prima della validazione. Le Weekly sono la singola scadenza del venerdì della settimana, **non** l'aggregato della settimana. In caso di festività/assenza di contratti nessun dato viene inventato. Per eventuali eccezioni servirà un'ulteriore verifica del metodo.

### Pubblicazione

Nel repository pubblico `Serafini-SPX-Dashboard` sostituire soltanto `app.py`, `requirements.txt` e caricare `net_gamma.py`, `gamma_section.py`; `storage.py`, `.gitignore` e i Secrets restano invariati. Non caricare file originali SPX, password o chiavi nel repository pubblico. Eseguire il test da Streamlit con uno snapshot già presente nella settimana 2026-W42.

### Test locale effettuato

Snapshot SPX del 15 ottobre 2026: 30.470 contratti nel file; per scadenza 2026-10-15 498 contratti e 249 strike disponibili; per 2026-10-16 2.060 contratti e 545 strike disponibili. Il modulo seleziona 50 strike per ciascuna scadenza se disponibili. Test di struttura e calcolo; nessuna verifica di corrispondenza con il grafico Overcharts/Serafini ancora eseguita.


## Estensione automatica NET GAMMA — soltanto grafici e Pine (31/10/2026)

- **Metodo Serafini invariato:** 25 strike sopra e 25 sotto il close SPX precedente
  per C1/P1, analisi, confronto e CSV numerico originale.
- **Visualizzazione:** l'app può aggiungere strike reali già presenti nella STESSA
  fotografia Cboe, per coprire il prezzo SPX fotografato ±2,5% del riferimento,
  con tetto di 140 strike per profilo e segnalazione di copertura parziale.
- **TradingView:** i Pine 07:30 e comparativi includono l'opzione «Estensione
  automatica NET GAMMA» attiva per default; OFF mostra solo gli originali.
  I tre profili comparativi usano al massimo 423 linee per baselines e Gamma,
  contro il limite Pine di 500. Non vengono inventati o richiesti dati live.
- **Replay:** i nuovi file compatti conservano separatamente `rows` 50 e
  `visual_rows` fino a 140. I Replay vecchi, privi dei livelli aggiuntivi,
  rimangono a 50; non è possibile recuperarli dal solo JSON compatto.
- I Pine esportati restano STATICI rispetto al rilevamento: per nuovi strike
  fuori dalla fascia coperta, selezionare una fotografia più recente e
  rigenerare il codice. Tutti i prezzi Cboe restano puntuali al download.
