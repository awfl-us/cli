"""Tool implementation; invoked by the SSE consumer, never by the API process."""
import asyncio
import codecs
import os
from pathlib import Path
import signal
import resource
import time
import uuid


def child_limits():
    resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
    resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))


class Runtime:
    def __init__(self, root, *, output_limit=1024 * 1024):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cwd = self.root
        self.executions = {}
        self.output_limit = output_limit

    def path(self, value=None):
        p = (self.cwd / (value or '.')).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError('Path must remain inside the workspace')
        return p

    def open_file(self, value, flags, create_parents=False):
        path = self.path(value)
        parts = path.relative_to(self.root).parts
        if not parts:
            raise ValueError('Expected file path')
        directory = os.open(self.root, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                if create_parents:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=directory)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            return os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        finally:
            os.close(directory)

    async def invoke(self, name, args):
        if name == 'environment_status':
            return {'state': 'ready', 'cwd': str(self.cwd), 'running': sum(x['state'] == 'running' for x in self.executions.values()),
                    'capabilities': ['shell', 'python', 'node', 'git', 'playwright', 'chromium', 'https']}
        if name == 'run_command':
            if sum(x['state'] == 'running' for x in self.executions.values()) >= 4:
                raise ValueError('Concurrent command limit (4) reached')
            if len(self.executions) >= 128:
                raise ValueError('Workspace execution limit (128) reached; create a new workspace')
            cwd = self.path(args.get('cwd'))
            if not cwd.is_dir():
                raise ValueError('Working directory does not exist')
            self.cwd = cwd  # Explicit cwd persists; shell-local cd/export does not.
            execution_id = uuid.uuid4().hex
            item = {'state': 'running', 'exit_code': None, 'chunks': [], 'bytes': 0, 'truncated': False, 'reason': None}
            self.executions[execution_id] = item
            # MCP commands run as this owner's regular AWFL cloud consumer.
            # Preserve its GitHub/Firebase/GCP credentials and other configured
            # environment variables, but do not pass the private MCP callback
            # capability to child shells.
            env = {k: v for k, v in os.environ.items() if not k.startswith('AWFL_EXECUTION_')}
            try:
                item['process'] = await asyncio.create_subprocess_exec(
                    '/bin/bash', '-c', args['command'], cwd=cwd, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True, preexec_fn=child_limits)
            except Exception:
                del self.executions[execution_id]
                raise
            item['task'] = asyncio.create_task(self._collect(item, args.get('timeout', 120)))
            await asyncio.wait({item['task']}, timeout=0.5)
            return {'execution_id': execution_id, **self.output(execution_id, 0)}
        if name == 'command_output':
            return self.output(args['execution_id'], args.get('cursor', 0))
        if name == 'command_cancel':
            item = self.executions[args['execution_id']]
            if item['state'] == 'running':
                item['reason'] = 'cancelled'
                self._kill(item)
                await item['task']
            return self.output(args['execution_id'], args.get('cursor', 0))
        if name == 'read_file':
            path = self.path(args['path'])
            # O_NOFOLLOW also prevents a final-component symlink swap.
            fd = self.open_file(args['path'], os.O_RDONLY)
            with os.fdopen(fd, 'rb') as f:
                import stat
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    raise ValueError('Only regular files can be read')
                f.seek(args.get('offset', 0))
                size = args.get('max_bytes', 32768)
                data = f.read(size + 1)
            return {'content': data[:size].decode('utf-8', errors='replace'),
                    'next_offset': args.get('offset', 0) + min(len(data), size), 'truncated': len(data) > size}
        if name == 'write_file':
            path = self.path(args['path'])
            fd = self.open_file(args['path'], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, create_parents=True)
            with os.fdopen(fd, 'w') as f:
                f.write(args['content'])
            return {'path': str(path), 'bytes': len(args['content'].encode())}
        raise ValueError('Unknown tool')

    def _kill(self, item):
        try:
            os.killpg(item['process'].pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    async def _collect(self, item, timeout):
        async def drain(stream, label):
            decoder = codecs.getincrementaldecoder('utf-8')('replace')
            while True:
                data = await stream.read(8192)
                if not data:
                    tail = decoder.decode(b'', final=True)
                    if tail:
                        item['chunks'].append({'stream': label, 'text': tail})
                    return
                available = max(0, self.output_limit - item['bytes'])
                if available:
                    item['chunks'].append({'stream': label, 'text': decoder.decode(data[:available])})
                    item['bytes'] += min(len(data), available)
                if len(data) > available:
                    item['truncated'] = True
                    item['reason'] = 'output_limit'
                    self._kill(item)

        tasks = [asyncio.create_task(drain(item['process'].stdout, 'stdout')),
                 asyncio.create_task(drain(item['process'].stderr, 'stderr'))]
        try:
            async with asyncio.timeout(timeout):
                await item['process'].wait()
                # Kill background children after the foreground shell exits.
                self._kill(item)
                await asyncio.gather(*tasks)
        except TimeoutError:
            item['reason'] = 'timeout'
            self._kill(item)
            await item['process'].wait()
        finally:
            self._kill(item)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            item['exit_code'] = item['process'].returncode
            item['state'] = 'completed'

    def output(self, execution_id, cursor):
        item = self.executions[execution_id]
        if cursor < 0 or cursor > len(item['chunks']):
            raise ValueError('Invalid output cursor')
        chunks = item['chunks'][cursor:cursor + 4]
        return {k: item[k] for k in ['state', 'exit_code', 'truncated', 'reason']} | {
            'chunks': chunks, 'next_cursor': cursor + len(chunks), 'has_more': cursor + len(chunks) < len(item['chunks'])}

    async def close(self):
        for item in self.executions.values():
            self._kill(item)
        await asyncio.gather(*(x['task'] for x in self.executions.values()), return_exceptions=True)
