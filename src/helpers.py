import asyncio
import csv
import time
import base64
import jsonrpcclient
import pathlib
import os
import time

from dataclasses import asdict, dataclass
from typing import Generic, Tuple, TypeVar

from anchorpy import Wallet

from solana.rpc.async_api import AsyncClient

from solders.keypair import Keypair  # type: ignore

from driftpy.drift_client import DriftClient
from driftpy.account_subscription_config import AccountSubscriptionConfig
from driftpy.admin import Admin
from driftpy.decode.user import decode_user
from driftpy.types import UserAccount

T = TypeVar("T")

@dataclass
class DataAndSlot(Generic[T]):
    slot: int
    data: T

async def load_local_users(
    _,
    connection: AsyncClient,
    keypairs_path='keypairs/',
    num_users: int = 10
) -> Tuple[list[DriftClient], Admin]:
    admin_ch = None
    chs = []
    sigs = []
    paths = sorted(pathlib.Path(keypairs_path).iterdir(), key=lambda p: p.name)

    for i, p in enumerate(paths):
        print(f"Loading user {i}/{num_users}", end='\r')
        if i == num_users:
            break
        with open(p, 'r') as f:
            s = f.read()
            kp = Keypair.from_seed(bytes.fromhex(s))

        sig = (await connection.request_airdrop(
            kp.pubkey(),
            int(1 * 1e9)
        )).value
        sigs.append(sig)

        # save clearing house
        wallet = Wallet(kp)
        
        if p.name == '1.secret':
            admin_ch = Admin(
                connection,
                wallet,
                "mainnet",
                account_subscription=AccountSubscriptionConfig("cached")
            )
        else:
            ch = DriftClient(
                connection,
                wallet,
                "mainnet",
                account_subscription=AccountSubscriptionConfig("cached")
            )
            chs.append(ch)

    await admin_ch.subscribe() # type: ignore
    for ch in chs: 
        await ch.subscribe()

    print(f"Loaded {len(chs) + 1} users.          ")

    return chs, admin_ch # type: ignore

async def load_nonidle_users_for_market(
    admin: Admin,
    market_index: int,
    keypairs_path="keypairs/",
):
    start = time.time()
    filters = [{"memcmp": {"offset": 0, "bytes": "TfwwBiNJtao"}}]
    filters.append({"memcmp": {"offset": 4350, "bytes": "1"}})

    rpc_request = jsonrpcclient.request(
        "getProgramAccounts",
        [
            str(admin.program_id),
            {"filters": filters, "encoding": "base64", "withContext": True},
        ],
    )

    post = admin.connection._provider.session.post(
        admin.connection._provider.endpoint_uri,
        json=rpc_request,
        headers={"content-encoding": "gzip"},
    )

    resp = await asyncio.wait_for(post, timeout=30)

    parsed_resp = jsonrpcclient.parse(resp.json())

    slot = int(parsed_resp.result["context"]["slot"])  # type: ignore

    rpc_response_values = parsed_resp.result["value"]  # type: ignore

    agents: list[DriftClient] = []
    tasks = []

    print("starting")
    counter = 0
    await admin.account_subscriber.update_cache()
    perp_market = admin.get_perp_market_account(market_index)
    users_with_lp_shares = 0
    running_lp_shares = 0
    print(f"Total users: {len(rpc_response_values)}")
    for i, program_account in enumerate(rpc_response_values):
        print(f"Processing user {i} for market {market_index}", end="\r")
        user: UserAccount = decode_user(
            base64.b64decode(program_account["account"]["data"][0])
        )
        for perp_position in user.perp_positions:
            if perp_position.market_index == market_index:
                running_lp_shares += perp_position.lp_shares
                if perp_position.lp_shares > 0:
                    users_with_lp_shares += 1
                counter += 1
                secret_file_path = (
                    pathlib.Path(keypairs_path) / f"{str(user.authority)}.secret"
                )

                with open(secret_file_path, "r") as f:
                    kp = Keypair.from_seed(bytes.fromhex(f.read()))

                task = asyncio.create_task(
                    admin.connection.request_airdrop(kp.pubkey(), int(1 * 1e9))
                )
                tasks.append(task)

                wallet = Wallet(kp)

                agent = DriftClient(
                    admin.connection,
                    wallet,
                    "mainnet",
                    account_subscription=AccountSubscriptionConfig("cached"),
                    initial_user_data=DataAndSlot(slot, user),
                )

                agents.append(agent)

    print(f"total users with lp shares: {users_with_lp_shares}")
    print(f"total identified lp shares: {running_lp_shares}")
    print(f"loaded {len(agents)} agents.          ")

    asyncio.gather(*tasks)

    print(f"Loaded {len(agents)} agents in {time.time() - start}s")

    return agents


def append_to_csv(data_object, filename, record_type):
    data_dict = asdict(data_object)
    data_dict["record_type"] = record_type  

    write_headers = not os.path.exists(filename) or os.path.getsize(filename) == 0

    with open(filename, "a", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=data_dict.keys())

        if write_headers:
            writer.writeheader()
        writer.writerow(data_dict)

    print(f"{record_type} data appended to {filename} successfully.")
