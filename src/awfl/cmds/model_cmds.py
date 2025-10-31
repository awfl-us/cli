import os
from awfl.utils import log_unique


def get_or_set_model(arg: str | None) -> bool:
    if not arg:
        model = os.getenv('LLM_MODEL', 'gpt-5')
        log_unique(f"🧠 Current model: {model}")
        return True
    os.environ['LLM_MODEL'] = arg.strip()
    log_unique(f"🧠 Set LLM_MODEL={os.environ['LLM_MODEL']}")
    return True
