from typing import Tuple
from anchorpy import Wallet
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
                account_subscription=AccountSubscriptionConfig("websocket")
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
