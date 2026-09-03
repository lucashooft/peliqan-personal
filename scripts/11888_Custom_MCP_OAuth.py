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

# MCP Server with oAuth
# Version 1.0
#
# SETUP IN PELIQAN:
# 
# Add two API endpoints and link them both to this script as APP handler (set both to Public):
#   POST /mcp
#   GET  /mcp/*/*
#
# ADDING CAPABILITIES TO THIS MCP SERVER:
#
# Add more MCP "tools": see below section in code with all MCP tools, decorate your function with @mcp.tool()


##### SETTINGS

# Provider
PROVIDER = "Microsoft" # "Google", "Microsoft" or "Peliqan"

# Peliqan account id
peliqan_account_id = 3166

# Add the usernames, and as value the personal API key from Peliqan (See user settings > API token). Store API keys in the Peliqan Secrets store !
user_mappings = {
    "test@peliqan.io": "INSERT YOUR PELIQAN API KEY HERE",
}

# Google client id:
google_client_id = "75886851179-su9mknnnf3f3sm2fi53fq7viobkjedod.apps.googleusercontent.com"

# Microsft Azure tenant id:
tenant_id = "a35e450d-10f3-43ec-bbb5-4f370161c30c"

# Microsoft token verification config:
MICROSOFT_EXPECTED_AUDIENCE = f"https://api.eu.peliqan.io/{peliqan_account_id}/mcp"

try:
    import jwt
    from jwt import PyJWKClient
    ms_jwks_client = PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys")
    ms_jwks_client.get_jwk_set()   # forces a real fetch now, at load time -- fails fast if tenant_id is garbage
    MICROSOFT_ISSUERS = (
        f"https://sts.windows.net/{tenant_id}/",         # v1.0 tokens
        f"https://login.microsoftonline.com/{tenant_id}/v2.0",  # v2.0 tokens
    )
except Exception:
    ms_jwks_client = None
    print(f"Warning: Microsoft JWKS unreachable with tenant_id={tenant_id!r} -- Microsoft provider disabled")

##### END OF SETTINGS
    
MCP_URL = f"https://api.eu.peliqan.io/{peliqan_account_id}/mcp"
AUTHORIZATION_SERVER_URL = MCP_URL


AUTHORIZATION_ENDPOINTS = {    
    "Peliqan" : f"https://app.eu.peliqan.io/oauth2/authorize",
    "Google" : "https://accounts.google.com/o/oauth2/v2/auth",
    "Microsoft" : f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize",
}

TOKEN_ENDPOINTS = {
    "Peliqan" : f"https://app.eu.peliqan.io/api/oauth2/token/",
    "Google" : "https://oauth2.googleapis.com/token",
    "Microsoft" : f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",  
}

SCOPES = {    
    "Peliqan" : ["openid", "email", "profile"],
    "Google" : ["openid", "email", "profile"],
    "Microsoft" : [f"{MCP_URL}/.default"],
}

AUTHORIZATION_ENDPOINT = AUTHORIZATION_ENDPOINTS[PROVIDER] 
TOKEN_ENDPOINT = TOKEN_ENDPOINTS[PROVIDER]
SCOPE = SCOPES[PROVIDER]

import json
import inspect
from typing import get_type_hints
from typing import List, Dict, Any
from urllib.parse import parse_qs
import base64

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

########################### ADD MCP TOOLS BELOW ###########################
@mcp.tool()
def say_hello(first_name: str, last_name: str = "") -> str:
    """
    Peliqan will say hello.

    :param first_name: the user's first name
    :param last_name: optional last name
    """
    return f"Hi there {first_name} {last_name} from Peliqan MCP server with OAuth!"

@mcp.tool()
def list_tables() -> List[Dict[str, Any]]:
    """
    Returns list of all the tables in the Peliqan account.
    """
    all_tables = []
    for db in pq_personal.list_databases():
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

    table_meta = pq_personal.get_table(table_id)
    columns = table_meta.get("all_fields", [])
    column_names = [col["name"] for col in columns if not col["name"].startswith("_sdc")]

    return column_names

@mcp.tool()
def execute_query(query: str) -> List[Dict[str, Any]]:
    """
    Executes an SQL query on the data warehouse and returns a list of rows.
    """
    query = query.replace('"' + pq_personal.DW_NAME + '".', '').replace(pq_personal.DW_NAME + '.', '')
    print("Final SQL query to execute:")
    print(query)
    dbconn = pq_personal.dbconnect(pq_personal.DW_NAME)
    rows = dbconn.fetch(pq_personal.DW_NAME, query = query)
    return rows

########################### END OF MCP TOOLS ###########################


def log_request(request):
    print("request method: ", request['method'])
    print("request url: ", request['url'])
    print("request query string: ", request['query_string'])
    print("request headers: ", request['headers'])
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

def mcp_response_oauth_protected_resource():
    # response for call to <mcp_server>/.well-known/oauth-protected-resource
    response_oauth_protected_resource = {
      "resource": MCP_URL,
      "authorization_servers": [AUTHORIZATION_SERVER_URL],
      "scopes_supported": SCOPE,
      "bearer_methods_supported": ["header"]
    }
    return response_oauth_protected_resource

def mcp_response_authorization_server_openid_config():
    # response for call to <authorization_server>/.well-known/openid-configuration
    response_openid_config_v2 = {
      "issuer": AUTHORIZATION_SERVER_URL,
      "authorization_endpoint": AUTHORIZATION_ENDPOINT,
      "token_endpoint": TOKEN_ENDPOINT,
      "response_types_supported": ["code"],
      "grant_types_supported": ["authorization_code"]
    }
    
    return response_openid_config_v2

def peliqan_access_token(access_token):
    import requests
    try:
        response = requests.get(
            "https://app.eu.peliqan.io/api/oauth2/userinfo/",
            headers={
                "Authorization": f"Bearer {access_token}"
            }
        )
        response.raise_for_status()
        userinfo = response.json()
    except Exception:
        return None
        
    username = userinfo.get("email")
    if not username:
        return None
    print(f"Peliqan email address from token: {username}")
    return username
    
def google_access_token(access_token):
    import requests
    try:
        response = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={
                "access_token": access_token
            }
        )        
        response.raise_for_status()
        tokeninfo = response.json()
    except Exception:
        return None

    # Ask Google to confirm this token was issued for our own client_id
    if google_client_id not in (tokeninfo.get("aud"), tokeninfo.get("azp")):
        return None

    username = tokeninfo.get("email")
    if not username:
        return None
    print(f"Google email address from token: {username}")

    return username

def microsoft_access_token(access_token):
    if ms_jwks_client is None:
        return None
    try:
        signing_key = ms_jwks_client.get_signing_key_from_jwt(access_token)
        claims = jwt.decode(
            access_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=MICROSOFT_EXPECTED_AUDIENCE,
        )
        if claims.get("iss") not in MICROSOFT_ISSUERS:
            return None
    except Exception:
        return None

    username = claims.get("upn") or claims.get("preferred_username")
    if not username:
        return None
    print(f"Azure username from token: {username}")
    
    return username

def check_access_token(access_token):
    global pq_personal

    if PROVIDER == "Google":
        username = google_access_token(access_token)
    elif PROVIDER == "Microsoft":
        username = microsoft_access_token(access_token)
    elif PROVIDER == "Peliqan":
        username = peliqan_access_token(access_token)
    else:
        return None
  
    if username not in user_mappings:
        return None
    else:
        # Apply user impersonation
        user_peliqan_api_key = user_mappings[username]
        pq_personal = Peliqan(user_peliqan_api_key)
      
    return username
    
def handler(request):
    log_request(request)

    if "/.well-known/oauth-protected-resource" in request['url']:
        print("MCP Server URL: oAuth protected resource URL called")
        return mcp_response_oauth_protected_resource()
    
    elif "/.well-known/openid-configuration" in request['url']:
        print("Authorization server URL: openid configuration called")
        return mcp_response_authorization_server_openid_config()
        
    elif "Authorization" in request['headers']:
        print("Checking Authorization header (should contain access token)")
        access_token = request['headers']['Authorization'].replace("Bearer ", "")
        username = check_access_token(access_token)
        if not username:
            print("Unknown user, make sure to add the username to user_mappings")
            return "Unauthorized", 401
        
    else:
        print("Not authorized")
        protected_resource_url = f"{MCP_URL}/.well-known/oauth-protected-resource"
        return "Unauthorized", 401, { 'WWW-Authenticate': f'Bearer resource_metadata="{protected_resource_url}"' }
    
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

            args = {}
            if "arguments" in mcp_req["params"]:
                args = mcp_req["params"]["arguments"]

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
            
    log_response(mcp_response)
    return mcp_response