if 'RUN_CONTEXT' in globals():  # Running on Peliqan
    RUN_ENV = 'peliqan'
else:  # Running outside of Peliqan
    RUN_ENV = 'local'
    from peliqan import Peliqan
    import streamlit as st
    import os
    api_key = os.getenv("PELIQAN_API_KEY")
    if not api_key:
        st.error("PELIQAN_API_KEY environment variable is not set.")
        st.stop()
    interface_id = os.getenv("PELIQAN_INTERFACE_ID", 0)
    pq = Peliqan(api_key)
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx() is not None:
            RUN_CONTEXT = "interactive"
    except Exception:
        RUN_CONTEXT = "background"

# Access is scoped per Peliqan account via interface_id, since the same
# source file is deployed separately to each account (no per-account secret
# needed - interface_id is injected into globals by the platform, and by the
# local dev shim above). Unknown interface_id -> restricted (fail-safe).
ACCESS_BY_INTERFACE_ID = {
    12943: "full",  # lucas@peliqan.io
    # TODO: fill in with the interface_id this script gets once pushed
    # to the dstest@peliqan.io account.
    0: "restricted",  # dstest@peliqan.io
}
ACCESS_LEVEL = ACCESS_BY_INTERFACE_ID.get(interface_id, "restricted")
assert ACCESS_LEVEL in ("full", "restricted")

import pandas as pd

st.title("My Table Data")

# connect to the data warehouse and fetch a table
dbconn = pq.dbconnect(pq.DW_NAME)
rows = dbconn.fetch(pq.DW_NAME, 'public', 'pg_stat_kcache')
df = pd.DataFrame(rows)

# Everyone gets the summary charts.
st.subheader("Summary")
numeric_cols = df.select_dtypes("number").columns.tolist()
if numeric_cols:
    st.bar_chart(df[numeric_cols])
else:
    st.text("No numeric columns to chart.")

# Only full access sees the underlying raw rows.
if ACCESS_LEVEL == "full":
    st.subheader("Raw data")
    st.dataframe(df)
else:
    st.caption("Raw data view is restricted for this account.")
