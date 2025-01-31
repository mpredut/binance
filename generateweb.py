import os


# Lista inițială de monede (top 10)
monede = [
    {"nume": "BTCUSDT", "cantitate": 0.5, "watch": True},
    {"nume": "TAOUSDT", "cantitate": 0.5, "watch": True},
    {"nume": "ETHUSDT", "cantitate": 1.5, "watch": False},
    {"nume": "BNBUSDT", "cantitate": 3.0, "watch": False},
    {"nume": "SOLUSDT", "cantitate": 4.0, "watch": False},
    {"nume": "XRPUSDT", "cantitate": 5.0, "watch": False},
    {"nume": "ADAUSDT", "cantitate": 6.0, "watch": False},
    {"nume": "DOGEUSDT", "cantitate": 7.0, "watch": False},
    {"nume": "MATICUSDT", "cantitate": 8.0, "watch": False},
    {"nume": "DOTUSDT", "cantitate": 9.0, "watch": False},
    {"nume": "LTCUSDT", "cantitate": 10.0, "watch": False}
]

monede_empty = [
]
def genereaza_html(monede, refresh_interval=10, base_url="https://5499-85-122-194-86.ngrok-free.app/"):
    # Stilizare CSS minimală
    stil_css = """
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 8px;
            text-align: center;
        }
        th {
            background-color: #f4f4f4;
        }
        input {
            width: 80px;
            text-align: center;
        }
        button {
            padding: 8px 12px;
            font-size: 14px;
            cursor: pointer;
            border: none;
            border-radius: 4px;
        }
        .btn-sell {
            background-color: #ff4d4d;
            color: white;
        }
        .btn-buy {
            background-color: #4caf50;
            color: white;
        }
        .btn-sell:hover {
            background-color: #ff1a1a;
        }
        .btn-buy:hover {
            background-color: #45a049;
        }
        .message {
            font-size: 18px;
            font-weight: bold;
            color: #333;
            margin-bottom: 20px;
        }
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
        <button onclick="enableAudio()">Activează sunetul</button>
        <button onclick="disableAudio()">Dezactivează sunetul</button>
<script>
    let audio = new Audio('/static/bip.wav');
    let audioEnabled = true; // Flag pentru activarea/dezactivarea audio
    function enableAudio() {{
        audioEnabled = true;
        audio.play().catch(err => console.error("Eroare la redarea sunetului:", err));
    }}
    function disableAudio() {{
        audioEnabled = false;
        audio.pause();
        audio.currentTime = 0; // Resetează sunetul la început
    }}
</script>
        <script>
            // Redă un sunet dacă există monede          
            if ({'true' if any(moneda["watch"] for moneda in monede) else 'false'}) {{
                if (audioEnabled) {{
                    const audio = new Audio('/static/bip.wav'); // Calea către fișierul audio
                    audio.play().catch(err => console.error("Eroare la redarea sunetului:", err));
                }}
            }}
            // Reîncarcă pagina la fiecare {refresh_interval} secunde
            setTimeout(() => {{
                location.reload();
            }}, {refresh_interval * 1000});
        </script>
    </head>
    <body>
        <div class="message">
            {'Avem monede noi pentru tranzacționare!' if monede else 'Nicio monedă disponibilă pentru tranzacționare.'}
        </div>
        <table>
            <thead>
                <tr>
                    <th>Monedă</th>
                    <th>Cantitate</th>
                    <th>Acțiune</th>
                </tr>
            </thead>
            <tbody>
    """

    # Adaugă rânduri pentru fiecare monedă
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

    # Închide tabelul și adaugă scripturile JS
    html += f"""
            </tbody>
        </table>
        <script>
            function actionSell(moneda) {{
                const cantitate = document.getElementById(`qty-${{moneda}}`).value;
                fetch('{base_url}trade/sell', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{ symbol: moneda, amount: parseFloat(cantitate) }})
                }})
                .then(response => response.json())
                .then(data => alert(`Vândut cantitate moneda: data.message`))
                .catch(err => console.error('Eroare la vânzare:', err));
            }}

            function actionBuy(moneda) {{
                const cantitate = document.getElementById(`qty-${{moneda}}`).value;
                fetch('{base_url}trade/buy', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{ symbol: moneda, amount: parseFloat(cantitate) }})
                }})
                .then(response => response.json())
                .then(data => alert(`Cumparat cantitate moneda: data.message`))
                .catch(err => console.error('Eroare la cumpărare:', err));
            }}
        </script>
    </body>
    </html>
    """
    return html


# Salvarea fișierului HTML
def salveaza_html(html, nume_fisier="index.html"):
    with open(nume_fisier, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Fișierul {nume_fisier} a fost generat cu succes!")


# Generare și salvare
html_content = genereaza_html(monede)
salveaza_html(html_content, "index.html")
