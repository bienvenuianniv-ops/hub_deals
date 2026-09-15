# Enregistre la tache « Bot vols - ecoute ». A lancer en session elevee :
#   Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Users\Dell\hub_deals\taches\installer_bot_ecoute.ps1'
# ExecutionTimeLimit PT0S dans le XML est indispensable : sans lui, le
# planificateur arrete la tache au bout de 72 h et l'ecoute meurt en silence.
$resultat = Join-Path $env:TEMP 'installer_bot_ecoute.txt'
try {
    $xml = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot 'bot_ecoute.xml')
    Register-ScheduledTask -TaskName 'Bot vols - ecoute' -Xml $xml -Force -ErrorAction Stop | Out-Null
    "OK $(Get-Date -Format s)" | Out-File -Encoding utf8 $resultat
} catch {
    "ECHEC $(Get-Date -Format s) : $_" | Out-File -Encoding utf8 $resultat
}
