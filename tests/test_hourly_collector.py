import unittest

from src.radiation_pipeline import has_valid_values, merge_rows


def row(value, quality="valid", source="jma_jaxa_himawari"):
    return {
        "station_id": "wuhan",
        "station_name": "Wuhan",
        "province": "Hubei",
        "latitude": 30.59,
        "longitude": 114.30,
        "time_utc": "2026-11-20T10:10:00Z",
        "ghi_wm2": value,
        "unit": "W/m²",
        "model_requested": "jma_jaxa_himawari",
        "temporal_resolution_seconds": 600,
        "source": source,
        "quality_flag": quality,
    }


class HourlyUpsertTests(unittest.TestCase):
    def test_valid_observation_is_not_downgraded_by_later_null(self):
        merged = merge_rows(
            [row(123.0)],
            [row("", "provider_returned_null")],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["ghi_wm2"], 123.0)
        self.assertEqual(merged[0]["quality_flag"], "valid")

    def test_null_can_be_replaced_by_later_valid_observation(self):
        merged = merge_rows(
            [row("", "provider_returned_null")],
            [row(0.0)],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["ghi_wm2"], 0.0)

    def test_upsert_deduplicates_by_station_and_timestamp(self):
        merged = merge_rows(
            [row(100.0)],
            [row(110.0)],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["ghi_wm2"], 110.0)


class AllNullDayGuardTests(unittest.TestCase):
    """A provider day with no measurements must never be written to disk."""

    def test_empty_batch_has_no_valid_values(self):
        self.assertFalse(has_valid_values([]))

    def test_all_null_batch_has_no_valid_values(self):
        self.assertFalse(has_valid_values([row("", "provider_timestamp_missing")] * 144))

    def test_single_measurement_counts(self):
        self.assertTrue(has_valid_values([row("", "provider_returned_null"), row(0.0)]))


if __name__ == "__main__":
    unittest.main()
