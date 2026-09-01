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

my_id = next(a["id"] for a in pq.list_scripts() if a["name"] == "Validate - Combined Data")
incoming_state = pq.get_interface_state(my_id) or {}
print("Validate - Combined Data: received upstream state:")
print(incoming_state)

customers = incoming_state.get("customers_extracted", 0)
orders = incoming_state.get("orders_extracted", 0)

print(f"Validate - Combined Data: checking {customers} customers and {orders} orders...")
time.sleep(2)

print("Validate - Combined Data: checking referential integrity...")
time.sleep(2)

if random.random() < 0.4:
    print("Validate - Combined Data: found orphaned order records, failing validation")
    raise RuntimeError("Validation failed: orphaned orders detected without matching customer")

print("Validate - Combined Data: all checks passed")

incoming_state["validation_passed"] = True
incoming_state["validated_customers"] = customers
incoming_state["validated_orders"] = orders
pq.set_interface_state(my_id, incoming_state)

print("Validate - Combined Data: done")