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

my_id = next(a["id"] for a in pq.list_scripts() if a["name"] == "Transform - Clean & Enrich")
incoming_state = pq.get_interface_state(my_id) or {}
print("Transform - Clean & Enrich: received upstream state:")
print(incoming_state)

print("Transform - Clean & Enrich: starting transformation pipeline...")
steps = ["deduplicating records", "normalizing addresses", "enriching with geo data", "computing derived fields", "applying business rules"]
for i, step in enumerate(steps, 1):
    print(f"Transform - Clean & Enrich: step {i}/{len(steps)} - {step}...")
    time.sleep(3)

incoming_state["transform_completed"] = True
pq.set_interface_state(my_id, incoming_state)

print("Transform - Clean & Enrich: done")