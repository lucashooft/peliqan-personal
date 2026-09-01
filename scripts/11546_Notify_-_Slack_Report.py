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

my_id = next(a["id"] for a in pq.list_scripts() if a["name"] == "Notify - Slack Report")
incoming_state = pq.get_interface_state(my_id) or {}
print("Notify - Slack Report: received upstream state:")
print(incoming_state)

customers = incoming_state.get("validated_customers", 0)
orders = incoming_state.get("validated_orders", 0)
table = incoming_state.get("warehouse_table", "unknown")

print("Notify - Slack Report: composing summary message...")
time.sleep(1)
message = f"ETL run complete: {customers} customers, {orders} orders loaded into {table}"
print(f'Notify - Slack Report: posting to #data-pipeline -> "{message}"')
time.sleep(1)

print("Notify - Slack Report: done")