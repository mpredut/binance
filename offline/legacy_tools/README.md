# Unelte istorice

Acest director înlocuiește vechiul director generic `altele/`. Conține experimente,
convertoare și operațiuni de mentenanță care se lansează numai manual.

Unele scripturi pot accesa API-uri reale sau pot modifica fișiere din `cachedb/`.
Pentru cele de mentenanță se folosește întâi modul dry-run, dacă există, iar procesele
writer trebuie oprite conform instrucțiunilor din script.
