import tempfile
import unittest
from pathlib import Path
from awfl.onboarding_project import prompt_for_project

class OnboardingProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'example-project'
        self.root.mkdir()

    def test_accept_default_name(self):
        answers = iter(['', ''])
        called = []
        def init(name, root):
            called.append((name, root))
            return 'project-123', root / '.awfl/config.json'
        self.assertEqual(prompt_for_project(lambda _: next(answers), init, self.root), 'project-123')
        self.assertEqual(called, [('example-project', self.root)])

    def test_custom_name(self):
        answers = iter(['yes', 'Custom Project'])
        called = []
        def init(name, root):
            called.append(name)
            return 'custom-id', root / '.awfl/config.json'
        self.assertEqual(prompt_for_project(lambda _: next(answers), init, self.root), 'custom-id')
        self.assertEqual(called, ['Custom Project'])

    def test_decline_does_not_create(self):
        self.assertIsNone(prompt_for_project(lambda _: 'n', lambda *_: self.fail('Unexpected creation'), self.root))

    def test_eof_does_not_create(self):
        def closed(_): raise EOFError
        self.assertIsNone(prompt_for_project(closed, lambda *_: self.fail('Unexpected creation'), self.root))

    def test_error_propagates(self):
        answers = iter(['y', 'Name'])
        def failed(*_): raise RuntimeError('Server unavailable')
        with self.assertRaisesRegex(RuntimeError, 'Server unavailable'):
            prompt_for_project(lambda _: next(answers), failed, self.root)
