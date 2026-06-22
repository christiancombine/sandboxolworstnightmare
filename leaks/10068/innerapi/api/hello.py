# api/collect_exchange_props.py
from flask import Flask, request, jsonify
import json

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def handler():
    # Your previous Flask route code
    user_id = request.args.get('userId', '112')
    return jsonify({"code": 200, "message": f"Hello {user_id}!"})