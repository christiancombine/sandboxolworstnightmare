import os
import json
import functools
from datetime import datetime
import hashlib
from flask import Flask, request, jsonify, g

# ---------------- App ----------------
app = Flask(__name__)

# ---------------- In-Memory ----------------
users_wealth = {}
users_props = {}
players = {}
parties = {}
party_cooldowns = {}

PARTY_COOLDOWNS = {"create": 300, "rename": 60}  # seconds
SECRET_KEY = "pq0194mxoqfh48L362G6R09T737E273X"

# ---------------- Helpers ----------------
def safe_parse(s, fallback=None):
    try:
        return json.loads(s)
    except:
        return fallback if fallback is not None else {}

def log_http(req, resp_data=None):
    """Optional logging"""
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "method": req.method,
        "url": req.path,
        "query": req.args.to_dict(),
        "headers": dict(req.headers),
        "body": req.get_json(silent=True),
        "response": resp_data
    }
    print(json.dumps(log_entry, indent=2, ensure_ascii=False))

# ---------------- Endpoints ----------------

@app.route("/activity/api/v1/inner/collect/exchange/game/props", methods=['GET', 'POST'])
def collect_exchange_props():
    params = request.args
    user_id = params.get('userId', '112')
    game_props_id = params.get('gamePropsId', 'YaoShi:1')
    props_amount = int(params.get('propsAmount', 1))
    players.setdefault(user_id, {"props": {}})
    players[user_id]["props"][game_props_id] = players[user_id]["props"].get(game_props_id, 0) + props_amount
    return jsonify({"code": 200, "message": "OK"})

@app.route("/friend/api/v1/inner/friends/filter", methods=["GET"])
def filter_friends():
    user_id = int(request.args.get("userId", 0))
    friend_ids = request.args.get("friendIds", "")
    all_friends = {160: [112, 113, 144, 176], 112: [160, 144]}
    friends_list = all_friends.get(user_id, [])
    requested_ids = [int(fid) for fid in friend_ids.split(",") if fid.isdigit()]
    filtered = [fid for fid in friends_list if fid in requested_ids]
    return jsonify({"code": 1, "message": "Success", "data": [{"userId": fid, "relation": 1} for fid in filtered]})

@app.route("/gameaide/api/v1/inner/user/game/props", methods=["GET"])
def game_props():
    user_id = int(request.args.get("userId", 112))
    user_props = users_props.get(user_id, {})
    props_list = [{"propId": pid, "count": count, "extra": {}} for pid, count in user_props.items()]
    return jsonify({"code": 1, "message": "Success", "data": props_list})

@app.route("/gameaide/api/v1/inner/user/segment/info", methods=["GET"])
def segment_info():
    user_id = int(request.args.get("userId", 112))
    data = {"userId": user_id, "segment": 2, "rank": 1, "integral": 1200, "timeRemains": 0, "needReward": 0}
    return jsonify({"code": 1, "message": "Success", "data": data})

@app.route("/pay/api/v1/pay/inner/has/user/recharge", methods=["GET"])
def has_user_recharge():
    user_id = request.args.get("userId", "0")
    return jsonify({"code": 1, "message": "Success", "data": {"userId": user_id, "hasRecharge": False}})

@app.route("/user/api/v1/inner/user/details", methods=["GET"])
def user_details():
    user_id = int(request.args.get("userId", 112))
    user = {"userId": user_id, "nickName": "Gyt3lyz", "level": 1, "experience": 0}
    return jsonify({"code": 1, "message": "Success", "data": user})

@app.route("/pay/v2/inner/pay/users/purchase/game/props", methods=["POST"])
@app.route("/pay/api/v2/inner/pay/users/purchase/game/props", methods=["POST"])
def purchase_props():
    body = request.json if request.is_json else {}
    user_id = int(body.get("userId", 112))
    prop_id = int(body.get("propsId", 1))
    quantity = int(body.get("quantity", 1))
    currency = body.get("currency", "gDiamonds")
    users_wealth.setdefault(user_id, {"gDiamonds": 99999})
    if currency not in users_wealth[user_id]:
        users_wealth[user_id][currency] = 0
    if users_wealth[user_id][currency] >= quantity:
        users_wealth[user_id][currency] -= quantity
    else:
        quantity = users_wealth[user_id][currency]
        users_wealth[user_id][currency] = 0
    users_props.setdefault(user_id, {})
    users_props[user_id][prop_id] = users_props[user_id].get(prop_id, 0) + quantity
    resp = {"userId": user_id, "propId": prop_id, "quantity": quantity, "currentGCube": users_wealth[user_id].get("gDiamonds", 0)}
    log_http(request, resp)
    return jsonify({"code": 1, "msg": "success", "data": resp})

@app.route("/pay/i/api/v1/wealth/users/<int:user_id>", methods=["GET"])
def get_wealth(user_id):
    users_wealth.setdefault(user_id, {"diamonds": 99999, "gold": 99999, "gDiamonds": 99999})
    return jsonify({"code": 1, "message": "Success", "data": {"userId": str(user_id), **users_wealth[user_id]}})

# ---------------- Required by Vercel ----------------
# Do NOT include app.run()
# Vercel automatically invokes Flask app via WSGI
