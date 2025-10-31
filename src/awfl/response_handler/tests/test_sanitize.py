import unittest

from cli.response_handler.rh_utils import sanitize_shell_command


class TestSanitizeShellCommand(unittest.TestCase):
    def test_adds_missing_closing_single_quote(self):
        cmd = "bash -lc 'sed -n \"1,220p\" /Users/paul/github/TopAigents/workflows/src/main/scala/workflows/codegen/SchemaDerivation.scala"
        sanitized, reason = sanitize_shell_command(cmd)
        self.assertTrue(sanitized.endswith("'"), sanitized)
        self.assertIn("added missing closing '", reason or "")
        # Should be runnable by bash -lc
        self.assertIn("bash -lc '", sanitized)

    def test_adds_missing_closing_double_quote(self):
        cmd = 'echo "hello'
        sanitized, reason = sanitize_shell_command(cmd)
        self.assertTrue(sanitized.endswith('"'), sanitized)
        self.assertIn('added missing closing "', reason or "")

    def test_removes_trailing_unmatched_single_quote(self):
        cmd = "echo hello'"
        sanitized, reason = sanitize_shell_command(cmd)
        self.assertEqual(sanitized, "echo hello")
        self.assertIn("removed trailing unmatched '", reason or "")

    def test_removes_trailing_unmatched_braces(self):
        cmd = "echo hi}}}"
        sanitized, reason = sanitize_shell_command(cmd)
        self.assertEqual(sanitized, "echo hi")
        self.assertIn("removed trailing unmatched }", reason or "")

    def test_no_change_when_balanced(self):
        cmds = [
            "echo 'ok'",
            'echo "ok"',
            "bash -lc 'echo "ok"'",
            "sed -n '1,10p' ./file.txt",
        ]
        for cmd in cmds:
            sanitized, reason = sanitize_shell_command(cmd)
            self.assertEqual(sanitized, cmd)
            self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
