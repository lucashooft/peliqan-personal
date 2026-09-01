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

# Here is some example Python code to get you started.
# Check the Data activation library section for useful code snippets and supported functions.

# The handler function is mandatory. This is the entry point for an API script's execution.

import json
from urllib.parse import parse_qs

def handler(request):
    # Get request details
    method = request['method']
    url = request['url']
    path = request['path']
    query_string = request['query_string']
    headers = request['headers']
    data = request['data']
    form = request['form']

    # Read querystring
    query_string_parsed = parse_qs(query_string)
    my_query_string_param = query_string_parsed["myparam"][0] if "myparam" in query_string_parsed else None

    # Read headers
    my_header = headers["Accept"] if "Accept" in headers else None

    # Read POST body (JSON)
    data_parsed = json.loads(data) if data else {}
    my_data_field = data_parsed["myfield"] if "myfield" in data_parsed else None

    # Read form fields (x-www-form-urlencoded post)
    my_form_field = form["myfield"] if "myfield" in form else None

    
    # Connect to the data warehouse
    dbconn = pq.dbconnect(pq.DW_NAME)

    # Fetch records from a table in the data warehouse
    rows = dbconn.fetch(pq.DW_NAME, 'schema_name', 'table_name')
    

    # Send a JSON response with a response code 200 (OK)
    return {'status': 'ok', 'rows': rows}, 200

    # Send a 429 (rate limit) response
    return "", 429

    # Send a response with response code and response headers
    return data, status_code, headers
    return "", 404, { "some_header": "some_value" }
