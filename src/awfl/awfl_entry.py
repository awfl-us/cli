# Entry point wrapper to run the in-package CLI main module as a script.
# This preserves the current behavior of cli/main.py (which runs on __main__).

import runpy

def main():
    runpy.run_module("awfl.main", run_name="__main__")
