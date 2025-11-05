from sc2.bot_ai import BotAI
from sc2.constants import UnitTypeId
import time

class NoPlayer(BotAI):
    def __init__(self):
        super().__init__()

    async def run(self, iteration: int):
        pass
