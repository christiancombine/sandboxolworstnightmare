from flask import jsonify, request

# In-memory storage for demo purposes
users_props = {}

def handler(request):
    user_id = int(request.args.get("userId", 112))
    user_props = users_props.get(user_id, {})
    props_list = [{"propId": pid, "count": count, "extra": {}} for pid, count in user_props.items()]
    return jsonify({"code": 1, "message": "Success", "data": props_list})