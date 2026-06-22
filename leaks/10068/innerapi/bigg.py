import os
import json
import re
import functools
from datetime import datetime
import hashlib
import time
from flask import Flask, request, jsonify, g
import pyodbc
import redis
import sqlite3
from dotenv import load_dotenv
from flask import Flask, render_template

# ---------------- Api Logger ----------------

LOG_FILE = os.path.join(os.path.dirname(__file__), "http_requests.log")

def log_request_response(func):
    """Decorator to log request and response data."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Log request
        req_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "method": request.method,
            "url": request.path,
            "query": request.args.to_dict(),
            "headers": dict(request.headers),
            "body": request.get_json(silent=True)
        }
        g._req_log = req_data  # store temporarily

        # Call the original route function
        resp = func(*args, **kwargs)

        try:
            resp_data = resp.get_json() if hasattr(resp, "get_json") else str(resp)
        except:
            resp_data = str(resp)

        # Log response
        log_entry = {**req_data, "response": resp_data}
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        print(json.dumps(log_entry, indent=2, ensure_ascii=False))
        return resp
    return wrapper

# ---------------- Load Config ----------------
load_dotenv()

MSSQL_USER = os.getenv("MSSQL_USER")
MSSQL_PASSWORD = os.getenv("MSSQL_PASSWORD")
MSSQL_SERVER = os.getenv("MSSQL_SERVER")
MSSQL_DATABASE = os.getenv("MSSQL_DATABASE")
MSSQL_ENCRYPT = os.getenv("MSSQL_ENCRYPT", "false").lower() == "true"

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
PORT = int(os.getenv("PORT", 8080))

# ---------------- App + DB ----------------
app = Flask(__name__)

DB_PATH = "blockman_go.db"

def get_db():
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={MSSQL_SERVER};"
        f"DATABASE={MSSQL_DATABASE};"
        f"UID={MSSQL_USER};"
        f"PWD={MSSQL_PASSWORD};"
        f"Encrypt={'yes' if MSSQL_ENCRYPT else 'no'};"
    )
    return pyodbc.connect(conn_str, autocommit=False)

# ---------------- Apply decorator to all routes ----------------
# This should be applied after all routes are registered, not here.

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# ---------------- In-Memory ----------------
users_wealth = {}
users_tasks = {}
users_achievements = {}
users_props = {}
users_ranks = {}
parties = {}
party_cooldowns = {}
players = {}

# ---------------- Config ----------------
PARTY_COOLDOWNS = {"create": 300, "rename": 60}  # seconds

# ---------------- Helpers ----------------

LOG_FILE = os.path.join(os.path.dirname(__file__), "http_requests.log")

def log_http(req, resp_data=None):
    """
    Logs HTTP request and optional response in JSON format.
    """
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "method": req.method,
        "url": req.path,
        "query": req.args.to_dict(),
        "headers": dict(req.headers),
        "body": req.get_json(silent=True),
        "response": resp_data
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    # Optional console output
    print(json.dumps(log_entry, indent=2, ensure_ascii=False))

def safe_parse(s, fallback=None):
    try:
        return json.loads(s)
    except:
        return fallback if fallback is not None else {}

def ensure_table_exists(table_name):
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", table_name)
    sql = f"""
    IF OBJECT_ID('{safe_name}', 'U') IS NULL
    BEGIN
      CREATE TABLE [{safe_name}] (
        userId BIGINT NOT NULL,
        subKey NVARCHAR(256) NOT NULL,
        data NVARCHAR(MAX) NOT NULL,
        CONSTRAINT PK_{safe_name} PRIMARY KEY (userId, subKey)
      )
    END
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
    return safe_name

def ensure_row_exists(table, user_id, sub_key, default_data=None):
    """Ensure the row exists in the SQL table. If missing, create it using tmp cache data if available."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM {table} WHERE userId=? AND subKey=?", (user_id, sub_key))
        if not cur.fetchone():
            # Use tmp cache if available
            cache_data = {}
            if str(user_id) in players and str(sub_key) in players[str(user_id)]:
                cache_data = players[str(user_id)][str(sub_key)]
            elif players.get(str(user_id), {}).get("props"):
                cache_data = players[str(user_id)]["props"]
            data_str = json.dumps(default_data or cache_data or {})
            cur.execute(f"INSERT INTO {table} (userId, subKey, data) VALUES (?,?,?)", (user_id, sub_key, data_str))
            conn.commit()

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

SECRET_KEY = "pq0194mxoqfh48L362G6R09T737E273X"

def verify_signature(params):
    timestamp = params.get("timestamp", "")
    nonce = params.get("nonce", "")
    friendIds = params.get("friendIds", "")
    userId = params.get("userId", "")
    signature = params.get("signature", "")

    data = f"{timestamp}{nonce}{friendIds}{userId}{SECRET_KEY}"
    hash_object = hashlib.sha1(data.encode())
    calculated_signature = hash_object.hexdigest()
    return calculated_signature == signature
# ---------------- API ----------------
@app.route('/')
def home():
    return render_template('index.html')

@app.route("/api/v2/game/data", methods=["GET"])
def get_game_data():
    table = request.args.get("tableName")
    user_id = request.args.get("userId")
    sub_key = request.args.get("subKey")

    if not table or not user_id or not sub_key:
        return jsonify({"code": 3, "message": "tableName, userId, subKey required"}), 400

    try:
        user_id = int(user_id)
    except ValueError:
        return jsonify({"code": 4, "message": "Invalid userId"}), 400

    table = ensure_table_exists(table)
    ensure_row_exists(table, user_id, sub_key)

    # Fetch DB data
    with lock, sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT data FROM {table} WHERE userId=? AND subKey=?", (user_id, sub_key))
        row = cur.fetchone()
        db_data = safe_parse(row[0], {}) if row else {}

    # Merge with cache if available
    tmp_cache_data = players.get(str(user_id), {}).get(str(sub_key), {})
    merged = deep_merge(db_data, tmp_cache_data)

    return jsonify({"code": 1, "message": "success", "data": merged})

# --- POST endpoint ---
@app.route("/api/v2/game/data", methods=["POST"])
def post_game_data():
    table = request.args.get("tableName") or (request.json.get("tableName") if request.json else None)
    if not table:
        return jsonify({"code": 3, "message": "tableName required"}), 400

    payload = request.json if isinstance(request.json, list) else [request.json]

    table = ensure_table_exists(table)

    try:
        with lock, sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            for row in payload:
                # Extract required fields
                user_id = int(row.get("userId", 0))
                sub_key = str(row.get("subKey", "default"))

                # Clean extra fields
                for f in ["__rm", "tableName", "updatedAt"]:
                    row.pop(f, None)

                # Ensure DB row exists
                default_data = players.get(str(user_id), {}).get(sub_key, {})
                ensure_row_exists(table, user_id, sub_key, default_data=default_data)

                # Fetch current DB data
                cur.execute(f"SELECT data FROM {table} WHERE userId=? AND subKey=?", (user_id, sub_key))
                db_row = cur.fetchone()
                existing_data = safe_parse(db_row[0], {})

                # Merge: DB data + cache + incoming POST
                tmp_cache_data = players.get(str(user_id), {}).get(sub_key, {})
                merged = deep_merge(existing_data, tmp_cache_data)
                merged = deep_merge(merged, row)

                # Save merged data
                cur.execute(f"UPDATE {table} SET data=? WHERE userId=? AND subKey=?",
                            (json.dumps(merged), user_id, sub_key))

            conn.commit()
        return jsonify({"code": 1, "message": "success"})
    except Exception as e:
        return jsonify({"code": 4, "message": "inner error", "detail": str(e)}), 500

@app.route("/pay/i/api/v1/wealth/users/<int:user_id>", methods=["GET"])
def get_wealth(user_id):
    table = ensure_table_exists("UserData")
    ensure_row_exists(table, user_id, "wealth", {"diamonds": 99999, "gold": 99999, "gDiamonds": 99999,
                                                 "gDiamondsProfit": 99999, "money": 99999, "ngDiamonds": 99999,
                                                 "sameUser": False, "firstPunch": False})
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT data FROM {table} WHERE userId=? AND subKey='wealth'", (user_id,))
        row = cur.fetchone()
        wealth = safe_parse(row[0], {})
        users_wealth[user_id] = wealth
        return jsonify({"code": 1, "message": "Success", "data": {"userId": str(user_id), **wealth}})

from flask import request, jsonify
from datetime import datetime

@app.route("/pay/api/v2/inner/pay/users/purchase/game/props", methods=["POST", "GET"])
def purchase_props_db():
    try:
        body = request.json if request.is_json else {}
        user_id = int(body.get("userId") or request.args.get("userId") or 112)
        prop_id = int(body.get("propsId") or request.args.get("propsId") or 1)
        quantity = int(body.get("quantity") or request.args.get("quantity") or 1)
        currency = str(body.get("currency") or request.args.get("currency") or "gDiamonds")
        if currency in ["0", "", "null", "undefined", None]:
            currency = "gDiamonds"

        table = ensure_table_exists("UserData")
        ensure_row_exists(table, user_id, "wealth", {
            "gDiamonds": 99999, "gDiamondsProfit": 99999,
            "ngDiamonds": 99999, "gold": 99999,
            "money": 99999, "diamonds": 99999
        })

        # Load current wealth from DB
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT data FROM {table} WHERE userId=? AND subKey='wealth'", (user_id,))
            row = cur.fetchone()
            wealth_data = json.loads(row[0]) if row else {}

            # Make sure currency exists
            if currency not in wealth_data:
                wealth_data[currency] = 0

            # Deduct or cap quantity
            if wealth_data[currency] >= quantity:
                wealth_data[currency] -= quantity
            else:
                quantity = wealth_data[currency]
                wealth_data[currency] = 0

            # Save updated wealth
            cur.execute(f"""
            MERGE {table} AS target
            USING (SELECT ? AS userId, ? AS subKey) AS src
            ON (target.userId=src.userId AND target.subKey=src.subKey)
            WHEN MATCHED THEN UPDATE SET data=?
            WHEN NOT MATCHED THEN INSERT (userId, subKey, data) VALUES (?, ?, ?);
            """, (user_id, "wealth", json.dumps(wealth_data),
                  user_id, "wealth", json.dumps(wealth_data)))
            conn.commit()

        # Update in-memory cache if used
        users_wealth[user_id] = wealth_data
        users_props.setdefault(user_id, {})
        users_props[user_id][prop_id] = users_props[user_id].get(prop_id, 0) + quantity

        # Prepare response
        response_data = {
            "userId": user_id,
            "propId": prop_id,
            "quantity": quantity,
            "currentGCube": int(wealth_data.get("gDiamonds", 0)),
            "gDiamonds": int(wealth_data.get("gDiamonds", 0)),
            "gDiamondsProfit": int(wealth_data.get("gDiamondsProfit", 0)),
            "ngDiamonds": int(wealth_data.get("ngDiamonds", 0)),
            "gold": int(wealth_data.get("gold", 0)),
            "money": int(wealth_data.get("money", 0)),
            "diamonds": int(wealth_data.get("diamonds", 0)),
            "sameUser": False,
            "firstPunch": False,
            "updatedAt": datetime.utcnow().isoformat() + "Z"
        }

        log_http(request, response_data)

        return jsonify({"code": 1, "msg": "success", "data": response_data})

    except Exception as e:
        return jsonify({"code": 0, "msg": "error", "error": str(e)})

# ---------------- Party Endpoints ----------------
@app.route("/gameaide/api/v1/inner/game/party/create", methods=["POST"])
def create_or_rename_party():
    user_id = int(request.args.get("userId", 0))
    game_id = request.args.get("gameId", "unknown")
    party_name = request.json.get("name") if request.is_json else f"Party_{user_id}"
    party_type = int(request.json.get("type", 0))

    now = time.time()
    user_cds = party_cooldowns.get(user_id, {"create": 0, "rename": 0})

    existing_party = next((p for p in parties.values() if p["ownerId"] == user_id and p["type"] == party_type), None)

    if existing_party:
        remaining = PARTY_COOLDOWNS["rename"] - (now - user_cds.get("rename", 0))
        if remaining > 0:
            return jsonify({
                "code": 2,
                "message": f"Rename cooldown active. Wait {int(remaining)} seconds.",
                "cooldown": int(remaining)
            }), 429
        existing_party["name"] = party_name
        party_id = existing_party["partyId"]
        user_cds["rename"] = now
    else:
        remaining = PARTY_COOLDOWNS["create"] - (now - user_cds.get("create", 0))
        if remaining > 0:
            return jsonify({
                "code": 2,
                "message": f"Create cooldown active. Wait {int(remaining)} seconds.",
                "cooldown": int(remaining)
            }), 429
        party_id = f"party_{len(parties) + 1}"
        parties[party_id] = {
            "partyId": party_id,
            "gameId": game_id,
            "ownerId": user_id,
            "name": party_name,
            "members": [user_id],
            "type": party_type
        }
        user_cds["create"] = now

    party_cooldowns[user_id] = user_cds

    return jsonify({
        "code": 1,
        "message": "Success",
        "data": parties[party_id],
        "cooldown": PARTY_COOLDOWNS["rename"] if existing_party else PARTY_COOLDOWNS["create"]
    })

@app.route("/gameaide/api/v1/inner/game/party/list/<string:game_id>", methods=["GET"])
def game_party_list(game_id):
    type_ = request.args.get("type", "0")
    filtered = [p for p in parties.values() if p["gameId"] == game_id and str(p["type"]) == type_]
    return jsonify({"code": 1, "message": "Success", "data": filtered})

@app.route("/gameaide/api/v1/inner/game/party/like/<string:game_id>/<int:target_user_id>", methods=["GET"])
def get_game_party_like(game_id, target_user_id):
    user_id = int(request.args.get("userId", 0))
    return jsonify({"code": 1, "message": "Success", "data": {"userId": user_id, "targetUserId": target_user_id, "likes": 0}})

# ---------------- Other endpoints ----------------
@app.route("/gameaide/api/v1/inner/my/game/build", methods=["GET"])
def get_my_game_build():
    user_id = int(request.args.get("userId", 0))
    game_id = request.args.get("gameId", "unknown")
    build_data = {"userId": user_id, "gameId": game_id, "buildGroups": [{"groupId": user_id, "role": "master", "members": []}]}
    return jsonify({"code": 1, "message": "Success", "data": build_data})

@app.route("/activity/api/v1/inner/collect/exchange/treasurebox/timeline", methods=["GET"])
def treasurebox_timeline():
    return jsonify({"code": 1, "message": "Success", "data": []})

@app.route("/activity/api/v1/inner/activity/games/settlement/rule", methods=["GET"])
def settlement_rule():
    return jsonify({"code": 1, "message": "Success", "data": {"rules": []}})

@app.route("/config/files/player-identity-config", methods=["GET"])
def player_identity_config():
    return jsonify({"code": 1, "message": "Success", "data": {}})

@app.route("/user/api/v1/inner/user/details", methods=["GET"])
def user_details():
    user_id = int(request.args.get("userId", 112))
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""IF OBJECT_ID('UserDetails', 'U') IS NULL
                       BEGIN
                         CREATE TABLE UserDetails (
                           userId BIGINT NOT NULL PRIMARY KEY,
                           nickName NVARCHAR(100) NOT NULL,
                           level INT NOT NULL DEFAULT 1,
                           experience BIGINT NOT NULL DEFAULT 0
                         )
                       END""")
        conn.commit()
        cur.execute("SELECT userId,nickName,level,experience FROM UserDetails WHERE userId=?", (user_id,))
        row = cur.fetchone()
        if row:
            user = {"userId": row[0], "nickName": row[1], "level": row[2], "experience": row[3]}
        else:
            user = {"userId": user_id, "nickName": "Gyt3lyz", "level": 1, "experience": 0}
            cur.execute("INSERT INTO UserDetails (userId,nickName,level,experience) VALUES (?,?,?,?)",
                        (user_id, user["nickName"], user["level"], user["experience"]))
            conn.commit()
    return jsonify({"code": 1, "message": "Success", "data": user})

@app.route("/api/v1/game/rank/list", methods=["GET"])
def game_rank_list():
    key = request.args.get("key")
    start = int(request.args.get("start", 0))
    end = int(request.args.get("end", 30))
    ranks = [{"userId": 112, "score": 9999}]
    return jsonify({"code": 1, "message": "Success", "data": ranks[start:end]})

@app.route("/gameaide/api/v1/inner/user/segment/info", methods=["GET"])
def segment_info():
    user_id = int(request.args.get("userId", 112))
    table = "UserSegmentInfo"
    SEGMENTS = [
        (0, 1000),  # Bronze
        (1, 1100),  # Silver
        (2, 1300),  # Gold
        (3, 1600),  # Diamond
        (4, 2100),  # Challenger
    ]

    with get_db() as conn:
        cur = conn.cursor()
        # Ensure table exists
        cur.execute(f"""
        IF OBJECT_ID('{table}', 'U') IS NULL
        BEGIN
            CREATE TABLE {table} (
                userId BIGINT PRIMARY KEY,
                segment INT DEFAULT 0,
                rank INT DEFAULT 0,
                integral INT DEFAULT 1000,
                timeRemains INT DEFAULT 0,
                needReward INT DEFAULT 0
            )
        END
        """)
        conn.commit()

        # Insert user if missing
        cur.execute(f"SELECT userId FROM {table} WHERE userId=?", (user_id,))
        if not cur.fetchone():
            cur.execute(f"INSERT INTO {table} (userId) VALUES (?)", (user_id,))
            conn.commit()

        # Fetch all users
        cur.execute(f"SELECT userId, integral FROM {table}")
        all_users = cur.fetchall()

        # Update segments based on integral
        for u in all_users:
            segment = 0
            for seg, min_integral in reversed(SEGMENTS):
                if u.integral >= min_integral:
                    segment = seg
                    break
            cur.execute(f"UPDATE {table} SET segment=? WHERE userId=?", (segment, u.userId))
        conn.commit()

        # Update ranks by integral descending
        cur.execute(f"SELECT userId FROM {table} ORDER BY integral DESC")
        ranked_users = cur.fetchall()
        for i, u in enumerate(ranked_users):
            cur.execute(f"UPDATE {table} SET rank=? WHERE userId=?", (i, u.userId))
        conn.commit()

        # Return user info
        cur.execute(f"SELECT userId, segment, rank, integral, timeRemains, needReward FROM {table} WHERE userId=?", (user_id,))
        row = cur.fetchone()
        data = {
            "userId": row[0],
            "segment": row[1],
            "rank": row[2],
            "integral": row[3],
            "timeRemains": row[4],
            "needReward": row[5]
        }

    return jsonify({"code": 1, "message": "Success", "data": data})

@app.route("/pay/api/v1/pay/inner/has/user/recharge", methods=["GET"])
def has_user_recharge():
    user_id = request.args.get("userId", "0")
    return jsonify({
        "code": 1,
        "message": "Success",
        "data": {"userId": user_id, "hasRecharge": False}
    })

@app.route("/gameaide/api/v1/inner/user/game/props", methods=["GET"])
def game_props():
    user_id = int(request.args.get("userId", 112))
    # Example: make sure props are dicts
    user_props = users_props.get(user_id, {})
    props_list = []
    for prop_id, count in user_props.items():
        props_list.append({
            "propId": prop_id,
            "count": count,
            "extra": {}  # if BedWar expects additional fields
        })
    return jsonify({"code": 1, "message": "Success", "data": props_list})

@app.route("/pay/api/v1/inner/user/game/recharge/sum/gDiamond", methods=["GET"])
def user_game_recharge_sum():
    user_id = request.args.get("userId", "0")
    game_id = request.args.get("gameId", "unknown")
    return jsonify({
        "code": 1,
        "message": "Success",
        "data": {"userId": user_id, "gameId": game_id, "gDiamondSum": 0}
    })

@app.route("/friend/api/v1/inner/friends/filter", methods=["GET"])
def filter_friends():
    user_id = int(request.args.get("userId", 0))
    friend_ids = request.args.get("friendIds", "")
    
    # ----------------------
    # For local testing, bypass signature verification
    # ----------------------
    # if not verify_signature(request.args):
    #     return jsonify({"code": 401, "message": "Invalid signature"}), 401
    
    # Example friend data
    all_friends = {
        160: [112, 113, 144, 176],
        112: [160, 144],
    }
    friends_list = all_friends.get(user_id, [])
    requested_ids = [int(fid) for fid in friend_ids.split(",") if fid.isdigit()]
    filtered = [fid for fid in friends_list if fid in requested_ids]

    return jsonify({
        "code": 1,
        "message": "Success",
        "data": [{"userId": fid, "relation": 1} for fid in filtered]  # relation=1 means friend
    })

@app.route('/activity/api/v1/inner/collect/exchange/game/props', methods=['GET', 'POST'])
def collect_exchange_props():
    try:
        params = request.args
        user_id = params.get('userId')
        game_id = params.get('gameId', 'g1008')
        game_props_id = params.get('gamePropsId', 'YaoShi:1')
        props_amount = int(params.get('propsAmount', 1))
        expiry_date = int(params.get('expiryDate', 0))

        if not user_id:
            return jsonify({"code": 400, "message": "Missing userId"}), 400

        # Initialize player
        if user_id not in players:
            players[user_id] = {"props": {}}

        # Initialize prop
        if game_props_id not in players[user_id]["props"]:
            players[user_id]["props"][game_props_id] = 0

        players[user_id]["props"][game_props_id] += props_amount

        print(f"Added {props_amount} of {game_props_id} to player {user_id} (expiry {expiry_date} sec)")
        return jsonify({"code": 200, "message": "OK"})

    except Exception as e:
        print(f"Error handling collect_exchange_props: {e}, raw params: {request.args}")
        return jsonify({"code": 500, "message": "Internal Server Error"}), 500

# ---------------- Run ----------------
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, debug=True)