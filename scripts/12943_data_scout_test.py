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

import base64
import json


def _decode_jwt_claims(token: str) -> dict:
    """Best-effort read of a JWT payload, no signature check - same trick
    peliqan-datascout's _decode_access_token_claims uses, since Peliqan's
    access token carries the logged-in username as a claim but the SDK
    object itself exposes no email/username field. Never raises."""
    parts = (token or "").split(".")
    if len(parts) != 3:
        return {}
    try:
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


# Access is scoped per logged-in Peliqan account, read from pq.JWT.
# Unknown/undecodable user -> restricted (fail-safe).
ACCESS_BY_USERNAME = {
    "lucas@peliqan.io": "full",
    "dstest@peliqan.io": "restricted",
}
CURRENT_USERNAME = _decode_jwt_claims(pq.JWT).get("username")
ACCESS_LEVEL = ACCESS_BY_USERNAME.get(CURRENT_USERNAME, "restricted")
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
