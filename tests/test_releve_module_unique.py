"""Le releve tourne comme script (pythonw.exe hub_deals_db.py).

Relecture du 2026-09-26 : Python chargeait alors DEUX copies du fichier,
`__main__` et `hub_deals_db`. Le releve remplissait LIENS_COURTS dans la
premiere ; abonnes.py et page.py lisaient la seconde, toujours vide. Les
abonnes recevaient donc des liens directs, que Travelpayouts ne compte
pas. Les autres tests importent le module normalement et ne pouvaient
pas le voir : celui-ci lance vraiment le fichier comme script.
"""
import os
import subprocess
import sys
import tempfile
import unittest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SONDE = r"""
import runpy, sys
sys.path.insert(0, {racine!r})
try:
    runpy.run_path({script!r}, run_name="__main__")
except SystemExit:
    pass
import abonnes, hub_deals_db
print("NOM=" + hub_deals_db.__name__)
print("MEME=" + str(abonnes.hub_deals_db is hub_deals_db))
"""


class TestUneSeuleCopieDuModule(unittest.TestCase):
    def test_les_modules_importes_voient_le_releve_en_cours(self):
        env = dict(os.environ)
        # sans jeton, le releve s'arrete tout de suite : rien ne part
        env.pop("TRAVELPAYOUTS_TOKEN", None)
        with tempfile.TemporaryDirectory() as dossier:
            sortie = subprocess.run(
                [sys.executable, "-c", _SONDE.format(
                    racine=RACINE,
                    script=os.path.join(RACINE, "hub_deals_db.py"))],
                cwd=dossier, env=env, capture_output=True, text=True,
                timeout=60)
        self.assertIn("NOM=__main__", sortie.stdout, sortie.stderr)
        self.assertIn("MEME=True", sortie.stdout, sortie.stderr)
