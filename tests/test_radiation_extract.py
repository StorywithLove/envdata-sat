import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock, patch

from src.radiation_extract import fetch_location, validate_native_cadence


class NativeCadenceTests(unittest.TestCase):
    def test_himawari_ten_minute(self):
        self.assertEqual(
            validate_native_cadence(
                ["2026-11-19T00:00", "2026-11-19T00:10", "2026-11-19T00:20"],
                strict_10min=True,
            ),
            [600, 600, 600],
        )

    def test_global_fifteen_minute_native(self):
        self.assertEqual(
            validate_native_cadence(
                ["2026-11-19T00:00", "2026-11-19T00:15", "2026-11-19T00:30"],
                strict_10min=False,
            ),
            [900, 900, 900],
        )

    def test_global_cadence_can_change_across_archive(self):
        self.assertEqual(
            validate_native_cadence(
                ["2026-11-19T00:00", "2026-11-19T00:10", "2026-11-19T00:20",
                 "2026-11-19T00:35", "2026-11-19T00:50"],
                strict_10min=False,
            ),
            [600, 600, 600, 900, 900],
        )

    def test_reject_hourly_fallback(self):
        with self.assertRaises(ValueError):
            validate_native_cadence(
                ["2026-11-19T00:00", "2026-11-19T01:00", "2026-11-19T02:00"],
                strict_10min=False,
            )

    def test_reject_himawari_wrong_cadence(self):
        with self.assertRaises(ValueError):
            validate_native_cadence(
                ["2026-11-19T00:00", "2026-11-19T00:15", "2026-11-19T00:30"],
                strict_10min=True,
            )


class DailyBackwardAverageWindowTests(unittest.TestCase):
    @patch("src.radiation_extract.requests.get")
    def test_daily_city_export_uses_144_interval_end_labels(self, get):
        day_start = datetime(2026, 11, 19, tzinfo=timezone.utc)
        timestamps = [
            (day_start + timedelta(minutes=10 * i)).strftime("%Y-%m-%dT%H:%M")
            for i in range(288)
        ]
        values = [0.0] * len(timestamps)
        values[2] = None
        timestamps.pop(3)  # Provider omitted the 00:30 interval-end timestamp.
        values.pop(3)
        response = Mock()
        response.json.return_value = {
            "model": "jma_jaxa_himawari",
            "hourly": {"time": timestamps, "shortwave_radiation": values},
        }
        get.return_value = response
        location = {
            "station_id": "wuhan", "station_name": "Wuhan", "province": "Hubei",
            "latitude": 30.59, "longitude": 114.30,
        }

        rows, summary = fetch_location(
            location, date(2026, 11, 19), date(2026, 11, 19),
            "jma_jaxa_himawari",
        )

        self.assertEqual(get.call_args.kwargs["params"]["end_date"], "2026-11-20")
        self.assertEqual(len(rows), 144)
        self.assertEqual(rows[0]["time_utc"], "2026-11-19T00:10:00Z")
        self.assertEqual(rows[-1]["time_utc"], "2026-11-20T00:00:00Z")
        self.assertEqual(rows[0]["temporal_resolution_seconds"], 600)
        self.assertEqual(rows[0]["ghi_wm2"], 0.0)
        self.assertEqual(rows[1]["ghi_wm2"], "")
        self.assertEqual(rows[1]["quality_flag"], "provider_returned_null")
        self.assertEqual(rows[2]["ghi_wm2"], "")
        self.assertEqual(rows[2]["quality_flag"], "provider_timestamp_missing")
        self.assertEqual(summary["missing_timestamp_records"], 1)
        self.assertEqual(summary["provider_null_records"], 1)
        self.assertEqual(summary["temporal_semantics"], "backward_average_interval_end")

    @patch("src.radiation_extract.requests.get")
    def test_successful_empty_provider_day_is_recorded_as_missing(self, get):
        response = Mock()
        response.json.return_value = {
            "model": "jma_jaxa_himawari",
            "hourly": {"time": [], "shortwave_radiation": []},
        }
        get.return_value = response
        location = {
            "station_id": "wuhan", "station_name": "Wuhan", "province": "Hubei",
            "latitude": 30.59, "longitude": 114.30,
        }

        rows, summary = fetch_location(
            location, date(2026, 11, 19), date(2026, 11, 19),
            "jma_jaxa_himawari",
        )

        self.assertEqual(len(rows), 144)
        self.assertTrue(all(row["ghi_wm2"] == "" for row in rows))
        self.assertTrue(all(row["quality_flag"] == "provider_timestamp_missing" for row in rows))
        self.assertEqual(summary["missing_timestamp_records"], 144)
        self.assertFalse(summary["cadence_verified"])

    @patch("src.radiation_extract.requests.get")
    def test_sparse_provider_steps_are_preserved_but_not_claimed_verified(self, get):
        day_start = datetime(2026, 11, 19, tzinfo=timezone.utc)
        timestamps = [
            (day_start + timedelta(minutes=30 * index)).strftime("%Y-%m-%dT%H:%M")
            for index in range(49)
        ]
        response = Mock()
        response.json.return_value = {
            "model": "jma_jaxa_himawari",
            "hourly": {"time": timestamps, "shortwave_radiation": [100.0] * len(timestamps)},
        }
        get.return_value = response
        location = {
            "station_id": "wuhan", "station_name": "Wuhan", "province": "Hubei",
            "latitude": 30.59, "longitude": 114.30,
        }

        rows, summary = fetch_location(
            location, date(2026, 11, 19), date(2026, 11, 19),
            "jma_jaxa_himawari",
        )

        self.assertEqual(len(rows), 144)
        self.assertEqual(summary["observed_time_steps_seconds"], [1800])
        self.assertFalse(summary["cadence_verified"])
        self.assertEqual(summary["missing_timestamp_records"], 96)
        self.assertEqual(summary["valid_records"], 48)


class ApiDateRangeTests(unittest.TestCase):
    """The provider rejects any end_date after the current UTC day."""

    @staticmethod
    def _response(day_start, points):
        timestamps = [
            (day_start + timedelta(minutes=10 * index)).strftime("%Y-%m-%dT%H:%M")
            for index in range(points)
        ]
        response = Mock()
        response.json.return_value = {
            "model": "jma_jaxa_himawari",
            "hourly": {"time": timestamps, "shortwave_radiation": [0.0] * points},
        }
        return response

    @patch("src.radiation_extract.utc_today")
    @patch("src.radiation_extract.requests.get")
    def test_current_day_never_requests_a_future_end_date(self, get, today):
        today.return_value = date(2026, 11, 20)
        get.return_value = self._response(datetime(2026, 11, 20, tzinfo=timezone.utc), 145)
        location = {
            "station_id": "wuhan", "station_name": "Wuhan", "province": "Hubei",
            "latitude": 30.59, "longitude": 114.30,
        }

        rows, _ = fetch_location(
            location, date(2026, 11, 20), date(2026, 11, 20), "jma_jaxa_himawari",
        )

        self.assertEqual(get.call_args.kwargs["params"]["end_date"], "2026-11-20")
        self.assertEqual(len(rows), 144)

    @patch("src.radiation_extract.utc_today")
    @patch("src.radiation_extract.requests.get")
    def test_previous_day_still_extends_one_day_for_the_2400_label(self, get, today):
        today.return_value = date(2026, 11, 20)
        get.return_value = self._response(datetime(2026, 11, 19, tzinfo=timezone.utc), 289)
        location = {
            "station_id": "wuhan", "station_name": "Wuhan", "province": "Hubei",
            "latitude": 30.59, "longitude": 114.30,
        }

        rows, _ = fetch_location(
            location, date(2026, 11, 19), date(2026, 11, 19), "jma_jaxa_himawari",
        )

        self.assertEqual(get.call_args.kwargs["params"]["end_date"], "2026-11-20")
        self.assertEqual(rows[-1]["time_utc"], "2026-11-20T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
