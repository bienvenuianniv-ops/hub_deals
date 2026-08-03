import os
import requests

TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN")
BASE_URL = "https://api.travelpayouts.com"


def get_special_offers(origin):
    url = f"{BASE_URL}/aviasales/v3/get_special_offers"
    params = {"origin": origin, "token": TOKEN}
    response = requests.get(url, params=params)
    response.raise_for_status()
    return response.json()


def get_latest_cheap_prices(currency="usd", limit=30):
    url = f"{BASE_URL}/v2/prices/latest"
    params = {
        "currency": currency,
        "limit": limit,
        "sorting": "price",
        "token": TOKEN,
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Il manque TRAVELPAYOUTS_TOKEN dans l'environnement.")

    import json

    print("1. Bonnes affaires depuis Dakar (DKR)...")
    try:
        offers = get_special_offers("DKR")
        print(json.dumps(offers, indent=2, ensure_ascii=False))
    except requests.HTTPError as e:
        print(f"   -> erreur : {e}")

    print("\n2. Billets les moins chers, toutes routes (48h)...")
    try:
        latest = get_latest_cheap_prices()
        print(json.dumps(latest, indent=2, ensure_ascii=False))
    except requests.HTTPError as e:
        print(f"   -> erreur : {e}")
