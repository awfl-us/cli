# Back-compat shim for callers importing handle_command from cli/commands.py
# New implementation lives in cli/cmds/ package.

from awfl.cmds import handle_command  # noqa: F401
