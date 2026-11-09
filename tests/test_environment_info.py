"""Environment snapshots show capabilities and credential presence, never secrets."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from awfl.environment_info import describe_environment


class EnvironmentInfoTests(unittest.TestCase):
    def test_known_token_files_and_project_are_reported_without_secret_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            google = Path(tmp) / 'google-token'
            github = Path(tmp) / 'github-token'
            google.write_text('GCP-SECRET-NEVER-LOG\n')
            github.write_text('GH-SECRET-NEVER-LOG\n')
            env = {
                'CLOUDSDK_AUTH_ACCESS_TOKEN_FILE': str(google),
                'AWFL_GITHUB_TOKEN_FILE': str(github),
                'CLOUDSDK_CORE_PROJECT': 'awfl-us',
                'AWFL_CREDENTIAL_REFRESH_CAPABILITY': 'SECRET-REFRESH-CAPABILITY',
                'GITHUB_TOKEN': 'GH-STARTUP-SECRET',
            }
            with patch.dict(os.environ, env, clear=True):
                result = describe_environment()
            self.assertTrue(result['credentials']['google_cloud']['available'])
            self.assertTrue(result['credentials']['github']['available'])
            self.assertEqual(result['credentials']['google_cloud']['project'], 'awfl-us')
            self.assertTrue(result['credentials']['google_cloud']['refresh_configured'])
            self.assertIsNone(result['credentials']['google_cloud']['token_file'])
            self.assertIsNone(result['credentials']['github']['token_file'])
            rendered = str(result)
            for forbidden in ('GCP-SECRET', 'GH-SECRET', 'SECRET-REFRESH', str(tmp)):
                self.assertNotIn(forbidden, rendered)

    def test_missing_credentials_are_not_reported_as_available(self):
        with patch.dict(os.environ, {}, clear=True):
            result = describe_environment()
        self.assertFalse(result['credentials']['google_cloud']['available'])
        self.assertFalse(result['credentials']['github']['available'])
        self.assertFalse(result['credentials']['google_cloud']['refresh_configured'])

    def test_invalid_project_string_cannot_escape_into_heartbeat(self):
        with patch.dict(os.environ, {'CLOUDSDK_CORE_PROJECT': 'secret-project\nAuthorization: secret'}, clear=True):
            result = describe_environment()
        self.assertIsNone(result['credentials']['google_cloud']['project'])

    def test_environment_reports_tools_as_booleans(self):
        with patch.dict(os.environ, {}, clear=True):
            tools = describe_environment()['tools']
        self.assertEqual(set(tools), {'git', 'gcloud', 'python', 'node', 'chromium_playwright', 'java', 'sbt'})
        self.assertTrue(all(isinstance(value, bool) for value in tools.values()))

    def test_scala_tooling_capabilities_are_detected_without_running_tools(self):
        with patch('awfl.environment_info.shutil.which', side_effect=lambda name: '/usr/bin/' + name if name in ('java', 'sbt') else None):
            tools = describe_environment()['tools']
        self.assertTrue(tools['java'])
        self.assertTrue(tools['sbt'])
        self.assertFalse(tools['node'])


if __name__ == '__main__':
    unittest.main()
