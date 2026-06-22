from flask import jsonify, request

users_wealth = {}

def handler(request):
    user_id = int(request.args.get("userId", 112))
    wealth = users_wealth.get(user_id, {
        "diamonds": 99999,
        "gold": 99999,
        "gDiamonds": 99999,
        "gDiamondsProfit": 99999,
        "money": 99999,
        "ngDiamonds": 99999,
        "sameUser": False,
        "firstPunch": False
    })
    return jsonify({"code": 1, "message": "Success", "data": {"userId": str(user_id), **wealth}})