import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from awfl.local_project import config_path, init_local_project, read_local_config, store_local_project


class LocalProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_atomic_config_creation_and_existing_binding_preserved(self):
        path = store_local_project('project-123', 'Local test', self.root)
        self.assertEqual(path, config_path(self.root))
        self.assertEqual(read_local_config(self.root), {'projectId': 'project-123', 'name': 'Local test'})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            store_local_project('other-project', 'Other', self.root)
        self.assertEqual(read_local_config(self.root)['projectId'], 'project-123')

    def test_invalid_config_is_not_silently_overwritten(self):
        path = config_path(self.root)
        path.parent.mkdir()
        path.write_text('{bad json')
        with self.assertRaises(json.JSONDecodeError):
            read_local_config(self.root)
        with self.assertRaises(FileExistsError):
            init_local_project('A project', self.root)

    def test_explicit_creation_posts_name_without_git_remote(self):
        listed = MagicMock()
        listed.json.return_value = {'projects': []}
        created = MagicMock()
        created.json.return_value = {'project': {'id': 'project-xyz', 'remote': None}}
        with patch('requests.get', return_value=listed) as get, \
             patch('requests.post', return_value=created) as post, \
             patch('awfl.auth.get_auth_headers', return_value={'Authorization': 'Bearer test'}), \
             patch('awfl.utils.get_api_origin', return_value='https://localhost.invalid'):
            project_id, _ = init_local_project('Local test', self.root)
        self.assertEqual(project_id, 'project-xyz')
        self.assertEqual(read_local_config(self.root)['projectId'], project_id)
        self.assertEqual(post.call_args.kwargs['json'], {'name': 'Local test', 'live': False})
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer test')

    def test_duplicate_name_does_not_create_server_project(self):
        listed = MagicMock()
        listed.json.return_value = {'projects': [{'name': 'Same', 'remote': None}]}
        with patch('requests.get', return_value=listed), \
             patch('requests.post') as post, \
             patch('awfl.auth.get_auth_headers', return_value={}), \
             patch('awfl.utils.get_api_origin', return_value='https://localhost.invalid'):
            with self.assertRaisesRegex(ValueError, 'already exists'):
                init_local_project('Same', self.root)
        post.assert_not_called()


class LocalProjectResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def test_saved_project_validated_without_git_discovery(self):
        from awfl.events.workspace import resolve_project_id
        store_local_project('project-123', 'Local', self.root)
        response = MagicMock()
        response.status = 200
        response.json = unittest.mock.AsyncMock(return_value={'project': {'id': 'project-123'}})
        class Context:
            async def __aenter__(self): return response
            async def __aexit__(self, *args): return None
        session = MagicMock()
        session.get.return_value = Context()
        with patch.dict(os.environ, {'AWFL_PROJECT_ID': ''}), \
             patch('awfl.local_project.Path.cwd', return_value=self.root), \
             patch('awfl.events.workspace.repo_remote') as remote, \
             patch('awfl.events.workspace.set_project_id') as set_project, \
             patch('awfl.events.workspace.get_auth_headers', return_value={}), \
             patch('awfl.events.workspace.get_api_origin', return_value='https://localhost.invalid'):
            found = await resolve_project_id(session)
        self.assertEqual(found, 'project-123')
        set_project.assert_called_once_with('project-123')
        remote.assert_not_called()

    async def test_explicit_environment_override_wins(self):
        from awfl.events.workspace import resolve_project_id
        session = MagicMock()
        with patch.dict(os.environ, {'AWFL_PROJECT_ID': 'override'}), \
             patch('awfl.events.workspace.set_project_id') as set_project:
            found = await resolve_project_id(session)
        self.assertEqual(found, 'override')
        set_project.assert_called_once_with('override')
        session.get.assert_not_called()
