from core.base import BaseService


class PlayerService(BaseService):

    def check(
        self,
        page,
        browser_service,
        buff_service,
        skill_service,
    ):
        data = browser_service.fetch_player(page)

        buff_service.process(data)
        skill_service.process(data)
