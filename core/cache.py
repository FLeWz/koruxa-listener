import json


class Cache:
    def __init__(self, path):
        self.path = path
        self.data = self._load()

    def _load(self):
        defaults = {
            "generated_at": None,
            "coins": None,
            "last_item_at": None,
        }

        if not self.path.exists():
            return defaults

        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                return defaults

            for key, value in defaults.items():
                data.setdefault(key, value)

            return data

        except (OSError, json.JSONDecodeError):
            return defaults

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def __contains__(self, key):
        return key in self.data

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)

        temp_file = self.path.with_suffix(self.path.suffix + ".tmp")

        with temp_file.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

        temp_file.replace(self.path)
