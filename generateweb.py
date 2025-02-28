import os
import json

# Lista inițială de monede (top 10)

#monede = [
#    {"nume": "BTCUSDT", "cantitate": 0.5, "watch": True},
#    {"nume": "TAOUSDT", "cantitate": 0.5, "watch": True},
#    {"nume": "ETHUSDT", "cantitate": 1.5, "watch": False},
#    {"nume": "BNBUSDT", "cantitate": 3.0, "watch": False},
#    {"nume": "SOLUSDT", "cantitate": 4.0, "watch": False},
#    {"nume": "ADAUSDT", "cantitate": 6.0, "watch": False},
#    {"nume": "DOGEUSDT", "cantitate": 7.0, "watch": False},
#    {"nume": "DOTUSDT", "cantitate": 9.0, "watch": False},
#    {"nume": "LTCUSDT", "cantitate": 10.0, "watch": False}
#    {"nume": "ETHUSDT", "cantitate": 1.5, "watch": False}
#]
 
# monede = [
    # {"nume": "BTCUSDT", "cantitate": 0.5, "watch": True},
    # {"nume": "TAOUSDT", "cantitate": 0.5, "watch": True},
    # {"nume": "ETHUSDT", "cantitate": 1.5, "watch": False}
# ]

monede_empty = [
]


# Lista inițială de monede (top 10)
monede = [
    {"nume": "BTCUSDT", "cantitate": 0.5, "watch": True},
    {"nume": "TAOUSDT", "cantitate": 0.5, "watch": True}
]

# Fișier pentru stocarea ultimei configurații
CONFIG_FILE = "last_watch_config.json"

def citeste_config_anterioara():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"watch_list": [], "repeat_count": 0}

def salveaza_config_actuala(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f)

def trebuie_sa_scoata_sunet():
    config_anterioara = citeste_config_anterioara()
    watch_list_anterioara = config_anterioara.get("watch_list", [])
    repeat_count = config_anterioara.get("repeat_count", 0)
    
    watch_list_actuala = [moneda["nume"] for moneda in monede if moneda["watch"]]
    
    if watch_list_actuala != watch_list_anterioara:
        # Lista s-a schimbat, resetăm contorul și scoatem sunet
        config_nou = {"watch_list": watch_list_actuala, "repeat_count": 1}
        salveaza_config_actuala(config_nou)
        return True
    elif repeat_count < 3:
        # Lista e aceeași, dar putem reda sunet de încă 3 ori
        config_nou = {"watch_list": watch_list_actuala, "repeat_count": repeat_count + 1}
        salveaza_config_actuala(config_nou)
        return True
    else:
        # Lista e aceeași și am depășit limita de 3 sunete
        return False

def genereaza_html(monede, refresh_interval=10, base_url="https://5499-85-122-194-86.ngrok-free.app/"):
    sunet_activ = trebuie_sa_scoata_sunet()
    
    # Stilizare CSS minimală
    stil_css = """
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: center; }
        th { background-color: #f4f4f4; }
        input { width: 80px; text-align: center; }
        button { padding: 8px 12px; font-size: 14px; cursor: pointer; border: none; border-radius: 4px; }
        .btn-sell { background-color: #ff4d4d; color: white; }
        .btn-buy { background-color: #4caf50; color: white; }
    </style>
    """

    # Conținutul principal HTML
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Monede de tranzacționat</title>
        {stil_css}
        <script>
            let audioEnabled = true;
            function enableAudio() {{ audioEnabled = true; }}
            function disableAudio() {{ audioEnabled = false; }}
        </script>
    </head>
    <body>
        <button onclick="enableAudio()">Activează sunetul</button>
        <button onclick="disableAudio()">Dezactivează sunetul</button>
        <div class="message">
            {'Avem monede noi pentru tranzacționare!' if monede else 'Nicio monedă disponibilă pentru tranzacționare.'}
        </div>
    """

    if sunet_activ:
        html += """
        <script>
            if (audioEnabled) {
                let audio = new Audio('/static/bip.wav');
                audio.play().catch(err => console.error("Eroare la redarea sunetului:", err));
            }
        </script>
        """

    html += "<table><thead><tr><th>Monedă</th><th>Cantitate</th><th>Acțiune</th></tr></thead><tbody>"
    for moneda in monede:
        if moneda["watch"]:
            html += f"""
            <tr>
                <td>{moneda['nume']}</td>
                <td><input type="number" value="{moneda['cantitate']}" id="qty-{moneda['nume']}"></td>
                <td>
                    <button class="btn-sell" onclick="actionSell('{moneda['nume']}')">Sell</button>
                    <button class="btn-buy" onclick="actionBuy('{moneda['nume']}')">Buy</button>
                </td>
            </tr>
            """

    html += """
        </tbody></table>
        <script>
            function actionSell(moneda) {
                const cantitate = document.getElementById(`qty-${moneda}`).value;
                fetch('{base_url}trade/sell', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbol: moneda, amount: parseFloat(cantitate) })
                })
                .then(response => response.json())
                .then(data => alert(`Vândut cantitate moneda: data.message`))
                .catch(err => console.error('Eroare la vânzare:', err));
            }
            function actionBuy(moneda) {
                const cantitate = document.getElementById(`qty-${moneda}`).value;
                fetch('{base_url}trade/buy', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbol: moneda, amount: parseFloat(cantitate) })
                })
                .then(response => response.json())
                .then(data => alert(`Cumpărat cantitate moneda: data.message`))
                .catch(err => console.error('Eroare la cumpărare:', err));
            }
        </script>
    </body>
    </html>
    """
    return html

# salvare
def salveaza_html(html, nume_fisier="index.html"):
    with open(nume_fisier, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Fișierul {nume_fisier} a fost generat cu succes!")

# Generare și salvare
html_content = genereaza_html(monede)
salveaza_html(html_content, "index.html")
