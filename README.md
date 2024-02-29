# Dexalot Bot - Python
This is an open source bot for automated trading on Dexalot.

## Install

```
git clone https://github.com/Beastlorion/dexalotBot_python.git
cd dexalotBot_python
vi .env.secret
i
```
paste this and enter your hot wallet private key:
```
export AVAX_USDC_pk=""
```
Then type: escape -> :wq -> enter
to save and exit the editor

### Install dependencies:
```
pip install web3
pip install python-dotenv
pip install python_binance
pip install shortuuid
pip install eth_account
```

See settings in .env.shared

### Run
```
python3 main.py AVAX_USDC
```

### Workflow
1. Starts Pricefeed using Binance prices
2. Cancels open orders
3. Places fresh orders of qty and spread as set in .env.shared
4. repeat after delay as set in .env.shared ~10 seconds by default

## Disclaimer
This is an open source project. I take no responsibility for any losses incured by using this code. Please be responsible.
