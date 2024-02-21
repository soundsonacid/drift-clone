import asyncio
import subprocess
import traceback

from typing import Optional

from solders.signature import Signature # type: ignore

from driftpy.admin import Admin
from driftpy.drift_client import DriftClient
from driftpy.decode.utils import decode_name
from driftpy.accounts import get_user_account_and_slot
from driftpy.addresses import get_user_account_public_key

from actions import *
from slack import ExpiredMarket, SimulationResultBuilder
from close import get_insurance_fund_balance, get_spot_vault_balance

async def oracle_jump(
    admin: Admin,
    sleep: int,
    market_index: int,
    price_delta: Optional[int] = None,
    pct_delta: Optional[float] = None
):  
    async def price_jump():
        while True:
            oracle = admin.get_perp_market_account(market_index).amm.oracle # type: ignore
            price = admin.get_oracle_price_data_for_perp_market(market_index).price # type: ignore
            print(f"old price: {price}")
            new_price = price + price_delta 
            print(f"new price: {new_price}")
            sig = await set_oracle_price(admin, oracle, new_price)
            print(f"new oracle price: {new_price} set for perp market: {market_index}: {sig}")
            await asyncio.sleep(sleep)
            await admin.account_subscriber.update_cache()

    async def pct_jump():
        while True:
            oracle = admin.get_perp_market_account(market_index).amm.oracle # type: ignore
            price = admin.get_oracle_price_data_for_perp_market(market_index).price # type: ignore
            print(f"old price: {price}")
            new_price = int(price * (1 + pct_delta)) 
            print(f"new price: {new_price}")
            sig = await set_oracle_price(admin, oracle, new_price)
            print(f"new oracle price: {new_price} set for perp market: {market_index}: {sig}")
            await asyncio.sleep(sleep)
            await admin.account_subscriber.update_cache()

    if price_delta is not None:
        asyncio.create_task(price_jump())
    elif pct_delta is not None:
        asyncio.create_task(pct_jump())
    else:
        raise ValueError("need to provide price or pct delta")

async def close_market(
    admin: Admin,
    agents: list[DriftClient],
    sim_results: SimulationResultBuilder,
    market_index: int
):
    # record stats pre-closing
    await admin.account_subscriber.update_cache()
    perp_market = admin.get_perp_market_account(market_index)
    sim_results.add_initial_perp_market(perp_market) # type: ignore

    spot_markets = admin.get_spot_market_accounts()
    for market in spot_markets:
        if_balance = await get_insurance_fund_balance(admin.connection, market)
        vault_balance = await get_spot_vault_balance(admin.connection, market)
        print(f"{decode_name(market.name)}: {if_balance} {vault_balance}")
        sim_results.add_initial_spot_market(if_balance, vault_balance, market)

    # update state
    await admin.update_perp_auction_duration(0)
    await admin.update_lp_cooldown_time(0)
    # i don't think i need this for delisting one perp market, right ?
    # for market in spot_markets:
    #     await admin.update_update_insurance_fund_unstaking_period(market.market_index, 0)
    #     await admin.update_withdraw_guard_threshold(market.market_index, 2**64 - 1)

    print(f"delisting market...")
    slot = (await admin.connection.get_slot()).value
    blocktime: int = (await admin.connection.get_block_time(slot)).value # type: ignore

    print("updating expiries")
    offset = 50
    sigs: list[Signature] = []
    sig = await admin.update_perp_market_expiry(market_index, blocktime + offset)
    sigs.append(sig)

    for market in spot_markets:
        sig = await admin.update_spot_market_expiry(market.market_index, blocktime + offset)

    before_user_lp_shares = perp_market.amm.user_lp_shares # type: ignore

    # remove liq
    print("removing all user liq")
    liq_sigs: list[Signature] = []
    print(f"removing lp for {len(agents)} agents")
    print(f"total market lp shares: {perp_market.amm.user_lp_shares}") # type: ignore
    running_lp_removed = 0
    for i, agent in enumerate(agents):
        print(f"removing liq for agent: {i}")
        for subaccount in agent.sub_account_ids:
            print(f"removing liq for agent: {i} subaccount: {subaccount}")
            position = agent.get_perp_position(market_index, subaccount)
            print(f"agent has position: {position is not None}")
            print(f"agent has lp shares: {position.lp_shares > 0}") # type: ignore
            print(f"total lp shares for agent: {position.lp_shares}") # type: ignore
            if position is not None and position.lp_shares > 0:
                print(
                    f"removing lp on market {market_index} "
                    f"for user: {str(agent.authority)} " 
                    f"(sub_account_id: {subaccount}, shares: {position.lp_shares})"
                )
                running_lp_removed += position.lp_shares
                sig = await agent.remove_liquidity(position.lp_shares, market_index, subaccount)
                command = ["solana", "confirm", f"{sig}"]
                output = subprocess.run(command, capture_output=True, text=True).stdout.strip()
                if "Confirmed" in output or "Processed" in output or "Finalized" in output:
                    print(f"confirmed remove liq tx: {sig}")
                else:
                    print(f"failed to confirm remove liq tx: {output}")  
                await asyncio.sleep(5)
                await admin.account_subscriber.update_cache()
                perp_market = admin.get_perp_market_account(market_index)
                assert perp_market.amm.user_lp_shares == before_user_lp_shares - running_lp_removed, f"user lp shares {perp_market.amm.user_lp_shares} dne {before_user_lp_shares - running_lp_removed}" # type: ignore 
                # liq_sigs.append(sig)

    # for i, sig in enumerate(liq_sigs):
    #     print(f"confirming remove liq tx: {i}/{len(liq_sigs)}")
    #     try:
    #         command = ["solana", "confirm", f"{sig}"]
    #         output = subprocess.run(command, capture_output=True, text=True).stdout.strip()
    #         if "Confirmed" in output or "Processed" in output or "Finalized" in output:
    #             print(f"confirmed remove liq tx: {sig}")
    #         else:
    #             print(f"failed to confirm remove liq tx: {output}")     
    #     except Exception as e:
    #         print(f"error confirming remove_liquidity error: {e}")
    
    await asyncio.sleep(15) # make sure we get a new account 
    await admin.account_subscriber.update_cache()
    perp_market = admin.get_perp_market_account(market_index)
    assert perp_market

    print(f"user lp shares: {before_user_lp_shares}") # type: ignore
    print(f"removed lp shares: {running_lp_removed}")
    print(f"total lp == removed lp: {before_user_lp_shares == running_lp_removed}") # type: ignore
    assert perp_market.amm.user_lp_shares == 0, f"user lp shares {perp_market.amm.user_lp_shares} dne 0" # type: ignore

    print("waiting for expiry...")

    # fully expire market
    for sig in sigs:
        try:
            command = ["solana", "confirm", f"{sig}"]
            output = subprocess.run(command, capture_output=True, text=True).stdout.strip()
            if "Confirmed" in output or "Processed" in output or "Finalized" in output:
                pass
            else:
                print(f"failed to confirm update transaction: {output}")                
        except Exception as e:
            print(f"error confirming update_[perp|spot]_market txs: {e}")
            traceback.print_exc()

    print("settling expired market")
    print(f"baa with unsettled lp: {perp_market.amm.base_asset_amount_with_unsettled_lp}")
    print(f"user lp shares: {perp_market.amm.user_lp_shares}")

    sig = await admin.settle_expired_market(perp_market.market_index)
    command = ["solana", "confirm", f"{sig}"]
    output = subprocess.run(command, capture_output=True, text=True).stdout.strip()
    if "Confirmed" in output or "Processed" in output or "Finalized" in output:
        print(f"confirmed settle tx: {sig}")
    else:
        print(f"failed to confirm settle tx: {output}")     

    await asyncio.sleep(30) # make sure we get a new account from update cache
    await admin.account_subscriber.update_cache()
    perp_market = admin.get_perp_market_account(market_index)
    assert perp_market

    print(
        f"market {perp_market.market_index} expiry_price vs twap/price",
        perp_market.status,
        perp_market.expiry_price,
        perp_market.amm.historical_oracle_data.last_oracle_price_twap,
        perp_market.amm.historical_oracle_data.last_oracle_price,
    )
    expired_market = ExpiredMarket(
        perp_market.market_index,
        perp_market.status,
        perp_market.expiry_price / PRICE_PRECISION,
        perp_market.amm.historical_oracle_data.last_oracle_price_twap
        / PRICE_PRECISION,
        perp_market.amm.historical_oracle_data.last_oracle_price / PRICE_PRECISION,
    )
    sim_results.add_settled_expired_market(expired_market)
    