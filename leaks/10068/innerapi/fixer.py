import time
import hashlib
import requests

# --- Configuration ---
BASE_URL = "http://127.0.0.1:8080"
SECRET = "pq0194mxoqfh48L362G6R09T737E273X"  # your server secret

# --- Generic request function ---
def make_signed_request(endpoint, params, sign_fields):
    # Generate fresh timestamp and nonce if not provided
    timestamp = str(int(time.time() * 1000))
    nonce = timestamp
    params.setdefault("timestamp", timestamp)
    params.setdefault("nonce", nonce)
    
    # Generate signature
    to_sign = "".join(str(params[field]) for field in sign_fields) + SECRET
    signature = hashlib.sha1(to_sign.encode()).hexdigest()
    params["signature"] = signature
    
    # Send GET request
    print(f"Requesting {endpoint} with params:\n{params}\n")
    response = requests.get(BASE_URL + endpoint, params=params)
    
    try:
        return response.json()
    except Exception:
        return response.text  # fallback to raw text if not JSON

# --- Main execution ---
if __name__ == "__main__":
    # Example: request game data
    params = {
        "tableName": "g1055",
        "userId": "112",
        "subKey": "default"
    }
    sign_fields = ["tableName", "userId", "subKey", "timestamp", "nonce"]
    
    result = make_signed_request("/api/v2/game/data", params, sign_fields)
    print("Server response:")
    print(result)