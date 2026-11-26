from unittest import mock

import requests

from testruns import jobs, views
from tests.support import ApiTestCase

CASES = [{"tc_id": "TC_01"}]


class JobTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.token, _ = self.signup()
        self.addCleanup(jobs.jobs.clear)

    def start(self):
        with mock.patch.object(jobs, "run_single_in_background"):
            return self.post("/api/tests/start", {"test_cases": CASES}, self.token).json()["jobId"]

    def test_only_the_owner_can_read_a_job(self):
        job_id = self.start()
        other, _ = self.signup("b@x.test")
        self.assertEqual(self.get(f"/api/tests/status/{job_id}", self.token).status_code, 200)
        self.assertEqual(self.get(f"/api/tests/status/{job_id}", other).status_code, 404)

    def test_owner_survives_completion(self):
        job_id = self.start()
        with mock.patch.object(jobs, "execute_on_python", return_value={"results": []}):
            jobs._run_single(job_id, CASES, 1, True)
        self.assertEqual(self.get(f"/api/tests/status/{job_id}", self.token).json()["status"], "done")

    def test_test_cases_must_be_objects(self):
        response = self.post("/api/tests/start", {"test_cases": ["text"]}, self.token)
        self.assertEqual(response.status_code, 400)

    def test_batch_range_is_clamped(self):
        reply = mock.Mock(**{"json.return_value": {"job_id": "p1"}})
        with mock.patch.object(views.requests, "post", return_value=reply), \
                mock.patch.object(jobs, "poll_batch_in_background"):
            body = {"test_cases": CASES, "start_index": -5, "end_index": 50}
            self.assertEqual(self.post("/api/tests/start-batch", body, self.token).json()["total"], 1)
            body = {"test_cases": CASES, "start_index": 7}
            self.assertEqual(self.post("/api/tests/start-batch", body, self.token).status_code, 400)

    def poll(self, **reply):
        job_id = jobs.create(1, owner="u")
        with mock.patch.object(jobs.time, "sleep"), mock.patch.object(jobs.requests, "get", **reply) as get:
            jobs._poll_batch(job_id, "p1")
        return jobs.jobs[job_id], get.call_count

    def test_batch_polling_stops_when_the_job_is_gone(self):
        job, calls = self.poll(return_value=mock.Mock(status_code=404))
        self.assertEqual((job["status"], calls), ("failed", 1))

    def test_batch_polling_gives_up_when_unreachable(self):
        job, calls = self.poll(side_effect=requests.ConnectionError())
        self.assertEqual((job["status"], calls), ("failed", jobs.MAX_POLL_FAILURES))

    def test_old_finished_jobs_are_removed(self):
        old = jobs.create(1, owner="u")
        jobs.jobs[old]["finishedAt"] = jobs.time.monotonic() - jobs.JOB_TTL - 1
        recent = jobs.create(1, owner="u")
        self.assertNotIn(old, jobs.jobs)
        self.assertIn(recent, jobs.jobs)


class RateLimitTests(ApiTestCase):
    def statuses(self, path, count, **extra):
        return [self.get(path, **extra).status_code for _ in range(count)]

    def test_polling_has_its_own_limit(self):
        self.assertNotIn(429, self.statuses("/api/tests/status/j", 1000))
        self.assertEqual(self.statuses("/health", 201), [200] * 200 + [429])
        self.assertEqual(self.statuses("/api/tests/status/j", 1), [429])

    def test_forwarded_header_is_ignored_without_a_trusted_proxy(self):
        self.statuses("/health", 200)
        self.assertEqual(self.statuses("/health", 1, HTTP_X_FORWARDED_FOR="9.9.9.9"), [429])

    def test_clients_behind_a_trusted_proxy_are_separate(self):
        with self.settings(TRUSTED_PROXY_COUNT=1):
            self.statuses("/health", 200, HTTP_X_FORWARDED_FOR="1.1.1.1")
            self.assertEqual(self.statuses("/health", 1, HTTP_X_FORWARDED_FOR="2.2.2.2"), [200])
            # a forged entry on the left does not get a fresh limit
            self.assertEqual(self.statuses("/health", 1, HTTP_X_FORWARDED_FOR="3.3.3.3, 1.1.1.1"), [429])
