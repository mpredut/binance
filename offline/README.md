# Offline tooling

Acest director conține doar unelte care nu fac parte din runtime-ul live.

Structură:

- `manual/` — diagnostice lansate explicit de un operator; unele accesează API-uri reale.
- `simulations/` — experimente locale, fără rol în pornirea flotei.
- `legacy_tools/` — utilitare istorice și migrări manuale, păstrate pentru audit.

Niciun modul din runtime nu trebuie să importe cod de aici. Scripturile care pot
accesa conturi reale trebuie rulate explicit și verificate înainte de utilizare.
