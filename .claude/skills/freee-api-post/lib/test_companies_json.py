import json
import os
import unittest

COMPANIES_JSON_PATH = os.path.join(
    os.path.dirname(__file__), "..", "companies.json"
)
if not os.path.exists(COMPANIES_JSON_PATH):  # 実ファイルが無ければ雛形で検証する
    COMPANIES_JSON_PATH = COMPANIES_JSON_PATH.replace("companies.json", "companies.example.json")


class TestCompaniesJsonSchema(unittest.TestCase):
    def test_is_valid_json_with_companies_list(self):
        with open(COMPANIES_JSON_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        self.assertIn("companies", config)
        self.assertIsInstance(config["companies"], list)
        self.assertGreaterEqual(len(config["companies"]), 1)

    def test_each_company_has_required_keys(self):
        with open(COMPANIES_JSON_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        required_keys = {
            "name", "company_id", "instance", "credentials_path", "slack_channel"
        }
        for company in config["companies"]:
            self.assertTrue(required_keys.issubset(company.keys()))

    def test_samplea_entry_present(self):
        with open(COMPANIES_JSON_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        names = [c["name"] for c in config["companies"]]
        self.assertIn("株式会社サンプルA", names)


if __name__ == "__main__":
    unittest.main()
