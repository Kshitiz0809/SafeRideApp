from http.server import BaseHTTPRequestHandler
import json

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data)

        # Extraction logic
        windows = data.get('windows', [])
        
        if not windows:
            self._send_response({"error": "No telemetry data provided"}, 400)
            return

        # --- SCORING ALGORITHM ---
        # 1. Calculate Average Window Score
        avg_window_score = sum(w['windowScore'] for w in windows) / len(windows)

        # 2. Calculate Penalties
        # Penalty for extreme jerk (braking/acceleration)
        max_jerk = max(w['jerkVariance'] for w in windows)
        jerk_penalty = max(0, (max_jerk - 20) * 0.5)

        # Penalty for aggressive cornering
        max_ci = max(w['maxCorneringIntensity'] for w in windows)
        cornering_penalty = max(0, (max_ci - 150) * 0.1)

        # 3. Final Overall Score Calculation
        overall_score = avg_window_score - jerk_penalty - cornering_penalty
        overall_score = max(0, min(100, overall_score)) # Clamp between 0-100

        # Determine Safety Rating
        rating = "Excellent"
        if overall_score < 60: rating = "Needs Improvement"
        elif overall_score < 85: rating = "Good"

        response = {
            "status": "success",
            "overallScore": round(overall_score, 1),
            "rating": rating,
            "processedWindows": len(windows),
            "summary": {
                "avgSpeed": sum(w['speed'] for w in windows) / len(windows),
                "maxRoll": max(w['maxRoll'] for w in windows)
            }
        }

        self._send_response(response, 200)

    def _send_response(self, message, status_code):
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(message).encode())

    def do_GET(self):
        self._send_response({"status": "Backend is running!"}, 200)
