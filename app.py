import sqlite3
from datetime import date

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Cassa Pomigliano 1", page_icon="⚜️")

CASSE = [
    "Cassa Contanti Gruppo",
    "Cassa Lupetti",
    "Cassa Reparto",
    "Cassa Clan",
    "Conto Corrente Gruppo",
]

CATEGORIE = [
    "Assicurazione extra",
    "Attività (uscite/bivacchi/campi/riunioni)",
    "Autofinanziamenti",
    "Censimenti",
    "Disavanzo anno precedente",
    "Gestione CC gruppo",
    "Gestione sede",
    "Giroconto",
    "Varie",
]

# Il dialog gira in un thread diverso da quello che ha aperto la connessione
conn = sqlite3.connect("bilancio.db", check_same_thread=False)
conn.execute(
    """CREATE TABLE IF NOT EXISTS movimenti (
        id INTEGER PRIMARY KEY,
        cassa TEXT NOT NULL,
        categoria TEXT NOT NULL,
        data TEXT NOT NULL,
        tipo TEXT NOT NULL CHECK (tipo IN ('Entrata', 'Uscita')),
        importo REAL NOT NULL CHECK (importo > 0),
        evento TEXT NOT NULL DEFAULT '',
        collegato_id INTEGER  -- per i giroconti: id dell'operazione corrispondente nell'altra cassa
    )"""
)
# Aggiunge le colonne ai database creati prima che esistessero
colonne = [col[1] for col in conn.execute("PRAGMA table_info(movimenti)")]
if "evento" not in colonne:
    conn.execute("ALTER TABLE movimenti ADD COLUMN evento TEXT NOT NULL DEFAULT ''")
if "collegato_id" not in colonne:
    conn.execute("ALTER TABLE movimenti ADD COLUMN collegato_id INTEGER")

cassa = st.sidebar.radio("Cassa", CASSE)

st.title(cassa)


TIPI = ["Entrata", "Uscita"]
AIUTO_EVENTO = "Specificare attività relativa alla spesa (bivacco, uscita, riunione, altro, etc...)"


def form_operazione(cassa, movimento=None):
    """Campi dell'operazione: vuoti per una nuova, precompilati se si modifica `movimento`."""
    st.caption(cassa)
    m = movimento
    categoria = st.selectbox(
        "Categoria",
        CATEGORIE,
        index=CATEGORIE.index(m["categoria"]) if m is not None else None,
        placeholder="Seleziona una categoria",
    )
    data = st.date_input(
        "Data operazione",
        value=date.fromisoformat(m["data"]) if m is not None else None,
        min_value=date(2026, 9, 1),
        max_value=date(2027, 9, 30),
        format="DD/MM/YYYY",
    )
    tipo = st.radio("Tipo", TIPI, index=TIPI.index(m["tipo"]) if m is not None else None, horizontal=True)
    collegato = int(m["collegato_id"]) if m is not None and pd.notna(m["collegato_id"]) else None
    altra_cassa = None
    if categoria == "Giroconto":
        altre_casse = [c for c in CASSE if c != cassa]
        cassa_collegata = None
        if collegato is not None:
            cassa_collegata = conn.execute("SELECT cassa FROM movimenti WHERE id = ?", (collegato,)).fetchone()[0]
        altra_cassa = st.selectbox(
            "Cassa di destinazione" if tipo == "Uscita" else "Cassa di provenienza",
            altre_casse,
            index=altre_casse.index(cassa_collegata) if cassa_collegata else None,
            placeholder="Seleziona una cassa",
            key="altra_cassa",  # chiave fissa: cambiando Tipo cambia l'etichetta ma la scelta resta
        )
    importo = st.number_input(
        "Importo (€)",
        value=m["importo"] if m is not None else None,
        min_value=0.01,
        step=0.01,
        format="%.2f",
    )
    evento = st.text_input("Evento", value=m["evento"] if m is not None else "", help=AIUTO_EVENTO)
    if categoria == "Giroconto":
        if tipo is None or importo is None or altra_cassa is None:
            st.caption("Compila Tipo, Importo e cassa per vedere l'anteprima del giroconto")
        else:
            da, a = (cassa, altra_cassa) if tipo == "Uscita" else (altra_cassa, cassa)
            st.info(
                f"**Anteprima giroconto**: € {importo:.2f} da **{da}** a **{a}**"
                + (f" il {data.strftime('%d/%m/%Y')}" if data else "")
                + f"\n- {da}: Uscita di € {importo:.2f}\n- {a}: Entrata di € {importo:.2f}"
            )
    if st.button("Salva", type="primary"):
        if categoria is None or data is None or tipo is None or importo is None or not evento.strip():
            st.error("Compila Categoria, Data operazione, Tipo, Importo ed Evento")
        elif categoria == "Giroconto" and altra_cassa is None:
            st.error("Seleziona la cassa del giroconto")
        else:
            valori = (categoria, data.isoformat(), tipo, importo, evento.strip())
            with conn:
                if m is None:
                    id_movimento = conn.execute(
                        "INSERT INTO movimenti (categoria, data, tipo, importo, evento, cassa) VALUES (?, ?, ?, ?, ?, ?)",
                        (*valori, cassa),
                    ).lastrowid
                else:
                    id_movimento = int(m.name)
                    conn.execute(
                        "UPDATE movimenti SET categoria = ?, data = ?, tipo = ?, importo = ?, evento = ? WHERE id = ?",
                        (*valori, id_movimento),
                    )
                if categoria == "Giroconto":
                    # Operazione speculare nell'altra cassa: stessi dati, tipo opposto
                    speculare = (categoria, data.isoformat(), TIPI[1 - TIPI.index(tipo)], importo, evento.strip(), altra_cassa)
                    if collegato is None:
                        collegato = conn.execute(
                            "INSERT INTO movimenti (categoria, data, tipo, importo, evento, cassa, collegato_id)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (*speculare, id_movimento),
                        ).lastrowid
                        conn.execute("UPDATE movimenti SET collegato_id = ? WHERE id = ?", (collegato, id_movimento))
                    else:
                        conn.execute(
                            "UPDATE movimenti SET categoria = ?, data = ?, tipo = ?, importo = ?, evento = ?, cassa = ?"
                            " WHERE id = ?",
                            (*speculare, collegato),
                        )
                elif collegato is not None:
                    # Non è più un giroconto: l'operazione speculare non serve più
                    conn.execute("DELETE FROM movimenti WHERE id = ?", (collegato,))
                    conn.execute("UPDATE movimenti SET collegato_id = NULL WHERE id = ?", (id_movimento,))
            st.rerun()


@st.dialog("Aggiungi operazione")
def aggiungi_operazione(cassa):
    form_operazione(cassa)


@st.dialog("Modifica operazione")
def modifica_operazione(cassa, movimento):
    form_operazione(cassa, movimento)
    st.divider()
    giroconto = pd.notna(movimento["collegato_id"])
    conferma = st.checkbox(
        "Confermo di voler eliminare questa operazione"
        + (" e quella collegata nell'altra cassa" if giroconto else "")
    )
    if st.button("Elimina operazione", disabled=not conferma):
        with conn:
            conn.execute(
                "DELETE FROM movimenti WHERE id = ? OR id = ?",
                (int(movimento.name), int(movimento["collegato_id"]) if giroconto else None),
            )
        st.rerun()


movimenti = pd.read_sql_query(
    "SELECT id, data, tipo, categoria, evento, importo, collegato_id FROM movimenti WHERE cassa = ? ORDER BY data",
    conn,
    params=(cassa,),
    index_col="id",
)
COLORI_TIPO = {
    "Entrata": "background-color: #d4edda; color: #155724",
    "Uscita": "background-color: #f8d7da; color: #721c24",
}
entrate = movimenti.loc[movimenti["tipo"] == "Entrata", "importo"].sum()
uscite = movimenti.loc[movimenti["tipo"] == "Uscita", "importo"].sum()
col_entrate, col_uscite, col_saldo = st.columns(3)
col_entrate.metric("Entrate", f"€ {entrate:.2f}")
col_uscite.metric("Uscite", f"€ {uscite:.2f}")
col_saldo.metric("Saldo", f"€ {entrate - uscite:.2f}")

if st.button("Aggiungi operazione"):
    aggiungi_operazione(cassa)

st.caption("Clicca su una riga per modificarla")
# Cambiare la key azzera la selezione, così la stessa riga si può ricliccare dopo aver chiuso la finestra
n_tabella = st.session_state.setdefault("n_tabella", 0)
selezione = st.dataframe(
    movimenti.style.apply(
        lambda riga: [COLORI_TIPO[riga["tipo"]] if col == "importo" else "" for col in riga.index],
        axis=1,
    )
    .format("€ {:.2f}", subset=["importo"])
    .format(lambda d: date.fromisoformat(d).strftime("%d/%m/%Y"), subset=["data"]),
    hide_index=True,
    column_order=["data", "evento", "categoria", "tipo", "importo"],
    column_config={
        "data": "Data",
        "evento": st.column_config.TextColumn("Evento", help=AIUTO_EVENTO),
        "categoria": "Categoria",
        "tipo": "Tipo",
        "importo": "Importo",
    },
    on_select="rerun",
    selection_mode="single-row",
    key=f"tabella_{n_tabella}",
)
if selezione.selection.rows:
    st.session_state["n_tabella"] += 1
    modifica_operazione(cassa, movimenti.iloc[selezione.selection.rows[0]])
