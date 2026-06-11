from http.server import BaseHTTPRequestHandler
import json
import os
import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase Admin using Environment Variable
firebase_app = None
if "FIREBASE_SERVICE_ACCOUNT" in os.environ:
    try:
        service_account_info = json.loads(os.environ.get("FIREBASE_SERVICE_ACCOUNT"))
        cred = credentials.Certificate(service_account_info)
        firebase_app = firebase_admin.initialize_app(cred)
    except Exception as e:
        print(f"Firebase Init Error: {e}")

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data)

        windows = data.get('windows', [])
        if not windows:
            self._send_response({"error": "No data"}, 400)
            return

        # Calculate Score
        avg_window_score = sum(w['windowScore'] for w in windows) / len(windows)
        max_jerk = max(w['jerkVariance'] for w in windows)
        max_ci = max(w['maxCorneringIntensity'] for w in windows)
        
        overall_score = round(max(0, min(100, avg_window_score - (max_jerk * 0.2))), 1)
        
        ride_id = windows[0].get('rideId', 'unknown')
        
        response = {
            "status": "success",
            "overallScore": overall_score,
            "summary": {
                "maxRoll": round(max(w['maxRoll'] for w in windows), 1),
                "avgSpeed": round(sum(w['speed'] for w in windows) / len(windows), 1)
            }
        }

        # Save to Firestore if initialized
        if firebase_app:
            try:
                db = firestore.client()
                db.collection('rides').document(ride_id).set({
                    'score': overall_score,
                    'maxRoll': response['summary']['maxRoll'],
                    'avgSpeed': response['summary']['avgSpeed'],
                    'timestamp': firestore.SERVER_TIMESTAMP,
                    'window_count': len(windows)
                }, merge=True)
            except Exception as e:
                print(f"Firestore Save Error: {e}")

        self._send_response(response, 200)

    def _send_response(self, message, status_code):
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(message).encode())

    def do_GET(self):
        self._send_response({"status": "Backend Active"}, 200)
