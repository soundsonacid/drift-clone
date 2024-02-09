from typing import Optional, Tuple
from anchorpy import Provider
from anchorpy import Wallet
from solana.rpc.async_api import AsyncClient
from driftpy.drift_client import DriftClient
from solders.keypair import Keypair # type: ignore
import pathlib
from subprocess import Popen
import os
import time
import signal
from driftpy.admin import Admin


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
) -> Tuple[list[DriftClient], Admin]:
    admin_ch = None
    chs = []
    sigs = []
    for p in pathlib.Path(keypairs_path).iterdir():
        with open(p, 'r') as f:
            s = f.read()
            kp = Keypair.from_bytes(bytes.fromhex(s))

        sig = (await connection.request_airdrop(
            kp.pubkey(),
            int(100 * 1e9)
        )).value
        sigs.append(sig)

        # save clearing house
        wallet = Wallet(kp)

        if p.name == 'state.secret':
            print('found admin...')
            admin_ch = Admin(
                connection,
                wallet,
                "mainnet"
            )
        else:
            ch = DriftClient(
                connection,
                wallet,
                "mainnet"
            )
            chs.append(ch)

    print('confirming SOL airdrops...')
    await connection.confirm_transaction(sigs[-1])

    return chs, admin_ch # type: ignore
