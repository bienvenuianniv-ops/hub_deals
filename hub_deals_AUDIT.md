# hub_deals — résumé dense

Détecteur de bonnes affaires vol, basé sur l'API Travelpayouts, avec stockage SQLite,
détection d'anomalie de prix vs historique, et notification Telegram. Tourne en tâche
planifiée Windows quotidienne.

**✅ Vérifié en conditions réelles le 2026-08-03 10:47** — tâche "Traqueur de vols" déclenchée manuellement après tous les correctifs ci-dessous : `LastTaskResult=0`, 6 hubs traités (BZV/FIH absents), token API et token Telegram lus depuis l'environnement et fonctionnels, `anomaly_detection.py` importé sans erreur, notification Telegram envoyée avec succès (2 anomalies), base passée de 437 à 471 lignes. Aucune erreur dans `flight_deals_log.txt`.

## Fichiers (`C:\Users\Dell\hub_deals\`)

| Fichier | Rôle |
|---|---|
| `hub_deals_db.py` | **Script principal**, exécuté par la tâche planifiée. Fetch → SQLite → classement → notif Telegram (délègue la détection d'anomalie à `anomaly_detection.py`). |
| `anomaly_detection.py` | **Ajouté le 2026-08-03.** Source unique de la logique de détection d'anomalie (moyenne historique, seuil `SEUIL_BAISSE`), importée par les deux scripts ci-dessous. |
| ~~`hub_deals.py`~~ | Supprimé le 2026-08-03 (obsolète, plus utilisé). |
| `detect_anomalies.py` | Outil CLI d'analyse/diagnostic (relit `flight_deals.db`), mode diagnostic inclus. Réécrit le 2026-08-03 en wrapper fin autour de `anomaly_detection.py`. |
| `test_travelpayouts.py` | Script de test API brut (offres DKR + prix les moins chers 48h). Dev/debug uniquement. |
| `flight_deals.db` | SQLite, table unique `offres`. 437 lignes, 15 relevés, du 2026-07-21 au 2026-08-03. |
| `flight_deals_log.txt` | Log texte append-only (12.6 Ko), horodaté UTC. |

## Tâche planifiée

- Nom : **"Traqueur de vols"** — État : Ready — Action : exécute `hub_deals_db.py`
- Fréquence observée dans les logs : ~quotidienne (ex. 2026-08-02 15:14, 2026-08-03 08:05)
- Le script attend 30s au démarrage (`time.sleep(30)`) pour laisser le réseau se stabiliser après ouverture de session — sans quoi l'appel API plante.

## Flux d'exécution (`hub_deals_db.py`)

1. `init_db()` — crée `offres` si absente
2. Pour chaque hub dans `RABATTEMENT` (8 hubs) → `get_special_offers()` (API Travelpayouts `/aviasales/v3/get_special_offers`) → `enregistrer_offres()`
3. `classement_du_jour()` — tri par `total_estime` ASC
4. `verifier_et_notifier_anomalies()` — moyenne historique par (hub, destination), si baisse ≥ `SEUIL_NOTIFICATION` (8%) et ≥2 relevés historiques → notif Telegram HTML
5. Log de chaque étape dans `flight_deals_log.txt` + console

## Schéma DB (`offres`)

```
id, date_collecte, hub_origine, destination_code, destination_nom,
prix_vol_hub, rabattement, total_estime, date_depart, lien
```
`total_estime = prix_vol_hub + rabattement` (coût de rabattement Dakar → hub, forfaitaire, pas dynamique).

## Hubs configurés (RABATTEMENT, coûts juillet 2026)

| IATA | Ville | Coût rabattement | Durée |
|---|---|---|---|
| CMN | Casablanca | 400€ | 4h |
| CDG | Paris | 300€ | 6h |
| IST | Istanbul | 400€ | 7h |
| ADD | Addis-Abeba | 500€ | 6h |
| NBO | Nairobi | 500€ | 8h |
| ABJ | Abidjan | 200€ | 2h |

~~BZV (Brazzaville, 470€/6h) et FIH (Kinshasa, 570€/7h)~~ — **retirés le 2026-08-03**, voir décision ci-dessous.

## Stats actuelles de la base

- 437 lignes / 15 relevés (2026-07-21 → 2026-08-03)
- Répartition : Paris 135, Istanbul 135, Casablanca 134, Nairobi 19, Addis-Abeba 13, Abidjan 1
- BZV et FIH : 0 offre sur 15 relevés historiques (avant retrait)

## ✅ Décision BZV/FIH (2026-08-03)

Appel direct de l'API Travelpayouts (`get_special_offers`) pour BZV et FIH : réponse valide `{"data":[],"success":true}`, contre 9 résultats pour CMN en contrôle. Confirmé : ce n'est pas un bug du script, Aviasales n'a simplement aucune donnée "special offers" sur les routes Brazzaville et Kinshasa (couverture insuffisante, pas un problème de code IATA).

**Décision : retrait de BZV et FIH de `RABATTEMENT` dans `hub_deals_db.py`.** Ils consommaient 2 appels API par exécution pour 0 résultat exploitable. Le dict passe de 8 à 6 hubs (CMN, CDG, IST, ADD, NBO, ABJ).

## ⚠️ Points de sécurité / dette technique

1. ~~**Secrets en dur dans le code**~~ — **Corrigé le 2026-08-03.** `hub_deals_db.py` lit désormais `TRAVELPAYOUTS_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` uniquement depuis `os.environ`, sans fallback. Valeurs persistées en variables d'environnement **utilisateur** Windows (`[Environment]::SetEnvironmentVariable(..., "User")`, visibles dans `HKCU:\Environment`) pour que la tâche planifiée "Traqueur de vols" continue de fonctionner. Le script s'arrête (`SystemExit`) si `TRAVELPAYOUTS_TOKEN` est absent.
   - ✅ Token du bot Telegram régénéré via BotFather le 2026-08-03 (l'ancien token, exposé en clair dans le code, est révoqué). Nouvelle valeur en place dans `HKCU:\Environment\TELEGRAM_BOT_TOKEN`.
   - Note technique : une session shell déjà ouverte avant ce changement ne verra pas les nouvelles variables (son bloc d'environnement est figé à son démarrage) — normal, pas un bug. La tâche planifiée, elle, relit l'environnement à chaque déclenchement et n'a pas besoin de reboot.
2. ~~**Duplication de logique**~~ — **Corrigé le 2026-08-03.** Extraction dans `anomaly_detection.py` (`get_dernier_releve`, `calculer_moyennes_historiques`, `detecter_anomalies`, `SEUIL_BAISSE`). `hub_deals_db.py` et `detect_anomalies.py` importent désormais ce module au lieu de recalculer chacun leur moyenne historique. Vérifié fonctionnel de bout en bout (`detect_anomalies.py` exécuté contre la base réelle, 437 lignes).
3. ~~**Pas de `.gitignore` / pas de dépôt git détecté**~~ — **Corrigé le 2026-08-03**, voir section Migration ci-dessous.
4. **`total_estime` statique** — le coût de rabattement Dakar→hub est un forfait fixe par hub (juillet 2026), pas recalculé dynamiquement ; peut dériver dans le temps.

## ✅ Vérification secrets — données (2026-08-03)

Scan complet de `flight_deals.db` et `flight_deals_log.txt` pour toute fuite de secret :

- **`flight_deals.db`** — table `offres` (+ `sqlite_sequence` technique). Colonnes : uniquement données de vol (prix, dates, destinations, liens Aviasales). Le champ `lien` contient des tokens de *recherche* Aviasales (session côté client, publics par nature dans un lien partageable), pas nos clés API. Recherche exhaustive des chaînes de token connues (ancien/nouveau bot Telegram, Travelpayouts) sur tout le contenu texte de la table : **0 correspondance**.
- **`flight_deals_log.txt`** — aucune ligne `ERREUR`, aucune URL contenant `token=`, aucune trace des tokens (ancien ou nouveau) sur tout l'historique du fichier.
- ⚠️ Risque latent identifié (pas un incident) : `requests.exceptions.RequestException` peut inclure l'URL complète (donc le token) dans son message si un appel API échoue un jour ; ces erreurs sont écrites dans `flight_deals_log.txt` via `log()`. Aucune occurrence à ce jour, mais à garder en tête si des erreurs réseau apparaissent.

**Conclusion : `hub_deals` est intégralement propre côté secrets — code (4 scripts), base SQLite et fichier de log.**

## ✅ Migration vers sous-dossier dédié + git local (2026-08-03)

Le projet vivait à plat dans `C:\Users\Dell` (le home directory), mélangé avec des fichiers personnels sans rapport — pas de `.gitignore`, pas de dépôt versionné.

**Actions effectuées :**
1. Création de `C:\Users\Dell\hub_deals\` et déplacement des 7 fichiers du projet (les 4 scripts, `flight_deals.db`, `flight_deals_log.txt`, `hub_deals_AUDIT.md`)
2. `.gitignore` ajouté : exclut `flight_deals.db`, `flight_deals_log.txt` (données générées à l'exécution), `__pycache__/`, `*.pyc`, `.env`
3. `git init` local dans `hub_deals\` + commit initial (6 fichiers versionnés, DB/log correctement ignorés) — **dépôt gardé local, pas de remote**
4. Tâche planifiée **"Traqueur de vols"** mise à jour : `WorkingDirectory` passé de `C:\Users\Dell` à `C:\Users\Dell\hub_deals` (modifié manuellement via l'UI Planificateur de tâches, `Set-ScheduledTask`/`Register-ScheduledTask` en PowerShell ayant échoué avec "Accès refusé" en session non-élevée)
5. **Vérifié en conditions réelles** : tâche déclenchée manuellement après migration → `LastTaskResult=0`, log écrit dans `C:\Users\Dell\hub_deals\flight_deals_log.txt` (216→234 lignes), base mise à jour au bon endroit (471→505 lignes), notification Telegram envoyée avec succès

## Prochaines actions suggérées

- [x] Régénérer le token du bot Telegram (fait le 2026-08-03)
- [x] Décider du sort de BZV/FIH → retirés le 2026-08-03 (confirmé : 0 couverture Aviasales, pas un bug)
- [x] Fusionner la logique de détection d'anomalie → extraite dans `anomaly_detection.py` (2026-08-03)
- [x] Vérifier absence de secrets dans le code, la DB et les logs → confirmé, rien trouvé (2026-08-03)
- [x] Ajouter `.gitignore` et versionner le projet en git → sous-dossier `C:\Users\Dell\hub_deals\`, dépôt local créé, tâche planifiée mise à jour et vérifiée (2026-08-03)
