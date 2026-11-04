import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from psycopg_pool import ConnectionPool

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

AMMINISTRATORE = "Antonio"
GESTIONE_PERMESSI = "Gestione permessi"
PERMESSI_INIZIALI = {
    "Antonio": CASSE,
    "Elena": CASSE,
    "Nicola": CASSE,
    "Lucia": ["Cassa Lupetti"],
    "Luca": ["Cassa Lupetti"],
    "Pina M.": ["Cassa Lupetti"],
    "Pina E.": ["Cassa Lupetti"],
    "Laura": ["Cassa Reparto"],
    "Enrico": ["Cassa Reparto"],
    "Raffaele": ["Cassa Reparto"],
    "Roberta": ["Cassa Reparto"],
    "Simone": ["Cassa Clan"],
    "Filomena": ["Cassa Clan"],
    "Rosaria": ["Cassa Clan"],
    "Giuseppe": ["Cassa Clan"],
}

class ConnessioneSQLite:
    """Accetta le stesse query di psycopg (segnaposto %s)."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, parametri=()):
        return self.conn.execute(sql.replace("%s", "?"), parametri)

    def cursor(self):
        return self

    def executemany(self, sql, righe):
        return self.conn.executemany(sql.replace("%s", "?"), righe)


class PoolSQLite:
    """Stessa interfaccia di ConnectionPool, per lavorare in locale su bilancio.db."""

    @contextmanager
    def connection(self):
        conn = sqlite3.connect("bilancio.db")
        try:
            with conn:
                yield ConnessioneSQLite(conn)
        finally:
            conn.close()


@st.cache_resource
def crea_pool():
    """Neon se nei secrets c'è database_url, altrimenti bilancio.db. Le tabelle vengono create una volta sola."""
    if "database_url" in st.secrets:
        tipo_id = "SERIAL"
        pool = ConnectionPool(
            st.secrets["database_url"],
            min_size=0,
            max_size=4,
            check=ConnectionPool.check_connection,  # Neon chiude le connessioni inattive
            kwargs={"prepare_threshold": None},  # richiesto dal connection pooling di Neon
        )
    else:
        tipo_id = "INTEGER"
        pool = PoolSQLite()
    with pool.connection() as conn:
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS movimenti (
                id {tipo_id} PRIMARY KEY,
                cassa TEXT NOT NULL,
                categoria TEXT NOT NULL,
                data TEXT NOT NULL,
                tipo TEXT NOT NULL CHECK (tipo IN ('Entrata', 'Uscita')),
                importo NUMERIC(12, 2) NOT NULL CHECK (importo > 0),
                evento TEXT NOT NULL DEFAULT '',
                collegato_id INTEGER,  -- per i giroconti: id dell'operazione corrispondente nell'altra cassa
                creato_da TEXT,
                creato_il TEXT,
                modificato_da TEXT,
                modificato_il TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS permessi (
                utente TEXT NOT NULL,
                cassa TEXT NOT NULL,
                PRIMARY KEY (utente, cassa)
            )"""
        )
        # Solo al primo avvio: poi i permessi si cambiano dall'app. L'amministratore non è modificabile,
        # quindi la tabella non resta mai vuota.
        if conn.execute("SELECT 1 FROM permessi LIMIT 1").fetchone() is None:
            conn.cursor().executemany(
                "INSERT INTO permessi (utente, cassa) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                [(nome, c) for nome, casse in PERMESSI_INIZIALI.items() for c in casse],
            )
    return pool


pool = crea_pool()

if "utente" not in st.session_state:
    st.title("Accesso")
    with st.form("login"):
        utente = st.selectbox("Utente", sorted(PERMESSI_INIZIALI), index=None, placeholder="Seleziona il tuo nome")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Entra", type="primary"):
            if utente is not None and password == st.secrets["password"]:
                st.session_state["utente"] = utente
                st.rerun()
            st.error("Utente o password non corretti")
    st.stop()

utente = st.session_state["utente"]
st.sidebar.write(f"Utente: **{utente}**")
if st.sidebar.button("Esci"):
    del st.session_state["utente"]
    st.rerun()

with pool.connection() as conn:
    permessi = set(conn.execute("SELECT utente, cassa FROM permessi").fetchall())
voci = [c for c in CASSE if (utente, c) in permessi]
if utente == AMMINISTRATORE:
    voci.append(GESTIONE_PERMESSI)
if not voci:
    st.info(f"Non hai accesso a nessuna cassa. Chiedi ad {AMMINISTRATORE} di abilitarti.")
    st.stop()

cassa = st.sidebar.radio("Cassa", voci)

st.title(cassa)

if cassa == GESTIONE_PERMESSI:
    altri_utenti = [nome for nome in sorted(PERMESSI_INIZIALI) if nome != AMMINISTRATORE]
    tabella = pd.DataFrame({c: [(nome, c) in permessi for nome in altri_utenti] for c in CASSE}, index=altri_utenti)
    tabella = st.data_editor(tabella)
    if st.button("Salva permessi", type="primary"):
        with pool.connection() as conn:
            conn.execute("DELETE FROM permessi WHERE utente != %s", (AMMINISTRATORE,))
            conn.cursor().executemany(
                "INSERT INTO permessi (utente, cassa) VALUES (%s, %s)",
                [(nome, c) for nome in altri_utenti for c in CASSE if tabella.loc[nome, c]],
            )
        st.success("Permessi salvati")
    st.stop()


TIPI = ["Entrata", "Uscita"]
AIUTO_EVENTO = "Specificare attività relativa alla spesa (bivacco, uscita, riunione, altro, etc...)"


def form_operazione(cassa, movimento=None):
    """Campi dell'operazione: vuoti per una nuova, precompilati se si modifica `movimento`."""
    st.caption(cassa)
    m = movimento
    if m is not None:
        # I valori mancanti possono arrivare come None o NaN a seconda della versione di pandas
        quando = lambda iso: datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M") if pd.notna(iso) else "?"
        autore = m["creato_da"] if pd.notna(m["creato_da"]) else "?"
        st.caption(
            f"Creata da {autore} il {quando(m['creato_il'])}"
            + (f" · Ultima modifica di {m['modificato_da']} il {quando(m['modificato_il'])}" if pd.notna(m["modificato_da"]) else "")
        )
    categoria = st.selectbox(
        "Categoria",
        CATEGORIE,
        index=CATEGORIE.index(m["categoria"]) if m is not None else None,
        placeholder="Seleziona una categoria",
    )
    data_minima, data_massima = date(2026, 10, 1), date(2027, 9, 30)
    data_salvata = date.fromisoformat(m["data"]) if m is not None else None
    data = st.date_input(
        "Data operazione",
        # Una data salvata fuori intervallo farebbe andare in errore il campo: va scelta di nuovo
        value=data_salvata if data_salvata and data_minima <= data_salvata <= data_massima else None,
        min_value=data_minima,
        max_value=data_massima,
        format="DD/MM/YYYY",
    )
    tipo = st.radio("Tipo", TIPI, index=TIPI.index(m["tipo"]) if m is not None else None, horizontal=True)
    collegato = int(m["collegato_id"]) if m is not None and pd.notna(m["collegato_id"]) else None
    altra_cassa = None
    if categoria == "Giroconto":
        altre_casse = [c for c in CASSE if c != cassa]
        cassa_collegata = None
        if collegato is not None:
            with pool.connection() as conn:
                cassa_collegata = conn.execute("SELECT cassa FROM movimenti WHERE id = %s", (collegato,)).fetchone()[0]
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
    evento = st.text_input("Descrizione entrata/spesa", value=m["evento"] if m is not None else "", help=AIUTO_EVENTO)
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
            st.error("Compila Categoria, Data operazione, Tipo, Importo e Descrizione entrata/spesa")
        elif categoria == "Giroconto" and altra_cassa is None:
            st.error("Seleziona la cassa del giroconto")
        else:
            valori = (categoria, data.isoformat(), tipo, importo, evento.strip())
            firma = (utente, datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M"))
            with pool.connection() as conn:
                if m is None:
                    id_movimento = conn.execute(
                        "INSERT INTO movimenti (categoria, data, tipo, importo, evento, cassa, creato_da, creato_il)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                        (*valori, cassa, *firma),
                    ).fetchone()[0]
                else:
                    id_movimento = int(m.name)
                    conn.execute(
                        "UPDATE movimenti SET categoria = %s, data = %s, tipo = %s, importo = %s, evento = %s,"
                        " modificato_da = %s, modificato_il = %s WHERE id = %s",
                        (*valori, *firma, id_movimento),
                    )
                if categoria == "Giroconto":
                    # Operazione speculare nell'altra cassa: stessi dati, tipo opposto
                    speculare = (categoria, data.isoformat(), TIPI[1 - TIPI.index(tipo)], importo, evento.strip(), altra_cassa)
                    if collegato is None:
                        collegato = conn.execute(
                            "INSERT INTO movimenti (categoria, data, tipo, importo, evento, cassa, collegato_id,"
                            " creato_da, creato_il) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                            (*speculare, id_movimento, *firma),
                        ).fetchone()[0]
                        conn.execute("UPDATE movimenti SET collegato_id = %s WHERE id = %s", (collegato, id_movimento))
                    else:
                        conn.execute(
                            "UPDATE movimenti SET categoria = %s, data = %s, tipo = %s, importo = %s, evento = %s, cassa = %s,"
                            " modificato_da = %s, modificato_il = %s WHERE id = %s",
                            (*speculare, *firma, collegato),
                        )
                elif collegato is not None:
                    # Non è più un giroconto: l'operazione speculare non serve più
                    conn.execute("DELETE FROM movimenti WHERE id = %s", (collegato,))
                    conn.execute("UPDATE movimenti SET collegato_id = NULL WHERE id = %s", (id_movimento,))
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
        with pool.connection() as conn:
            conn.execute(
                "DELETE FROM movimenti WHERE id = %s OR id = %s",
                (int(movimento.name), int(movimento["collegato_id"]) if giroconto else None),
            )
        st.rerun()


with pool.connection() as conn:
    risultato = conn.execute(
        "SELECT id, data, tipo, categoria, evento, CAST(importo AS DOUBLE PRECISION) AS importo, collegato_id,"
        " creato_da, creato_il, modificato_da, modificato_il"
        " FROM movimenti WHERE cassa = %s ORDER BY data, id",
        (cassa,),
    )
    movimenti = pd.DataFrame(risultato.fetchall(), columns=[c[0] for c in risultato.description]).set_index("id")
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
        "evento": st.column_config.TextColumn("Descrizione entrata/spesa", help=AIUTO_EVENTO),
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
