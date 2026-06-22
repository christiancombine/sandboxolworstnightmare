import os, json, re, functools, hashlib, time
from datetime import datetime
import pyodbc
import redis
from flask import request

# Database
def get_db():
    MSSQL_USER = os.getenv("MSSQL_USER")
    MSSQL_PASSWORD = os.getenv("MSSQL_PASSWORD")
    MSSQL_SERVER = os.getenv("MSSQL_SERVER")
    MSSQL_DATABASE = os.getenv("MSSQL_DATABASE")
    MSSQL_ENCRYPT = os.getenv("MSSQL_ENCRYPT", "false").lower() == "true"

    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={MSSQL_SERVER};"
        f"DATABASE={MSSQL_DATABASE};"
        f"UID={MSSQL_USER};"
        f"PWD={MSSQL_PASSWORD};"
        f"Encrypt={'yes' if MSSQL_ENCRYPT else 'no'};"
    )
    return pyodbc.connect(conn_str, autocommit=False)

# Special merge for game data
def apply_special_merge(existing, incoming):
    if not isinstance(existing, dict):
        existing = {}
    result = dict(existing)
    if "__rm" in incoming and isinstance(incoming["__rm"], list):
        for k in incoming["__rm"]:
            result.pop(k, None)
    for k, v in incoming.items():
        if k == "__rm":
            continue
        if isinstance(v, dict):
            if "__add" in v:
                new_v = dict(v)
                new_v.pop("__add", None)
                result[k] = new_v
            else:
                result[k] = apply_special_merge(result.get(k, {}), v)
        else:
            result[k] = v
    result["updatedAt"] = datetime.utcnow().isoformat()
    return result