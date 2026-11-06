# MARKET MAP — Archivio dei 4 Pine giornalieri (proposta non attiva)

**Stato: opt-in, disattivato.** Questa modifica non cambia i calcoli o le versioni degli esportatori Serafini.

Il codice si inserisce nel punto della Dashboard successivo alla generazione dei 4 Pine **e dopo l'eventuale aggiunta delle news macro**; salva su Google Drive i byte esatti del testo prodotto e il relativo SHA256. Non genera file per slot privi di dati validi.

## I 4 slot
- 07:30: 0DTE + Weekly
- 13:30: confronto 07:30 → 13:30 (se esiste il frame di studio)
- 15:50: confronto 07:30 → 15:50
- 20:30: confronto 15:50 → 20:30

## Folder
Utilizzare la cartella **SPX Pine Daily** creata con il medesimo OAuth client Google Drive (non la cartella creata inizialmente dall'integrazione ChatGPT), con permesso `drive.file`.

Gli script sono conservati in sottocartelle `YYYY-MM-DD`, con nome
`Serafini_NET_GAMMA_YYYY-MM-DD_HHMM_<sha12>.pine` e un file `*.metadata.json` contenente slot, fonte, timestamp di download, differenziale ES−SPX, SHA256 e ora di archiviazione.

Un nuovo Pine generato con input differenti NON sovrascrive quello precedente: viene conservato con un hash distinto. Questo preserva gli artefatti originali e permette controlli su revisioni e look-ahead.

## Configurazione Streamlit privata
Quando si decide di attivare la PR, aggiungere ai **Streamlit Secrets** della Dashboard (mai nel repository):
- `GOOGLE_DRIVE_CLIENT_ID` (stesso OAuth client di GitHub)
- `GOOGLE_DRIVE_CLIENT_SECRET`
- `GOOGLE_DRIVE_REFRESH_TOKEN`
- `MARKET_MAP_DRIVE_PINE_FOLDER_ID` (cartella creata dal programma autorizzato)
- `MARKET_MAP_PINE_ARCHIVE_ENABLED = true`

**Attenzione:** non incollare segreti su ChatGPT. Si richiedono test OAuth e accesso folder sul deployment Streamlit.

## Limite importante
Questa prima proposta **non garantisce quattro salvataggi senza visite alla Dashboard**: archivia i Pine quando la Dashboard viene eseguita e i quattro exporter vengono generati. L'archiviazione completamente autonoma, anche a dashboard chiusa, richiede un job separato alimentato dalle fonti point-in-time e dal basis ES−SPX congelato. Non ricostruire retroattivamente news o differenziali mancanti.

## Verifica prima di unire la PR
1. Eseguire l'archivio per un giorno con tutti e quattro gli slot verificati.
2. Confrontare i byte SHA256 con i quattro download manuali nello stesso momento.
3. Verificare mancati slot, ritardi, newsletter news e nessuna operazione retroattiva.
4. Assicurare la sicurezza dei secrets e l'assenza di effetti sulla Dashboard se Drive non risponde.
5. Valutare successivamente job schedulato indipendente se occorre completo funzionamento unattended.

Nessuna modifica al test MARKET MAP W45 né alle formule Serafini/Edge.
