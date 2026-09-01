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

import uuid
import string
import threading
import json
import datetime
import time

st.set_page_config(layout="wide")

if "chosenApps" not in st.session_state:
    st.session_state["chosenApps"] = []

if "toast_message" not in st.session_state:
    st.session_state["toast_message"] = None

# Needs to be in state because st.rerun() would remove the message
if st.session_state["toast_message"]:
    st.toast(st.session_state["toast_message"])
    st.session_state["toast_message"] = None

if "orchestration_running" not in st.session_state:
    st.session_state["orchestration_running"] = False
    
orchestration_running = st.session_state["orchestration_running"]

@st.cache_data(ttl=300)
def get_app_list():
    return pq.list_scripts()

@st.cache_data(ttl=300)
def list_saved_orchestrations_cached():
    return list_saved_orchestrations()

@st.cache_data(ttl=300)
def get_distinct_orchestration_names_from_runs_cached():
    return get_distinct_orchestration_names_from_runs()

@st.cache_data(ttl=300)
def get_run_summaries_cached(orchestration_name, limit=20):
    return get_run_summaries(orchestration_name, limit)

@st.cache_data
def get_run_blocks_cached(run_id):
    return get_run_blocks(run_id)

@st.cache_data(ttl=3600)
def ensure_block_outputs_table():
    try:
        dbconn = pq.dbconnect(pq.DW_NAME)
        dbconn.fetch(pq.DW_NAME, query='SELECT 1 FROM "Orchestrator".block_outputs LIMIT 1')
    except Exception:
        dw = pq.dbconnect(pq.DW_NAME)
        dw.write("Orchestrator", "block_outputs", [{
            "run_id": "__bootstrap__",
            "block_id": "__bootstrap__",
            "letter": "",
            "script_name": "",
            "input_state": "{}",
            "output_state": "{}",
            "created_at": datetime.datetime.utcnow().isoformat(),
        }])
    return True

appList = get_app_list()
appDict = {app['name']: app for app in appList}
ensure_block_outputs_table()

WEEKDAY_OPTIONS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
INTERVAL_OPTIONS = {
    "Every minute": 60, "Every 5 minutes": 300, "Every 15 minutes": 900,
    "Every 30 minutes": 1800, "Every hour": 3600, "Every 6 hours": 21600,
    "Every 12 hours": 43200, "Every 24 hours": 86400,
}

MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 5
BLOCK_TIMEOUT_SECONDS = 600
CONTENTION_MAX_WAIT_SECONDS = 300
UNSAVED_ORCHESTRATION_NAME = "(unsaved orchestration)"


HEADLESS_RUNNER_TEMPLATE = '''
import datetime
import json
import uuid
import time
import threading

ORCHESTRATION_NAME = "__ORCHESTRATION_NAME__"
MAX_RETRIES = __MAX_RETRIES__
RETRY_DELAY_SECONDS = __RETRY_DELAY_SECONDS__
BLOCK_TIMEOUT_SECONDS = __BLOCK_TIMEOUT_SECONDS__
CONTENTION_MAX_WAIT_SECONDS = __CONTENTION_MAX_WAIT_SECONDS__
SHARE_STATE = __SHARE_STATE__

def is_already_running_error(exc):
    return "ERROR_INTERFACE_ALREADY_RUNNING" in str(exc)

script_locks = {}
script_locks_guard = threading.Lock()

def get_lock_for_script(script_id):
    with script_locks_guard:
        if script_id not in script_locks:
            script_locks[script_id] = threading.Lock()
        return script_locks[script_id]

def store_block_output(run_id, block_id, letter, script_name, input_state, output_state):
    try:
        dw = pq.dbconnect(pq.DW_NAME)
        dw.write("Orchestrator", "block_outputs", [{
            "run_id": run_id,
            "block_id": block_id,
            "letter": letter,
            "script_name": script_name,
            "input_state": json.dumps(input_state or {}),
            "output_state": json.dumps(output_state or {}),
            "created_at": datetime.datetime.utcnow().isoformat(),
        }])
    except Exception:
        pass

def get_block_output(run_id, block_id):
    try:
        dbconn = pq.dbconnect(pq.DW_NAME)
        safe_run = run_id.replace("'", "''")
        safe_block = block_id.replace("'", "''")
        rows = dbconn.fetch(
            pq.DW_NAME,
            query=f\'\'\'SELECT output_state FROM "Orchestrator".block_outputs
                      WHERE run_id = \\'{safe_run}\\' AND block_id = \\'{safe_block}\\' \'\'\'
        )
        if not rows:
            return {}
        return json.loads(rows[0]["output_state"] or "{}")
    except Exception:
        return {}

def get_merged_input_state(run_id, depends_on_block_ids, ignore_conflicts=False):
    merged = {}
    conflicts = []
    for dep_block_id in depends_on_block_ids:
        dep_output = get_block_output(run_id, dep_block_id)
        for k, v in dep_output.items():
            if k in merged and merged[k] != v and not ignore_conflicts:
                conflicts.append(k)
            merged[k] = v
    if conflicts:
        raise ValueError(f"State merge conflict: key(s) {sorted(set(conflicts))} were written by more than one dependency with different values.")
    return merged

def run_block(app, shared, bid):
    script_id = app["id"]
    holder = {"actually_started": False, "started_wall_time": None}

    def start_it():
        wait = 2
        waited = 0
        while True:
            try:
                holder["actually_started"] = True
                holder["started_wall_time"] = time.time()
                holder["result"] = pq.run_script(script_id=script_id)
                return
            except Exception as e:
                if not is_already_running_error(e) or waited >= CONTENTION_MAX_WAIT_SECONDS:
                    holder["result"] = {"status": "ERROR", "exit_code": -1, "logs": f"Exception while starting: {e}"}
                    return
                holder["actually_started"] = False
                holder["started_wall_time"] = None
                shared["logs"][bid] = f"Waiting for another run of this script to finish (contention backoff, next retry in {wait}s, waited {waited}s so far)..."
                time.sleep(wait)
                waited += wait
                wait = min(wait * 2, 30)

    thread = threading.Thread(target=start_it, daemon=True)
    thread.start()
    start_time = time.time()
    claimed_run_id = None

    def find_our_run_id():
        nonlocal claimed_run_id
        if claimed_run_id is not None:
            return claimed_run_id
        if not holder.get("actually_started"):
            return None
        my_started_wall_time = holder.get("started_wall_time")
        if my_started_wall_time is None:
            return None
        try:
            runs = pq.get_script_runs(script_id=script_id, page=1, per_page=10)
            data = runs.get("data", []) if runs else []
        except Exception:
            return None

        claimed_ids = shared.setdefault("claimed_run_ids", set())
        best, best_diff = None, None
        for r in data:
            if r.get("status") != "RUNNING" or r.get("id") in claimed_ids:
                continue
            ts_str = r.get("timestamp_start")
            if not ts_str:
                continue
            try:
                run_started = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            diff = abs(run_started - my_started_wall_time)
            if diff < 10 and (best_diff is None or diff < best_diff):
                best, best_diff = r["id"], diff

        if best is not None:
            claimed_ids.add(best)
            claimed_run_id = best
        return best

    def give_up_and_stop():
        if holder.get("actually_started"):
            try:
                confirmed_id = find_our_run_id()
                if confirmed_id is not None:
                    pq.stop_script_run(confirmed_id)
            except Exception:
                pass
            return {"status": "TIMEOUT", "exit_code": -1,
                    "logs": f"Exceeded {BLOCK_TIMEOUT_SECONDS}s timeout. Requested the remote run to stop."}
        else:
            return {"status": "TIMEOUT", "exit_code": -1,
                    "logs": f"Exceeded {BLOCK_TIMEOUT_SECONDS}s timeout while still waiting for another run of this script to finish. Gave up without touching it."}

    run_id_inner = None
    while run_id_inner is None and thread.is_alive():
        if time.time() - start_time > BLOCK_TIMEOUT_SECONDS:
            return give_up_and_stop()
        run_id_inner = find_our_run_id()
        time.sleep(0.5)

    while thread.is_alive():
        if time.time() - start_time > BLOCK_TIMEOUT_SECONDS:
            result = give_up_and_stop()
            result["logs"] = shared["logs"].get(bid, "") + "\\n\\n" + result["logs"]
            return result
        try:
            log_response = pq.get_script_run_logs(run_id_inner) if run_id_inner else None
            shared["logs"][bid] = log_response.get("logs", "") if log_response else ""
        except Exception:
            shared["logs"][bid] = "(log fetch failed, retrying...)"
        time.sleep(0.5)

    thread.join()
    result = holder.get("result") or {"status": "UNKNOWN", "exit_code": -1, "logs": "No result was returned."}
    shared["logs"][bid] = result.get("logs", "")
    return result

def run_block_with_retries(app, shared, bid):
    attempt = 1
    max_attempts = MAX_RETRIES + 1
    attempt_logs = []

    while True:
        shared["attempts"][bid] = (attempt, max_attempts)
        result = run_block(app, shared, bid)
        success = result.get("status") == "COMPLETED" and result.get("exit_code") == 0
        outcome = "DONE" if success else f"FAILED ({result.get('status')})"
        attempt_logs.append(f"--- Attempt {attempt}/{max_attempts}: {outcome} ---\\n{result.get('logs', '')}")

        if success or attempt >= max_attempts:
            return success, "\\n\\n".join(attempt_logs)

        attempt += 1
        time.sleep(RETRY_DELAY_SECONDS)

def has_cycle(orchestration):
    done = set()
    while True:
        ready = [b for b in orchestration if b["block_id"] not in done
                 and all(dep in done for dep in b["depends_on"])]
        if not ready:
            break
        for b in ready:
            done.add(b["block_id"])
    return len(done) != len(orchestration)

def load_orchestration_by_name(name):
    dbconn = pq.dbconnect(pq.DW_NAME)
    safe_name = name.replace("'", "''")
    rows = dbconn.fetch(
        pq.DW_NAME,
        query=f"SELECT definition FROM \\"Orchestrator\\".orchestrations WHERE name = '{safe_name}'"
    )
    if not rows:
        return None

    saved_blocks = json.loads(rows[0]["definition"])
    app_by_id = {app["id"]: app for app in pq.list_scripts()}

    orchestration = []
    for b in saved_blocks:
        app = app_by_id.get(b["script_id"])
        if app is None:
            continue
        orchestration.append({
            "block_id": b["block_id"],
            "letter": b["letter"],
            "app": app,
            "depends_on": b["depends_on"],
        })
    return orchestration

def log_block(run_id, block, status, logs_text, started_at, ended_at):
    dw = pq.dbconnect(pq.DW_NAME)
    dw.write("Orchestrator", "orchestration_runs", [{
        "run_id": run_id,
        "orchestration_name": ORCHESTRATION_NAME,
        "block_id": block["block_id"],
        "block_letter": block["letter"],
        "script_name": block["app"]["name"],
        "status": status,
        "started_at": started_at,
        "ended_at": ended_at,
        "logs": logs_text,
    }])

def orchestration_worker(orchestration, run_id, shared):
    status = shared["status"]
    started_bids = set()
    active_threads = {}

    def start_block(b):
        bid = b["block_id"]
        status[bid] = "running"
        started_at = datetime.datetime.utcnow().isoformat()

        def run_one():
            script_id = b["app"]["id"]
            lock = get_lock_for_script(script_id)

            if not SHARE_STATE:
                with lock:
                    reset_ok = False
                    for _ in range(3):
                        try:
                            pq.set_interface_state(script_id, None)
                            if not pq.get_interface_state(script_id):
                                reset_ok = True
                                break
                        except Exception:
                            pass
                        time.sleep(1)
                    if not reset_ok:
                        ended_at = datetime.datetime.utcnow().isoformat()
                        combined_logs = "Could not confirm the script's state was reset before running (previous run's state may still be present). Block not run."
                        shared["results"][bid] = {
                            "letter": b["letter"], "name": b["app"]["name"],
                            "status": "failed", "logs": combined_logs,
                        }
                        store_block_output(run_id, bid, b["letter"], b["app"]["name"], {}, {})
                        status[bid] = "failed"
                        log_block(run_id, b, "failed", combined_logs, started_at, ended_at)
                        return
                    success, combined_logs = run_block_with_retries(b["app"], shared, bid)
                ended_at = datetime.datetime.utcnow().isoformat()
                final_status = "done" if success else "failed"
                shared["results"][bid] = {
                    "letter": b["letter"], "name": b["app"]["name"],
                    "status": final_status, "logs": combined_logs,
                }
                status[bid] = final_status
                try:
                    log_block(run_id, b, final_status, combined_logs, started_at, ended_at)
                except Exception as e:
                    shared["results"][bid]["logs"] += f"\\n\\n(log_block failed: {e})"
                return

            try:
                input_state = get_merged_input_state(run_id, b["depends_on"])
            except ValueError as e:
                ended_at = datetime.datetime.utcnow().isoformat()
                combined_logs = f"State merge conflict, block not run: {e}"
                shared["results"][bid] = {
                    "letter": b["letter"], "name": b["app"]["name"],
                    "status": "failed", "logs": combined_logs,
                }
                store_block_output(run_id, bid, b["letter"], b["app"]["name"], {}, {})
                status[bid] = "failed"
                log_block(run_id, b, "failed", combined_logs, started_at, ended_at)
                return

            with lock:
                try:
                    pq.set_interface_state(script_id, None)
                    pq.set_interface_state(script_id, input_state)
                except Exception:
                    pass
                success, combined_logs = run_block_with_retries(b["app"], shared, bid)
                try:
                    output_state = pq.get_interface_state(script_id)
                except Exception:
                    output_state = {}

            ended_at = datetime.datetime.utcnow().isoformat()
            final_status = "done" if success else "failed"
            store_block_output(run_id, bid, b["letter"], b["app"]["name"], input_state, output_state)
            shared["results"][bid] = {
                "letter": b["letter"], "name": b["app"]["name"],
                "status": final_status, "logs": combined_logs,
            }
            status[bid] = final_status
            try:
                log_block(run_id, b, final_status, combined_logs, started_at, ended_at)
            except Exception as e:
                shared["results"][bid]["logs"] += f"\\n\\n(log_block failed: {e})"

        t = threading.Thread(target=run_one, daemon=True)
        t.start()
        active_threads[bid] = t

    while any(s == "pending" for s in status.values()) or active_threads:
        for b in orchestration:
            bid = b["block_id"]
            if bid in started_bids or status[bid] != "pending":
                continue
            deps = b["depends_on"]
            if all(status[dep] in ("done", "failed", "skipped") for dep in deps):
                started_bids.add(bid)
                if any(status[dep] in ("failed", "skipped") for dep in deps):
                    status[bid] = "skipped"
                    shared["results"][bid] = {
                        "letter": b["letter"], "name": b["app"]["name"],
                        "status": "skipped", "logs": "(skipped - a dependency failed or was skipped)",
                    }
                    try:
                        log_block(run_id, b, "skipped", "", None, None)
                    except Exception:
                        pass
                else:
                    start_block(b)

        for bid in list(active_threads.keys()):
            if not active_threads[bid].is_alive():
                del active_threads[bid]

        time.sleep(0.5)

def print_grouped_summary(orchestration, shared):
    for b in sorted(orchestration, key=lambda x: x["letter"]):
        res = shared["results"].get(b["block_id"])
        if res is None:
            letter, name, status_text, logs_text = b["letter"], b["app"]["name"], "unknown", "(no result recorded)"
        else:
            letter, name, status_text, logs_text = res["letter"], res["name"], res["status"], res["logs"]
        print("")
        print(f"===== {letter}. {name} - {status_text.upper()} =====")
        print(logs_text or "(no output)")

orchestration = load_orchestration_by_name(ORCHESTRATION_NAME)

if orchestration is None:
    print(f"orchestration '{ORCHESTRATION_NAME}' not found - aborting.")
    raise RuntimeError(f"Orchestration '{ORCHESTRATION_NAME}' not found.")
elif has_cycle(orchestration):
    print(f"orchestration '{ORCHESTRATION_NAME}' has a cycle - aborting.")
    raise RuntimeError(f"Orchestration '{ORCHESTRATION_NAME}' has a cycle.")
else:
    run_id = str(uuid.uuid4())
    shared = {
        "status": {b["block_id"]: "pending" for b in orchestration},
        "logs": {},
        "attempts": {},
        "results": {},
    }
    print(f"Starting scheduled run {run_id} for '{ORCHESTRATION_NAME}'")
    orchestration_worker(orchestration, run_id, shared)

    print_grouped_summary(orchestration, shared)

    done = [b["letter"] for b in orchestration if shared["status"][b["block_id"]] == "done"]
    failed_blocks = [b["letter"] for b in orchestration if shared["status"][b["block_id"]] == "failed"]
    skipped = [b["letter"] for b in orchestration if shared["status"][b["block_id"]] == "skipped"]

    print("")
    print(f"Run {run_id} finished - {len(done)} done, {len(failed_blocks)} failed, {len(skipped)} skipped.")

    if failed_blocks:
        raise RuntimeError(f"orchestration '{ORCHESTRATION_NAME}' had failed block(s): {', '.join(sorted(failed_blocks))}")
'''

def state(key):
    return st.session_state[key]

def clear_all_blocks():
    for b in state("chosenApps"):
        st.session_state.pop(f"deps_{b['block_id']}", None)
    st.session_state["chosenApps"] = []
    st.session_state["loaded_orchestration_name"] = None

def remove_block(block_id):
    orchestration = state("chosenApps")
    orchestration[:] = [n for n in orchestration if n["block_id"] != block_id]
    for n in orchestration:
        n["depends_on"] = [d for d in n["depends_on"] if d != block_id]
    for n in orchestration:
        st.session_state.pop(f"deps_{n['block_id']}", None)
    st.session_state.pop(f"deps_{block_id}", None)

def has_cycle(orchestration):
    done = set()
    while True:
        ready = [
            b for b in orchestration
            if b["block_id"] not in done
            and all(dep in done for dep in b["depends_on"]) # No dependencies = start of graph
        ]
        if not ready:
            break                   
        for b in ready:
            done.add(b["block_id"])
    return len(done) != len(orchestration)

def build_dot(orchestration, status=None):
    status = status or {}
    colors = {
        "pending": "#d3d3d3",   # light gray
        "running": "#4a90d9",   # blue
        "done": "#4caf50",      # green
        "failed": "#e53935",    # red
        "skipped": "#f5a623",   # orange
        "cancelled": "#9e9e9e", # granit
    }
    dot = "digraph { rankdir=LR; node [shape=box, style=\"rounded,filled\", fontname=\"Helvetica\"];\n"
    for b in orchestration:
        bid = b["block_id"]
        node_label = f"{b['letter']}. {b['app']['name']}"
        color = colors.get(status.get(bid, "pending"), "#d3d3d3")
        dot += f'  "{bid}" [label="{node_label}", fillcolor="{color}"];\n'
    for b in orchestration:
        for dep in b["depends_on"]:
            dot += f'  "{dep}" -> "{b["block_id"]}";\n'
    dot += "}"
    return dot

def log_run_block_safe(run_id, orchestration_name, block, status, logs_text, started_at, ended_at):
    if orchestration_name == UNSAVED_ORCHESTRATION_NAME:
        return
    try:
        log_run_block(run_id, orchestration_name, block, status, logs_text, started_at, ended_at)
    except Exception:
        pass

def log_run_block(run_id, orchestration_name, block, status, logs_text, started_at, ended_at):
    dw = pq.dbconnect(pq.DW_NAME)
    dw.write("Orchestrator", "orchestration_runs", [{
        "run_id": run_id,
        "orchestration_name": orchestration_name,
        "block_id": block["block_id"],
        "block_letter": block["letter"],
        "script_name": block["app"]["name"],
        "status": status,
        "started_at": started_at,
        "ended_at": ended_at,
        "logs": logs_text,
    }])

def is_already_running_error(exc):
    return "ERROR_INTERFACE_ALREADY_RUNNING" in str(exc)
    
def run_block_no_ui(app, shared, bid):
    script_id = app["id"]
    holder = {"actually_started": False, "started_wall_time": None}

    def start_it():
        wait = 2
        waited = 0
        while True:
            try:
                holder["actually_started"] = True
                holder["started_wall_time"] = time.time()
                holder["result"] = pq.run_script(script_id=script_id)
                return
            except Exception as e:
                if not is_already_running_error(e) or waited >= CONTENTION_MAX_WAIT_SECONDS:
                    holder["result"] = {"status": "ERROR", "exit_code": -1, "logs": f"Exception while starting: {e}"}
                    return
                holder["actually_started"] = False
                holder["started_wall_time"] = None
                shared["logs"][bid] = f"Waiting for another run of this script to finish (contention backoff, next retry in {wait}s, waited {waited}s so far)..."
                time.sleep(wait)
                waited += wait
                wait = min(wait * 2, 30)

    thread = threading.Thread(target=start_it, daemon=True)
    thread.start()
    start_time = time.time()
    claimed_run_id = None

    def find_our_run_id():
        nonlocal claimed_run_id
        if claimed_run_id is not None:
            return claimed_run_id
        if not holder.get("actually_started"):
            return None
        my_started_wall_time = holder.get("started_wall_time")
        if my_started_wall_time is None:
            return None
        try:
            runs = pq.get_script_runs(script_id=script_id, page=1, per_page=10)
            data = runs.get("data", []) if runs else []
        except Exception:
            return None

        claimed_ids = shared.setdefault("claimed_run_ids", set())
        best, best_diff = None, None
        for r in data:
            if r.get("status") != "RUNNING" or r.get("id") in claimed_ids:
                continue
            ts_str = r.get("timestamp_start")
            if not ts_str:
                continue
            try:
                run_started = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            diff = abs(run_started - my_started_wall_time)
            if diff < 10 and (best_diff is None or diff < best_diff):
                best, best_diff = r["id"], diff

        if best is not None:
            claimed_ids.add(best)
            claimed_run_id = best
        return best

    def give_up_and_stop():
        if holder.get("actually_started"):
            try:
                confirmed_id = find_our_run_id()
                if confirmed_id is not None:
                    pq.stop_script_run(confirmed_id)
            except Exception:
                pass
            return {"status": "TIMEOUT", "exit_code": -1,
                    "logs": f"Exceeded {BLOCK_TIMEOUT_SECONDS}s timeout. Requested the remote run to stop."}
        else:
            return {"status": "TIMEOUT", "exit_code": -1,
                    "logs": f"Exceeded {BLOCK_TIMEOUT_SECONDS}s timeout while still waiting for another run of this script to finish. Gave up without touching it."}

    run_id_inner = None
    while run_id_inner is None and thread.is_alive():
        if time.time() - start_time > BLOCK_TIMEOUT_SECONDS:
            return give_up_and_stop()
        run_id_inner = find_our_run_id()
        time.sleep(0.5)

    while thread.is_alive():
        if time.time() - start_time > BLOCK_TIMEOUT_SECONDS:
            result = give_up_and_stop()
            result["logs"] = shared["logs"].get(bid, "") + "\n\n" + result["logs"]
            return result
        try:
            log_response = pq.get_script_run_logs(run_id_inner) if run_id_inner else None
            shared["logs"][bid] = log_response.get("logs", "") if log_response else ""
        except Exception:
            shared["logs"][bid] = "(log fetch failed, retrying...)"
        time.sleep(0.5)

    thread.join()
    result = holder.get("result") or {"status": "UNKNOWN", "exit_code": -1, "logs": "No result was returned."}
    shared["logs"][bid] = result.get("logs", "")
    return result
    
def run_block_with_retries(app, shared, bid):
    attempt = 1
    max_attempts = MAX_RETRIES + 1
    attempt_logs = []

    while True:
        shared["attempts"][bid] = (attempt, max_attempts)
        result = run_block_no_ui(app, shared, bid)
        success = result.get("status") == "COMPLETED" and result.get("exit_code") == 0
        outcome = "DONE" if success else f"FAILED ({result.get('status')})"
        attempt_logs.append(f"--- Attempt {attempt}/{max_attempts}: {outcome} ---\n{result.get('logs', '')}")

        if success or attempt >= max_attempts:
            return success, "\n\n".join(attempt_logs)

        attempt += 1
        shared["logs"][bid] = f"Attempt {attempt - 1}/{max_attempts} failed. Retrying in {RETRY_DELAY_SECONDS}s (attempt {attempt}/{max_attempts})..."
        time.sleep(RETRY_DELAY_SECONDS)

def orchestration_worker(orchestration, orchestration_name, run_id, shared):
    status = shared["status"]
    started_bids = set()
    active_threads = {}

    def start_block(b):
        bid = b["block_id"]
        status[bid] = "running"
        shared["current_blocks"][bid] = True
        started_at = datetime.datetime.utcnow().isoformat()

        def run_one():
            script_id = b["app"]["id"]
            lock = get_lock_for_script(script_id)

            if not shared.get("share_state", False):
                with lock:
                    reset_ok = False
                    for _ in range(3):
                        try:
                            pq.set_interface_state(script_id, None)
                            if not pq.get_interface_state(script_id):
                                reset_ok = True
                                break
                        except Exception:
                            pass
                        time.sleep(1)

                    if not reset_ok:
                        ended_at = datetime.datetime.utcnow().isoformat()
                        combined_logs = "Could not confirm the script's state was reset before running (previous run's state may still be present). Block not run."
                        status[bid] = "failed"
                        shared["logs"][bid] = combined_logs
                        shared["current_blocks"].pop(bid, None)
                        store_block_output(run_id, bid, b["letter"], b["app"]["name"], {}, {})
                        log_run_block_safe(run_id, orchestration_name, b, "failed", combined_logs, started_at, ended_at)
                        return

                    success, combined_logs = run_block_with_retries(b["app"], shared, bid)
                ended_at = datetime.datetime.utcnow().isoformat()
                final_status = "done" if success else "failed"
                status[bid] = final_status
                shared["logs"][bid] = combined_logs
                shared["current_blocks"].pop(bid, None)
                log_run_block_safe(run_id, orchestration_name, b, final_status, combined_logs, started_at, ended_at)
                return

            try:
                input_state = get_merged_input_state(run_id, b["depends_on"])
            except ValueError as e:
                ended_at = datetime.datetime.utcnow().isoformat()
                combined_logs = f"State merge conflict, block not run: {e}"
                status[bid] = "failed"
                shared["logs"][bid] = combined_logs
                shared["current_blocks"].pop(bid, None)
                store_block_output(run_id, bid, b["letter"], b["app"]["name"], {}, {})
                log_run_block_safe(run_id, orchestration_name, b, "failed", combined_logs, started_at, ended_at)
                return

            with lock:
                try:
                    pq.set_interface_state(script_id, None)
                    pq.set_interface_state(script_id, input_state)
                except Exception:
                    pass
                success, combined_logs = run_block_with_retries(b["app"], shared, bid)
                try:
                    output_state = pq.get_interface_state(script_id)
                except Exception:
                    output_state = {}

            ended_at = datetime.datetime.utcnow().isoformat()
            final_status = "done" if success else "failed"
            store_block_output(run_id, bid, b["letter"], b["app"]["name"], input_state, output_state)
            status[bid] = final_status
            shared["logs"][bid] = combined_logs
            shared["current_blocks"].pop(bid, None)
            log_run_block_safe(run_id, orchestration_name, b, final_status, combined_logs, started_at, ended_at)
        
        t = threading.Thread(target=run_one, daemon=True)
        t.start()
        active_threads[bid] = t

    try:
        while any(s == "pending" for s in status.values()) or active_threads:
            if shared["cancel_requested"]:
                for bid, s in status.items():
                    if s == "pending":
                        status[bid] = "cancelled"
            else:
                for b in orchestration:                
                    bid = b["block_id"]
                    if bid in started_bids or status[bid] != "pending":
                        continue
                    deps = b["depends_on"]
                    if all(status[dep] in ("done", "failed", "skipped", "cancelled") for dep in deps):
                        started_bids.add(bid)
                        if any(status[dep] in ("failed", "skipped", "cancelled") for dep in deps):
                            status[bid] = "skipped"
                            log_run_block_safe(run_id, orchestration_name, b, "skipped", "", None, None)
                        else:
                            start_block(b)

            for bid in list(active_threads.keys()):
                if not active_threads[bid].is_alive():
                    del active_threads[bid]

            time.sleep(0.5)

    finally:
        shared["finished"] = True
        
def serialize_orchestration(orchestration):
    return [
        {
            "block_id": b["block_id"],
            "letter": b["letter"],
            "script_id": b["app"]["id"],
            "depends_on": b["depends_on"],
        }
        for b in orchestration
    ]

def save_orchestration(name, orchestration):
    dw = pq.dbconnect(pq.DW_NAME)
    record = {
        "name": name,
        "definition": json.dumps(serialize_orchestration(orchestration)),
        "updated_at": datetime.datetime.utcnow().isoformat(),
    }
    dw.write("Orchestrator", "orchestrations", [record], pk="name")

def list_saved_orchestrations():
    try:
        dbconn = pq.dbconnect(pq.DW_NAME)
        return dbconn.fetch(
            pq.DW_NAME,
            query='SELECT name, updated_at FROM "Orchestrator".orchestrations WHERE deleted_at IS NULL ORDER BY name ASC'
        )
    except Exception:
        try:
            dbconn = pq.dbconnect(pq.DW_NAME)
            return dbconn.fetch(
                pq.DW_NAME,
                query='SELECT name, updated_at FROM "Orchestrator".orchestrations ORDER BY name ASC'
            )
        except Exception:
            return []

def load_orchestration(name, app_by_id):
    try:
        dbconn = pq.dbconnect(pq.DW_NAME)
        rows = dbconn.fetch(
            pq.DW_NAME,
            query=f'SELECT definition FROM "Orchestrator".orchestrations WHERE name = \'{name}\' AND (deleted_at IS NULL OR deleted_at = \'\')'
        )
    except Exception:
        try:
            dbconn = pq.dbconnect(pq.DW_NAME)
            rows = dbconn.fetch(
                pq.DW_NAME,
                query=f'SELECT definition FROM "Orchestrator".orchestrations WHERE name = \'{name}\''
            )
        except Exception:
            return None, []

    if not rows:
        return None, []

    saved_blocks = json.loads(rows[0]["definition"])
    orchestration, missing = [], []
    for b in saved_blocks:
        app = app_by_id.get(b["script_id"])
        if app is None:
            missing.append(b["script_id"])
            continue
        orchestration.append({
            "block_id": b["block_id"],
            "letter": b["letter"],
            "app": app,
            "config": {},
            "depends_on": b["depends_on"],
        })
    return orchestration, missing

def delete_orchestration(name):
    dw = pq.dbconnect(pq.DW_NAME)
    dw.write("Orchestrator", "orchestrations", [{
        "name": name,
        "definition": "[]",
        "updated_at": datetime.datetime.utcnow().isoformat(),
        "deleted_at": datetime.datetime.utcnow().isoformat(),
    }], pk="name")

def get_or_create_scheduled_runner(orchestration_name, template, group_id, share_state):
    runner_name = f"Scheduled - {orchestration_name}"
    existing = next((a for a in pq.list_scripts() if a["name"] == runner_name), None)
    source = template.replace("__ORCHESTRATION_NAME__", orchestration_name)
    source = source.replace("__MAX_RETRIES__", str(MAX_RETRIES))
    source = source.replace("__RETRY_DELAY_SECONDS__", str(RETRY_DELAY_SECONDS))
    source = source.replace("__BLOCK_TIMEOUT_SECONDS__", str(BLOCK_TIMEOUT_SECONDS))
    source = source.replace("__CONTENTION_MAX_WAIT_SECONDS__", str(CONTENTION_MAX_WAIT_SECONDS))
    source = source.replace("__SHARE_STATE__", str(share_state))

    if existing:
        pq.update_script(script_id=existing["id"], raw_script=source)
        register_generated_script(existing["id"], orchestration_name)
        return existing["id"]

    result = pq.add_script(
        name=runner_name,
        raw_script=source,
        run_mode="STREAMLIT",
        group_id=group_id,
    )
    register_generated_script(result["id"], orchestration_name)
    return result["id"]

def register_generated_script(script_id, orchestration_name):
    dw = pq.dbconnect(pq.DW_NAME)
    dw.write("Orchestrator", "generated_scripts", [{
        "script_id": script_id,
        "orchestration_name": orchestration_name,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }], pk="script_id")

def get_generated_script_ids():
    try:
        dbconn = pq.dbconnect(pq.DW_NAME)
        rows = dbconn.fetch(
            pq.DW_NAME,
            query='SELECT script_id FROM "Orchestrator".generated_scripts'
        )
        return {r["script_id"] for r in rows}
    except Exception:
        return set()

def round_to_nearest_quarter(dt):
    minutes = dt.hour * 60 + dt.minute
    rounded = round(minutes / 15) * 15
    rounded %= 24 * 60
    return datetime.time(hour=rounded // 60, minute=rounded % 60)

def set_schedule(script_id, weekday_labels, start_time, interval_label):
    schedule_settings = {
        "schedule_settings": {
            "schedule": True,
            "timezone": "UTC",
            "weekdays": [WEEKDAY_OPTIONS[d] for d in weekday_labels],
            "start_time": start_time.strftime("%H:%M:%S"),
            "run_interval": INTERVAL_OPTIONS[interval_label],
        }
    }
    return pq.update_script(script_id=script_id, settings=schedule_settings)

def get_distinct_orchestration_names_from_runs():
    try:
        dbconn = pq.dbconnect(pq.DW_NAME)
        rows = dbconn.fetch(
            pq.DW_NAME,
            query='SELECT DISTINCT orchestration_name FROM "Orchestrator".orchestration_runs ORDER BY orchestration_name'
        )
        return [r["orchestration_name"] for r in rows]
    except Exception:
        return []

def get_run_blocks(run_id):
    try:
        dbconn = pq.dbconnect(pq.DW_NAME)
        safe_id = run_id.replace("'", "''")
        return dbconn.fetch(
            pq.DW_NAME,
            query=f'''SELECT block_id, block_letter, script_name, status, started_at, ended_at, logs
                      FROM "Orchestrator".orchestration_runs
                      WHERE run_id = '{safe_id}'
                      ORDER BY started_at'''
        )
    except Exception:
        return []

def get_run_summaries(orchestration_name, limit=20):
    try:
        dbconn = pq.dbconnect(pq.DW_NAME)
        safe_name = orchestration_name.replace("'", "''")
        query = f'''
            SELECT run_id, orchestration_name,
                   MIN(started_at) AS run_started,
                   MAX(ended_at) AS run_ended,
                   COUNT(*) AS block_count,
                   SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                   SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped_count,
                   SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_count
            FROM "Orchestrator".orchestration_runs
            WHERE orchestration_name = '{safe_name}'
            GROUP BY run_id, orchestration_name
            ORDER BY MIN(started_at) DESC
            LIMIT {limit}
        '''
        return dbconn.fetch(pq.DW_NAME, query=query)
    except Exception:
        return []

script_locks = {}
script_locks_guard = threading.Lock()

def get_lock_for_script(script_id):
    with script_locks_guard:
        if script_id not in script_locks:
            script_locks[script_id] = threading.Lock()
        return script_locks[script_id]

def store_block_output(run_id, block_id, letter, script_name, input_state, output_state):
    try:
        dw = pq.dbconnect(pq.DW_NAME)
        dw.write("Orchestrator", "block_outputs", [{
            "run_id": run_id,
            "block_id": block_id,
            "letter": letter,
            "script_name": script_name,
            "input_state": json.dumps(input_state or {}),
            "output_state": json.dumps(output_state or {}),
            "created_at": datetime.datetime.utcnow().isoformat(),
        }])
    except Exception:
        pass

def get_block_output(run_id, block_id):
    try:
        dbconn = pq.dbconnect(pq.DW_NAME)
        safe_run = run_id.replace("'", "''")
        safe_block = block_id.replace("'", "''")
        rows = dbconn.fetch(
            pq.DW_NAME,
            query=f'''SELECT output_state FROM "Orchestrator".block_outputs
                      WHERE run_id = '{safe_run}' AND block_id = '{safe_block}' '''
        )
        if not rows:
            return {}
        return json.loads(rows[0]["output_state"] or "{}")
    except Exception:
        return {}

def get_block_output_full(run_id, block_id):
    try:
        dbconn = pq.dbconnect(pq.DW_NAME)
        safe_run = run_id.replace("'", "''")
        safe_block = block_id.replace("'", "''")
        rows = dbconn.fetch(
            pq.DW_NAME,
            query=f'''SELECT input_state, output_state FROM "Orchestrator".block_outputs
                      WHERE run_id = '{safe_run}' AND block_id = '{safe_block}' '''
        )
        if not rows:
            return None
        return {
            "input_state": json.loads(rows[0]["input_state"] or "{}"),
            "output_state": json.loads(rows[0]["output_state"] or "{}"),
        }
    except Exception:
        return None

def get_merged_input_state(run_id, depends_on_block_ids):
    merged = {}
    conflicts = []
    for dep_block_id in depends_on_block_ids:
        dep_output = get_block_output(run_id, dep_block_id)
        for k, v in dep_output.items():
            if k in merged and merged[k] != v:
                conflicts.append(k)
            merged[k] = v
    if conflicts:
        raise ValueError(f"State merge conflict: key(s) {sorted(set(conflicts))} were written by more than one dependency with different values.")
    return merged
        
@st.fragment(run_every=0.5)
def watch_orchestration(orchestration_snapshot):
    try:
        shared = st.session_state["run_shared"]

        if shared["cancel_requested"]:
            st.button("Cancelling...", disabled=True, key="cancel_btn")
        else:
            if st.button("Cancel orchestration", type="primary", key="cancel_btn"):
                shared["cancel_requested"] = True
                for bid, s in shared["status"].items():
                    if s == "pending":
                        shared["status"][bid] = "cancelled"
                        b = next(bb for bb in orchestration_snapshot if bb["block_id"] == bid)
                        log_run_block_safe(shared["run_id"], shared["orchestration_name"], b, "cancelled", "", None, None)
                st.toast("Cancelling - will stop after the current step finishes.")

        st.graphviz_chart(build_dot(orchestration_snapshot, shared["status"]))

        current_blocks = shared.get("current_blocks", {})
        for b in sorted(orchestration_snapshot, key=lambda x: x["letter"]):
            bid = b["block_id"]
            s = shared["status"][bid]

            if s in ("done", "failed", "skipped", "cancelled"):
                text = shared["logs"].get(bid, "")
                with st.expander(f"{b['letter']}. {b['app']['name']} ({s.upper()})"):
                    if text:
                        st.code(text, language=None)
                    elif s == "skipped":
                        st.caption("Skipped because a dependency failed.")
                    elif s == "cancelled":
                        st.caption("Cancelled before this block started.")
            elif bid in current_blocks:
                text = shared["logs"].get(bid, "")
                attempt, total = shared.get("attempts", {}).get(bid, (1, 1))
                suffix = f" (RUNNING, attempt {attempt}/{total})" if total > 1 else " (RUNNING)"
                with st.expander(f"{b['letter']}. {b['app']['name']}{suffix}", expanded=True):
                    st.code(text or "starting...", language=None)

        if shared["finished"]:
            get_distinct_orchestration_names_from_runs_cached.clear()
            get_run_summaries_cached.clear()
            st.session_state["orchestration_running"] = False
            st.rerun()
    except Exception as e:
        st.error(f"watch_orchestration crashed: {e}")
        st.exception(e)





generated_ids = get_generated_script_ids()
addable_app_names = [name for name, app in appDict.items() if app["id"] not in generated_ids]

with st.sidebar:
    st.subheader("Add Data App")
    
    if "clear_app_picker" not in st.session_state:
        st.session_state["clear_app_picker"] = False
    
    if st.session_state["clear_app_picker"]:
        st.session_state["app_picker"] = None
        st.session_state["clear_app_picker"] = False

    if "clear_pick_name" not in st.session_state:
        st.session_state["clear_pick_name"] = False
    if st.session_state["clear_pick_name"]:
        st.session_state["pick_name_select"] = None
        st.session_state["clear_pick_name"] = False
    
    option = st.selectbox(
        "Which data app do you want to add to the orchestration?",
        addable_app_names,
        index=None,
        placeholder="Select a data app",
        key="app_picker",
    )
    
    col_add, col_refresh = st.columns(2)
    with col_add:
        add_clicked = st.button("Add app", use_container_width=True, disabled=orchestration_running)
    with col_refresh:
        refresh_clicked = st.button("Refresh apps", use_container_width=True, disabled=orchestration_running)

    if add_clicked and option:
        orchestration = state("chosenApps")
        used = {b["letter"] for b in orchestration}
        letter = next(l for l in string.ascii_uppercase if l not in used)
        orchestration.append({
            "block_id": str(uuid.uuid4()),
            "letter": letter,
            "app": appDict[option],
            "config": {},
            "depends_on": [],
        })
        st.session_state["clear_app_picker"] = True
        st.rerun()

    if refresh_clicked:
        get_app_list.clear()
        st.rerun()
    
    st.divider()

    if "loaded_orchestration_name" not in st.session_state:
        st.session_state["loaded_orchestration_name"] = None

    if "pending_delete_orch" not in st.session_state:
        st.session_state["pending_delete_orch"] = None

    if "clear_new_orch_name" not in st.session_state:
        st.session_state["clear_new_orch_name"] = False

    @st.dialog("Save orchestration")
    def save_dialog():
        if st.session_state["clear_new_orch_name"]:
            st.session_state["new_orch_name_input"] = ""
            st.session_state["clear_new_orch_name"] = False

        saved_now = list_saved_orchestrations()
        saved_names_now = [r["name"] for r in saved_now]

        new_name = st.text_input("Orchestration name", key="new_orch_name_input")
        if st.button("Save", key="save_new_orch_btn"):
            if not new_name:
                st.error("Enter a name first.")
            elif new_name in saved_names_now:
                st.error(f"'{new_name}' already exists.")
            else:
                save_orchestration(new_name, state("chosenApps"))
                list_saved_orchestrations_cached.clear()
                st.session_state["loaded_orchestration_name"] = new_name
                st.session_state["toast_message"] = f"Saved as '{new_name}'"
                st.session_state["clear_new_orch_name"] = True
                st.rerun()

    @st.dialog("Manage orchestrations")
    def manage_dialog():
        saved_now = list_saved_orchestrations()
        if not saved_now:
            st.info("No saved orchestrations yet.")
            return

        for r in saved_now:
            name = r["name"]
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                st.write(name)
            with col2:
                if st.button("Load", key=f"load_orch_{name}", use_container_width=True):
                    app_by_id = {app["id"]: app for app in appList}
                    loaded, missing = load_orchestration(name, app_by_id)
                    if loaded is not None:
                        st.session_state["chosenApps"] = loaded
                        for k in list(st.session_state.keys()):
                            if k.startswith("deps_"):
                                del st.session_state[k]
                        st.session_state["loaded_orchestration_name"] = name
                        note = f" ({len(missing)} script(s) no longer exist and were skipped)" if missing else ""
                        st.session_state["toast_message"] = f"Loaded '{name}'{note}"
                        st.rerun()
            with col3:
                if st.button("Delete", key=f"del_orch_{name}", use_container_width=True):
                    delete_orchestration(name)
                    list_saved_orchestrations_cached.clear()
                    if st.session_state["loaded_orchestration_name"] == name:
                        st.session_state["loaded_orchestration_name"] = None
                    st.session_state["toast_message"] = f"Deleted '{name}'"
                    st.rerun()

    @st.dialog("Schedule orchestration")
    def schedule_dialog():
        saved_now = list_saved_orchestrations()
        saved_names_now = [r["name"] for r in saved_now]
        if not saved_names_now:
            st.info("Save an orchestration first before scheduling it.")
            return

        loaded = st.session_state.get("loaded_orchestration_name")
        default_index = saved_names_now.index(loaded) if loaded in saved_names_now else 0
        target_name = st.selectbox("Which orchestration?", options=saved_names_now, index=default_index, key="sched_target_name")

        st.caption("Run on")
        days = list(WEEKDAY_OPTIONS.keys())
        weekday_checked = {}
        row1 = st.columns(4)
        for i, day in enumerate(days[:4]):
            with row1[i]:
                weekday_checked[day] = st.checkbox(day, value=True, key=f"schedwd_{target_name}_{day}")
        row2 = st.columns(4)
        for i, day in enumerate(days[4:]):
            with row2[i]:
                weekday_checked[day] = st.checkbox(day, value=True, key=f"schedwd_{target_name}_{day}")
        weekday_labels = [day for day, checked in weekday_checked.items() if checked]

        interval_label = st.selectbox("Repeat", options=list(INTERVAL_OPTIONS.keys()), index=4, key=f"schediv_{target_name}")

        if interval_label == "Every 24 hours":
            default_start = round_to_nearest_quarter(datetime.datetime.utcnow())
            start_time = st.time_input("Start time (UTC)", value=default_start, key=f"schedst_{target_name}")
        else:
            start_time = datetime.time(0, 0)
            st.caption("Start time only applies to the 'Every 24 hours' interval; other intervals begin as soon as scheduling is enabled.")

        share_state_scheduled = st.checkbox("Share state between scripts", key=f"sched_share_state_{target_name}")

        if st.button("Create & Schedule", key=f"sched_confirm_{target_name}"):
            own_app = next((a for a in appList if a["id"] == pq.INTERFACE_ID), None)
            group_id = own_app["group"] if own_app else ""
            script_id = get_or_create_scheduled_runner(target_name, HEADLESS_RUNNER_TEMPLATE, group_id, share_state_scheduled)
            set_schedule(script_id, weekday_labels, start_time, interval_label)
            st.session_state["toast_message"] = f"Scheduled '{target_name}'"
            st.rerun()

    col_save, col_manage, col_schedule = st.columns(3)
    with col_save:
        if st.button("Save", use_container_width=True, disabled=orchestration_running):
            loaded = st.session_state.get("loaded_orchestration_name")
            if loaded is None:
                save_dialog()
            else:
                save_orchestration(loaded, state("chosenApps"))
                list_saved_orchestrations_cached.clear()
                st.toast(f"Updated '{loaded}'")
    with col_manage:
        if st.button("Manage", use_container_width=True, disabled=orchestration_running):
            manage_dialog()
    with col_schedule:
        if st.button("Schedule", use_container_width=True, disabled=orchestration_running):
            schedule_dialog()

if orchestration_running:
    st.subheader("Orchestration running")
    shared = st.session_state["run_shared"]
    orchestration_snapshot = st.session_state["run_orchestration_snapshot"]
    watch_orchestration(orchestration_snapshot)
    
else:
    tab_builder, tab_history = st.tabs(["Orchestration Builder", "Run History"])

    with tab_builder:
        col_blocks, col_graph = st.columns([2, 3])

        with col_blocks:
            st.subheader("Orchestration Builder")
            if state("chosenApps"):
                if st.button("Clear all blocks", disabled=orchestration_running):
                    st.session_state["pending_clear_blocks"] = True

                if st.session_state.get("pending_clear_blocks"):
                    st.warning("Remove all blocks from the builder? Unsaved changes will be lost.")
                    col_confirm2, col_cancel2 = st.columns(2)
                    with col_confirm2:
                        if st.button("Yes, clear it", use_container_width=True, key="confirm_clear_btn"):
                            clear_all_blocks()
                            st.session_state["pending_clear_blocks"] = False
                            st.session_state["toast_message"] = "Cleared all blocks."
                            st.rerun()
                    with col_cancel2:
                        if st.button("Cancel", use_container_width=True, key="cancel_clear_btn"):
                            st.session_state["pending_clear_blocks"] = False
                            st.rerun()
            elif not state("chosenApps"):
                st.info("Blocks can be added from the sidebar.")
            blokken = state("chosenApps")
            per_rij = 2

            for start in range(0, len(blokken), per_rij):
                rij = blokken[start:start + per_rij]
                kolommen = st.columns(per_rij)
                for kol, blok in zip(kolommen, rij):
                    with kol:
                        with st.container(border=True):
                            head, rm = st.columns([8, 1])
                            head.markdown(f"**{blok['letter']}. {blok['app']['name']}**")
                            if rm.button("✕", key=f"del_{blok['block_id']}"):
                                remove_block(blok["block_id"])
                                st.rerun()

                            others = [n for n in state("chosenApps") if n["block_id"] != blok["block_id"]]
                            label = {
                                n["block_id"]: f"{n['letter']}. {n['app']['name']}"
                                for n in others
                            }
                            key = f"deps_{blok['block_id']}"

                            if key not in st.session_state:
                                st.session_state[key] = [d for d in blok["depends_on"] if d in label]

                            st.multiselect(
                                "Depends on",
                                options=[n["block_id"] for n in others],
                                format_func=lambda bid: label.get(bid, "?"),
                                key=key,
                            )
                            blok["depends_on"] = st.session_state[key]

        with col_graph:
            if state("chosenApps"):
                st.subheader("Overview")
                graph_placeholder = st.empty()
                graph_placeholder.graphviz_chart(build_dot(state("chosenApps")))
                if has_cycle(state("chosenApps")):
                    st.error("There is a cycle in your orchestration - it cannot be executed.")
                else:
                    share_state = st.checkbox("Share state between scripts")
                    if st.button("Run orchestration", type="primary"):
                        run_id = str(uuid.uuid4())
                        orchestration_name_for_log = st.session_state.get("loaded_orchestration_name") or UNSAVED_ORCHESTRATION_NAME
                        orchestration_snapshot = state("chosenApps")
                        shared = {
                            "status": {b["block_id"]: "pending" for b in orchestration_snapshot},
                            "logs": {},
                            "cancel_requested": False,
                            "finished": False,
                            "current_blocks": {},
                            "attempts": {},
                            "run_id": run_id,
                            "orchestration_name": orchestration_name_for_log,
                            "share_state": share_state,
                        }
                        st.session_state["run_shared"] = shared
                        st.session_state["run_orchestration_snapshot"] = orchestration_snapshot
                        worker = threading.Thread(
                            target=orchestration_worker,
                            args=(orchestration_snapshot, orchestration_name_for_log, run_id, shared),
                        )
                        worker.start()
                        st.session_state["orchestration_running"] = True
                        st.rerun()

    with tab_history:
        st.subheader("Run History")
        names = get_distinct_orchestration_names_from_runs_cached()

        if not names:
            st.info("No runs recorded yet.")
        else:
            filter_name = st.selectbox("Orchestration", options=names, key="history_orchestration_select")
            runs = get_run_summaries_cached(filter_name)

            if not runs:
                st.info(f"No runs recorded yet for '{filter_name}'.")
            else:
                done_count = sum(1 for r in runs if r["failed_count"] == 0 and r["skipped_count"] == 0)
                failed_count = sum(1 for r in runs if r["failed_count"] > 0)
                partial_count = len(runs) - done_count - failed_count

                st.caption(
                    f"{done_count} done, {failed_count} failed, {partial_count} partial: "
                    f"showing last {len(runs)} run(s)"
                )

                for r in runs:
                    if r["cancelled_count"] > 0:
                        overall = "CANCELLED"
                    elif r["failed_count"] > 0:
                        overall = "FAILED"
                    elif r["skipped_count"] > 0:
                        overall = "PARTIAL"
                    else:
                        overall = "DONE"

                    title = f"{overall}: {r['run_started']} ({r['block_count']} blocks)"
                    with st.expander(title):
                        blocks = get_run_blocks_cached(r["run_id"])
                        for i, b in enumerate(blocks):
                            st.markdown(f"**{b['block_letter']}. {b['script_name']}**: {b['status']}")
                            if b["logs"]:
                                st.code(b["logs"], language=None)
                            block_state = get_block_output_full(r["run_id"], b["block_id"])
                            if block_state:
                                st.caption("Input state")
                                st.json(block_state["input_state"])
                                st.caption("Output state")
                                st.json(block_state["output_state"])
                            if i < len(blocks) - 1:
                                    st.divider()