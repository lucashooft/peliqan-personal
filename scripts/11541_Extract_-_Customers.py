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

print("Extract - Customers: connecting to source system...")
time.sleep(2)

row_count = random.randint(800, 1200)
print(f"Extract - Customers: found {row_count} customer records")

for i in range(1, 4):
    print(f"Extract - Customers: fetching batch {i}/3...")
    time.sleep(1)

my_id = next(a["id"] for a in pq.list_scripts() if a["name"] == "Extract - Customers")
state = pq.get_interface_state(my_id) or {}
state["customers_extracted"] = row_count
state["customers_source"] = "crm_system"
pq.set_interface_state(my_id, state)

print(f"Extract - Customers: done, {row_count} rows staged")