import unittest
from src.radiation_extract import validate_native_cadence


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


if __name__ == "__main__":
    unittest.main()
