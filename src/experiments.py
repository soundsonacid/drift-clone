import asyncio
import os
import pathlib
import subprocess
import datetime as dt
import time

from dataclasses import dataclass

from solana.rpc.async_api import AsyncClient

from solders.keypair import Keypair  # type: ignore
from solders.pubkey import Pubkey  # type: ignore

from anchorpy import Wallet

from driftpy.accounts import get_user_account_public_key
from driftpy.account_subscription_config import AccountSubscriptionConfig
from driftpy.types import MarketType, UserAccount, is_variant
from driftpy.drift_client import DriftClient
from driftpy.drift_user import DriftUser
from driftpy.address_lookup_table import get_address_lookup_table

from src.slack import SimulationResultBuilder, Slack
from src.helpers import append_to_csv, load_local_users, load_nonidle_users_for_market
from src.actions import get_action
from src.scenarios import move_oracle_up_40, move_oracle_down_40


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

    # for ch in active_chs:
    #     await ch.account_subscriber.update_cache()
    return active_chs


class Simulator:
    def __init__(self, sim_results: SimulationResultBuilder):
        self.admin = None
        self.agents: list[DriftClient] = []
        self.connection = AsyncClient("http://127.0.0.1:8899")
        self.tester = None
        self.sim_results = sim_results

    async def setup(self):
        agents, admin = await load_local_users(None, self.connection, num_users=1)

        self.admin = admin

        slot = (await self.connection.get_slot()).value
        self.sim_results.set_start_slot(slot)

        users = 0
        # for agent in agents:
        #     data = agent.get_user().get_user_account_and_slot()
        #     print(f"user: {agent.authority}")
        #     print(data is not None)
        #     for _ in agent.sub_account_ids:
        #         users += 1
        # self.sim_results.add_total_users(users)

        agents = await load_nonidle_users_for_market(admin, 9)
        self.agents = await load_subaccounts(agents)
        for agent in self.agents:
            for _ in agent.sub_account_ids:
                users += 1
        self.sim_results.add_total_users(users)
        await asyncio.sleep(30)

    async def generate_and_execute_action(self):
        action = get_action(self.admin)
        await action.execute(self.admin)

    async def experiment(self, num_actions: int):
        for _ in range(num_actions):
            await self.generate_and_execute_action()

    async def create_tester(self):
        print("initializing tester")
        tester_kp = Keypair()  # random new keypair
        wallet = Wallet(tester_kp)

        print(f"requesting airdrop for tester pubkey: {tester_kp.pubkey()}")
        sig = (
            await self.connection.request_airdrop(tester_kp.pubkey(), int(10 * 1e9))
        ).value
        await asyncio.sleep(3)  # give the airdrop a second

        command = ["solana", "confirm", f"{sig}"]
        output = subprocess.run(command, capture_output=True, text=True).stdout.strip()
        print(f"airdrop status: {output}")

        drift_client = DriftClient(
            self.connection,
            wallet,
            "mainnet",
            account_subscription=AccountSubscriptionConfig("websocket"),
        )

        sig = await drift_client.initialize_user()
        await asyncio.sleep(15)

        command = ["solana", "confirm", f"{sig}"]
        output = subprocess.run(command, capture_output=True, text=True).stdout.strip()
        print(f"initialize user status: {output}")

        await drift_client.subscribe()
        # await drift_client.add_user(0)

        drift_user = drift_client.get_user()
        # await drift_user.subscribe()

        self.tester = Tester(drift_client, drift_user)

        await asyncio.sleep(3)

        user_account = drift_user.get_user_account_and_slot()
        print(user_account)

        print(f"initialized tester")

    async def test_exchange_behavior(self, market_index: int):
        """
        This is currently impl for the move oracle up & down 40% scenario
        """
        # dump initial state of amm & insurance into csv
        admin = self.admin

        lut = await get_address_lookup_table(admin.connection, Pubkey.from_string("D9cnvzswDikQDf53k4HpQ3KJ9y1Fv3HGGDFYMXnK5T6c"))  # type: ignore
        print(len(lut.addresses))

        market = admin.get_perp_market_account(market_index)  # type: ignore
        append_to_csv(market, "sim_results.csv", "init market")

        usdc_spot_market = admin.get_spot_market_account(0)  # type: ignore
        insurance_vault = usdc_spot_market.insurance_fund  # type: ignore
        append_to_csv(insurance_vault, "sim_results.csv", "init if")

        # tasks = []

        def orders(user):
            counter = 0
            for subaccount in user.sub_account_ids:
                user_account: UserAccount = user.get_user(subaccount).get_user_account()
                for order in user_account.orders:
                    if is_variant(order.market_type, "Perp"):
                        if order.market_index == market_index:
                            counter += 1
            return counter

        start = time.time()
        counter = 0
        tasks = []
        for user in self.agents:
            await user.account_subscriber.update_cache()
            for subaccount in user.sub_account_ids:
                try:
                    num = orders(user)
                    if num > 0:
                        print(
                            f"canceling orders: {num} for user: {user.authority} market index: {market_index}"
                        )
                        tasks.append(
                            asyncio.create_task(
                                user.cancel_orders(
                                    sub_account_id=subaccount,
                                    market_index=market_index,
                                    market_type=MarketType.Perp(),
                                )
                            )
                        )
                        counter += 1

                except Exception as e:
                    print(e)
                    raise e

        await asyncio.gather(*tasks)
        print(f"cancelled {counter} orders in {time.time() - start}s")
        await asyncio.sleep(30)

        for user in self.agents:
            await user.get_user().account_subscriber.update_cache()
            for subaccount in user.sub_account_ids:
                num = orders(user)
                assert (
                    num == 0
                ), f"orders: {num} remaining for user: {user.authority} market index: {market_index}"

        await move_oracle_down_40(admin, 9)  # type: ignore

        # this is a hacky way to wait for the keepyrs script to be done
        # i was having issues getting keepyrs into here as a dep so i run it separately
        # see https://github.com/soundsonacid/keepyrs/tree/sim-keepyrs for the script
        flag_file = os.path.expanduser("~/file.txt")
        while True:
            if os.path.exists(flag_file):
                print("keepyrs done")
                os.remove(flag_file)
                break
            else:
                print("waiting for keepyrs to finish..")
            await asyncio.sleep(1)

        # dump final state of amm & insurance into csv
        await asyncio.sleep(30)
        await admin.account_subscriber.update_cache()  # type: ignore

        market = admin.get_perp_market_account(market_index)  # type: ignore
        append_to_csv(market, "sim_results.csv", "final market")

        usdc_spot_market = admin.get_spot_market_account(0)  # type: ignore
        insurance_vault = usdc_spot_market.insurance_fund  # type: ignore
        append_to_csv(insurance_vault, "sim_results.csv", "final if")


async def main():
    print("spinning up drift simulation..")
    slack = Slack()
    sim_results = SimulationResultBuilder(slack)
    sim_results.set_start_time(dt.datetime.utcnow())

    simulator = Simulator(sim_results)

    await simulator.setup()

    await simulator.test_exchange_behavior(9)

if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
