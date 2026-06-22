from flask import jsonify, request

def handler(request):
    user_id = request.args.get("userId", "0")
    return jsonify({
        "code": 1,
        "message": "Success",
        "data": {"userId": user_id, "hasRecharge": False}
    })
