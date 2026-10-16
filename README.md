# Serafini SPX Dashboard v2 — repository PUBBLICO, dati PRIVATI

Questa cartella è **solo** l'interfaccia di consultazione/esportazione per `spx-snapshots`. Non contiene downloader, GitHub Actions, password o chiavi. Non modificare il repository **privato** `Serafini-SPX-Cloud`: continua a eseguire la raccolta SPX 07:30 e 15:30 (ora Roma).

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
5. Deploy. Alla prima apertura prova la password amici: ZIP sì, cancellazione assente. Esci e prova la password admin: ZIP e (solo dopo conferma/hash) cancellazione.

## Sicurezza (leggere)
- Il repository dell'interfaccia è **pubblico**, ma gli snapshot restano in un bucket Supabase **privato**. I Secrets sono lato server.
- Password condivise NON identificano individualmente gli amici. La protezione da tentativi ripetuti è **solo basilare** e non efficace contro attacchi distribuiti; per un gruppo ampio o dati sensibili preferire Supabase Auth/login individuali.
- La chiave Supabase `sb_secret_...` è ad alto privilegio. Conservarla **solo** nei Secrets del server; mai in browser, codice, repository o screenshot. Una vulnerabilità nell'app potrebbe compromettere il progetto: usare un progetto Supabase dedicato (già creato) ed evitare dati sensibili nello stesso progetto.
- I download ZIP sono generati in RAM; l'app Free può avere limiti di memoria. Le due password devono essere diverse.
- Non possiamo garantire che il file sia stato salvato sul PC: la cancellazione richiede conferma manuale, hash e controllo che i file cloud non siano cambiati. Non elimina automaticamente.
- La lettura di snapshot SPX da Cboe, la programmazione e il metodo GEX restano invariati.

## Aggiornamento v3 — NET GAMMA in verifica

La dashboard conserva login amministratore/amici, elenco snapshot, ZIP e cancellazione con conferma. Aggiunge in fondo il pannello **NET GAMMA 0DTE / Weekly — modulo in verifica**.

* `net_gamma.py`: parsing SPX/SPXW OCC, formula slide 132: `(OI_CALL + VOL_CALL)*GAMMA_CALL - (OI_PUT + VOL_PUT)*GAMMA_PUT` per strike.
* `gamma_section.py`: scelta dello snapshot, riferimento alla chiusura SPX precedente, differenziale ES−SPX manuale facoltativo, profili 0DTE e venerdì settimanale separati, tabella e CSV.
* Lo snapshot Cboe originale non viene modificato, il modulo fa SOLO lettura e NON cambia GitHub Actions, bucket, password o permessi di cancellazione.

### Importante: metodo Serafini

Le slide documentano 50 livelli intorno alla chiusura SPX, ma non il criterio algoritmico inequivocabile. **Al momento il programma prende i 50 strike disponibili con distanza assoluta più bassa dal riferimento**: implementazione tecnica ESPLICITAMENTE PROVVISORIA, NON attribuibile a Serafini finché non venga verificata sul video. Non usare i valori per negoziare prima della validazione. Le Weekly sono la singola scadenza del venerdì della settimana, **non** l'aggregato della settimana. In caso di festività/assenza di contratti nessun dato viene inventato. Per eventuali eccezioni servirà un'ulteriore verifica del metodo.

### Pubblicazione

Nel repository pubblico `Serafini-SPX-Dashboard` sostituire soltanto `app.py`, `requirements.txt` e caricare `net_gamma.py`, `gamma_section.py`; `storage.py`, `.gitignore` e i Secrets restano invariati. Non caricare file originali SPX, password o chiavi nel repository pubblico. Eseguire il test da Streamlit con uno snapshot già presente nella settimana 2026-W42.

### Test locale effettuato

Snapshot SPX del 15 ottobre 2026: 30.470 contratti nel file; per scadenza 2026-10-15 498 contratti e 249 strike disponibili; per 2026-10-16 2.060 contratti e 545 strike disponibili. Il modulo seleziona 50 strike per ciascuna scadenza se disponibili. Test di struttura e calcolo; nessuna verifica di corrispondenza con il grafico Overcharts/Serafini ancora eseguita.
