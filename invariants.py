#%%
import sys
sys.path.append('driftpy/src/')

import driftpy
print(driftpy.__path__)

from driftpy.types import UserAccount
from driftpy.constants.config import configs, Config
from anchorpy import Provider
from anchorpy import Wallet
from solana.rpc.async_api import AsyncClient
from driftpy.accounts import *
from solders.keypair import Keypair
import os 
from helpers import *
from driftpy.math.perp_position import is_available

async def validate_market_metrics(program: Program, config: Config):
    user_accounts = await program.account["User"].all()
    n_markets = len(config.perp_markets)

    for market_index in range(n_markets):
        market = await get_perp_market_account(
            program, 
            market_index
        )
        market_total_baa = market.amm.base_asset_reserve + market.amm.base_asset_amount_with_unsettled_lp 

        lp_shares = 0
        user_total_baa = 0 
        for user in user_accounts:
            user: UserAccount = user.account # type: ignore
            position: PerpPosition = [p for p in user.perp_positions if p.market_index == market_index and not is_available(p)] # type: ignore
            
            user_total_baa += position.base_asset_amount
            lp_shares += position.lp_shares

        assert lp_shares == market.amm.user_lp_shares, f"lp shares out of wack: {lp_shares} {market.amm.user_lp_shares}"
        assert user_total_baa == market_total_baa, f"market {market_index}: user baa != market baa ({user_total_baa} {market_total_baa})"
    
    print('market invariants validated!')

async def main():
    script_file = 'start_local.sh'
    os.system(f'cat {script_file}')
    print()
    validator = LocalValidator(script_file)
    validator.start()

    config = configs['mainnet'] # cloned 
    url = 'http://127.0.0.1:8899'
    connection = AsyncClient(url)

    kp = Keypair()
    wallet = Wallet(kp)
    provider = Provider(connection, wallet)
    ch = DriftClient(
        connection,
        wallet,
        "mainnet"
    )

    print('validating...')
    await validate_market_metrics(ch.program, config)

    validator.stop()
    print('done :)')

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())    
