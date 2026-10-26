import json
import os
import unittest

COMPANIES_EXAMPLE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "companies.example.json"
)


class TestCompaniesExampleSchema(unittest.TestCase):
    def _load(self):
        with open(COMPANIES_EXAMPLE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_is_valid_json_with_companies_list(self):
        config = self._load()
        self.assertIsInstance(config["companies"], list)
        self.assertGreaterEqual(len(config["companies"]), 1)

    def test_each_company_has_required_keys(self):
        required_keys = {"name", "office_id", "instance", "credentials_path", "slack_channel"}
        for company in self._load()["companies"]:
            self.assertTrue(required_keys.issubset(company.keys()))

    def test_credentials_pathはcredentialsで始まるファイル名(self):
        # .gitignore の credentials* に当たり、コミットされないことを保つ
        for company in self._load()["companies"]:
            self.assertTrue(os.path.basename(company["credentials_path"]).startswith("credentials"))


if __name__ == "__main__":
    unittest.main()
