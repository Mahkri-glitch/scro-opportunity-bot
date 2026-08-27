import unittest

from scanner import Job, canonical_url, looks_non_us, score_job


class ScannerTests(unittest.TestCase):
    def test_manufacturing_intern_scores_highly(self):
        job = Job(
            company="Example Semiconductor",
            title="Process Integration Engineering Intern",
            location="Orlando, Florida",
            url="https://example.com/jobs/123",
            source="Test",
        )
        ranked = score_job(job)
        self.assertIsNotNone(ranked)
        self.assertGreaterEqual(ranked.score, 15)
        self.assertIn("Florida", ranked.tags)

    def test_unrelated_finance_intern_is_excluded(self):
        job = Job(
            company="Example Semiconductor",
            title="Finance Intern",
            location="Austin, Texas",
            url="https://example.com/jobs/456",
            source="Test",
        )
        self.assertIsNone(score_job(job))

    def test_experienced_role_is_excluded(self):
        job = Job(
            company="Example Semiconductor",
            title="Senior Yield Engineer",
            location="Boise, Idaho",
            url="https://example.com/jobs/789",
            source="Test",
        )
        self.assertIsNone(score_job(job))

    def test_non_us_intern_is_excluded(self):
        job = Job(
            company="Example Semiconductor",
            title="Manufacturing Engineering Intern",
            location="Singapore",
            url="https://example.com/jobs/321",
            source="Test",
        )
        self.assertTrue(looks_non_us(job.location))
        self.assertIsNone(score_job(job, us_only=True))

    def test_url_query_is_removed_from_identity(self):
        self.assertEqual(
            canonical_url("https://Example.com/jobs/123/?source=campus"),
            "https://example.com/jobs/123",
        )


if __name__ == "__main__":
    unittest.main()
