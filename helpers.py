import asyncio
import time
import base64
from typing import Tuple
from anchorpy import Wallet
import jsonrpcclient
from solana.rpc.async_api import AsyncClient
from driftpy.drift_client import DriftClient
from driftpy.account_subscription_config import AccountSubscriptionConfig
from solders.keypair import Keypair # type: ignore
import pathlib
from subprocess import Popen
import os
import time
import signal
from driftpy.admin import Admin
from driftpy.decode.user import decode_user
import subprocess

class LocalValidator:
    def __init__(self, script_file) -> None:
        self.script_file = script_file

    def start(self):
        """
        starts a new solana-test-validator by running the given script path
        and logs the stdout/err to the logfile
        """
        self.log_file = open('node.txt', 'w')
        self.proc = Popen(
            f'bash {self.script_file}'.split(' '),
            stdout=self.log_file,
            stderr=self.log_file,
            preexec_fn=os.setsid
        )
        time.sleep(5)

    def stop(self):
        self.log_file.close()
        os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)


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

    print('Confirming SOL airdrops...')
    confirmed_count = 0  

    for i, sig in enumerate(sigs):
        command = ["solana", "confirm", f"{sig}"]
        output = subprocess.run(command, capture_output=True, text=True).stdout.strip()
        if "0x1" not in output:
            confirmed_count += 1  

        print(f"Confirming airdrops: {confirmed_count}/{len(sigs)} confirmed", end='\r')

    print(f"\nConfirmed {confirmed_count}/{len(sigs)} airdrops successfully.")


    return chs, admin_ch # type: ignore


async def load_nonidle_users_for_market(
    admin: Admin,
    market_index: int,
    keypairs_path='keypairs/',
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

    rpc_response_values = parsed_resp.result["value"] # type: ignore

    agents: list[DriftClient] = []
    tasks = []

    print("starting")
    counter = 0
    await admin.account_subscriber.update_cache()
    perp_market = admin.get_perp_market_account(market_index)
    print(perp_market.__dict__) # type: ignore
    lp_shares = perp_market.amm.user_lp_shares # type: ignore
    users_with_lp_shares = 0
    running_lp_shares = 0
    print(f"Total users: {len(rpc_response_values)}")
    for i, program_account in enumerate(rpc_response_values):
        print(f"Processing user {i}", end='\r')
        user = decode_user(
            base64.b64decode(program_account["account"]["data"][0])
        )
        for perp_position in user.perp_positions:
            if perp_position.market_index == market_index:
                print(f"User {i} has position on market {market_index}: {perp_position.market_index}")
                print(f"Total users in market: {counter + 1}")
                # assert user.user
                running_lp_shares += perp_position.lp_shares
                if perp_position.lp_shares > 0:
                    users_with_lp_shares += 1
                counter += 1
                secret_file_path = pathlib.Path(keypairs_path) / f"{str(user.authority)}.secret"
                
                with open(secret_file_path, 'r') as f:
                    kp = Keypair.from_seed(bytes.fromhex(f.read()))

                task = asyncio.create_task(admin.connection.request_airdrop(
                    kp.pubkey(),
                    int(1 * 1e9)
                ))
                tasks.append(task)

                wallet = Wallet(kp)

                agent = DriftClient(
                    admin.connection,
                    wallet,
                    "mainnet",
                    account_subscription=AccountSubscriptionConfig("cached")
                )

                agents.append(agent)

    print(f"total users with lp shares: {users_with_lp_shares}")
    print(f"total identified lp shares: {running_lp_shares}")
    assert lp_shares == running_lp_shares, f"lp shares {lp_shares} dne {running_lp_shares}" # type: ignore
    for agent in agents:
        await agent.subscribe()
        await agent.account_subscriber.update_cache()
    # print(f"loaded {counter} agents.          ")

    print(f"loaded {len(agents)} agents.          ")
    confirmed_count = 0  

    asyncio.gather(*tasks)

    # for i, sig in enumerate(sigs):
    #     command = ["solana", "confirm", f"{sig}"]
    #     output = subprocess.run(command, capture_output=True, text=True).stdout.strip()
    #     if "0x1" not in output:
    #         confirmed_count += 1  

    #     print(f"Confirming airdrops: {confirmed_count}/{len(sigs)} confirmed", end='\r')

    # print(f"\nConfirmed {confirmed_count}/{len(sigs)} airdrops successfully.")

    print(f"Loaded {len(agents)} agents in {time.time() - start}s")

    return agents


    





                

