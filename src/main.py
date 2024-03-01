import sys
import pprint
import json

sys.path.insert(0, "../")
sys.path.insert(0, "../driftpy/src/")

from driftpy.math.amm import *
from driftpy.math.market import *

from driftpy.types import *
from driftpy.constants.numeric_constants import *
from driftpy.drift_client import DriftClient

from driftpy.drift_client import DriftClient
from driftpy.setup.helpers import adjust_oracle_pretrade

from anchorpy import Provider

from termcolor import colored
from solders.instruction import Instruction  # type: ignore

from solana.rpc.core import RPCException  # type: ignore
import re  # type: ignore


@dataclass
class Event:
    timestamp: int

    def serialize_parameters(self):
        try:
            params = json.dumps(
                self, default=lambda o: o.__dict__, sort_keys=True, indent=4
            )
            return json.loads(params)
        except Exception as e:
            print(self._event_name)
            print(e)
            print("ERRRRR")
            print(self.__dict__)
            print([(x, type(x)) for key, x in self.__dict__.items()])
            return {}

    def serialize_to_row(self):
        parameters = self.serialize_parameters()
        # print(parameters)
        timestamp = parameters.pop("timestamp")
        event_name = parameters.pop("_event_name")
        row = {
            "event_name": event_name,
            "timestamp": timestamp,
            "parameters": json.dumps(parameters),
        }
        return row

    @staticmethod
    def deserialize_from_row(class_type, event_row):
        event = json.loads(event_row.to_json())
        params = json.loads(event["parameters"])
        params["_event_name"] = event["event_name"]
        params["timestamp"] = event["timestamp"]
        event = class_type(**params)
        return event

    # this works for all Event subclasses
    @staticmethod
    def run_row(class_type, clearing_house: DriftClient, event_row) -> DriftClient:
        event = Event.deserialize_from_row(class_type, event_row)
        return event.run(clearing_house)

    @staticmethod
    def run_row_sdk(class_type, clearing_house: DriftClient, event_row) -> DriftClient:
        event = Event.deserialize_from_row(class_type, event_row)
        return event.run_sdk(clearing_house)

    def run(self, clearing_house: DriftClient) -> DriftClient:
        raise NotImplementedError

    # theres a lot of different inputs for this :/
    async def run_sdk(self, *args, **kwargs) -> DriftClient:
        raise NotImplementedError


@dataclass
class SettleLPEvent(Event):
    user_index: int
    market_index: int
    _event_name: str = "settle_lp"

    # async def run(self, clearing_house: DriftClient, verbose=False) -> DriftClient:
    #     if verbose:
    #         print(f"u{self.user_index} {self._event_name}...")

    #     clearing_house = await clearing_house.settle_lp(
    #         self.market_index,
    #         self.user_index,
    #     )

    #     return clearing_house

    async def run_sdk(self, clearing_house: DriftClient):
        return await clearing_house.get_settle_lp_ix(
            clearing_house.authority, self.market_index
        )


@dataclass
class SettlePnLEvent(Event):
    user_index: int
    market_index: int
    _event_name: str = "settle_pnl"

    def run(self, clearing_house: DriftClient, verbose=False) -> DriftClient:
        pass
        # not implemented yet...
        return clearing_house

    async def run_sdk(self, clearing_house: DriftClient):
        position = clearing_house.get_perp_position(self.market_index)
        if position is None or position.base_asset_amount == 0:
            return None

        user_account = clearing_house.get_user_account()

        return await clearing_house.get_settle_pnl_ix(
            clearing_house.authority, user_account, self.market_index
        )


@dataclass
class ClosePositionEvent(Event):
    user_index: int
    market_index: int
    _event_name: str = "close_position"

    def run(self, clearing_house: DriftClient, verbose=False) -> DriftClient:
        if verbose:
            print(f"u{self.user_index} {self._event_name}...")
        clearing_house = clearing_house.close_position(
            self.user_index, self.market_index
        )

        return clearing_house

    async def run_sdk(
        self,
        clearing_house: DriftClient,
        oracle_program=None,
        adjust_oracle_pre_trade=False,
    ) -> DriftClient:
        # tmp -- sim is quote open position v2 is base only
        market = clearing_house.get_perp_market_account(self.market_index)
        user = clearing_house.get_user_account()

        position = None
        for _position in user.perp_positions:
            if _position.market_index == self.market_index:
                position = _position
                break
        assert position is not None, "user not in market"

        direction = (
            PositionDirection.Long()
            if position.base_asset_amount < 0
            else PositionDirection.Short()
        )

        print(f"closing: {abs(position.base_asset_amount)} {direction}")

        if adjust_oracle_pre_trade:
            assert oracle_program is not None
            await adjust_oracle_pretrade(
                position.base_asset_amount, direction, market, oracle_program  # type: ignore
            )

        return await clearing_house.get_close_position_ix(self.market_index)


async def _send_ix(
    ch: DriftClient,
    ix: Instruction,
    event_name: str,
    # ix_args: dict,
    silent_fail=False,
    silent_success=False,
    view_logs_flag=False,
):
    failed = 1  # 1 = fail, 0 = success
    provider: Provider = ch.program.provider
    slot = (await provider.connection.get_slot()).value
    compute_used = -1
    err = None
    sig = None
    logs = None
    try:
        if event_name == SettleLPEvent._event_name:
            sig = await ch.send_ixs(ix, signers=[])
        else:
            sig = await ch.send_ixs(ix)
        failed = 0
        if view_logs_flag:
            logs = await view_logs(sig, provider, False)  # type: ignore

    except RPCException as e:
        err = e.args

    if not failed and not silent_success:
        print(colored(f"> {event_name} success", "green"))
    elif failed and not silent_fail or view_logs_flag:
        print(colored(f"> {event_name} failed", "red"))
        # pprint.pprint(ix_args)
        pprint.pprint(err)

    if logs:
        try:
            logs = await logs
            if view_logs_flag:
                pprint.pprint(logs)
            for log in logs:
                if "compute units" in log:
                    result = re.search(r".* consumed (\d+) of (\d+)", log)
                    compute_used = result.group(1)  # type: ignore
        except Exception as e:
            pprint.pprint(e)

    # ix_args["user_index"] = ch.active_sub_account_id

    # return failed, sig, (slot, event_name, ix_args, err, compute_used)
    return failed, sig, (slot, event_name, err, compute_used)
