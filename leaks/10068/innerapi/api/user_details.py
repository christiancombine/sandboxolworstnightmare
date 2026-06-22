from datetime import datetime
from flask import jsonify, request

def handler(request):
    user_id = int(request.args.get("userId", 112))
    user = {
        "userId": user_id,
        "nickName": "Gyt3lyz",
        "level": 1,
        "experience": 0
    }
    return jsonify({"code": 1, "message": "Success", "data": user})