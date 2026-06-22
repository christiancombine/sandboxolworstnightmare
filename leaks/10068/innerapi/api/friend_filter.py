from flask import jsonify, request

def handler(request):
    user_id = int(request.args.get("userId", 0))
    friend_ids = request.args.get("friendIds", "")
    all_friends = {160: [112, 113, 144, 176], 112: [160, 144]}
    friends_list = all_friends.get(user_id, [])
    requested_ids = [int(fid) for fid in friend_ids.split(",") if fid.isdigit()]
    filtered = [fid for fid in friends_list if fid in requested_ids]
    return jsonify({
        "code": 1,
        "message": "Success",
        "data": [{"userId": fid, "relation": 1} for fid in filtered]
    })