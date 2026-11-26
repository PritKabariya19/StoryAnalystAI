import json
from unittest import mock

from reports import views
from tests.support import ApiTestCase


class ReportTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.token, _ = self.signup()
        self.addCleanup(views.mem_reports.clear)

    def test_memory_only_save_carries_a_warning(self):
        response = self.post("/api/reports/save", {"type": "test", "results": [{}]}, self.token)
        self.assertIn("lost", response.json()["warning"])

    def test_large_reports_drop_step_logs(self):
        report = {"results": [{"tc_id": "1", "log": "x" * views.MAX_DOC_BYTES}]}
        fitted = views._fit_for_firestore(report)
        self.assertEqual(fitted["results"], [{"tc_id": "1"}])
        self.assertLess(len(json.dumps(fitted)), views.MAX_DOC_BYTES)

    def listing(self, query):
        db = mock.Mock()
        db.collection.return_value.where.return_value = query
        with mock.patch.object(views, "_has_firestore", return_value=True), \
                mock.patch.object(views.firebase, "db", return_value=db):
            return self.get("/api/reports/list", self.token).json()

    def doc(self, key, timestamp):
        return mock.Mock(id=key, **{"to_dict.return_value": {"timestamp": timestamp}})

    def test_list_works_without_the_composite_index(self):
        query = mock.Mock()
        query.order_by.side_effect = RuntimeError("index required")
        query.stream.return_value = [self.doc("old", 1), self.doc("new", 2)]
        self.assertEqual([r["id"] for r in self.listing(query)["reports"]], ["new", "old"])

    def test_list_says_when_saved_reports_cannot_be_loaded(self):
        query = mock.Mock()
        query.order_by.side_effect = query.stream.side_effect = RuntimeError("down")
        self.assertIn("could not be loaded", self.listing(query)["warning"])
