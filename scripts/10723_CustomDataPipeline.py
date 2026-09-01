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

import requests

API_URL = "https://jsonplaceholder.typicode.com/todos"

data = []

# Example of a record
# Record = {"id": 1, "userId": 1, "title": "delectus aut autem"}

def get_all():
    response = requests.get(f"{API_URL}")
    return response.json()
    
def get_page(n):
    response = requests.get(f"{API_URL}?page={n}")
    return response.json()
    
def get_all_pages():
    all_data = []
    page = 1
    while True:
        page_data = get_page(page)
        if not page_data:
            break
        all_data.extend(page_data)
        page += 1
    return all_data
    
def get_incremental():
    from datetime import datetime
    bookmark = pq.get_state()
    if not bookmark:
        bookmark = "1900-01-01T00:00:00"
    
    response = requests.get(
        f"{API_URL}?after={bookmark}"
        )
    source_records = response.json()
    
    for source_record in source_records:
        source_record_timestamp = datetime.strptime(source_record["timestamp_last_update"], "%Y-%m-%dT%H:%M:%SZ")
        bookmark_timestamp = datetime.strptime(bookmark, "%Y-%m-%dT%H:%M:%SZ")
        if source_record_timestamp > bookmark_timestamp:
            bookmark = source_record["timestamp_last_update"]
    
    pq.set_state(bookmark)
    
    return source_records 
        
data = get_all()
data = get_page(2)
data = get_all_pages()
data = get_incremental()

# More info: https://help.peliqan.io/low-code-python-data-apps/writing-data-to-tables
dw = pq.dbconnect(pq.DW_NAME)
result = dw.write('CustomDataPipeline', 'CustomData', data, pk="id")
st.json(result)
jn