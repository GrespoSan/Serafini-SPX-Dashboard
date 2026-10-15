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
