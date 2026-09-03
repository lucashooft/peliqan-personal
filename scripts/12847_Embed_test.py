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

import hmac
import hashlib
import base64
import json
import datetime
import streamlit.components.v1 as components

st.set_page_config(layout="wide")
st.title("App Hub")

# ponytail: embed secrets committed to source (git-tracked, CI-deployed per this
# repo's CLAUDE.md) instead of a runtime store; upgrade to a DB-backed or
# platform secret store if more than a couple of apps need embedding, or if
# this repo's access ever widens beyond trusted maintainers.
APP_EMBED_CONFIG = {
    12846: {
        "embed_url": "https://app.eu.peliqan.io/apps/UmJ5N1RUa212UlA5SDlVZjU2Y1AxMTZjTlhDMkJGVFRRWGZ4Z1lQYklqZU1RR1JUdkFXZjBRd0Rya2RTMlB1Vg==/",
        "secret_key": "zfOTay78eclh7gRzur2wJmrMIJzgzY68pA4RFjYpHFcBnhZFdfav742SAlfEA99n",
    },
}

@st.cache_data(ttl=300)
def get_app_list():
    return pq.list_scripts()

def build_session_token(app_id, secret_key):
    timestamp = datetime.datetime.now(datetime.timezone.utc).timestamp()
    message = f"{app_id}_{timestamp}"
    digest = hmac.new(secret_key.encode(), message.encode(), hashlib.sha1).digest()
    token = {"digest": base64.b64encode(digest).decode(), "timestamp": timestamp}
    return base64.b64encode(json.dumps(token).encode()).decode()

apps = get_app_list()

if "selected_app_id" not in st.session_state:
    st.session_state["selected_app_id"] = None

st.subheader("Apps")
cols = st.columns(4)
for i, app in enumerate(apps):
    with cols[i % 4]:
        is_selected = st.session_state["selected_app_id"] == app["id"]
        if st.button(app["name"], key=f"open_{app['id']}", use_container_width=True,
                     type="primary" if is_selected else "secondary"):
            st.session_state["selected_app_id"] = app["id"]
            st.rerun()

st.divider()

selected_id = st.session_state["selected_app_id"]
if selected_id is None:
    st.info("Click an app above to open it here.")
else:
    app = next((a for a in apps if a["id"] == selected_id), None)
    st.subheader(app["name"] if app else f"App {selected_id}")
    config = APP_EMBED_CONFIG.get(selected_id)

    if config:
        session_token = build_session_token(selected_id, config["secret_key"])
        separator = "&" if "?" in config["embed_url"] else "?"
        full_url = f"{config['embed_url']}{separator}embed=true&session_token={session_token}"
        components.iframe(full_url, height=800, scrolling=True)
    else:
        st.warning(f"App {selected_id} isn't embeddable yet: add it to APP_EMBED_CONFIG in this script.")
