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

import time
import random

st.title("Extract - Orders")

with st.status("Connecting to source system...", expanded=True) as status:
    print("Extract - Orders: connecting to source system...")
    time.sleep(2)

    row_count = random.randint(3000, 5000)
    st.write(f"Found {row_count} order records")
    print(f"Extract - Orders: found {row_count} order records")

    progress = st.progress(0.0)
    for i in range(1, 5):
        st.write(f"Fetching batch {i}/4...")
        print(f"Extract - Orders: fetching batch {i}/4...")
        time.sleep(1)
        progress.progress(i / 4)

    status.update(label="Extraction complete", state="complete")

my_id = next(a["id"] for a in pq.list_scripts() if a["name"] == "Extract - Orders")
state = pq.get_interface_state(my_id) or {}
state["orders_extracted"] = row_count
state["orders_source"] = "billing_system"
pq.set_interface_state(my_id, state)

st.metric("Orders extracted", row_count)
st.success(f"Done — {row_count} rows staged from billing_system")
print(f"Extract - Orders: done, {row_count} rows staged")