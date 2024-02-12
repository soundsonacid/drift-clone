import asyncio

from typing import Optional

from driftpy.admin import Admin

from actions import *


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
            break

    async def pct_jump():
        # while True:
        oracle = admin.get_perp_market_account(market_index).amm.oracle # type: ignore
        price = admin.get_oracle_price_data_for_perp_market(market_index).price # type: ignore
        print(f"old price: {price}")
        new_price = int(price * (1 + pct_delta)) 
        print(f"new price: {new_price}")
        sig = await set_oracle_price(admin, oracle, new_price)
        print(f"new oracle price: {new_price} set for perp market: {market_index}: {sig}")
        # await asyncio.sleep(sleep)
            # return

    if price_delta is not None:
        asyncio.create_task(price_jump())
    elif pct_delta is not None:
        await pct_jump()
        # asyncio.create_task(pct_jump())
    else:
        raise ValueError("need to provide price or pct delta")
