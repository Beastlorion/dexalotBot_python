# Dexalot Bot - Python
This is an open source bot for automated trading on Dexalot.

## Install

### Build envrionment
If this is your first time using python, you'll need to set up your build environment.

```
sudo apt-get install build-essential
sudo apt-get update
sudo apt install python3-pip
vi ~/.bashrc
i
```

paste this in on the last line and then type :wq to save an quit
```
export PATH="/.local/bin:$PATH"
```
Then type: escape -> :wq -> enter

close and reopen your terminal instance or disconnect and reconnect for the changes to take hold.

## Download and add private key
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
4. After delay as set in .env.shared (~10 seconds by default), start checking to see if price has moved more than the refreshTolerance % setting in .env.shared
5. If the price has moved by refreshTolerance % or more, cancel and place new orders.

## Disclaimer
This is an open source project. I take no responsibility for any losses incured by using this code. Please be responsible.
