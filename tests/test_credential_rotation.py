from pathlib import Path
import tempfile
import unittest

from awfl.consumer_credentials import subprocess_credentials


class CredentialRotationTests(unittest.TestCase):
    def test_latest_token_file_is_used_for_each_subprocess(self):
        with tempfile.TemporaryDirectory() as root:
            token_file = Path(root) / "github-token"
            env = {
                "AWFL_GITHUB_TOKEN_FILE": str(token_file),
                "GITHUB_TOKEN": "old-token",
            }
            token_file.write_text("new-token-one\n")
            first = subprocess_credentials(env)
            self.assertEqual(first["GITHUB_TOKEN"], "new-token-one")
            self.assertEqual(first["GH_TOKEN"], "new-token-one")
            token_file.write_text("new-token-two\n")
            second = subprocess_credentials(env)
            self.assertEqual(second["GITHUB_TOKEN"], "new-token-two")
            self.assertEqual(env["GITHUB_TOKEN"], "old-token")

    def test_missing_token_file_preserves_initial_credential(self):
        with tempfile.TemporaryDirectory() as root:
            env = {"AWFL_GITHUB_TOKEN_FILE": root + "/missing",
                   "GITHUB_TOKEN": "startup-token"}
            self.assertEqual(subprocess_credentials(env)["GITHUB_TOKEN"], "startup-token")


if __name__ == "__main__":
    unittest.main()
