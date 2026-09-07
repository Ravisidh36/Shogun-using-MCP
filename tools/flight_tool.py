import os 
import re 
import certifi
import airportsdata
import pycountry
import requests
from dotenv import load_dotenv

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

API_KEY = os.getenv("AVIATIONSTACK_API_KEY")

# Default origin when user says only destination, e.g. "Japan trip"
# Change this if your default location is not Bangladesh/Dhaka.
DEFAULT_ORIGIN_IATA = os.getenv("DEFAULT_ORIGIN_IATA", "DAC")


BASE_URL = "https://api.aviationstack.com/v1/flights"


AIRPORTS = airportsdata.load("IATA")



COUNTRY_ALIASES = {
    "usa": "US",
    "u.s.a": "US",
    "u.s.": "US",
    "america": "US",
    "united states": "US",
    "uk": "GB",
    "u.k.": "GB",
    "britain": "GB",
    "england": "GB",
    "uae": "AE",
    "dubai": "AE",
    "south korea": "KR",
    "korea": "KR",
    "russia": "RU",
    "vietnam": "VN",
    "bangladesh": "BD",
    "india": "IN",
    "japan": "JP",
    "china": "CN",
    "singapore": "SG",
    "malaysia": "MY",
    "thailand": "TH",
    "indonesia": "ID",
    "nepal": "NP",
    "qatar": "QA",
    "saudi arabia": "SA",
    "turkey": "TR",
    "canada": "CA",
    "australia": "AU",
    "germany": "DE",
    "france": "FR",
    "italy": "IT",
    "spain": "ES",
}


# Preferred main airport for country-level search
COUNTRY_MAIN_AIRPORT = {
    "BD": "DAC",
    "IN": "DEL",
    "JP": "NRT",
    "US": "JFK",
    "GB": "LHR",
    "AE": "DXB",
    "SG": "SIN",
    "MY": "KUL",
    "TH": "BKK",
    "ID": "CGK",
    "CN": "PEK",
    "KR": "ICN",
    "NP": "KTM",
    "QA": "DOH",
    "SA": "JED",
    "TR": "IST",
    "CA": "YYZ",
    "AU": "SYD",
    "DE": "FRA",
    "FR": "CDG",
    "IT": "FCO",
    "ES": "MAD",
}




CITY_MAIN_AIRPORT = {
    "dhaka": "DAC",
    "delhi": "DEL",
    "new delhi": "DEL",
    "mumbai": "BOM",
    "kolkata": "CCU",
    "chennai": "MAA",
    "bangalore": "BLR",
    "bengaluru": "BLR",
    "tokyo": "NRT",
    "osaka": "KIX",
    "kyoto": "KIX",
    "new york": "JFK",
    "london": "LHR",
    "dubai": "DXB",
    "singapore": "SIN",
    "kuala lumpur": "KUL",
    "bangkok": "BKK",
    "doha": "DOH",
    "istanbul": "IST",
    "toronto": "YYZ",
    "sydney": "SYD",
    "paris": "CDG",
    "rome": "FCO",
    "madrid": "MAD",
    "frankfurt": "FRA",
}


def clean_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    stop_words = [
        "flight", "flights", "ticket", "tickets", "trip", "travel",
        "plan", "complete", "days", "day", "including", "hotel",
        "hotels", "sightseeing", "under", "budget", "info", "information"
    ]
    words = [w for w in text.split() if w not in stop_words]
    return " ".join(words).strip()



def country_name_to_code(text: str):
    text = clean_text(text)

    if text in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[text]

    try:
        country = pycountry.countries.lookup(text)
        return country.alpha_2
    except LookupError:
        pass

    # Detect country name inside longer text
    for country in pycountry.countries:
        country_name = country.name.lower()
        if country_name in text:
            return country.alpha_2

    for alias, code in COUNTRY_ALIASES.items():
        if alias in text:
            return code

    return None



def airport_country_matches(airport: dict, country_code: str) -> bool:
    airport_country = str(airport.get("country", "")).upper().strip()

    if airport_country == country_code:
        return True

    try:
        country = pycountry.countries.get(alpha_2=country_code)
        if country and airport_country.lower() == country.name.lower():
            return True
    except Exception:
        pass

    return False




def get_best_airport_for_country(country_code: str):
    preferred = COUNTRY_MAIN_AIRPORT.get(country_code)

    if preferred and preferred in AIRPORTS:
        return preferred

    candidates = []

    for iata, airport in AIRPORTS.items():
        if not iata:
            continue

        if airport_country_matches(airport, country_code):
            name = str(airport.get("name", "")).lower()
            city = str(airport.get("city", "")).lower()

            score = 0

            if "international" in name:
                score += 50
            if "intl" in name:
                score += 40
            if "capital" in name:
                score += 20
            if city:
                score += 5

            candidates.append((score, iata))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]




def resolve_location_to_iata(location: str):
    """
    Converts country/city/airport/IATA into IATA code.

    Examples:
    Bangladesh -> DAC
    Japan -> NRT
    Dhaka -> DAC
    Tokyo -> NRT
    DAC -> DAC
    """

    if not location:
        return None

    raw_location = location.strip()

    # Direct IATA code
    if re.fullmatch(r"[A-Za-z]{3}", raw_location):
        code = raw_location.upper()
        if code in AIRPORTS:
            return code

    location_clean = clean_text(raw_location)

    if not location_clean:
        return None

    # City preferred airport
    if location_clean in CITY_MAIN_AIRPORT:
        return CITY_MAIN_AIRPORT[location_clean]

    # Country preferred airport
    country_code = country_name_to_code(location_clean)
    if country_code:
        airport = get_best_airport_for_country(country_code)
        if airport:
            return airport

    # Exact city match from airport database
    city_matches = []

    for iata, airport in AIRPORTS.items():
        city = str(airport.get("city", "")).lower().strip()
        name = str(airport.get("name", "")).lower().strip()

        score = 0

        if city == location_clean:
            score += 100
        elif location_clean in city:
            score += 70

        if location_clean in name:
            score += 50

        if "international" in name:
            score += 10

        if score > 0:
            city_matches.append((score, iata))

    if city_matches:
        city_matches.sort(reverse=True)
        return city_matches[0][1]

    return None




def find_location_mentions(query: str):
    """
    Finds country or city names inside a natural language query,
    ordered by where they actually appear in the text (not by
    dict/iteration order, which was the old bug).

    Returns a list of (start_index, matched_text) tuples, sorted
    by position, with overlapping matches collapsed to the longest
    match at that position.
    """

    q = query.lower()
    candidates = []

    # Country aliases
    for alias in COUNTRY_ALIASES:
        for m in re.finditer(rf"\b{re.escape(alias)}\b", q):
            candidates.append((m.start(), m.end(), alias))

    # Country names from pycountry
    for country in pycountry.countries:
        name = country.name.lower()
        if len(name) >= 4:
            for m in re.finditer(rf"\b{re.escape(name)}\b", q):
                candidates.append((m.start(), m.end(), name))

    # City names from our preferred city map
    for city in CITY_MAIN_AIRPORT:
        for m in re.finditer(rf"\b{re.escape(city)}\b", q):
            candidates.append((m.start(), m.end(), city))

    # Sort by position; prefer the longer match when two candidates
    # start at the same spot (e.g. "new delhi" over "delhi")
    candidates.sort(key=lambda c: (c[0], -(c[1] - c[0])))

    taken_spans = []
    mentions = []

    def overlaps(start, end):
        return any(not (end <= s or start >= e) for s, e in taken_spans)

    for start, end, text in candidates:
        if overlaps(start, end):
            continue
        taken_spans.append((start, end))
        mentions.append((start, text))

    mentions.sort(key=lambda m: m[0])
    return mentions


def parse_route(query: str):
    """
    Returns:
    dep_iata, arr_iata

    Can return:
    None, None  -> global live flights
    DAC, NRT    -> filtered route
    DAC, None   -> all flights from DAC
    None, NRT   -> all flights to NRT
    """

    q = query.strip()
    q_lower = q.lower()

    # Global / all-country query
    global_keywords = [
        "all country",
        "all countries",
        "global flight",
        "global flights",
        "all flight",
        "all flights",
        "worldwide flight",
        "worldwide flights",
    ]

    if any(keyword in q_lower for keyword in global_keywords):
        return None, None

    # Direct IATA code route: DAC to NRT
    codes = re.findall(r"\b[A-Z]{3}\b", q)

    if len(codes) >= 2:
        dep = codes[0].upper()
        arr = codes[1].upper()
        return dep, arr

    # Pattern: from X to Y
    match = re.search(
        r"\bfrom\s+(.+?)\s+\bto\s+(.+?)(?:\s+(?:on|for|under|including|with|in|at)\b|[.!?]|$)",
        q_lower,
    )

    if match:
        origin_text = match.group(1)
        dest_text = match.group(2)

        dep_iata = resolve_location_to_iata(origin_text)
        arr_iata = resolve_location_to_iata(dest_text)

        return dep_iata, arr_iata

    # Pattern: to Y from X
    match = re.search(
        r"\bto\s+(.+?)\s+\bfrom\s+(.+?)(?:\s+(?:on|for|under|including|with|in|at)\b|[.!?]|$)",
        q_lower,
    )

    if match:
        dest_text = match.group(1)
        origin_text = match.group(2)

        dep_iata = resolve_location_to_iata(origin_text)
        arr_iata = resolve_location_to_iata(dest_text)

        return dep_iata, arr_iata

    # Multi-location fallback: find every country/city mentioned,
    # in the order they actually appear in the text. This runs
    # BEFORE the loose "from X" / "to X" regexes below, because
    # those are greedy and misfire on phrasing like
    # "India trip from Bangladesh" (destination stated before
    # origin, no literal "to" anywhere in the sentence) --
    # they used to swallow the whole rest of the string as the
    # origin and silently drop the destination.
    mentions = find_location_mentions(q)  # list of (start_idx, text)

    unique_texts = []
    for _, text in mentions:
        if text not in unique_texts:
            unique_texts.append(text)

    if len(unique_texts) >= 2:
        from_pos = q_lower.find("from")

        if from_pos != -1:
            # Origin = the mention that appears closest AFTER "from".
            after_from = [(s, t) for s, t in mentions if s > from_pos]

            if after_from:
                _, origin_text = min(after_from, key=lambda item: item[0])
                dep_iata = resolve_location_to_iata(origin_text)

                dest_candidates = [t for t in unique_texts if t != origin_text]
                if dest_candidates:
                    arr_iata = resolve_location_to_iata(dest_candidates[0])
                    return dep_iata, arr_iata

        # No "from" anchor (or it didn't help) -- fall back to
        # first-mentioned = origin, second-mentioned = destination.
        dep_iata = resolve_location_to_iata(unique_texts[0])
        arr_iata = resolve_location_to_iata(unique_texts[1])
        return dep_iata, arr_iata

    if len(unique_texts) == 1:
        only_start, only_text = mentions[0]
        from_pos = q_lower.find("from")
        to_pos = q_lower.find(" to ")

        # If "from" precedes the mention (and there's no "to"
        # ahead of it doing the same job), treat it as the ORIGIN,
        # e.g. "flights from Dhaka" -> (DAC, None), not (DAC, DAC).
        if from_pos != -1 and from_pos < only_start and (to_pos == -1 or to_pos > only_start):
            dep_iata = resolve_location_to_iata(only_text)
            return dep_iata, None

        arr_iata = resolve_location_to_iata(only_text)
        return DEFAULT_ORIGIN_IATA, arr_iata

    # Pattern: flights from X (only one location mentioned in the
    # whole query, following "from" -- e.g. "flights from Dhaka")
    match = re.search(r"\bfrom\s+(.+?)(?:[.!?]|$)", q_lower)

    if match:
        origin_text = match.group(1)
        dep_iata = resolve_location_to_iata(origin_text)
        return dep_iata, None

    # Pattern: flights to X
    match = re.search(r"\bto\s+(.+?)(?:[.!?]|$)", q_lower)

    if match:
        dest_text = match.group(1)
        arr_iata = resolve_location_to_iata(dest_text)
        return None, arr_iata

    return None, None


def format_flight(flight: dict):
    airline = flight.get("airline", {}).get("name") or "Unknown airline"
    flight_number = flight.get("flight", {}).get("iata") or "Unknown flight number"
    status = flight.get("flight_status") or "Unknown"

    dep = flight.get("departure", {}) or {}
    arr = flight.get("arrival", {}) or {}

    dep_airport = dep.get("airport") or "Unknown departure airport"
    dep_iata = dep.get("iata") or "Unknown"
    dep_terminal = dep.get("terminal") or "N/A"
    dep_gate = dep.get("gate") or "N/A"
    dep_scheduled = dep.get("scheduled") or "Unknown"
    dep_delay = dep.get("delay")
    dep_delay_text = f"{dep_delay} minutes" if dep_delay is not None else "N/A"

    arr_airport = arr.get("airport") or "Unknown arrival airport"
    arr_iata = arr.get("iata") or "Unknown"
    arr_terminal = arr.get("terminal") or "N/A"
    arr_gate = arr.get("gate") or "N/A"
    arr_scheduled = arr.get("scheduled") or "Unknown"
    arr_delay = arr.get("delay")
    arr_delay_text = f"{arr_delay} minutes" if arr_delay is not None else "N/A"

    return f"""
Airline: {airline}
Flight: {flight_number}
Status: {status}

Departure:
- Airport: {dep_airport}
- IATA: {dep_iata}
- Terminal: {dep_terminal}
- Gate: {dep_gate}
- Scheduled: {dep_scheduled}
- Delay: {dep_delay_text}

Arrival:
- Airport: {arr_airport}
- IATA: {arr_iata}
- Terminal: {arr_terminal}
- Gate: {arr_gate}
- Scheduled: {arr_scheduled}
- Delay: {arr_delay_text}
""".strip()


def structure_flight(flight: dict):
    """
    Same raw AviationStack record as format_flight(), but shaped as a
    plain dict for JSON/UI consumption (flight cards) instead of a
    preformatted text block.
    """
    airline = flight.get("airline", {}).get("name") or "Unknown airline"
    flight_number = flight.get("flight", {}).get("iata") or "—"
    status = (flight.get("flight_status") or "unknown").lower()

    dep = flight.get("departure", {}) or {}
    arr = flight.get("arrival", {}) or {}

    return {
        "airline": airline,
        "flight_number": flight_number,
        "status": status,
        "departure": {
            "airport": dep.get("airport") or "Unknown airport",
            "iata": dep.get("iata") or "—",
            "terminal": dep.get("terminal"),
            "gate": dep.get("gate"),
            "scheduled": dep.get("scheduled"),
            "delay_minutes": dep.get("delay"),
        },
        "arrival": {
            "airport": arr.get("airport") or "Unknown airport",
            "iata": arr.get("iata") or "—",
            "terminal": arr.get("terminal"),
            "gate": arr.get("gate"),
            "scheduled": arr.get("scheduled"),
            "delay_minutes": arr.get("delay"),
        },
    }


# Friendly, non-alarming explanation shown whenever we have flight
# *status* data but (as always, on this API/tier) no fares. This
# replaces the old wording that pointed people at Amadeus's
# self-service API, which Amadeus discontinued for new developers
# in July 2026 -- so that suggestion had gone stale.
PRICE_NOTICE = (
    "This is live flight status data, not ticket prices -- "
    "AviationStack's free tier doesn't include fares. "
    "Check Google Flights, Skyscanner, or the airline's own site for pricing."
)


def fetch_flight_report(query: str, limit: int = 10):
    """
    Makes exactly ONE call to the AviationStack API and returns
    everything both the LLM-facing text and the UI cards need, so
    callers never have to hit the API twice for the same request
    (the free tier only allows ~100 calls/month total).

    Returns a dict:
    {
        "summary_text": str,       # plain text for the LLM prompt
        "route_info": str,         # e.g. "Live flights from DAC to DEL"
        "flights": [ {...}, ... ], # structured records for UI cards
        "notice": str | None,      # short, friendly caveat for the UI
        "error": str | None,       # set only on hard failure
    }
    """
    if not API_KEY:
        msg = (
            "Flight API error: AVIATIONSTACK_API_KEY is missing.\n"
            "Please add this in your .env file:\n"
            "AVIATIONSTACK_API_KEY=your_api_key_here"
        )
        return {
            "summary_text": msg,
            "route_info": "",
            "flights": [],
            "notice": None,
            "error": msg,
        }

    dep_iata, arr_iata = parse_route(query)

    params = {
        "access_key": API_KEY,
        "limit": min(limit, 100),
    }

    if dep_iata:
        params["dep_iata"] = dep_iata

    if arr_iata:
        params["arr_iata"] = arr_iata

    try:
        response = requests.get(BASE_URL, params=params, timeout=30)
        data = response.json()
    except requests.exceptions.RequestException as e:
        msg = f"Flight API request failed: {e}"
        return {"summary_text": msg, "route_info": "", "flights": [], "notice": None, "error": msg}
    except ValueError:
        msg = "Flight API returned invalid JSON."
        return {"summary_text": msg, "route_info": "", "flights": [], "notice": None, "error": msg}

    if "error" in data:
        error = data["error"]
        msg = (
            "Flight API error:\n"
            f"Code: {error.get('code', 'Unknown')}\n"
            f"Message: {error.get('message', 'Unknown error')}"
        )
        return {"summary_text": msg, "route_info": "", "flights": [], "notice": None, "error": msg}

    raw_flights = data.get("data", [])

    route_info = "Global live flights"
    if dep_iata and arr_iata:
        route_info = f"Live flights from {dep_iata} to {arr_iata}"
    elif dep_iata:
        route_info = f"Live flights from {dep_iata}"
    elif arr_iata:
        route_info = f"Live flights to {arr_iata}"

    if not raw_flights:
        route_text = ""
        if dep_iata and arr_iata:
            route_text = f" {dep_iata} \u2192 {arr_iata}"
        elif dep_iata:
            route_text = f" from {dep_iata}"
        elif arr_iata:
            route_text = f" to {arr_iata}"

        summary_text = f"No live flights currently in range for{route_text}.\n{PRICE_NOTICE}"

        return {
            "summary_text": summary_text,
            "route_info": route_info,
            "flights": [],
            "notice": PRICE_NOTICE,
            "error": None,
        }

    limited = raw_flights[:limit]
    formatted_flights = [format_flight(f) for f in limited]
    structured_flights = [structure_flight(f) for f in limited]

    summary_text = f"{route_info}\n\n" + "\n\n---\n\n".join(formatted_flights)

    return {
        "summary_text": summary_text,
        "route_info": route_info,
        "flights": structured_flights,
        "notice": PRICE_NOTICE,
        "error": None,
    }


def search_flights(query: str, limit: int = 10):
    """
    Backward-compatible plain-text entry point (used directly by
    itinerary_agent's LLM prompt, and by the __main__ block below).
    Internally just unwraps fetch_flight_report()'s summary_text.
    """
    return fetch_flight_report(query, limit)["summary_text"]


if __name__ == "__main__":
    print(search_flights("Plan a 7 days Japan trip from Bangladesh"))
    print("\n" + "=" * 80 + "\n")
    print(search_flights("all country flight info"))