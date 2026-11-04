# cassa-pomigliano1

App Streamlit per aiutare i membri del gruppo scout Pomigliano 1 a compilare il bilancio annuale, da riportare poi su bilancio.agesci.it.

## Avvio

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Database

In `.streamlit/secrets.toml` serve `password`. Se è presente anche `database_url` l'app usa Neon (Postgres), altrimenti `bilancio.db` (SQLite). In locale lascia solo `password`, così le prove non finiscono sui dati veri. I Secrets di Streamlit Cloud contengono entrambe le chiavi.
