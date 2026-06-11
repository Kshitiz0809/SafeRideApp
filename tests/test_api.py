import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "api" / "index.py"
SPEC = importlib.util.spec_from_file_location("api_index", MODULE_PATH)
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


class SafeRideBackendTests(unittest.TestCase):
    def test_accepts_mobile_snake_case_payload(self):
        response, status = api._process_sync_payload(
            {
                "timestamp": "2026-06-11T20:00:00",
                "windows": [
                    {
                        "id": "w1",
                        "ride_id": "ride-1",
                        "speed": 40,
                        "max_roll": 10,
                        "max_cornering_intensity": 30,
                        "jerk_variance": 5,
                        "window_score": 100,
                    },
                    {
                        "id": "w2",
                        "ride_id": "ride-1",
                        "speed": 55,
                        "max_roll": 18,
                        "max_cornering_intensity": 90,
                        "jerk_variance": 12,
                        "window_score": 96,
                    },
                ],
            },
            {},
        )

        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["rideId"], "ride-1")
        self.assertEqual(response["overallScore"], 97.8)
        self.assertEqual(response["summary"]["avgSpeed"], 47.5)
        self.assertEqual(response["summary"]["windowCount"], 2)

    def test_accepts_legacy_camel_case_payload(self):
        response, status = api._process_sync_payload(
            {
                "windows": [
                    {
                        "id": "w1",
                        "rideId": "ride-2",
                        "speed": 80,
                        "maxRoll": 25,
                        "maxCorneringIntensity": 130,
                        "jerkVariance": 30,
                        "windowScore": 50,
                    }
                ]
            },
            {},
        )

        self.assertEqual(status, 200)
        self.assertEqual(response["rideId"], "ride-2")
        self.assertEqual(response["overallScore"], 40.0)
        self.assertEqual(response["summary"]["harshWindowCount"], 1)

    def test_rejects_bad_numeric_payload(self):
        response, status = api._process_sync_payload({"windows": [{"speed": "fast"}]}, {})

        self.assertEqual(status, 400)
        self.assertEqual(response["status"], "error")
        self.assertIn("speed must be a number", response["error"])

    def test_rejects_missing_windows(self):
        response, status = api._process_sync_payload({"windows": []}, {})

        self.assertEqual(status, 400)
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"], "windows must be a non-empty array")


if __name__ == "__main__":
    unittest.main()
