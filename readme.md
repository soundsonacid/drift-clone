## Quick Setup (method 1)
```bash
# setup env
conda create -n tmp python=3.10
pip install -r req.txt
# inits other submodules
git submodule update --init 
```

note: need solana-cli v1.14.7 or greater for local validator's --account-dir flag to work 
(`sh -c "$(curl -sSfL https://release.solana.com/v1.14.7/install)"`)


## Quick Run (method 2)
```
python3.10 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

in a different terminal, run bash start_local.sh to start the local validator with all accounts

in the original terminal, run bash run.sh
```

## Quick Run (method 3 - poetry (recommended))
1. run `poetry shell`
2. run `poetry install`
3. run `python clone.py` to clone mainnet state (clone.py:495 determines which market index to filter for)
4. Copy the FsJ3A..so file into `accounts` (Pyth oracle program shared object file)
5. run `solana config set --url http://127.0.0.1:8899`
6. run `bash start_local.sh` in a separate terminal to start the local validator
7. once the local validator is started, run `poetry run python -m src.experiments` (make sure to change the market index on experiments.py:147 and experiments.py:74 to match the market index of the market you cloned users for)

## Environment Variables

These environment varibles are required to run the scripts

**Required**:
* one of the following are required
    * `RPC_URL`, full Solana RPC node URL 
    * `API_KEY`, API token for https://drift-cranking.rpcpool.com/

**Optional**:
* `SLACK_BOT_TOKEN` optional, slack messages will be no-op without this 
* `SLACK_CHANNEL` optional, slack messages will be no-op without this

    
## main files
- `clone.py`: clones mainnet accounts to disk (is later loaded into a local validator)
- `close.py`: closes all of the users positions
- `invariants.py`: assert invariants hold true (eg, market.net_baa = sum(user.baa))

## Close Procedure 

- state is updated so users can close out 
```python 

    # update state 
    await state_ch.update_perp_auction_duration(0)
    await state_ch.update_lp_cooldown_time(0)
    for i in range(n_spot_markets):
        await state_ch.update_update_insurance_fund_unstaking_period(i, 0)
        await state_ch.update_withdraw_guard_threshold(i, 2**64 - 1)
```
- markets' expiry times are set 
- all lps are closed
- markets are fully expired 
- users are liquidated (both perp and spot) 
- IF stakes are removed for full amounts across all spot markets 
- expired perp market positions are settled 
   - we use a brute-force approach (each user is settled)
   - after the first round all negative pnl should be settled and so the pool should have enough pnl to settle all the users 
   - we attempt to successfully settle all users 5 times - on the 6th loop we exit the closing process and log 'something went wrong during settle expired position...'
- all spot market borrows are paid back (we mint more of the token to user's ATA to pay back the full amount) 
- all money is withdrawn from the protocol into ATAs 
- all expired perp markets call settle_expired_market_pools_to_revenue_pool
- final metrics are logged
- fin 

## random notes
- when you scrape make sure... 
    - program_id is up to date in the driftpy sdk 
    - make sure program in driftpy/protocol-v2 is the same version as program_id 
    - idl is up to date (can use `update_idl.sh` to do this)
- sometimes the validator doesnt shutdown cleanly 
    - check the pid from `ps aux | grep solana` and kill



# Developing

Use the formater/linter (add this to vs code):
```
autopep8 --in-place --aggressive --aggressive
```