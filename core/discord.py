from config import GREEN, RED, YELLOW
from core.base import BaseService


class DiscordService(BaseService):
    def send(self, message, color=GREEN, webhook=None):
        self.notify(message, color, webhook)

    def success(self, message, webhook=None):
        self.send(message, GREEN, webhook)

    def warning(self, message, webhook=None):
        self.send(message, YELLOW, webhook)

    def error(self, message, webhook=None):
        self.send(message, RED, webhook)
