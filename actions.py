from solders.pubkey import Pubkey # type: ignore
from dataclasses import dataclass
from driftpy.admin import Admin
from driftpy.drift_client import DEFAULT_TX_OPTIONS
from typing import List, Type
import random
from pathlib import Path
from anchorpy import Program, Idl, Provider
from driftpy.setup.helpers import set_price_feed, get_feed_data
from solana.rpc.core import RPCException
import re

@dataclass
class Action:
    market_index: int

    async def execute(self, admin: Admin):
        raise NotImplementedError("Each action must implement an execute method.")

@dataclass
class UpdateCurveAction(Action):
    new_peg_candidate: int

    async def execute(self, admin: Admin):
        perp_market = admin.get_perp_market_account(self.market_index)
        print(f"updating curve for market: {self.market_index} old peg: {perp_market.amm.peg_multiplier} new peg candidate: {self.new_peg_candidate}") # type: ignore
        try:
            sig = (await admin.repeg_curve(self.new_peg_candidate, self.market_index)).tx_sig
            print(f"updated peg for {self.market_index}: {sig}")
        except RPCException as e:
            print(f"failed to update peg for {self.market_index}")
            print(f"error message: {extract_error(e.args[0])}") # type: ignore

@dataclass
class UpdateKAction(Action):
    sqrt_k: int

    async def execute(self, admin: Admin):
        perp_market = admin.get_perp_market_account(self.market_index)
        print(f"updating sqrt_k for market: {self.market_index} old sqrt_k: {perp_market.amm.sqrt_k} new sqrt_k: {self.sqrt_k}") # type: ignore
        try:
            sig = (await admin.update_k(self.sqrt_k, self.market_index)).tx_sig
            print(f"updated sqrt_k for {self.market_index}: {sig}")
        except RPCException as e:
            print(f"failed to update sqrt_k for {self.market_index}")
            print(f"error message: {extract_error(e.args[0])}") # type: ignore

@dataclass
class UpdateImfAction(Action):
    imf_factor: int
    upnl_imf_factor: int

    async def execute(self, admin: Admin):
        perp_market = admin.get_perp_market_account(self.market_index)
        print(f"updating imf for market: {self.market_index} old imf: {perp_market.imf_factor} new imf: {self.imf_factor} old upnl_imf: {perp_market.unrealized_pnl_imf_factor} new upnl_imf: {self.upnl_imf_factor}") # type: ignore
        try:
            sig = await admin.update_perp_market_imf_factor(self.market_index, self.imf_factor, self.upnl_imf_factor) # type: ignore
            print(f"updated imf factors for {self.market_index}: {sig}")
        except RPCException as e:
            print(f"failed to update imf factors for {self.market_index}")
            print(f"error message: {extract_error(e.args[0])}") # type: ignore

@dataclass
class UpdateOracleAction(Action):
    oracle: Pubkey
    oracle_price: int

    async def execute(self, admin: Admin):  
        price = admin.get_oracle_price_data_for_perp_market(self.market_index).price # type: ignore
        print(f"updating oracle for market: {self.market_index} old price: {price} new price: {self.oracle_price}")
        file_path = Path("pyth.json")
        raw = file_path.read_text()
        idl = Idl.from_json(raw)
        provider = Provider(admin.connection, admin.wallet, DEFAULT_TX_OPTIONS)
        program = Program(
            idl,
            Pubkey.from_string("FsJ3A3u2vn5cTVofAjvy6y5kwABJAqYWpe4975bi2epH"),
            provider
        )
        data = await get_feed_data(program, self.oracle)
        exp = data.exponent
        try:
            price_normalized = (self.oracle_price) // (10 ** (exp * -1))
            sig = await set_price_feed(program, self.oracle, price_normalized)
            print(f"updated oracle price for {self.market_index}: {sig}")
        except RPCException as e:
            print(f"failed to update oracle price for {self.market_index}")
            print(f"error message: {extract_error(e.args[0])}") # type: ignore

def get_action(admin: Admin) -> Action:
    action_classes: List[Type[Action]] = [UpdateCurveAction, UpdateKAction, UpdateImfAction, UpdateOracleAction]
    chosen_action_class = random.choice(action_classes)
    market_index = random.randint(0, 23)  
    perp_market = admin.get_perp_market_account(market_index)
    pct_delta = random.uniform(-0.1, 0.1)

    if chosen_action_class == UpdateCurveAction:
        old_peg = perp_market.amm.peg_multiplier # type: ignore
        peg_delta = int(old_peg * pct_delta)
        new_peg_candidate = old_peg + peg_delta
        return UpdateCurveAction(market_index=market_index, new_peg_candidate=new_peg_candidate)
    elif chosen_action_class == UpdateKAction:
        old_sqrtk = perp_market.amm.sqrt_k # type: ignore
        sqrtk_delta = int(old_sqrtk * pct_delta)
        sqrt_k = old_sqrtk + sqrtk_delta
        return UpdateKAction(market_index=market_index, sqrt_k=sqrt_k)
    elif chosen_action_class == UpdateImfAction:
        old_imf = perp_market.imf_factor # type: ignore
        old_upnl_imf = perp_market.unrealized_pnl_imf_factor # type: ignore
        imf_delta = int(old_imf * pct_delta)
        upnl_imf_delta = int(old_upnl_imf * pct_delta)
        imf_factor = old_imf + imf_delta
        upnl_imf_factor = old_upnl_imf + upnl_imf_delta
        return UpdateImfAction(market_index=market_index, imf_factor=imf_factor, upnl_imf_factor=upnl_imf_factor)
    else:
        oracle = perp_market.amm.oracle # type: ignore
        price = admin.get_oracle_price_data_for_perp_market(market_index).price # type: ignore
        price_delta = int(price * pct_delta)
        new_price = price + price_delta
        return UpdateOracleAction(market_index=market_index, oracle=oracle, oracle_price=new_price)
    
def extract_error(logs):
    # Define the pattern to search for error messages
    error_pattern = re.compile(r"Error Message: (.+)")
    for log in logs.data.logs:
        match = error_pattern.search(log)
        if match:
            # If a match is found, return the error message
            return match.group(1)
    
    # Return None if no error message is found in any of the logs
    return None