# server.py
import os
from fastapi import FastAPI
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from fastapi.middleware.cors import CORSMiddleware

from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="static"), name="static")
#app.mount("/files", StaticFiles(directory="/home/predut/binance"), name="files")
app = FastAPI()

# Configurare CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite cereri de la toate originile
    allow_credentials=True,
    allow_methods=["*"],  # Permite toate metodele (GET, POST etc.)
    allow_headers=["*"],  # Permite toate anteturile
)


from binance.client import Client
from binance.exceptions import BinanceAPIException

#from apikeys import api_key, api_secret

# my imports
#import binanceapi as api
import log





@app.get("/")
def read_root():
    headers = {"ngrok-skip-browser-warning": "true"}
    index_path = "index.html"
    
    if os.path.exists(index_path):
        return FileResponse(index_path, headers=headers)
    else:
        return Response(content="index.html not found", headers=headers, status_code=404)

    
# Modele de date
class TradeRequest(BaseModel):
    symbol: str
    amount: float

class AlertRequest(BaseModel):
    symbol: str
    threshold: float
    direction: str  # "up" sau "down"

# Funcționalități
@app.post("/trade/sell")
async def sell(request: TradeRequest):
    # Logica de vânzare prin Binance API
    print(f"Vândut {request.amount} din {request.symbol}")
    current_price = api.get_current_price(str(request.symbol))
    sell_price = current_price * (1 + 0.01 ) + 500
    print(f"Pret BTC {current_price} {sell_price}")
    api.place_order_smart("SELL", str(request.symbol), sell_price, request.amount)
    # place_order_smart(order_type, symbol, price, qty, cancelorders=True, hours=5, pair=True)
    return {"message": f"Vândut {request.amount} din {request.symbol}"}

@app.post("/trade/buy")
async def buy(request: TradeRequest):
    # Logica de cumpărare prin Binance API
    return {"message": f"Cumpărat {request.amount} din {request.symbol}"}

@app.get("/status/get")
async def get_status(symbol: str):
    # Logica pentru a obține starea pieței
    return {"symbol": symbol, "status": "Stable"}

@app.post("/alert/set")
async def set_alert(request: AlertRequest):
    # Logica pentru a seta alerta
    return {
        "message": f"Alertă setată pentru {request.symbol}: {request.direction} la {request.threshold}"
    }

