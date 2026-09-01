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

# Use your MCP Server in e.g. ChatGPT or Claude: add a Remote MCP server with the URL API endpoint set for this script.
# Example: POST https://api.eu.peliqan.io/123/mcp?api_key=82ac4ac4-8ab6-4c2c-a039-548d19387d1d
#
# Add custom MCP tools: see below section, decorate your function with @mcp.tool()
# Version 2.2

api_key = pq.get_secret("SecretMCP")

import json
import inspect
import difflib
from typing import get_type_hints
from typing import get_origin, get_args
from typing import List, Dict, Any
from urllib.parse import parse_qs

dbconn = pq.dbconnect(pq.DW_NAME)

ENABLE_RAG = False
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

            TYPE_MAP = {"str": "string", "int": "number", "float": "number", "bool": "boolean"}

            for name, param in sig.parameters.items():
                hint = type_hints.get(name, str)
                origin = get_origin(hint)

                if origin in (list, List):
                    args = get_args(hint)
                    item_hint = args[0] if args else str
                    item_type_name = item_hint.__name__ if isinstance(item_hint, type) else "str"
                    prop = {
                        "type": "array",
                        "items": {"type": TYPE_MAP.get(item_type_name, "string")},
                        "description": param_docs.get(name, "")
                    }
                else:
                    type_str = hint.__name__ if isinstance(hint, type) else str(hint)
                    prop = {
                        "type": TYPE_MAP.get(type_str, "string"),
                        "description": param_docs.get(name, "")
                    }

                if param.default is not inspect._empty:
                    prop["default"] = param.default
                else:
                    required.append(name)
                properties[name] = prop

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
def insert_logboek(datum: str, samenvatting: str, peliqan_activiteit: str = "") -> str:
    """
    Voegt een dagelijkse samenvatting toe aan het logboek in de Logboek.logboek tabel.

    :param datum: Datum in YYYY-MM-DD formaat, bv. 2026-07-02
    :param samenvatting: De samenvatting van de dag
    :param peliqan_activiteit: Optionele beschrijving van Peliqan platformactiviteit
    """
    from datetime import datetime

    dbconn.execute(pq.DW_NAME, query="""
        CREATE TABLE IF NOT EXISTS "Logboek".logboek (
            datum DATE PRIMARY KEY,
            peliqan_activiteit TEXT,
            samenvatting TEXT,
            aangemaakt_op TIMESTAMP DEFAULT NOW()
        )
    """)

    sam = samenvatting.replace("'", "''")
    act = peliqan_activiteit.replace("'", "''")
    nu = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    dbconn.execute(pq.DW_NAME, query="""
        INSERT INTO "Logboek".logboek (datum, peliqan_activiteit, samenvatting, aangemaakt_op)
        VALUES ('""" + datum + """', '""" + act + """', '""" + sam + """', '""" + nu + """')
        ON CONFLICT (datum) DO UPDATE SET
            samenvatting = EXCLUDED.samenvatting,
            peliqan_activiteit = EXCLUDED.peliqan_activiteit,
            aangemaakt_op = EXCLUDED.aangemaakt_op
    """)
    return "Logboek entry opgeslagen voor " + datum

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
def preview_table_by_id(table_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Returns first n rows from a table, identified by its unique table ID instead of its name.

    :param table_id: the table ID, see list_tables for available IDs
    :param columns: optional comma-separated list of column names, e.g. "name,city". Leave empty for all columns.
    """
    table_meta = pq.get_table(table_id)
    qualified_name = table_meta["name_in_query"]
    query = f"SELECT * FROM {qualified_name} LIMIT {limit} "
    rows = dbconn.fetch(pq.DW_NAME, query=query)
    return rows

@mcp.tool()
def get_table_by_id(table_id: int, columns: str = "") -> List[Dict[str, Any]]:
    """
    Returns rows from a table, identified by its unique table ID instead of its name.

    :param table_id: the table ID, see list_tables for available IDs
    :param columns: optional comma-separated list of column names, e.g. "name,city". Leave empty for all columns.
    """
    table_meta = pq.get_table(table_id)
    qualified_name = table_meta["name_in_query"]
    col_list = [c.strip() for c in columns.split(",") if c.strip()]
    select_clause = ",".join(col_list) if col_list else "*"
    query = f"SELECT {select_clause} FROM {qualified_name}"
    rows = dbconn.fetch(pq.DW_NAME, query=query)
    return rows

@mcp.tool()
def execute_select(query: str) -> List[Dict[str, Any]]:
    """
    Executes a read-only SELECT query on the data warehouse.
    """
    if not query.strip().lower().startswith("select"):
        return [{"error": "Only SELECT queries are allowed."}]
    rows = dbconn.fetch(pq.DW_NAME, query=query)
    return rows

@mcp.tool()
def shoe_revenue_per_customer() -> List[Dict[str, Any]]:
    """
    Returns revenue per customer for invoices with at least one shoe product.
    """
    return dbconn.fetch(pq.DW_NAME, query="SELECT *  FROM totalWA1ShoePerCustomer_sf")

def _find_similar_companies_fuzzy(name, threshold=0.75, top_k=3):
    rows = dbconn.fetch(pq.DW_NAME, query='SELECT name FROM "MCPReverseETL".stg_companies')
    matches = []
    for row in rows:
        existing = row["name"]
        ratio = difflib.SequenceMatcher(None, name.strip().lower(), existing.strip().lower()).ratio()
        if ratio >= threshold:
            matches.append({"company_name": existing, "similarity": round(ratio, 2)})
    matches.sort(key=lambda m: m["similarity"], reverse=True)
    return matches[:top_k]

@mcp.tool()
def teamleader_find_similar_company(name: str) -> List[Dict[str, Any]]:
    """
    Finds existing companies with a similar name using fuzzy text matching, no embeddings needed.

    :param name: the company name to check
    """
    return _find_similar_companies_fuzzy(name)

@mcp.tool()
def teamleader_create_company(name: str, street: str = "", number: str = "", postal_code: str = "", city: str = "", country: str = "") -> str:
    """
    Creates a new company in Teamleader Focus, after checking for likely duplicates via fuzzy name matching.

    :param name: company name
    :param street: optional street name, required together with number, postal_code, city, country if any address field is given
    :param number: optional house/building number
    :param postal_code: optional postal code
    :param city: optional city
    :param country: optional ISO country code, e.g. BE
    """
    similar = _find_similar_companies_fuzzy(name)
    if similar:
        top = similar[0]
        return f"Niet aangemaakt: lijkt op bestaand bedrijf '{top['company_name']}' (gelijkenis {top['similarity']}). Roep teamleader_find_similar_company aan om te bevestigen, of pas de naam aan indien dit een ander bedrijf is."

    tl_api = pq.connect('Teamleader Focus')
    company = {"name": name}
    if street and number and postal_code and city and country:
        company["addresses"] = [{
            "address": {
                "street": street,
                "number": number,
                "postal_code": postal_code,
                "city": city,
                "country": country
            },
            "type": "primary"
        }]
    result = tl_api.add('company', company)
    return f"Company created: {result}"

@mcp.tool()
def teamleader_sync_companies_from_table(table_id: int) -> List[Dict[str, Any]]:
    """
    Reverse-ETL: creates a Teamleader company for every row in MCPReverseETL.stg_companies.
    Note: stg_companies has no postal_code column, so companies are created with name and email only, without an address.
    """
    table_meta = pq.get_table(table_id)
    qualified_name = table_meta["name_in_query"]
    tl_api = pq.connect('Teamleader Focus')
    query = f"SELECT * FROM {qualified_name}"
    rows = dbconn.fetch(pq.DW_NAME, query=query)
    results = []
    for row in rows:
        company = {"name": row["name"]}
        if row.get("email"):
            company["emails"] = [{"type": "primary", "email": row["email"]}]
        result = tl_api.add('company', company)
        results.append({"company_id": row["company_id"], "name": row["name"], "result": result})
    return results

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
        return "Unauthorized", 401

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
            mcp_response = ""
        elif mcp_req["method"] == "tools/list":
            mcp_response = mcp_response_tools_list(id)
        elif mcp_req["method"] == "tools/call":
            tool_name = mcp_req["params"]["name"]

            args = {}
            if "arguments" in mcp_req["params"]:
                args = mcp_req["params"]["arguments"]

            isError = False
            try:
                tool_response = globals()[tool_name](**args)
            except Exception as e:
                print(e)
                tool_response = str(e)
                isError = True

            tool_response_format = get_tool_response_format(tool_name)
            response_type = "text"
            mcp_response = mcp_response_tools_call(id, response_type)
            if isError:
                mcp_response["result"]["isError"] = isError
            
            if tool_response_format == "str":
                mcp_response["result"]["content"][0][response_type] = tool_response
            else:
                mcp_response["result"]["content"][0][response_type] = json.dumps(tool_response)
            
    log_response(mcp_response)
    return mcp_response