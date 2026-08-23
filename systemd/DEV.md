# Profil DEV / WSL

Audit la 2026-08-23: checkout-ul DEV este `/home/mariusp/binance`, branch
`main`, remote `origin`, fără cron propriu și fără unități systemd Binance/PIA.
Acesta este comportamentul intenționat: DEV/backtest nu pornește automat boti
live și nu primește secrete PROD prin Git.

Refacere DEV: clonează `main`, creează `.venv`, instalează `requirements.txt` și
restaurează numai datele de research/backtest necesare. Nu rula
`systemd/install_prod.sh` în WSL DEV.
