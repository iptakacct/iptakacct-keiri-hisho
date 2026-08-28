import json
import os
import unittest

ACCOUNTS_JSON_PATH = os.path.join(
    os.path.dirname(__file__), "..", "accounts.json"
)
if not os.path.exists(ACCOUNTS_JSON_PATH):  # 実ファイルが無ければ雛形で検証する
    ACCOUNTS_JSON_PATH = ACCOUNTS_JSON_PATH.replace("accounts.json", "accounts.example.json")


class TestAccountsJsonSchema(unittest.TestCase):
    def test_is_valid_json_with_accounts_list(self):
        with open(ACCOUNTS_JSON_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        self.assertIn("accounts", config)
        self.assertIsInstance(config["accounts"], list)
        self.assertGreaterEqual(len(config["accounts"]), 1)

    def test_each_account_has_required_keys(self):
        with open(ACCOUNTS_JSON_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        required_keys = {
            "name", "invox_company_code", "instance", "credentials_path", "slack_channel"
        }
        for account in config["accounts"]:
            self.assertTrue(required_keys.issubset(account.keys()))

    def test_sample_entry_present(self):
        with open(ACCOUNTS_JSON_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        names = [a["name"] for a in config["accounts"]]
        self.assertIn("サンプルA", names)


if __name__ == "__main__":
    unittest.main()
