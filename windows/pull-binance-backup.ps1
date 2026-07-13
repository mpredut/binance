# pull-binance-backup.ps1 — trage backup-ul de secrete de pe server in WSL local.
# INTERIMAR (pana la Storj off-site). Rulat de un task Windows (vezi docs/DISASTER_RECOVERY.md).
# AUTH CU CHEIE SSH (fara parola in fisier) -> necesita ~/.ssh/id_binance + cheia publica in
# authorized_keys pe server. Serverul reface backup-ul zilnic la 03:30 (cron); task-ul la 04:00.
#
# Caile sunt specifice dev box-ului (C:\Users\<user>, distro WSL) — ajusteaza daca difera.
$ErrorActionPreference = 'Stop'
$key = "$env:USERPROFILE\.ssh\id_binance"
$src = 'predut@192.168.0.144:/home/predut/binance-secrets-backup.tar.gz'
$dst = '\\wsl.localhost\ubuntu-24.04\home\mariusp\binance-secrets-backup.tar.gz'

scp -i "$key" -P 32238 -o StrictHostKeyChecking=accept-new -o BatchMode=yes "$src" "$dst"
if ($LASTEXITCODE -eq 0) {
    $sz = [math]::Round((Get-Item $dst).Length / 1MB, 1)
    Write-Host ("{0} OK - backup tras in WSL ({1} MB): {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm'), $sz, $dst)
} else {
    Write-Host ("{0} ESUAT (scp exit {1}) - e WSL pornit? e serverul accesibil (VPN)?" -f (Get-Date -Format 'yyyy-MM-dd HH:mm'), $LASTEXITCODE)
    exit 1
}
