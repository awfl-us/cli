# Entry point wrapper to run the in-package CLI main module as a script.
# This preserves the current behavior of cli/main.py (which runs on __main__).

import runpy

def main() -> None:  # console_scripts target
    # Execute the installed 'main' module as if run via `python -m main`
    # This will trigger the top-level asyncio.run(main()) inside cli/main.py
    runpy.run_module("main", run_name="__main__")
