# server.py
from fastapi import FastAPI

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from fastapi.middleware.cors import CORSMiddleware


app = FastAPI()

# Configurare CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite cereri de la toate originile
    allow_credentials=True,
    allow_methods=["*"],  # Permite toate metodele (GET, POST etc.)
    allow_headers=["*"],  # Permite toate anteturile
)


@app.get("/")
def read_root():
    return {"message": "Hello, World!"}
    
# Modele de date
class TradeRequest(BaseModel):
    currency: str
    amount: float

class AlertRequest(BaseModel):
    currency: str
    threshold: float
    direction: str  # "up" sau "down"

# Funcționalități
@app.post("/trade/sell")
async def sell(request: TradeRequest):
    # Logica de vânzare prin Binance API
    return {"message": f"Vândut {request.amount} din {request.currency}"}

@app.post("/trade/buy")
async def buy(request: TradeRequest):
    # Logica de cumpărare prin Binance API
    return {"message": f"Cumpărat {request.amount} din {request.currency}"}

@app.get("/status/get")
async def get_status(currency: str):
    # Logica pentru a obține starea pieței
    return {"currency": currency, "status": "Stable"}

@app.post("/alert/set")
async def set_alert(request: AlertRequest):
    # Logica pentru a seta alerta
    return {
        "message": f"Alertă setată pentru {request.currency}: {request.direction} la {request.threshold}"
    }

