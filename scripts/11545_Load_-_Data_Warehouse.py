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

my_id = next(a["id"] for a in pq.list_scripts() if a["name"] == "Load - Data Warehouse")
incoming_state = pq.get_interface_state(my_id) or {}
print("Load - Data Warehouse: received upstream state:")
print(incoming_state)

customers = incoming_state.get("validated_customers", 0)
orders = incoming_state.get("validated_orders", 0)

print(f"Load - Data Warehouse: loading {customers} customers into dim_customers...")
time.sleep(2)
print(f"Load - Data Warehouse: loading {orders} orders into fact_orders...")
time.sleep(2)
print("Load - Data Warehouse: refreshing materialized views...")
time.sleep(2)

incoming_state["load_completed"] = True
incoming_state["warehouse_table"] = "analytics.fact_orders"
pq.set_interface_state(my_id, incoming_state)

print("Load - Data Warehouse: done")