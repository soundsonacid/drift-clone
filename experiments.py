import asyncio
import pathlib

import datetime as dt

from solana.rpc.async_api import AsyncClient

from driftpy.accounts import get_user_account_public_key
from driftpy.drift_client import DriftClient
from driftpy.admin import Admin

from slack import SimulationResultBuilder, Slack
from helpers import load_local_users
from actions import Action, get_action

async def load_subaccounts(chs):
    accounts = [p.stem for p in pathlib.Path("accounts").iterdir()]
    active_chs = []
    for ch in chs:
        subaccount_ids = []
        for sid in range(10):
            user_pk = get_user_account_public_key(ch.program_id, ch.authority, sid)
            if str(user_pk) in accounts:
                subaccount_ids.append(sid)

        for id in subaccount_ids:
            await ch.add_user(id)
        if len(subaccount_ids) != 0:
            active_chs.append(ch)
    return active_chs


class Simulator:
    def __init__(self, sim_results: SimulationResultBuilder):
        self.admin = None
        self.agents: list[DriftClient] = []
        self.connection = AsyncClient("http://127.0.0.1:8899")
        self.sim_results = sim_results

    async def setup(self):
        agents, admin = await load_local_users(None, self.connection)

        self.admin = admin
        self.agents = await load_subaccounts(agents)

        slot = (await self.connection.get_slot()).value
        self.sim_results.set_start_slot(slot)

        users = 0
        for agent in agents:
            for _ in agent.sub_account_ids:
                users += 1
        self.sim_results.add_total_users(users)

    async def generate_and_execute_action(self):
        action = get_action(self.admin)
        await action.execute(self.admin)

    async def experiment(self, num_actions: int):
        for _ in range(num_actions):
            await self.generate_and_execute_action()

async def main():
    print("spinning up drift simulation..")
    slack = Slack()
    sim_results = SimulationResultBuilder(slack)
    sim_results.set_start_time(dt.datetime.utcnow())

    simulator = Simulator(sim_results)

    await simulator.setup()

    await simulator.experiment(10)

if __name__ == "__main__":
    import asyncio

    asyncio.run(main())