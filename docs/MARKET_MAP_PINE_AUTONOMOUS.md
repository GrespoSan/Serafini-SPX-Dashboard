# MARKET MAP — Pine giornalieri automatici, senza nuovi download CBOE

**Stato: proposta disattivata, nessuna modifica in produzione.**

## Obiettivo
Produzione autonoma fino a quattro Pine TradingView nelle ore successive ai rilevamenti 07:30, 13:30, 15:50 e 20:30 Roma. Il processo riusa i reader e gli exporter Serafini già disponibili in questo repository. **Non chiama CBOE**: scarica soltanto dati grezzi immutabili e log dal bucket privato Supabase, usando gli stessi metodi della Dashboard.

### File
- `market_map_pine_autogen.py` — generatore autonomo con controlli point-in-time, SHA256 e manifest di provenienza.
- `.github/workflows/market-map-pine-daily.yml` — cron UTC orario, con timezone Europe/Rome gestito nel programma.
- `ops/PineArchive.gs` — aggiunta facoltativa al progetto Google Apps Script esistente, separata dal raw SPX.
- `tests/test_market_map_pine_autogen.py` — test indipendenti senza rete.

## Condizioni necessarie
1. Su **Serafini-SPX-Dashboard**, GitHub Actions Secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `FINANCE_CALENDAR_API_KEY` (stessi valori privati della Dashboard, inseriti esclusivamente nel pannello GitHub).
2. Variabile repository `MARKET_MAP_PINE_UNATTENDED_ENABLED=true` **soltanto dopo test manuali**.
3. La catena deve essere già archiviata su Supabase. I log ufficiali definiscono data/ora di download e precedenza degli slot.
4. Il differenziale ES−SPX è calcolato con il modulo Yahoo della Dashboard, arrotondato come nella Dashboard e congelato in un artifact per la giornata. Può **differire da eventuali modifiche manuali** dell'utente nella UI: non chiamare mai questi output copie bit-per-bit della UI se non dopo un confronto SHA256.
5. Il calendario news deve rispondere correttamente con un'API key valida. Se non disponibile, nessun Pine viene emesso: WAIT_NEWS_SOURCE_UNVERIFIED.
6. Il programma esegue esclusivamente il **giorno corrente** e non genera Pine da snapshot futuri o troppo vecchi: finestra massima di 75 minuti dal download effettivo. Le finestre mancanti sono WAIT, mai ricostruzioni retroattive.

## Prove richieste prima dell'attivazione
- Verificare in GitHub Actions che le quattro situazioni siano prodotte durante una giornata con tutti gli slot disponibili.
- Confrontare output, basis, future news e timestamp con i pulsanti Pine della Dashboard; eventuali divergenze richiedono audit.
- Verificare un giorno con 13:30 mancante: non inventare il relativo confronto.
- Verificare un giorno festivo e un errore FinanceCalendar: nessun artefatto retroattivo.
- Verificare l'immutabilità giornaliera del basis fra run successivi e nessuna scrittura in Supabase.
- Rivedere ambito e retention dei GitHub artifacts (14 giorni) prima dell'uso per backtest.

## Versamento permanente su Drive
- La pipeline genera gli artifact `mm-pine-daily-<run id>` su GitHub, inizialmente con retention 14 giorni.
- Nel progetto Apps Script privato **MARKET MAP — Backup SPX**, aggiungere un nuovo file `PineArchive.gs` con il contenuto di `ops/PineArchive.gs`. Non sostituire `Codice.gs` del backup raw.
- Nelle proprietà dello script aggiungere `PINE_GITHUB_TOKEN` (nuovo PAT limitato al repository **Serafini-SPX-Dashboard**, Actions Read-only) e `PINE_FOLDER_ID=170dXWgu78Ow5rhKsoN-SklbNgXMRntXW` (cartella Drive creata con il programma autorizzato).
- Eseguire manualmente `pineCheckConnection`, quindi `pineBackupArtifacts` dopo che il primo Pine artifact sarà disponibile.
- Controllare Drive: sottocartelle per giorno, `.pine` e `.metadata.json` con hash uguale. Solo dopo impostare un **secondo trigger** ogni 2 ore per `pineBackupArtifacts`.
- Il token PAT scade secondo la sua data di scadenza e richiede rinnovo. L'app Apps Script non necessita del Refresh Token OAuth esterno.
- Il backup raw esistente rimane indipendente e non va interrotto.

## Limiti del progetto
- Gli output automatici non corrispondono necessariamente agli export manuali se il differenziale ES−SPX è stato corretto nella UI o il calendario è cambiato fra le letture.
- Nessuna affermazione di edge predittivo: questa procedura conserva input e codici per futuri test walk-forward, con dati mancanti registrati.
- Il servizio Supabase viene usato in lettura ma il Service Role può avere privilegi più ampi: il secret va limitato al repository privato; valutare una credenziale tecnica read-only dedicata.
- Nessuna rimozione dell'archivio esistente Serafini/Edge, nessuna formula gamma alterata e nessun nuovo download CBOE.
