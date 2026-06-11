from http.server import BaseHTTPRequestHandler
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import firebase_admin
    from firebase_admin import auth, credentials, firestore
except ImportError:
    firebase_admin = None
    auth = None
    credentials = None
    firestore = None


FIREBASE_APP = None


def _initialize_firebase() -> None:
    global FIREBASE_APP
    if firebase_admin is None:
        return
    if FIREBASE_APP is not None:
        return
    if firebase_admin._apps:
        FIREBASE_APP = firebase_admin.get_app()
        return

    raw_service_account = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not raw_service_account:
        return

    try:
        service_account_info = json.loads(raw_service_account)
        cred = credentials.Certificate(service_account_info)
        FIREBASE_APP = firebase_admin.initialize_app(cred)
    except Exception as exc:
        print(f"Firebase Init Error: {exc}")


_initialize_firebase()


def _first_present(source: Dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return default


def _as_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field_name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a number")
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise ValueError(f"{field_name} must be finite")
    return parsed


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _normalize_window(raw_window: Dict[str, Any], index: int) -> Dict[str, Any]:
    if not isinstance(raw_window, dict):
        raise ValueError(f"windows[{index}] must be an object")

    speed = _as_float(_first_present(raw_window, ("speed",), 0.0), f"windows[{index}].speed")
    max_roll = _as_float(
        _first_present(raw_window, ("max_roll", "maxRoll"), 0.0),
        f"windows[{index}].max_roll",
    )
    max_cornering_intensity = _as_float(
        _first_present(raw_window, ("max_cornering_intensity", "maxCorneringIntensity"), 0.0),
        f"windows[{index}].max_cornering_intensity",
    )
    jerk_variance = _as_float(
        _first_present(raw_window, ("jerk_variance", "jerkVariance"), 0.0),
        f"windows[{index}].jerk_variance",
    )
    window_score = _as_float(
        _first_present(raw_window, ("window_score", "windowScore"), 100.0),
        f"windows[{index}].window_score",
    )

    return {
        "id": _first_present(raw_window, ("id", "windowId")),
        "timestamp": _first_present(raw_window, ("timestamp", "created_at", "createdAt")),
        "ride_id": _first_present(raw_window, ("ride_id", "rideId")),
        "user_id": _first_present(raw_window, ("user_id", "userId")),
        "speed": _clamp(speed, 0.0, 300.0),
        "max_roll": _clamp(abs(max_roll), 0.0, 180.0),
        "max_cornering_intensity": max(0.0, max_cornering_intensity),
        "jerk_variance": max(0.0, jerk_variance),
        "window_score": _clamp(window_score, 0.0, 100.0),
    }


def _percentile(sorted_values: List[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]

    rank = (len(sorted_values) - 1) * percentile
    lower_index = int(rank)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = rank - lower_index
    return sorted_values[lower_index] + (sorted_values[upper_index] - sorted_values[lower_index]) * fraction


def _average(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _calculate_ride_score(windows: List[Dict[str, Any]]) -> Dict[str, Any]:
    scores = [window["window_score"] for window in windows]
    speeds = [window["speed"] for window in windows]
    rolls = [window["max_roll"] for window in windows]
    cornering = [window["max_cornering_intensity"] for window in windows]
    jerk_variances = [window["jerk_variance"] for window in windows]

    sorted_scores = sorted(scores)
    avg_window_score = _average(scores)
    p10_score = _percentile(sorted_scores, 0.10)

    harsh_windows = sum(
        1
        for window in windows
        if window["window_score"] < 60.0
        or window["jerk_variance"] > 25.0
        or window["max_cornering_intensity"] > 120.0
    )
    harsh_ratio = harsh_windows / len(windows)

    # The app already scores each 3-second window using jerk and cornering penalties.
    # The backend aggregates those windows without re-penalizing normal variance, while
    # still giving short dangerous bursts enough influence to affect the final ride.
    overall_score = (avg_window_score * 0.85) + (p10_score * 0.15) - (harsh_ratio * 10.0)
    overall_score = round(_clamp(overall_score, 0.0, 100.0), 1)

    return {
        "overallScore": overall_score,
        "summary": {
            "windowCount": len(windows),
            "avgWindowScore": round(avg_window_score, 1),
            "lowestWindowScore": round(min(scores), 1),
            "maxRoll": round(max(rolls), 1),
            "avgSpeed": round(_average(speeds), 1),
            "maxSpeed": round(max(speeds), 1),
            "maxCorneringIntensity": round(max(cornering), 1),
            "maxJerkVariance": round(max(jerk_variances), 1),
            "harshWindowCount": harsh_windows,
        },
    }


def _store_ride_result(
    ride_id: str,
    user_id: Optional[str],
    normalized_windows: List[Dict[str, Any]],
    result: Dict[str, Any],
) -> None:
    if FIREBASE_APP is None or firestore is None:
        return

    try:
        db = firestore.client()
        payload = {
            "rideId": ride_id,
            "userId": user_id,
            "score": result["overallScore"],
            "summary": result["summary"],
            "windowCount": len(normalized_windows),
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }
        db.collection("rides").document(ride_id).set(payload, merge=True)
    except Exception as exc:
        print(f"Firestore Save Error: {exc}")


def _verify_bearer_token(headers: Any) -> Optional[Dict[str, Any]]:
    auth_header = headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or FIREBASE_APP is None or auth is None:
        return None

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None

    try:
        return auth.verify_id_token(token)
    except Exception as exc:
        print(f"Firebase Token Verification Error: {exc}")
        return None


def _process_sync_payload(payload: Dict[str, Any], headers: Any) -> Tuple[Dict[str, Any], int]:
    raw_windows = payload.get("windows")
    if not isinstance(raw_windows, list) or not raw_windows:
        return {"status": "error", "error": "windows must be a non-empty array"}, 400

    try:
        normalized_windows = [_normalize_window(window, index) for index, window in enumerate(raw_windows)]
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}, 400

    decoded_token = _verify_bearer_token(headers)
    token_user_id = decoded_token.get("uid") if decoded_token else None
    ride_id = _first_present(payload, ("ride_id", "rideId")) or normalized_windows[0].get("ride_id")
    user_id = _first_present(payload, ("user_id", "userId")) or token_user_id or normalized_windows[0].get("user_id")

    if not ride_id:
        ride_id = f"anonymous-{normalized_windows[0].get('id') or payload.get('timestamp') or 'ride'}"

    result = _calculate_ride_score(normalized_windows)
    result.update(
        {
            "status": "success",
            "rideId": ride_id,
            "userId": user_id,
            "authVerified": decoded_token is not None,
        }
    )

    _store_ride_result(ride_id, user_id, normalized_windows, result)
    return result, 200


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self._send_response({}, 204)

    def do_GET(self) -> None:
        self._send_response(
            {
                "status": "ok",
                "service": "SafeRide backend",
                "firebaseConfigured": FIREBASE_APP is not None,
                "endpoints": {
                    "POST /": "sync telemetry windows",
                    "POST /sync": "sync telemetry windows",
                    "GET /health": "health check",
                },
            },
            200,
        )

    def do_POST(self) -> None:
        payload, error = self._read_json_body()
        if error is not None:
            self._send_response({"status": "error", "error": error}, 400)
            return

        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", "/sync", "/api/sync"):
            response, status_code = _process_sync_payload(payload, self.headers)
            self._send_response(response, status_code)
            return

        self._send_response({"status": "error", "error": "Not found"}, 404)

    def _read_json_body(self) -> Tuple[Dict[str, Any], Optional[str]]:
        content_length_header = self.headers.get("Content-Length", "0")
        try:
            content_length = int(content_length_header)
        except ValueError:
            return {}, "Content-Length must be an integer"

        if content_length <= 0:
            return {}, "Request body is required"

        try:
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}, "Request body must be valid JSON"
        except UnicodeDecodeError:
            return {}, "Request body must be UTF-8 encoded"

        if not isinstance(payload, dict):
            return {}, "Request body must be a JSON object"
        return payload, None

    def _send_response(self, message: Dict[str, Any], status_code: int) -> None:
        body = b"" if status_code == 204 else json.dumps(message).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
