from pathlib import Path
import io
import json
import tempfile
import unittest
from unittest import mock

from awfl.consumer_credentials import (
    MAX_REFRESH_SECONDS, MIN_REFRESH_SECONDS, next_refresh_delay,
    subprocess_credentials, refresh, main,
)


class CredentialRotationTests(unittest.TestCase):
    def test_expiry_aware_schedule_from_actual_remaining_ttl(self):
        self.assertEqual(next_refresh_delay(3600), MAX_REFRESH_SECONDS)
        self.assertEqual(next_refresh_delay(2613), MAX_REFRESH_SECONDS)
        self.assertEqual(next_refresh_delay(2200), 1900)
        self.assertEqual(next_refresh_delay(900), 600)
        self.assertEqual(next_refresh_delay(320), 20)
        self.assertEqual(next_refresh_delay(300), MIN_REFRESH_SECONDS)
        self.assertEqual(next_refresh_delay(120), MIN_REFRESH_SECONDS)
        self.assertEqual(next_refresh_delay(0), MIN_REFRESH_SECONDS)
        self.assertEqual(next_refresh_delay('invalid'), MIN_REFRESH_SECONDS)
        self.assertEqual(next_refresh_delay(float('nan')), MIN_REFRESH_SECONDS)
        self.assertEqual(next_refresh_delay(None), MAX_REFRESH_SECONDS)

    def test_refreshed_token_writes_atomically_and_returns_expiry_based_delay(self):
        with tempfile.TemporaryDirectory() as root:
            google = Path(root) / 'google'
            github = Path(root) / 'github'
            env = {
                'AWFL_CREDENTIAL_REFRESH_ORIGIN': 'https://api.example.test',
                'AWFL_USER_ID': 'u',
                'AWFL_PROJECT_ID': 'p',
                'AWFL_CONSUMER_ID': 'consumer-1',
                'AWFL_CREDENTIAL_REFRESH_CAPABILITY': 'test-capability',
                'CLOUDSDK_AUTH_ACCESS_TOKEN_FILE': str(google),
                'AWFL_GITHUB_TOKEN_FILE': str(github),
            }
            payload = json.dumps({
                'accessToken': 'fake-google-access-token',
                'expiresIn': 620,
                'githubToken': 'fake-github-token',
            }).encode()
            with mock.patch.dict('os.environ', env), mock.patch(
                'awfl.consumer_credentials.urllib.request.urlopen',
                return_value=io.BytesIO(payload),
            ):
                self.assertEqual(refresh(), 320)
            self.assertEqual(google.read_text().strip(), 'fake-google-access-token')
            self.assertEqual(github.read_text().strip(), 'fake-github-token')
            self.assertEqual(google.stat().st_mode & 0o777, 0o600)
            self.assertEqual(github.stat().st_mode & 0o777, 0o600)

    def test_loop_uses_returned_ttl_without_adding_delay_past_deadline(self):
        with mock.patch('awfl.consumer_credentials.refresh', return_value=320), \
             mock.patch('awfl.consumer_credentials.random.randrange', return_value=13), \
             mock.patch('awfl.consumer_credentials.time.sleep',
                        side_effect=RuntimeError('stop loop')) as sleeper, \
             mock.patch.object(__import__('sys'), 'argv', ['awfl-credentials', '--loop']):
            with self.assertRaisesRegex(RuntimeError, 'stop loop'):
                main()
            sleeper.assert_called_once_with(307)

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
