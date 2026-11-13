# Laboratori GEX 0DTE — specifica e stato (13 novembre 2026)

## Obiettivo

Studiare **se** la distribuzione gamma e le curve GEX modellate contengano informazioni
utili sull'ES successivo. Nessun segnale LONG/SHORT automatico e nessun risultato
predittivo certificato. NON modificare metodo Serafini né Edge esistenti.

## 1. REPLAY E CONFRONTO → Replay Curva teorica GEX Edge + ES

- File: `edge_curve_replay.py`; ingresso: modalità aggiuntiva in `app.py`.
- Replay 0DTE: per ogni slot, legge il compatto `replay/` e il sidecar
  `replay-edge/`, verifica origine, data, scadenza, ora e SHA-256 con
  `edge_replay_data.read_pair`.
- Ufficiali: usa solo file Cboe archiviati con log valido; applica il motore
  Edge indipendente `edge_auto.result_from_snapshot` alla stessa fotografia.
  Il caricamento è facoltativo (interruttore OFF), per limitare download ed egress.
- Ogni frame contiene la **propria curva** archiviata/ricalcolata: non sostituisce
  la curva con una traslazione what-if del prezzo.
- Prezzo ES: unica fonte storica scelta da `replay_prices.load_es_candles`;
  sole chiusure 5m precedenti al download; prezzo puntuale solo se recente
  (massimo 11 minuti). Nessun prezzo inventato o estrapolato.
- Differenziale ES−SPX: stessa base già confermata in `ANALISI NET GAMMA`;
  se assente il Replay sospende la conversione, non ne crea una propria.
- Due pannelli sincronizzati dal frame Play/Pausa: curva GEX in funzione del
  prezzo ES teorico e cronologia ES effettiva in funzione del tempo.
- Marker: estremi campionati della curva, gamma flip modellato, spot Cboe
  convertito (NON ES live), ES chiuso interpolato sulla curva per il display.
- L'asse della curva è costante su tutta la giornata per permettere il
  confronto visivo: la scala totale non è un'informazione ex ante.
- Timestamp ripetuti, qualità insufficiente, curve mancanti, versioni diverse
  e scadenza post 0DTE richiedono avvisi o esclusione.
- Non scarica alcun nuovo dato Cboe, non modifica i file archiviati.

## 2. GEX EDGE — CONFRONTO → LABORATORIO DI RICERCA — NET GAMMA vs GEX

- File: `research_gamma_three_models_ui.py`; avvio **OFF** in
  `edge_triptych.py`, contenitore indipendente.
- Richiede fotografia ufficiale Cboe ORIGINALE ancora presente in archivio;
  i replay compatti non bastano a ricalcolare il contributo gamma **solo OI**.
- Modello A: formula Serafini inalterata per i 25+25 strike.
- Modello B: **stessi 50 strike**, call OI×gamma − put OI×gamma, opzioni
  Cboe originali e riconciliazione della somma (OI+Volume)×gamma con A.
- Modello C: Edge GEX 0DTE su tutto il book modello (unità: USD/1% di
  movimento SPX, ipotesi dealer). Per grafico per strike visualizza solo gli
  strike in comune, senza assegnare zero a quelli assenti.
- Confronto giornaliero: risultati dei download ufficiali ordinati per
  timestamp; blocco su versione Edge non omogenea, qualità insufficiente o
  diverso insieme di strike nei confronti temporali.
- Ulteriore grafico di **gamma positivo sopra lo SPX iniziale fissato**:
  le due versioni Serafini e il GEX Edge per gli stessi 50 strike sono
  normalizzate rispetto al primo valore. Base zero o strike assenti = N/D.
- Le scale A/B e C sono diverse: nessuna somma o graduatoria diretta è lecita.
  Non si è ancora verificato un edge ES: servono eventi ex ante, prezzi
  successivi, benchmark, costi, controllo delle finestre sovrapposte,
  campione OOS.

## Isolamento e test

I file di calcolo Serafini `net_gamma.py`, GEX Edge `edge_auto.py`,
`gex_price_sensitivity_lab.py` e il precedente Replay `gamma_replay.py`
non sono stati modificati. L'integrazione comprende soltanto una nuova
modalità radio e un pannello opzionale dentro il confronto Edge.

- Test sintetici aggiunti: `test_research_gamma_three_models.py`:
  riconciliazione OI, rifiuto dati incoerenti, curva originale e controllo
  anti-lookahead sul prezzo ES.
- **Stato:** codice su GitHub `main`. Nessuna conferma di esecuzione in
  Streamlit Cloud o dei test Python nel suo ambiente: verificare sul deploy.
- Eventuali audit del 9-10 novembre non vanno reinterpretati ex post con
  nuovi filtri non congelati. Gli snapshot ufficiali possono essere rimossi
  dall'archivio notturno; non inventare backfill.
