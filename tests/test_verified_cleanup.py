import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts.cleanup_archived_month import cleanup_month


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class VerifiedCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_root = self.root / "data" / "daily"
        self.month = "2026-02"
        self.source_dir = self.data_root / "2026" / "02"
        self.source_dir.mkdir(parents=True)
        self.manifest_path = self.root / "manifest.json"
        days = []
        start = date(2026, 2, 1)
        for offset in range(28):
            day = start + timedelta(days=offset)
            compact = day.strftime("%Y%m%d")
            csv_path = self.source_dir / f"radiation_{compact}.csv"
            status_path = self.source_dir / f"radiation_{compact}.status.json"
            csv_path.write_text(f"csv-{compact}", encoding="utf-8")
            status_path.write_text(f"status-{compact}", encoding="utf-8")
            days.append({
                "date_utc": day.isoformat(),
                "csv": {"sha256": digest(csv_path)},
                "status": {"sha256": digest(status_path)},
            })
        self.manifest_path.write_text(json.dumps({
            "month": self.month,
            "expected_days": 28,
            "archived_days": 28,
            "days": days,
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_matching_manifest_removes_all_month_files(self):
        removed = cleanup_month(self.month, self.manifest_path, self.data_root, today=date(2027, 4, 1))
        self.assertEqual(len(removed), 56)
        self.assertEqual(list(self.source_dir.iterdir()), [])

    def test_month_inside_one_year_retention_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "one-year retention"):
            cleanup_month(self.month, self.manifest_path, self.data_root, today=date(2026, 11, 20))
        self.assertEqual(len(list(self.source_dir.iterdir())), 56)

    def test_hash_mismatch_prevents_any_deletion(self):
        first_csv = self.source_dir / "radiation_20260201.csv"
        first_csv.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            cleanup_month(self.month, self.manifest_path, self.data_root)
        self.assertEqual(len(list(self.source_dir.iterdir())), 56)


if __name__ == "__main__":
    unittest.main()
