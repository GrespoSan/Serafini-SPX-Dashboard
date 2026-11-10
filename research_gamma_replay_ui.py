"""Analisi esplorativa read-only dei frame gia' caricati dal Replay."""
import csv
import io
import math

import streamlit as st

from research_gamma_distribution import compare_profiles


def render(available, kind, day):
    with st.expander("LABORATORIO · Evoluzione NET GAMMA (sperimentale)", expanded=False):
        st.caption("Solo ricerca: nessun segnale trading, nessuna modifica a Serafini. "
                   "Confronti consecutivi ordinati per download Roma, stessa scadenza.")
        if len(available) < 2:
            st.info("Servono almeno due snapshot validi.")
            return
        mode = st.radio("Universo di analisi", ("Originale Serafini · 50 strike",
                                               "Strike visuali archiviati"),
                        horizontal=True, key=f"research_mode_{day}_{kind}")
        key = "rows" if mode.startswith("Originale") else "visual_rows"
        # Reference spot fissato PRIMA del secondo frame: nessun prezzo futuro.
        results = []
        details = []
        for (slot0, f0), (slot1, f1) in zip(available, available[1:]):
            p0 = f0["profiles"][kind]
            p1 = f1["profiles"][kind]
            rows0 = p0.get(key) or []
            rows1 = p1.get(key) or []
            try:
                spot = float(f0["spot"])
                if not math.isfinite(spot) or spot <= 0:
                    spot = None
            except (TypeError, ValueError):
                spot = None
            try:
                result = compare_profiles(rows0, rows1, spot=spot)
            except (ValueError, TypeError) as exc:
                st.warning(f"{slot0} → {slot1}: dati non confrontabili ({exc})")
                continue
            if result["status"] == "N/D":
                continue
            # Timestamp identico non prova identita', ma richiede cautela.
            t0, t1 = f0.get("cboe_timestamp_original"), f1.get("cboe_timestamp_original")
            freshness = ("TIMESTAMP RIPETUTO" if t0 and t1 and t0 == t1
                         else "NON VERIFICABILE" if not t0 or not t1 else "TIMESTAMP DIVERSO")
            same_reference = (f0.get("reference_spx") == f1.get("reference_spx"))
            distinct_download = (f0.get("download_rome") != f1.get("download_rome"))
            eligible = (result["status"] == "OK"
                        and freshness == "TIMESTAMP DIVERSO"
                        and same_reference and distinct_download)
            row = {
                "Da": slot0, "A": slot1,
                "Download iniziale": f0.get("download_rome"),
                "Download finale": f1.get("download_rome"),
                "Origine iniziale": f0.get("origin"),
                "Origine finale": f1.get("origin"),
                "Freschezza": freshness,
                "Copertura": result["status"],
                "Riferimento SPX coerente": same_reference,
                "Confronto esplorativo idoneo": eligible,
                "Strike comuni": result["common_count"],
                "Strike prima": result["before_count"],
                "Strike dopo": result["after_count"],
                "SPX riferimento iniziale": spot,
                "Gamma positivo sopra SPX, var. %": result.get("positive_above_change_pct"),
                "Gamma negativo sotto SPX, var. %": result.get("negative_below_change_pct"),
                "Somma |gamma| prima": result["total_abs_before"],
                "Somma |gamma| dopo": result["total_abs_after"],
            }
            results.append(row)
            for strike in result["strikes"]:
                details.append({"Da": slot0, "A": slot1, **strike})
        if not results:
            st.info("Nessun confronto utilizzabile negli snapshot selezionati.")
            return
        st.dataframe(results, hide_index=True, width="stretch")
        # Persistenza su TRE frame: stessa soglia spot del primo frame e
        # stesso universo di strike in tutte e tre le osservazioni.
        # Non concatenare due variazioni con soglie spot differenti.
        streaks = []
        for i in range(len(available) - 2):
            (slot0, f0), (slot1, f1), (slot2, f2) = available[i:i + 3]
            frames = (f0, f1, f2)
            try:
                fixed_spot = float(f0["spot"])
                if not math.isfinite(fixed_spot) or fixed_spot <= 0:
                    continue
                values = []
                for frame in frames:
                    mapping = {}
                    for r in frame["profiles"][kind].get(key, []):
                        k, v = float(r["strike_spx"]), float(r["net_gamma"])
                        if k in mapping or not math.isfinite(k) or not math.isfinite(v):
                            raise ValueError("Dati strike invalidi")
                        mapping[k] = v
                    values.append(mapping)
                common = set(values[0]) & set(values[1]) & set(values[2])
                # Evita che il cambio di copertura produca false contrazioni.
                if not common or any(set(v) != common for v in values):
                    continue
                if any(not frame.get("cboe_timestamp_original") for frame in frames):
                    continue
                stamps = [frame["cboe_timestamp_original"] for frame in frames]
                if len(set(stamps)) != 3:
                    continue
                clocks = [frame.get("download_rome") for frame in frames]
                if any(not clock for clock in clocks) or len(set(clocks)) != 3:
                    continue
                if len({frame.get("reference_spx") for frame in frames}) != 1:
                    continue
                strengths = [
                    sum(max(v[k], 0.0) for k in common if k > fixed_spot)
                    for v in values
                ]
                if strengths[0] > strengths[1] > strengths[2]:
                    streaks.append(f"{slot0} → {slot1} → {slot2}")
            except (ValueError, TypeError, KeyError):
                continue
        st.caption("Persistenza: contrazione strettamente consecutiva del "
                   "gamma positivo sopra uno SPX FISSO (primo frame), "
                   "con gli stessi strike e tre timestamp Cboe distinti. "
                   f"Sequenze: {len(streaks)}. Solo descrizione, non segnale.")
        if streaks:
            st.write("; ".join(streaks))

        n_eligible = sum(1 for row in results if row["Confronto esplorativo idoneo"])
        st.caption(f"Confronti esplorativi idonei: {n_eligible}/{len(results)}. "
                   "Idoneo significa: stessa copertura, riferimento coerente, "
                   "download distinti e timestamp Cboe diversi. "
                   "NON certifica dati indipendenti o capacità predittiva.")
        st.caption("Le variazioni sopra/sotto il prezzo usano lo SPX del frame INIZIALE "
                   "come soglia fissa. Copertura parziale = strike mancanti; "
                   "non interpretarli come gamma zero. I timestamp ripetuti "
                   "non certificano la freschezza dei dati.")
        def csv_bytes(rows):
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=list(rows[0]), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            return out.getvalue().encode("utf-8-sig")
        st.download_button("Esporta confronti CSV", csv_bytes(results),
                           file_name=f"gamma_research_{day}_{kind}_summary.csv",
                           mime="text/csv", key=f"research_csv_summary_{day}_{kind}")
        st.download_button("Esporta strike CSV", csv_bytes(details),
                           file_name=f"gamma_research_{day}_{kind}_strikes.csv",
                           mime="text/csv", key=f"research_csv_strikes_{day}_{kind}")
        st.caption("Nessuna verifica predittiva ES eseguita: serviranno prezzi futuri "
                   "allineati temporalmente, costi e baseline. "
                   "Il NET GAMMA Serafini include volume cumulato.")
