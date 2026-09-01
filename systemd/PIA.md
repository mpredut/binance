# PIA VPN — runbook

Botii Binance ies obligatoriu prin IP-ul **dedicat** al PIA, fiindca acela e
whitelist-uit pe cheile API. Daca tunelul cade sau urca pe alt IP, Binance
raspunde `-2015` la orice cerere semnata. Documentul asta e ce trebuie sa stii
cand PIA face figuri.

## Comenzi utile

`piactl` vorbeste cu daemonul prin socket. **Pune mereu `timeout`**: cand
daemonul e agatat, `piactl` blocheaza la nesfarsit.

```bash
timeout 8 piactl get connectionstate   # Disconnected | Connecting | Connected | ...
timeout 8 piactl get vpnip             # IP-ul de iesire prin tunel  <-- asta conteaza
timeout 8 piactl get pubip             # IP-ul conexiunii FIZICE     <-- NU e un bug, vezi mai jos
timeout 8 piactl get region            # regiunea SELECTATA
timeout 8 piactl get regions           # regiunile DISPONIBILE (aici apare cea dedicata)
timeout 8 piactl get portforward
piactl background enable               # obligatoriu pe server, vezi capcane
piactl connect / piactl disconnect
piactl dedicatedip add /home/predut/piatoken.txt
piactl login /home/predut/pia.txt      # user pe linia 1, parola pe linia 2
piactl set debuglogging true           # creeaza /opt/piavpn/var/daemon.log (root)
```

Diagnostic rapid, fara sa atingi nimic:

```bash
/home/predut/binance/pia_selfheal.sh --check
```

Verificarea REALA a iesirii (nu te baza pe `connectionstate`):

```bash
curl -s --interface tun0 https://ipinfo.io/ip     # trebuie sa fie IP-ul dedicat
```

## Capcane care ne-au costat 34 de zile

**`pubip` NU e IP-ul prin care iesi.** E IP-ul public al conexiunii fizice (linia
ISP-ului). Ramane pe IP-ul de acasa si cand tunelul e perfect sanatos. Pentru
iesirea efectiva foloseste `vpnip` sau `curl --interface tun0`.

**`piactl connect` e ignorat IN TACERE fara GUI.** Pe server nu ruleaza clientul
grafic, deci fara `piactl background enable` daemonul accepta RPC-ul `connectVPN`,
raporteaza succes si ramane senin `Disconnected`.

**`connectionstate = Connected` poate minti.** In timpul unui flap PIA raporteaza
Connected desi tun0/DNS/HTTPS sunt deja moarte. Proba trebuie legata explicit de
`tun0`.

**Logout-ul sterge IP-ul dedicat.** Inregistrarea DIP e legata de sesiunea
contului. Dupa `piactl logout` (sau o resetare), `piactl get regions` nu mai
contine nicio linie `dedicated-*`, iar regiunea ramane setata pe una inexistenta
-> `Unknown region`, conectarea esueaza si — daca ceva cade pe `auto` — tunelul
urca pe un IP din pool, deci `-2015` la Binance.

**Tokenul DIP re-adaugat poate intoarce ALT IP.** Pe 1 sep 2026: `85.122.194.86`
-> `85.122.194.79`. De aceea `pia_start.sh` **nu mai hardcodeaza regiunea**, o
deduce din `piactl get regions`. Cand IP-ul se schimba, **whitelist-ul de pe
contul Binance trebuie actualizat manual** — nu exista automatizare pentru asta.

**Killswitch-ul taie tot cand nu exista tun0.** Simptomul e inselator: masina
raspunde la ping in LAN, dar nu iese nicaieri. Pare problema de retea, e de fapt
killswitch peste un tunel inexistent. Confirmare: `curl --interface ens18 ...`
intoarce gol.

**`binance.service` are `Requires=pia.service`.** Daca PIA cade, flota nu
porneste deloc — iar `systemctl is-active binance.service` poate raporta `active`
in timp ce niciunul dintre cei 7 membri nu ruleaza. Verifica procesele, nu
serviciul: `./healthcheck.sh --check`.

## Incidentul 1 sep 2026 (de ce exista pia_selfheal.sh)

Lantul complet, verificat in loguri:

1. `pia-daemon` a crapat pe `ExceptionHandler::GenerateDump sys_pipe failed: Too
   many open files` si a ramas proces-fantoma cu un copil `[pia-openvpn]
   <defunct>`. Orice `piactl` returna `Timed out after 5 sec`.
2. Fara tun0, killswitch-ul a taiat tot traficul de iesire.
3. `pia.service` a intrat in bucla de restart — a ajuns la **6975** reporniri.
4. Flota nu a mai pornit deloc (vezi `Requires=` mai sus).
5. **Nicio alerta nu a ajuns**: `healthcheck.sh` si `deadman_switch.sh` isi
   faceau treaba, dar toate alertele trec prin ntfy.sh, adica prin exact
   internetul care lipsea (`278 x "EROARE curl"` in `logs/deadman.log`).

Reparare: restart `piavpn.service` (+ `kill -9` pe daemon) a readus internetul,
dar conectarea esua cu `AUTH_FAILED` — cont PIA. Dupa re-login si token DIP nou,
totul a revenit pe `85.122.194.79`.

## pia_selfheal.sh

Ruleaza din **crontab-ul root** la 5 minute (`systemd/crontab.root.prod.txt`) —
ca predut ar esua fix in scenariul pentru care exista, fiindca treptele 3-4 cer
`systemctl` si `kill` pe pia-daemon.

Scara de escaladare; se opreste la prima treapta care rezolva, fiecare urmata de
o proba reala HTTPS prin `tun0`:

| # | Actiune |
|---|---------|
| 1 | `background enable` + `connect` |
| 2 | `disconnect` + `connect` |
| 3 | stop servicii -> `kill -9` daemon -> start -> re-inregistrare DIP -> `set region` -> `connect` |
| 4 | descarcare + reinstalare PIA (cooldown 24h) |

Daca `piactl` nu raspunde deloc, treptele 1-2 se sar din start.

**Alertele intra intr-un spool pe disc** (`/var/lib/pia_selfheal/alert_spool`) si
se livreaza retroactiv cand revine conectivitatea. Asta rezolva gaura din
incident: povestea caderii ajunge la telefon chiar daca in timpul ei nu putea iesi
niciun pachet. Durata reala a caderii se masoara din `/var/lib/pia_selfheal/outage_since`.

**Gratie la boot:** in primele 5 minute de uptime scriptul nu escaladeaza, fiindca
VPN-ul e legitim jos cat timp `pia.service` (Restart=always) negociaza tunelul.

Reglabil prin variabile de mediu: `PIA_BOOT_GRACE`, `PIA_CONNECT_WAIT`,
`PIA_FALLBACK_REGION`, `PIA_DIP_TOKEN`, `PIA_VERSION`, `PIA_REINSTALL_COOLDOWN`.

Despre treapta 4: installerul PIA refuza sa ruleze ca root si poate cere
escaladare interactiva, deci **poate esua legitim** — in acest caz scriptul
trimite alerta sa reinstalezi manual, nu pretinde ca a reusit. Nu executa
niciodata fisierul descarcat daca e sub 20 MB sau nu incepe cu `#!` (o pagina de
eroare HTML nu ajunge sa fie rulata). URL-ul trebuie **versionat**:
`pia-linux-latest.run` da 403, iar endpoint-ul "latest" de pe site intoarce HTML.

## Ce se intampla la reboot

- `piavpn.service`, `pia.service`, `binance.service`, `cron.service` — toate `enabled`.
- SSH e **socket-activated** pe Ubuntu 24.04: `ssh.service` apare `disabled`, dar
  `ssh.socket` e `enabled` si asculta pe 32238. Nu e o problema.
- Crontab-urile (predut si root) persista peste reboot.
- Botii non-flota nu au `@reboot`; ii reia `healthcheck.sh --supervise` in <=3 min.
- `pia_selfheal.sh` nu intervine in primele 5 minute (gratia de boot).
