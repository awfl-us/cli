class SSEParser:
    """Minimal SSE parser for text/event-stream.

    Accumulates fields until a blank line, then yields a complete event dict:
      { 'id': str|None, 'event': str|None, 'data': str }
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._id = None
        self._event = None
        self._data_lines = []

    def feed_line(self, line: str):
        # Heartbeat/comment lines start with ':'; ignore
        if line.startswith(":"):
            return None

        # End of event
        if line.strip() == "":
            if not self._data_lines and not self._id and not self._event:
                # Spurious blank line
                return None
            evt = {
                "id": self._id,
                "event": self._event or "message",
                "data": "\n".join(self._data_lines),
            }
            self.reset()
            return evt

        # Field parsing
        if line.startswith("data:"):
            self._data_lines.append(line[5:].lstrip())
            return None
        if line.startswith("id:"):
            self._id = line[3:].lstrip().rstrip("\n\r")
            return None
        if line.startswith("event:"):
            self._event = line[6:].lstrip().rstrip("\n\r")
            return None
        # Unknown field -> ignore
        return None
