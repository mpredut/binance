# Offline tooling

Acest director conține doar unelte care nu fac parte din runtime-ul live.

Structură:

- `backtests/` — engine-uri de replay/backtest care reutilizează codul runtime.
- `research/` — experimente și documentație de cercetare.
- `runners/` — orchestrarea backtesturilor și a fluxului prod→dev.
- `manual/` — diagnostice lansate explicit de un operator; unele accesează API-uri reale.
- `simulations/` — experimente locale, fără rol în pornirea flotei.
- `legacy_tools/` — utilitare istorice și migrări manuale, păstrate pentru audit.

Niciun modul din runtime nu trebuie să importe cod de aici. Codul offline poate
importa engine-uri și strategii runtime, dar dependența inversă este interzisă.
Scripturile care pot
accesa conturi reale trebuie rulate explicit și verificate înainte de utilizare.
