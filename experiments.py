import asyncio
import pathlib
import subprocess
import datetime as dt

from dataclasses import dataclass

from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair # type: ignore

from anchorpy import Wallet

from driftpy.accounts import get_user_account_public_key
from driftpy.account_subscription_config import AccountSubscriptionConfig
from driftpy.drift_client import DriftClient
from driftpy.drift_user import DriftUser

from slack import SimulationResultBuilder, Slack
from helpers import load_local_users
from actions import get_action
from scenarios import oracle_jump

@dataclass
class Tester:
    drift_client: DriftClient
    drift_user: DriftUser

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
        self.tester = None
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

    async def create_tester(self):
        print("initializing tester")
        tester_kp = Keypair() # random new keypair
        wallet = Wallet(tester_kp)

        print(f"requesting airdrop for tester pubkey: {tester_kp.pubkey()}")
        sig = (await self.connection.request_airdrop(tester_kp.pubkey(), int(10 * 1e9))).value
        await asyncio.sleep(3) # give the airdrop a second

        command = ["solana", "confirm", f"{sig}"]
        output = subprocess.run(command, capture_output=True, text=True).stdout.strip()
        print(f"airdrop status: {output}")

        drift_client = DriftClient(
            self.connection,
            wallet,
            "mainnet",
            account_subscription=AccountSubscriptionConfig("websocket")
        )

        sig = (await drift_client.initialize_user())
        await asyncio.sleep(3)

        command = ["solana", "confirm", f"{sig}"]
        output = subprocess.run(command, capture_output=True, text=True).stdout.strip()
        print(f"initialize user status: {output}")

        await drift_client.add_user(0)
        await drift_client.subscribe()

        drift_user = drift_client.get_user()
        await drift_user.subscribe()

        self.tester = Tester(drift_client, drift_user)

        print(f"initialized tester")

    async def test_exchange_behavior(self):
        # TODO
        pass

async def main():
    print("spinning up drift simulation..")
    slack = Slack()
    sim_results = SimulationResultBuilder(slack)
    sim_results.set_start_time(dt.datetime.utcnow())

    simulator = Simulator(sim_results)

    await simulator.setup()

    await oracle_jump(simulator.admin, 5, 0, None, 0.01)

    await asyncio.sleep(3)

    opd = simulator.admin.get_oracle_price_data_for_perp_market(0).price # type: ignore
    print(opd)

    # while True:
    #     await asyncio.sleep(3_500)
    # await simulator.experiment(10)

    # await simulator.create_tester()

    # await simulator.test_exchange_behavior()

if __name__ == "__main__":
    import asyncio

    asyncio.run(main())