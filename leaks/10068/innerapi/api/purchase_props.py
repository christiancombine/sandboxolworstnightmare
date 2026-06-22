from flask import jsonify, request

users_wealth = {}
users_props = {}

def handler(request):
    body = request.json or {}
    user_id = int(body.get("userId", 112))
    prop_id = int(body.get("propsId", 1))
    quantity = int(body.get("quantity", 1))

    # default wealth
    wealth = users_wealth.get(user_id, {"gDiamonds": 99999})
    if wealth["gDiamonds"] >= quantity:
        wealth["gDiamonds"] -= quantity
    else:
        quantity = wealth["gDiamonds"]
        wealth["gDiamonds"] = 0
    users_wealth[user_id] = wealth

    # add props
    users_props.setdefault(user_id, {})
    users_props[user_id][prop_id] = users_props[user_id].get(prop_id, 0) + quantity

    response_data = {
        "userId": user_id,
        "propId": prop_id,
        "quantity": quantity,
        "currentGCube": wealth["gDiamonds"],
        "gDiamonds": wealth["gDiamonds"]
    }
    return jsonify({"code": 1, "msg": "success", "data": response_data})