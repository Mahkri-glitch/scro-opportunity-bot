import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scanner import (
    HttpRequestError,
    Job,
    RankedJob,
    Scanner,
    canonical_url,
    has_explicit_us_location,
    is_us_based,
    looks_non_us,
    matches_target_role,
    post_scan_summary,
    rank_jobs,
    score_job,
)


class ScannerTests(unittest.TestCase):
    def test_config_covers_every_requested_company(self):
        requested = {
            "Applied Materials", "Lam Research", "KLA", "ASML", "ASM International",
            "Tokyo Electron", "Onto Innovation", "Axcelis Technologies", "Veeco",
            "MKS Instruments", "INFICON", "Intel", "Micron", "GlobalFoundries", "TSMC",
            "Samsung Semiconductor", "Texas Instruments", "Wolfspeed", "SkyWater Technology",
            "onsemi", "Analog Devices", "Infineon", "STMicroelectronics", "NXP", "Qorvo",
            "Amkor", "ASE", "Teradyne", "Advantest", "Entegris", "Air Products",
            "Air Liquide", "Linde", "FUJIFILM Electronic Materials", "Shin-Etsu",
            "GlobalWafers",
        }
        config = json.loads((Path(__file__).parents[1] / "companies.json").read_text())
        configured = {source["company"] for source in config["sources"] if source.get("enabled", True)}

        self.assertEqual(configured, requested)
        self.assertEqual(len(config["sources"]), len(requested))

    def test_each_requested_role_area_is_eligible(self):
        role_titles = (
            "Process Engineering Intern",
            "Yield Engineering Intern",
            "Manufacturing Engineering Intern",
            "Product Engineering Intern",
            "Equipment Engineering Intern",
            "Metrology Intern",
            "Process Integration Co-op",
            "Lithography Engineering Intern",
            "Etch Process Intern",
            "Deposition Engineering Intern",
            "CVD Intern",
            "PVD Engineering Co-op",
            "ALD Process Intern",
            "CMP Engineering Intern",
            "Advanced Packaging Intern",
            "Test Engineering Intern",
            "Reliability Engineering Intern",
            "Semiconductor Engineering Intern",
        )

        for index, title in enumerate(role_titles):
            with self.subTest(title=title):
                job = Job(
                    company="Example Semiconductor",
                    title=title,
                    location="Austin, TX",
                    url=f"https://example.com/jobs/target-{index}",
                    source="Test",
                )
                self.assertTrue(matches_target_role(title))
                self.assertIsNotNone(score_job(job))

    def test_generic_engineering_intern_is_excluded(self):
        job = Job(
            company="Example Semiconductor",
            title="Electrical Engineering Intern",
            location="Austin, TX",
            url="https://example.com/jobs/generic-engineering",
            source="Test",
        )
        self.assertIsNone(score_job(job))

    def test_description_cannot_make_generic_title_eligible(self):
        job = Job(
            company="Example Semiconductor",
            title="Mechanical Engineering Intern",
            location="Austin, TX",
            url="https://example.com/jobs/description-only",
            source="Test",
            description="Work on semiconductor manufacturing, yield, process, and equipment.",
        )
        self.assertIsNone(score_job(job))

    def test_product_management_intern_is_excluded(self):
        job = Job(
            company="Example Semiconductor",
            title="Product Management Intern",
            location="Austin, TX",
            url="https://example.com/jobs/product-management",
            source="Test",
        )
        self.assertFalse(matches_target_role(job.title))
        self.assertIsNone(score_job(job))

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

    @patch("scanner.request_json")
    def test_eightfold_adapter_normalizes_public_positions(self, request_json_mock):
        request_json_mock.side_effect = [
            {
                "data": {
                    "count": 1,
                    "positions": [
                        {
                            "id": "EF-1",
                            "name": "Yield Engineering Intern",
                            "standardizedLocations": ["Boise, ID, United States"],
                            "positionUrl": "/careers/job/EF-1-yield-engineering-intern",
                            "postedTs": 1780000000000,
                        }
                    ],
                }
            },
            {
                "data": {
                    "position": {
                        "id": "EF-1",
                        "name": "Yield Engineering Intern",
                        "standardizedLocations": ["Boise, ID, United States"],
                        "positionUrl": "/careers/job/EF-1-yield-engineering-intern",
                        "jobDescription": "Pursuing a bachelor's or master's degree.",
                    }
                }
            },
        ]
        source = {
            "company": "Example Semiconductor",
            "type": "eightfold",
            "host": "example.eightfold.ai",
            "domain": "example.com",
            "search_terms": ["intern"],
        }

        jobs = Scanner().scan(source)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "EF-1")
        self.assertIn("bachelor's", jobs[0].description)
        self.assertIn("/api/pcsx/search", request_json_mock.call_args_list[0].args[1])
        self.assertIn("/api/pcsx/position_details", request_json_mock.call_args_list[1].args[1])
        self.assertIsNotNone(score_job(jobs[0]))

    @patch("scanner.request_json")
    def test_eightfold_adapter_falls_back_to_classic_api(self, request_json_mock):
        request_json_mock.side_effect = [
            HttpRequestError("HTTP 403: PCSX is not enabled"),
            {
                "count": 1,
                "positions": [
                    {
                        "id": "EF-2",
                        "name": "Process Engineering Co-op",
                        "location": "Hillsboro, OR, United States",
                        "canonicalPositionUrl": "https://jobs.example.com/process-coop",
                    }
                ],
            },
        ]

        jobs = Scanner().scan(
            {
                "company": "Example Semiconductor",
                "type": "eightfold",
                "host": "classic.eightfold.ai",
                "search_terms": ["co-op"],
            }
        )

        self.assertEqual(jobs[0].external_id, "EF-2")
        self.assertIn("/api/apply/v2/jobs", request_json_mock.call_args_list[1].args[1])
        self.assertIsNotNone(score_job(jobs[0]))

    @patch("scanner.request_json")
    def test_oracle_adapter_normalizes_candidate_experience_feed(self, request_json_mock):
        request_json_mock.return_value = {
            "items": [
                {
                    "TotalJobsCount": 1,
                    "requisitionList": [
                        {
                            "Id": "1234",
                            "Title": "Process Engineering Intern",
                            "PrimaryLocation": "Dallas, TX, United States",
                            "PostedDate": "2026-08-20",
                            "ShortDescriptionStr": "Bachelor's or master's student.",
                        }
                    ],
                }
            ]
        }
        source = {
            "company": "Example Semiconductor",
            "type": "oracle",
            "host": "careers.example.com",
            "site": "CX",
            "search_terms": ["intern"],
        }

        jobs = Scanner().scan(source)

        self.assertEqual(jobs[0].title, "Process Engineering Intern")
        self.assertIn("/sites/CX/job/1234/", jobs[0].url)
        self.assertIsNotNone(score_job(jobs[0]))

    @patch("scanner.request_text")
    def test_successfactors_rss_adapter_reads_location_and_degree_text(self, request_text_mock):
        request_text_mock.return_value = """<?xml version="1.0"?>
        <rss xmlns:g="http://base.google.com/ns/1.0"><channel><item>
          <title>Equipment Engineering Co-op (Portland, OR, United States)</title>
          <link>https://careers.example.com/job/1</link>
          <guid>SF-1</guid>
          <description>Pursuing a bachelor's or master's degree.</description>
        </item></channel></rss>"""

        jobs = Scanner().scan(
            {
                "company": "Example Semiconductor",
                "type": "successfactors",
                "feed_url": "https://careers.example.com/sitemal.xml",
            }
        )

        self.assertEqual(jobs[0].location, "Portland, OR, United States")
        self.assertEqual(jobs[0].title, "Equipment Engineering Co-op")
        self.assertIsNotNone(score_job(jobs[0]))

    @patch("scanner.request_json")
    @patch("scanner.request_text_with_headers")
    def test_dayforce_adapter_keeps_structured_us_location(self, text_mock, json_mock):
        class Headers:
            @staticmethod
            def get_all(_name):
                return ["__Host-next-auth.csrf-token=cookie-value; Path=/; Secure"]

        text_mock.return_value = ('{"csrfToken":"csrf-value"}', Headers())
        json_mock.side_effect = [
            {"jobBoardCode": "CANDIDATEPORTAL"},
            {
                "maxCount": 1,
                "jobPostings": [
                    {
                        "jobPostingId": "DF-1",
                        "jobTitle": "Manufacturing Engineering Intern",
                        "postingLocations": [
                            {"city": "Bloomington", "state": "MN", "country": "US"}
                        ],
                        "jobDescription": "Open to undergraduate students.",
                    }
                ],
            },
        ]

        jobs = Scanner().scan(
            {
                "company": "Example Semiconductor",
                "type": "dayforce",
                "host": "jobs.dayforcehcm.com",
                "client": "example",
                "board": "CANDIDATEPORTAL",
            }
        )

        self.assertIn("Bloomington", jobs[0].location)
        self.assertIsNotNone(score_job(jobs[0]))
        post_headers = json_mock.call_args_list[1].kwargs["headers"]
        self.assertEqual(post_headers["X-CSRF-TOKEN"], "csrf-value")
        self.assertEqual(
            post_headers["Cookie"], "__Host-next-auth.csrf-token=cookie-value"
        )

    @patch("scanner.request_text")
    def test_static_adapter_splits_inficon_style_title_and_location(self, request_text_mock):
        request_text_mock.return_value = (
            '<a href="/en/career/process-engineering-intern">'
            "Process Engineering Intern United States, NY, East Syracuse</a>"
        )
        jobs = Scanner().scan(
            {
                "company": "INFICON",
                "type": "static",
                "page_url": "https://www.inficon.com/en/careers/open-positions",
                "anchor_href_pattern": "/career/",
                "title_location_regex": (
                    r"^(?P<title>.+?)\s+(?P<location>United States,\s*[A-Z]{2},\s*.+)$"
                ),
            }
        )

        self.assertEqual(jobs[0].title, "Process Engineering Intern")
        self.assertEqual(jobs[0].location, "United States, NY, East Syracuse")
        self.assertIsNotNone(score_job(jobs[0]))

    @patch("scanner.request_text")
    def test_static_adapter_uses_reader_fallback_for_blocked_page(self, request_text_mock):
        request_text_mock.side_effect = [
            HttpRequestError("HTTP 403 from official page"),
            """Title: Careers
URL Source: https://ase.aseglobal.com/careers-us/
Markdown Content:
## Packaging Engineering Intern

## Senior Finance Manager
""",
        ]

        jobs = Scanner().scan(
            {
                "company": "ASE",
                "type": "static",
                "page_url": "https://ase.aseglobal.com/careers-us/",
                "reader_fallback_url": "https://r.jina.ai/http://ase.aseglobal.com/careers-us/",
                "default_location": "United States",
                "include_headings": True,
            }
        )

        packaging = next(job for job in jobs if job.title == "Packaging Engineering Intern")
        self.assertEqual(packaging.url, "https://ase.aseglobal.com/careers-us/")
        self.assertIsNotNone(score_job(packaging))

    @patch("scanner.request_text")
    def test_static_reader_pairs_inficon_link_with_following_location(self, request_text_mock):
        request_text_mock.side_effect = [
            "",
            """[Process Engineering Co-Op/Intern](/en/career/process-co-op-intern)
United States, NY, Syracuse
""",
        ]

        jobs = Scanner().scan(
            {
                "company": "INFICON",
                "type": "static",
                "page_url": "https://www.inficon.com/en/careers/open-positions",
                "reader_fallback_url": "https://r.jina.ai/http://www.inficon.com/en/careers/open-positions",
                "anchor_href_pattern": "/career/",
                "title_location_regex": (
                    r"^(?P<title>.+?)\s+(?P<location>United States,\s*[A-Z]{2},\s*.+)$"
                ),
            }
        )

        self.assertEqual(jobs[0].title, "Process Engineering Co-Op/Intern")
        self.assertEqual(jobs[0].location, "United States, NY, Syracuse")
        self.assertIsNotNone(score_job(jobs[0]))

    @patch("scanner.request_json")
    def test_adp_myjobs_adapter_uses_public_career_token(self, request_json_mock):
        request_json_mock.side_effect = [
            {"myJobsToken": "public-token", "properties": {"myadpUrl": "https://api.adp.com"}},
            {
                "count": 1,
                "jobRequisitions": [
                    {
                        "reqId": "ADP-1",
                        "publishedJobTitle": "Test Engineering Intern",
                        "postingLocations": [
                            {
                                "address": {
                                    "cityName": "San Jose",
                                    "countrySubdivisionLevel1": {"codeValue": "CA"},
                                    "country": {"codeValue": "US"},
                                }
                            }
                        ],
                        "jobQualifications": "Currently pursuing a bachelor's degree.",
                    }
                ],
            },
        ]

        jobs = Scanner().scan(
            {
                "company": "Advantest",
                "type": "adp_myjobs",
                "slug": "advantestcareers",
            }
        )

        self.assertEqual(jobs[0].external_id, "ADP-1")
        self.assertIn("San Jose", jobs[0].location)
        self.assertIsNotNone(score_job(jobs[0]))

    @patch("scanner.request_json")
    @patch("scanner.request_text_with_headers")
    def test_csod_adapter_replays_public_bootstrap_token(self, text_mock, json_mock):
        class Headers:
            @staticmethod
            def get_all(_name):
                return ["session=abc; Path=/; Secure"]

        text_mock.return_value = ('{"token":"anonymous.jwt"}', Headers())
        json_mock.return_value = {
            "data": {
                "totalCount": 1,
                "requisitions": [
                    {
                        "requisitionId": "CSOD-1",
                        "displayJobTitle": "Process Engineering Intern",
                        "postingEffectiveDate": "8/20/2026",
                        "locations": [
                            {"city": "Tonawanda", "state": "NY", "country": "United States"}
                        ],
                    }
                ],
            }
        }
        jobs = Scanner().scan(
            {
                "company": "Linde",
                "type": "csod",
                "host": "linde.csod.com",
                "site_id": 23,
                "corp": "linde",
                "search_terms": ["intern"],
            }
        )

        self.assertEqual(jobs[0].external_id, "CSOD-1")
        self.assertIsNotNone(score_job(jobs[0]))
        self.assertEqual(json_mock.call_args.kwargs["headers"]["Cookie"], "session=abc")

    def test_url_query_is_removed_from_identity(self):
        self.assertEqual(
            canonical_url("https://Example.com/jobs/123/?source=campus"),
            "https://example.com/jobs/123",
        )

    @patch("scanner.request_json")
    def test_empty_scan_summary_reports_coverage(self, request_json_mock):
        post_scan_summary(
            "https://discord.com/api/webhooks/123/token",
            successful_sources=36,
            configured_sources=36,
            matching_jobs=12,
        )

        payload = request_json_mock.call_args.kwargs["payload"]
        self.assertEqual(payload["username"], "Jensen Huang")
        self.assertIn("36/36", payload["content"])
        self.assertIn("12", payload["content"])
        self.assertIn("No new opportunities today", payload["content"])

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
