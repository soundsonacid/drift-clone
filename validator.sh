#!/bin/bash
if [ -d "test-ledger" ]; then
    echo "test-ledger directory exists. Deleting..."
    rm -rf test-ledger
fi

solana-test-validator --account-dir accounts/ --bpf-program dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH accounts/dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH.so --bpf-program FsJ3A3u2vn5cTVofAjvy6y5kwABJAqYWpe4975bi2epH accounts/FsJ3A3u2vn5cTVofAjvy6y5kwABJAqYWpe4975bi2epH.so --reset