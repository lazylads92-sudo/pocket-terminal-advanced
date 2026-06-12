import inspect, sys
from ..core.terminal import SESSION
from ..core.style import Style

CMD_CATALOGUE = []

class CommandRegistry:
    def __init__(self):
        self._commands = {}

    def register(self, name, fn, description="", examples=None):
        name = name.lower()
        self._commands[name] = fn
        CMD_CATALOGUE.append((name, description, examples or []))
        return fn

    def get(self, name):
        return self._commands.get(name.lower())

    def all(self):
        return dict(self._commands)

    def execute(self, name, args):
        fn = self.get(name)
        if fn is None:
            return None
        SESSION.record(f"{name} {' '.join(args)}")
        try:
            result = fn(args)
            if result is None:
                return ""
            return result
        except Exception as e:
            SESSION.record(f"{name} {' '.join(args)}", ok=False)
            return f"{Style.RED}Error: {e}{Style.RESET}"


registry = CommandRegistry()
