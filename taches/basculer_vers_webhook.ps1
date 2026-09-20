# Deux reglages de la bascule du 2026-09-20, qui demandent l'elevation.
#
# 1. Desactiver l'ecoute locale : Telegram refuse webhook et long polling
#    ensemble, et la tache repartirait a la prochaine ouverture de session.
# 2. Reveiller la machine pour le releve de 13h : le verrou anti-veille
#    etait pose par bot_ecoute.py, qui ne tourne plus.
#
# Lancement (UAC a valider) :
#   Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Users\Dell\hub_deals\taches\basculer_vers_webhook.ps1'

$journal = "$env:TEMP\basculer_vers_webhook.txt"
"=== $(Get-Date -Format s) ===" | Out-File $journal -Encoding utf8

try {
    Disable-ScheduledTask -TaskName "Bot vols - ecoute" -ErrorAction Stop | Out-Null
    "ecoute locale desactivee : OK" | Out-File $journal -Append -Encoding utf8
} catch {
    "ECHEC desactivation ecoute : $_" | Out-File $journal -Append -Encoding utf8
}

try {
    $t = Get-ScheduledTask -TaskName "Traqueur de vols" -ErrorAction Stop
    $t.Settings.WakeToRun = $true
    Set-ScheduledTask -InputObject $t -ErrorAction Stop | Out-Null
    "reveil pour le releve : OK" | Out-File $journal -Append -Encoding utf8
} catch {
    "ECHEC reveil : $_" | Out-File $journal -Append -Encoding utf8
}

# Relecture : on ecrit ce qui est CONSTATE, pas ce qu'on a demande.
$etat = (Get-ScheduledTask -TaskName "Bot vols - ecoute").State
$reveil = (Get-ScheduledTask -TaskName "Traqueur de vols").Settings.WakeToRun
"constate -- ecoute : $etat, WakeToRun : $reveil" | Out-File $journal -Append -Encoding utf8
Get-Content $journal
Read-Host "Termine. Appuyez sur Entree pour fermer"
