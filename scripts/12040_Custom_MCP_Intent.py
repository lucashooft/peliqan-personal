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

# Add an API endpoint with method POST and link it to this API handler script.
# Use your MCP Server in e.g. ChatGPT or Claude: add a Remote MCP server with the URL API endpoint set for this script.
# Example: POST https://api.eu.peliqan.io/123/mcp?api_key=82ac4ac4-8ab6-4c2c-a039-548d19387d1d
#
# Add custom MCP tools: see below section, decorate your function with @mcp.tool()
# Version 2.2

# Change below API key and put it in Peliqan's secret store (under Connections)
# api_key = pq.get_secret("MCP API key")
api_key = "82ac4ac4-8ab6-4c2c-a039-548d19387d1d" # for demo only, remove this

import json
import inspect
import time
from typing import get_type_hints
from typing import List, Dict, Any
from urllib.parse import parse_qs

dbconn = pq.dbconnect(pq.DW_NAME)

TOOL_INTENT_DESCRIPTION = (
    "Explain the original intent of the user, taking into account previous instructions from the user,"
    "so that the full intent from the user and reason to invoke this tool is clear. Never include credentials"
)

ENABLE_RAG = True
MCP_TOOLS = []
class mcp:
    def tool():
        def decorator(func):
            sig = inspect.signature(func)
            type_hints = get_type_hints(func)

            # parse descriptions from docstring using :param style
            raw_doc = func.__doc__ or ""
            param_docs = {}
            for line in raw_doc.splitlines():
                line = line.strip()
                if line.startswith(":param"):
                    # example: ":param first_name: the user's first name"
                    try:
                        _, rest = line.split("param", 1)
                        name, desc = rest.split(":", 1)
                        param_docs[name.strip()] = desc.strip()
                    except ValueError:
                        pass
            properties = {}
            required = []

            for name, param in sig.parameters.items():
                hint = type_hints.get(name, "any")
                type_str = hint.__name__ if isinstance(hint, type) else str(hint)
                
                if type_str == "str":
                    mcp_type_str = "string"
                elif type_str == "int":
                    mcp_type_str = "number"
                else:
                    mcp_type_str = "string"
                    
                prop = {
                    "type": mcp_type_str,
                    "description": param_docs.get(name, "")
                }
                if param.default is not inspect._empty:
                    prop["default"] = param.default
                else:
                    required.append(name)
                properties[name] = prop

            # Intent detection: injected into every tool's schema here, rather
            # than added as a real parameter to each function individually.
            # _handle_tool_call strips it back out before calling the real
            # function, so none of the ~18 tool functions ever need to know
            # this field exists.
            properties["tool_intent"] = {
                "type": "string",
                "description": TOOL_INTENT_DESCRIPTION,
            }

            MCP_TOOLS.append({
                "name": func.__name__,
                "description": raw_doc.strip().split("\n")[0] if raw_doc else "",
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            })
            return func
        return decorator

def _log_tool_intent(tool_name, tool_intent):
    """
    Best-effort: log the calling agent's stated intent for this tool call.
    A logging failure must never block the actual tool call - same
    principle as _log_denied_login in ts_auth_server.
    """
    if not tool_intent:
        print("not tool_intent")
        return
    try:
        dbconn.write("logs", "tool_intent_log", [{
            "logged_at": str(int(time.time())),
            "tool_name": tool_name,
            "tool_intent": tool_intent,
        }], pk="logged_at", object_schema={
            "properties": {
                "logged_at": {"type": "string"},
                "tool_name": {"type": "string"},
                "tool_intent": {"type": "string"},
            }
        })
    except Exception:
        pass

def get_tool_response_format(tool_name):
    func = globals().get(tool_name)
    response_annotation = inspect.signature(func).return_annotation
    if hasattr(response_annotation, "__name__"):
        return response_annotation.__name__
    return str(response_annotation).replace("typing.", "").replace("class '", "").replace("'>","")

if ENABLE_RAG:
    openai_api = pq.connect('OpenAI')
    EMBED_MODELS = {
        "text-embedding-3-large": 3072,
        "text-embedding-3-small": 1536
    }
    model = "text-embedding-3-large"
    dimension = EMBED_MODELS[model]
    
def create_embedding(text):
    """Create embedding using OpenAI API"""
    embedding_request = {
        "input": text,
        "model": model,
        "dimensions": dimension
    }
    response = openai_api.get('embeddings', embedding_request)
    embedding = response.get("data", [{}])[0].get("embedding")
    return embedding

########################### ADD MCP TOOLS BELOW ###########################
@mcp.tool()
def say_hello(first_name: str, last_name: str = "") -> str:
    """
    Peliqan will say hello.

    :param first_name: the user's first name
    :param last_name: optional last name
    """
    return f"Hi there {first_name} {last_name} from Peliqan MCP server !"

@mcp.tool()
def list_connections() -> List[str]:
    """
    Returns list of all ELT connections in the Peliqan account.
    """
    connections = pq.list_connections()
    conn_names = []
    for connection in connections:
        conn_names.append(connection["name"])
    #return ", ".join(conn_names)
    return conn_names

@mcp.tool()
def list_tables() -> List[Dict[str, Any]]:
    """
    Returns list of all the tables in the Peliqan account.
    """
    all_tables = []
    for db in pq.list_databases():
        for table in db["tables"]:
            schema_name = next((s["name"] for s in db["schemas"] if s["id"] == table["schema_id"]), None)
            all_tables.append({
                "db_id": db["id"],
                "db": db["name"],
                "schema_id": table["schema_id"],
                "schema": schema_name,
                "table_id": table["id"],
                "table": table["name"]
            })
    return all_tables

@mcp.tool()
def list_columns(table_id: int) -> List[str]:
    """
    Returns list of all the fields (columns) of a table.
    """

    table_meta = pq.get_table(table_id)
    columns = table_meta.get("all_fields", [])
    column_names = [col["name"] for col in columns if not col["name"].startswith("_sdc")]

    return column_names

@mcp.tool()
def execute_query(query: str) -> List[Dict[str, Any]]:
    """
    Executes an SQL query on the data warehouse and returns a list of rows.
    """
    query = query.replace('"' + pq.DW_NAME + '".', '').replace(pq.DW_NAME + '.', '')
    print("Final SQL query to execute:")
    print(query)
    rows = dbconn.fetch(pq.DW_NAME, query = query)
    return rows

if ENABLE_RAG:
    @mcp.tool()
    def rag_search(search_text: str, schema: str, table: str) -> List[Dict[str, Any]]:
        """
        Executes a RAG search on embedding tables in the data warehouse and returns a list of rows.
        """
        top_k = 5
        print(f"Search text: {search_text}, schema: {schema}, table: {table}")
        search_embedding = create_embedding(search_text)
        if search_embedding:
            embedding_str = "[" + ",".join(map(str, search_embedding)) + "]"
            search_query = f"""
            SELECT id, text, metadata,
                1 - (embedding <#> '{embedding_str}'::vector) AS similarity
            FROM "{schema}"."rag_{table}"
            ORDER BY similarity DESC
            LIMIT {top_k}
            """
            search_result = dbconn.execute(pq.DW_NAME, query=search_query)
            records = search_result["detail"]
            # Convert from list format with headers to list of dicts
            dict_records = []
            if records and len(records) > 0:
                headers = records[0]  # First row contains column names
                for row in records[1:]:  # Skip header row
                    record_dict = {}
                    for i, header in enumerate(headers):
                        record_dict[header] = row[i]
                    dict_records.append(record_dict)
            return dict_records
        else:
            return []

@mcp.tool()
def pipedrive_add_contact(*args, **kwargs):
    """
    Example of a writeback function: add a contact to Pipedrive (CRM).
    """
    pipedrive_api = pq.connect('Pipedrive')
    
    person = {}
    if "name" in kwargs:
        person["name"] = kwargs["name"]
    if "email" in kwargs:
        person["emails"] = [{'label': 'work', 'value': kwargs["email"], 'primary': True}]
        
    result = pipedrive_api.add('person', person)

    if "status" in result and result["status"] == "success":
        new_contact = result.get("detail", {}).get("data", {})
        return f"Contact added to Pipedrive: {new_contact}"
    else:
        print("Response from Pipedrive:", result)
        error = result.get("detail", {}).get("response_json", {}).get("error", "")
        return f"Error adding contact to Pipedrive: {error}"   

########################### END OF MCP TOOLS ###########################


def log_request(request):
    print("request method: ", request['method'])
    print("request url: ", request['url'])
    print("request query string: ", request['query_string'])
    print("request body:")
    try:
        print(json.dumps(request['data'], indent=2))
    except:
        print(request['data'])
        
def log_response(response):
    print("Response:")
    print(json.dumps(response, indent=2))

def mcp_response_initialize(id):
    response_initialize = {
        "jsonrpc": "2.0",
        "id": id,
        "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {
                "callTool": True,
                "listTools": True,
                "tools": {
                    "listChanged": False
                }
            },
            "serverInfo": {
                "name": "peliqan-mcp",
                "version": "0.0.1"
            }
        }
    }
    return response_initialize

def mcp_response_tools_list(id):
    response_tools_list = {
        "jsonrpc": "2.0",
        "id": id,
        "result": {
            "tools": MCP_TOOLS
        }
    }
    return response_tools_list

def mcp_response_tools_call(id, response_type):
    response_tools_call = {
        "jsonrpc": "2.0",
        "id": id,
        "result": {
            "content": [
                {
                    "type": response_type,
                    response_type: ""
                }
            ],
            "isError": False
        }
    }
    return response_tools_call

def check_api_key(request):
    query_string_parsed = parse_qs(request.get("query_string", ""))
    request_api_key = query_string_parsed["api_key"][0] if "api_key" in query_string_parsed else None
    if request_api_key != api_key:
        print("Incorrect API key, stopping")
        return False
    else:
        return True
        
def handler(request):
    log_request(request)
    if not check_api_key(request):
        return "Unauthorized", 401 #, {"WWW-Authenticate": 'Bearer resource_metadata="https://your-server.com/"'}
    
    data = request['data']
    
    if not data:
        data = "{}"
    mcp_req = json.loads(data)

    id = 0
    if "id" in mcp_req:
        id = mcp_req["id"]

    mcp_response = mcp_response_initialize(id)
    if "method" in mcp_req:
        if mcp_req["method"] == "initialize":
            response = mcp_response_initialize(id)
        elif mcp_req["method"] == "notifications/initialized":
            return "", 202
        elif mcp_req["method"] == "tools/list":
            mcp_response = mcp_response_tools_list(id)
        elif mcp_req["method"] == "tools/call":
            tool_name = mcp_req["params"]["name"]
            args = dict(mcp_req["params"].get("arguments", {}))
            tool_intent = args.pop("tool_intent", "")
            _log_tool_intent(tool_name, tool_intent)

            isError = False
            try:
                tool_response = globals()[tool_name](**args) #Invoking tool
            except Exception as e:
                print(e)
                tool_response =  str(e)
                isError = True

            tool_response_format = get_tool_response_format(tool_name) # str, List
            response_type = "text"
            mcp_response = mcp_response_tools_call(id, response_type)
            if isError:
                mcp_response["result"]["isError"] = isError
            
            if tool_response_format == "str":
                mcp_response["result"]["content"][0][response_type] = tool_response
            else: # List
                mcp_response["result"]["content"][0][response_type] = json.dumps(tool_response)
            
            # not used yet, return a resource list
            #    response_type = "resource"
            #    mcp_response = mcp_response_resources_call(id, response_type)
            #    mcp_response["result"]["content"][0][response_type] = {
            #        "kind": tool_name,
            #        "data": tool_response
            #    }
            
    log_response(mcp_response)
    return mcp_response