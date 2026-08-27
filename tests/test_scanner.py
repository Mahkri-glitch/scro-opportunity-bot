import unittest
from unittest.mock import patch

from scanner import (
    Job,
    RankedJob,
    Scanner,
    canonical_url,
    has_explicit_us_location,
    is_us_based,
    looks_non_us,
    rank_jobs,
    score_job,
)


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

    def test_country_in_title_overrides_us_location(self):
        job = Job(
            company="Example Semiconductor",
            title="Manufacturing Engineering Intern (Singapore)",
            location="Austin, Texas",
            url="https://example.com/jobs/singapore-intern",
            source="Test",
        )
        self.assertFalse(is_us_based(job.title, job.location))
        self.assertIsNone(score_job(job, us_only=True))

    def test_vague_or_remote_only_location_is_excluded(self):
        self.assertFalse(has_explicit_us_location(""))
        self.assertFalse(has_explicit_us_location("Multiple Locations"))
        self.assertFalse(has_explicit_us_location("Remote"))

    def test_us_state_abbreviation_is_accepted(self):
        self.assertTrue(has_explicit_us_location("Austin, TX"))
        self.assertTrue(has_explicit_us_location("US-OR-Hillsboro"))
        self.assertTrue(has_explicit_us_location("Indianapolis, Indiana"))
        self.assertFalse(has_explicit_us_location("Bangalore, India"))

    def test_bachelors_and_masters_requirements_raise_priority(self):
        baseline = Job(
            company="Example Semiconductor",
            title="Process Engineering Intern",
            location="Phoenix, AZ",
            url="https://example.com/jobs/process-baseline",
            source="Test",
        )
        degree_role = Job(
            company="Example Semiconductor",
            title="Process Engineering Intern",
            location="Phoenix, AZ",
            url="https://example.com/jobs/process-degree",
            source="Test",
            description="Candidates pursuing a bachelor's or master's degree are encouraged to apply.",
        )
        baseline_ranked = score_job(baseline)
        degree_ranked = score_job(degree_role)
        self.assertIsNotNone(baseline_ranked)
        self.assertIsNotNone(degree_ranked)
        self.assertGreater(degree_ranked.score, baseline_ranked.score)
        self.assertIn("Bachelor's", degree_ranked.tags)
        self.assertIn("Master's", degree_ranked.tags)

    def test_phd_only_title_is_excluded(self):
        job = Job(
            company="Example Semiconductor",
            title="PhD Process Engineering Intern",
            location="Santa Clara, CA",
            url="https://example.com/jobs/phd-intern",
            source="Test",
        )
        self.assertIsNone(score_job(job))

    @patch("scanner.request_json")
    def test_workday_details_supply_degree_requirements(self, request_json_mock):
        request_json_mock.side_effect = [
            {
                "total": 1,
                "jobPostings": [
                    {
                        "title": "Process Engineering Intern",
                        "locationsText": "Austin, TX",
                        "externalPath": "/job/Austin-TX/Process-Engineering-Intern_R123",
                        "bulletFields": ["R123"],
                    }
                ],
            },
            {
                "jobPostingInfo": {
                    "title": "Process Engineering Intern",
                    "location": "Austin, TX",
                    "jobDescription": "Pursuing a bachelor's or master's degree in engineering.",
                }
            },
        ]
        source = {
            "company": "Example Semiconductor",
            "type": "workday",
            "host": "example.wd1.myworkdayjobs.com",
            "tenant": "example",
            "site": "External",
            "search_terms": ["intern"],
        }

        jobs = Scanner().scan(source)

        self.assertEqual(len(jobs), 1)
        self.assertIn("bachelor's", jobs[0].description)
        self.assertEqual(request_json_mock.call_count, 2)

    def test_url_query_is_removed_from_identity(self):
        self.assertEqual(
            canonical_url("https://Example.com/jobs/123/?source=campus"),
            "https://example.com/jobs/123",
        )

    def test_rank_jobs_returns_ranked_jobs_not_filter_booleans(self):
        jobs = [
            Job(
                company="Example Semiconductor",
                title="Yield Engineering Intern",
                location="Austin, Texas",
                url="https://example.com/jobs/yield-intern",
                source="Test",
            ),
            Job(
                company="Example Semiconductor",
                title="Finance Intern",
                location="Austin, Texas",
                url="https://example.com/jobs/finance-intern",
                source="Test",
            ),
        ]

        ranked = rank_jobs(jobs)

        self.assertEqual(len(ranked), 1)
        self.assertIsInstance(ranked[0], RankedJob)
        self.assertEqual(ranked[0].job.title, "Yield Engineering Intern")


if __name__ == "__main__":
    unittest.main()
