settings = {
  'AVAX_USDC':{
    "secret_name": '', 
    "secret_location": '', 
    "useCancelReplace": True, 
    "useVolSpread": False, 
    "useCustomPrice": False,
    "refreshTolerance":0.05,
    "defensiveSkew":0.01,
    "levels":[{"level":1,"spread":0.05,"qty":1,"refreshTolerance":-0.05},
              {"level":2,"spread":2,"qty":2,"refreshTolerance":0.25}]
    },
  'AVAX_USDT':{
    "secret_name": '', 
    "useCancelReplace": True, 
    "useVolSpread": True, 
    "useCustomPrice": False, 
    "refreshTolerance":0.05,
    "defensiveSkew":0.01,
    "levels":[
      {"level":1,"spread":0.3,"qty":5,"refreshTolerance":0.25},
      {"level":2,"spread":2,"qty":2,"refreshTolerance":1}]
    },
  'USDT_USDC':{
    "secret_name": '', 
    "secret_location": '', 
    "useCancelReplace": True, 
    "useVolSpread": True, 
    "useCustomPrice": False, 
    "refreshTolerance":0.01,
    "defensiveSkew":0.01,
    "levels":[
      {"level":1,"spread":0.01,"qty":50,"refreshTolerance":0.01},
      {"level":2,"spread":0.02,"qty":100,"refreshTolerance":0.01}]
    },
  'BTC_USDC':{
    "secret_name": '', 
    "secret_location": '', 
    "useCancelReplace": True, 
    "useVolSpread": True, 
    "useCustomPrice": False, 
    "refreshTolerance":0.05,
    "defensiveSkew":0.01,
    "levels":[{"level":1,"spread":0.3,"qty":0.01,"refreshTolerance":0.25},
              {"level":2,"spread":2,"qty":0.1,"refreshTolerance":1}]
    },
  'ETH_USDC':{
    "secret_name": '', 
    "secret_location": '', 
    "useCancelReplace": True, 
    "useVolSpread": True, 
    "useCustomPrice": False, 
    "refreshTolerance":0.05,
    "defensiveSkew":0.01,
    "levels":[{"level":1,"spread":0.3,"qty":0.05,"refreshTolerance":0.25},
              {"level":2,"spread":2,"qty":1,"refreshTolerance":1}]
    },
  'sAVAX_AVAX':{
    "secret_name": '', 
    "secret_location": '', 
    "useCancelReplace": True, 
    "useVolSpread": True, 
    "useCustomPrice": False, 
    "refreshTolerance":0.01,
    "defensiveSkew":0.01,
    "levels":[
      {"level":1,"spread":0.03,"qty":10,"refreshTolerance":0.03},
      {"level":2,"spread":0.05,"qty":100,"refreshTolerance":0.05}]
    },
}