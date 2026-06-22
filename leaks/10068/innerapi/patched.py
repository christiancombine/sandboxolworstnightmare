import os
import json
import re
import functools
import threading
from datetime import datetime
import hashlib
import time
from flask import Flask, request, jsonify, g, render_template
import pyodbc
import redis
import sqlite3
from dotenv import load_dotenv

# ---------------- Thread Lock ----------------
lock = threading.Lock()

# ---------------- Deep Merge Helper ----------------
def safe_parse(s, default):
    try:
        return json.loads(s)
    except:
        return default

def get_db():
    return sqlite3.connect(DB_PATH)

def ensure_table_exists(table):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            userId INTEGER NOT NULL,
            subKey TEXT NOT NULL DEFAULT 'default',
            data TEXT NOT NULL DEFAULT '{{}}',
            PRIMARY KEY (userId, subKey)
        )
        """)
        conn.commit()
    return table

def ensure_row_exists(table, user_id, sub_key, default_data=None):
    default_data = default_data or {}
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM {table} WHERE userId=? AND subKey=?", (user_id, sub_key))
        if not cur.fetchone():
            cur.execute(f"INSERT INTO {table} (userId, subKey, data) VALUES (?, ?, ?)",
                        (user_id, sub_key, json.dumps(default_data)))
        conn.commit()

def clean_and_merge(existing, incoming):
    merged = dict(existing)

    # Remove keys
    if "__rm" in incoming:
        for key in incoming["__rm"]:
            merged.pop(key, None)
        incoming = {k: v for k, v in incoming.items() if k != "__rm"}

    # Add / merge arrays or dicts
    if "__add" in incoming:
        incoming = {k: v for k, v in incoming.items() if k != "__add"}
        for k, v in incoming.items():
            if isinstance(v, list):
                merged.setdefault(k, [])
                merged[k].extend(v)
            elif isinstance(v, dict):
                merged.setdefault(k, {})
                merged[k].update(v)
            else:
                merged[k] = v
        return merged

    merged.update(incoming)
    return merged

# ---------------- Api Logger ----------------
LOG_FILE = os.path.join(os.path.dirname(__file__), "http_requests.log")

def log_request_response(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        req_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "method": request.method,
            "url": request.path,
            "query": request.args.to_dict(),
            "headers": dict(request.headers),
            "body": request.get_json(silent=True)
        }
        g._req_log = req_data
        resp = func(*args, **kwargs)
        try:
            resp_data = resp.get_json() if hasattr(resp, "get_json") else str(resp)
        except:
            resp_data = str(resp)
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

# This will create a new empty SQLite database file (robust against corrupt files)
if os.path.exists(DB_PATH):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1;")
    except sqlite3.DatabaseError:
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        corrupt_path = f"{DB_PATH}.corrupt.{ts}.bak"
        # ensure connection is closed before attempting to move the file
        try:
            if conn:
                conn.close()
        except Exception:
            pass
        try:
            os.rename(DB_PATH, corrupt_path)
            print(f"Backed up corrupt DB to: {corrupt_path}")
        except PermissionError:
            # file locked by another process — try copying as a fallback
            try:
                import shutil
                shutil.copyfile(DB_PATH, corrupt_path)
                print(f"Copied corrupt DB to: {corrupt_path} (original still in use)")
            except Exception as e:
                print("Unable to backup DB file (it may be locked by another process):", e)
        except Exception as e:
            print("Failed to move corrupt DB file:", e)
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

# create fresh DB if missing or after backup
try:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS test(id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
except Exception as e:
    print("Failed to create fresh DB file:", e)

def get_mssql_db():
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={MSSQL_SERVER};"
        f"DATABASE={MSSQL_DATABASE};"
        f"UID={MSSQL_USER};"
        f"PWD={MSSQL_PASSWORD};"
        f"Encrypt={'yes' if MSSQL_ENCRYPT else 'no'};"
    )
    try:
        return pyodbc.connect(conn_str, autocommit=False)
    except Exception as e:
        # If ODBC driver is missing (development machine), fallback to local sqlite
        print("pyodbc connect failed, falling back to sqlite:", e)
        return sqlite3.connect(DB_PATH)

# ---------------- Redis ----------------
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
SECRET_KEY = "pq0194mxoqfh48L362G6R09T737E273X"

# ---------------- Helpers ----------------
def log_http(req, resp_data=None):
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
    print(json.dumps(log_entry, indent=2, ensure_ascii=False))

def safe_parse(s, fallback=None):
    try:
        return json.loads(s)
    except:
        return fallback if fallback is not None else {}

def ensure_table_exists(table_name):
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", table_name)
    sql = f"""
    CREATE TABLE IF NOT EXISTS [{safe_name}] (
        userId INTEGER NOT NULL,
        subKey TEXT NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY (userId, subKey)
    )
    """
    with lock, sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
    return safe_name

def ensure_row_exists(table, user_id, sub_key, default_data=None):
    with lock, sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM {table} WHERE userId=? AND subKey=?", (user_id, sub_key))
        if not cur.fetchone():
            cache_data = {}
            if str(user_id) in players and str(sub_key) in players[str(user_id)]:
                cache_data = players[str(user_id)][str(sub_key)]
            elif players.get(str(user_id), {}).get("props"):
                cache_data = players[str(user_id)]["props"]
            data_str = json.dumps(default_data or cache_data or {})
            cur.execute(f"INSERT INTO {table} (userId, subKey, data) VALUES (?,?,?)", (user_id, sub_key, data_str))
            conn.commit()

def verify_signature(params):
    timestamp = params.get("timestamp", "")
    nonce = params.get("nonce", "")
    friendIds = params.get("friendIds", "")
    userId = params.get("userId", "")
    signature = params.get("signature", "")
    data = f"{timestamp}{nonce}{friendIds}{userId}{SECRET_KEY}"
    return hashlib.sha1(data.encode()).hexdigest() == signature

# ---------------- Routes ----------------
@app.route('/')
def home():
    return render_template('index.html')

def clean_and_merge(existing, incoming):
    # Copy existing data
    merged = dict(existing)

    # Handle __rm (remove keys)
    if "__rm" in incoming:
        for key in incoming["__rm"]:
            merged.pop(key, None)
        incoming = {k: v for k, v in incoming.items() if k != "__rm"}

    # Handle __add (merge arrays/dicts instead of overwrite)
    if "__add" in incoming:
        incoming = {k: v for k, v in incoming.items() if k != "__add"}
        for k, v in incoming.items():
            if isinstance(v, list):
                merged.setdefault(k, [])
                merged[k].extend(v)
            elif isinstance(v, dict):
                merged.setdefault(k, {})
                merged[k].update(v)
            else:
                merged[k] = v
        return merged

    # Normal merge
    merged.update(incoming)
    return merged


@app.route("/api/v2/game/data", methods=["GET"])
def get_game_data():
    table = request.args.get("tableName")
    user_id = request.args.get("userId")
    sub_key = request.args.get("subKey")
    if not table or not user_id or not sub_key:
        return jsonify({"code": 3, "message": "tableName, userId, subKey required"}), 400

    table = ensure_table_exists(table)
    ensure_row_exists(table, user_id, sub_key)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT data FROM {table} WHERE userId=? AND subKey=?", (user_id, sub_key))
        row = cur.fetchone()
        db_data = safe_parse(row[0], {}) if row else {}

    tmp_cache_data = players.get(str(user_id), {}).get(str(sub_key), {})
    merged = clean_and_merge(db_data, tmp_cache_data)
    return jsonify({"code": 1, "message": "success", "data": merged})

@app.route("/api/v2/game/data", methods=["POST"])
def post_game_data():
    table = request.args.get("tableName")
    if not table:
        return jsonify({"code": 3, "message": "tableName required"}), 400

    table = ensure_table_exists(table)
    payload = request.json if isinstance(request.json, list) else [request.json]

    try:
        with get_db() as conn:
            cur = conn.cursor()
            for row in payload:
                user_id = row.get("userId")
                sub_key = str(row.get("subKey", "default"))

                ensure_row_exists(table, user_id, sub_key)
                cur.execute(f"SELECT data FROM {table} WHERE userId=? AND subKey=?", (user_id, sub_key))
                db_row = cur.fetchone()
                existing_data = safe_parse(db_row[0], {}) if db_row else {}

                # Merge existing data with new payload
                merged = clean_and_merge(existing_data, row)
                cur.execute(f"UPDATE {table} SET data=? WHERE userId=? AND subKey=?",
                            (json.dumps(merged), user_id, sub_key))
            conn.commit()
        return jsonify({"code": 1, "message": "success"})
    except Exception as e:
        return jsonify({"code": 4, "message": "inner error", "detail": str(e)}), 500

# ---------------- Wealth Endpoint ----------------
@app.route("/pay/i/api/v1/wealth/users/<int:user_id>", methods=["GET"])
def get_wealth(user_id):
    table = ensure_table_exists("UserData")
    ensure_row_exists(table, user_id, "wealth", {
        "diamonds": 0, "gold": 0, "gDiamonds": 0,
        "gDiamondsProfit": 0, "money": 0, "ngDiamonds": 0,
        "sameUser": False, "firstPunch": False
    })
    with lock, sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT data FROM {table} WHERE userId=? AND subKey='wealth'", (user_id,))
        row = cur.fetchone()
        wealth = safe_parse(row[0], {})
        users_wealth[user_id] = wealth
        return jsonify({"code": 1, "message": "Success", "data": {"userId": str(user_id), **wealth}})
        
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
            "gDiamonds": 0, "gDiamondsProfit": 0,
            "ngDiamonds": 0, "gold": 0,
            "money": 0, "diamonds": 0
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
            return jsonify({"code": 2,"message": f"Rename cooldown active. Wait {int(remaining)} seconds.","cooldown": int(remaining)}), 429
        existing_party["name"] = party_name
        party_id = existing_party["partyId"]
        user_cds["rename"] = now
    else:
        remaining = PARTY_COOLDOWNS["create"] - (now - user_cds.get("create", 0))
        if remaining > 0:
            return jsonify({"code": 2,"message": f"Create cooldown active. Wait {int(remaining)} seconds.","cooldown": int(remaining)}), 429
        party_id = f"party_{len(parties) + 1}"
        parties[party_id] = {"partyId": party_id,"gameId": game_id,"ownerId": user_id,"name": party_name,"members": [user_id],"type": party_type}
        user_cds["create"] = now
    party_cooldowns[user_id] = user_cds
    return jsonify({"code": 1,"message": "Success","data": parties[party_id],"cooldown": PARTY_COOLDOWNS["rename"] if existing_party else PARTY_COOLDOWNS["create"]})

# ---------------- Game Props ----------------
@app.route("/gameaide/api/v1/inner/user/game/props", methods=["GET"])
def game_props():
    user_id = int(request.args.get("userId", 112))
    user_props = users_props.get(user_id, {})
    props_list = [{"propId": prop_id, "count": count, "extra": {}} for prop_id, count in user_props.items()]
    return jsonify({"code": 1, "message": "Success", "data": props_list})

# ---------------- Recharge Info ----------------
@app.route("/pay/api/v1/inner/user/game/recharge/sum/gDiamond", methods=["GET"])
def user_game_recharge_sum():
    user_id = request.args.get("userId", "0")
    game_id = request.args.get("gameId", "unknown")
    return jsonify({"code": 1, "message": "Success", "data": {"userId": user_id, "gameId": game_id, "gDiamondSum": 0}})

@app.route("/pay/api/v1/pay/inner/has/user/recharge", methods=["GET"])
def has_user_recharge():
    user_id = request.args.get("userId", "0")
    return jsonify({"code": 1, "message": "Success", "data": {"userId": user_id, "hasRecharge": False}})

# ---------------- Treasure Box ----------------
@app.route("/activity/api/v1/inner/collect/exchange/treasurebox/timeline", methods=["GET"])
def treasurebox_timeline():
    return jsonify({"code": 1, "message": "Success", "data": []})

@app.route("/activity/api/v1/inner/collect/exchange/game/props", methods=['GET', 'POST'])
def collect_exchange_props():
    try:
        params = request.args
        user_id = str(params.get('userId'))
        game_id = params.get('gameId', 'g1008')
        game_props_id = params.get('gamePropsId', 'YaoShi:1')
        props_amount = int(params.get('propsAmount', 1))
        expiry_date = int(params.get('expiryDate', 0))

        if not user_id:
            return jsonify({"code": 400, "message": "Missing userId"}), 400

        players.setdefault(user_id, {"props": {}})
        players[user_id]["props"].setdefault(game_props_id, 0)
        players[user_id]["props"][game_props_id] += props_amount

        return jsonify({"code": 200, "message": "OK"})
    except Exception as e:
        return jsonify({"code": 500, "message": "Internal Server Error", "error": str(e)}), 500

# ---------------- Segment Info ----------------
@app.route("/gameaide/api/v1/inner/user/segment/info", methods=["GET"])
def segment_info():
    import json
    user_id = int(request.args.get("userId", 112))
    table = "UserSegmentInfo"

    SEGMENTS = [
        (0, 1000),  # Bronze
        (1, 1100),  # Silver
        (2, 1300),  # Gold
        (3, 1600),  # Diamond
        (4, 2100),  # Challenger
    ]

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()

        # Create table if it doesn't exist
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            userId INTEGER PRIMARY KEY,
            subKey TEXT NOT NULL DEFAULT 'default',
            integral INTEGER NOT NULL DEFAULT 1000,
            segment INTEGER NOT NULL DEFAULT 0,
            rank INTEGER NOT NULL DEFAULT 0,
            timeRemains INTEGER NOT NULL DEFAULT 0,
            needReward INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL DEFAULT '{{}}'
        )
        """)

        # Insert user if missing
        cur.execute(f"SELECT userId FROM {table} WHERE userId=? AND subKey=?", (user_id, "default"))
        if not cur.fetchone():
            cur.execute(
                f"INSERT INTO {table} (userId, subKey, data) VALUES (?, ?, ?)",
                (user_id, "default", json.dumps({}))
            )
            conn.commit()

        # Update segment based on integral
        cur.execute(f"SELECT userId, integral FROM {table}")
        all_users = cur.fetchall()
        for u in all_users:
            segment = 0
            for seg, min_integral in reversed(SEGMENTS):
                if u[1] >= min_integral:
                    segment = seg
                    break
            cur.execute(f"UPDATE {table} SET segment=? WHERE userId=?", (segment, u[0]))
        conn.commit()

        # Update ranks by integral descending
        cur.execute(f"SELECT userId FROM {table} ORDER BY integral DESC")
        ranked_users = cur.fetchall()
        for i, u in enumerate(ranked_users):
            cur.execute(f"UPDATE {table} SET rank=? WHERE userId=?", (i, u[0]))
        conn.commit()

        # Fetch the user's info
        cur.execute(f"""
            SELECT userId, segment, rank, integral, timeRemains, needReward
            FROM {table} WHERE userId=?""",
            (user_id,)
        )
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

# ---------------- Game Rank ----------------
@app.route("/api/v1/game/rank/list", methods=["GET"])
def game_rank_list():
    start = int(request.args.get("start", 0))
    end = int(request.args.get("end", 30))
    ranks = [{"userId": 112, "score": 9999}]
    return jsonify({"code": 1, "message": "Success", "data": ranks[start:end]})

# ---------------- Friends Filter ----------------
@app.route("/friend/api/v1/inner/friends/filter", methods=["GET"])
def filter_friends():
    user_id = int(request.args.get("userId", 0))
    friend_ids = request.args.get("friendIds", "")
    all_friends = {160:[112,113,144,176], 112:[160,144]}
    requested_ids = [int(fid) for fid in friend_ids.split(",") if fid.isdigit()]
    filtered = [fid for fid in all_friends.get(user_id, []) if fid in requested_ids]
    return jsonify({"code": 1, "message": "Success", "data":[{"userId":fid,"relation":1} for fid in filtered]})

# ---------------- User Details ----------------
@app.route("/user/api/v1/inner/user/details", methods=["GET"])
def user_details():
    user_id = int(request.args.get("userId", 112))
    table = ensure_table_exists("UserDetails")
    ensure_row_exists(table, user_id, "details", {"nickName": "Gyt3lyz", "level": 1, "experience": 0})
    with lock, sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT data FROM {table} WHERE userId=? AND subKey='details'", (user_id,))
        row = cur.fetchone()
        user = safe_parse(row[0], {"nickName": "Gyt3lyz", "level": 1, "experience": 0}) if row else {"nickName": "Gyt3lyz", "level": 1, "experience": 0}
    return jsonify({"code": 1, "message": "Success", "data": {"userId": user_id, **user}})

# ---------------- Player Identity Config ----------------
@app.route("/config/files/player-identity-config", methods=["GET"])
def player_identity_config():
    return jsonify({"code": 1, "message": "Success", "data": {}})

# ---------------- Activity Settlement Rules ----------------
@app.route("/activity/api/v1/inner/activity/games/settlement/rule", methods=["GET"])
def settlement_rule():
    return jsonify({"code": 1, "message": "Success", "data": {"rules": []}})

# ---------------- My Game Build ----------------
@app.route("/gameaide/api/v1/inner/my/game/build", methods=["GET"])
def get_my_game_build():
    user_id = int(request.args.get("userId", 0))
    game_id = request.args.get("gameId", "unknown")
    build_data = {"userId": user_id, "gameId": game_id, "buildGroups": [{"groupId": user_id, "role": "master", "members": []}]}
    return jsonify({"code": 1, "message": "Success", "data": build_data})
    
@app.route("/charmingtown/api/v1/inner/manor/praise", methods=["POST"])
def manor_praise():
    data = request.get_json(silent=True) or {}
    userId = data.get("userId")
    targetUserId = data.get("targetUserId")

    if not userId or not targetUserId:
        return jsonify({
            "code": 400,
            "msg": "missing params"
        })

    increase_praise(targetUserId)
    count = get_praise_count(targetUserId)

    return jsonify({
        "code": 0,
        "msg": "ok",
        "data": {
            "praiseCount": count
        }
    })

# ---------------- Run ----------------
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, debug=True)