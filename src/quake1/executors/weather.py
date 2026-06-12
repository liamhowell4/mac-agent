"""weather.* — open-meteo (free, no key). The one networked domain besides web."""

from __future__ import annotations

from functools import lru_cache

import httpx

from ._util import ToolError

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"

WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
       45: "fog", 51: "drizzle", 61: "rain", 63: "rain", 65: "heavy rain",
       71: "snow", 80: "showers", 95: "thunderstorm"}


@lru_cache(maxsize=128)
def _geocode_cached(location: str) -> tuple[float, float, str]:
    r = httpx.get(GEO, params={"name": location, "count": 1}, timeout=10)
    r.raise_for_status()
    hits = r.json().get("results") or []
    if not hits:
        raise ToolError(f"Couldn't find a place called {location!r}")
    h = hits[0]
    return h["latitude"], h["longitude"], h["name"]


def _geocode(location: str | None) -> tuple[float, float, str]:
    if not location:
        raise ToolError("a location is required (e.g. 'Chicago')")
    return _geocode_cached(str(location).strip().lower())


def current(args: dict) -> dict:
    lat, lon, name = _geocode(args.get("location"))
    r = httpx.get(FORECAST, params={
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,weather_code,wind_speed_10m",
        "temperature_unit": "fahrenheit",
    }, timeout=10)
    r.raise_for_status()
    c = r.json().get("current", {})
    return {"location": name,
            "temperature_f": c.get("temperature_2m"),
            "conditions": WMO.get(c.get("weather_code"), "unknown"),
            "wind_mph": c.get("wind_speed_10m")}


def forecast(args: dict) -> dict:
    lat, lon, name = _geocode(args.get("location"))
    days = min(int(args.get("days") or 3), 7)
    r = httpx.get(FORECAST, params={
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "temperature_unit": "fahrenheit", "forecast_days": days,
    }, timeout=10)
    r.raise_for_status()
    d = r.json().get("daily", {})
    out = []
    for i, date in enumerate(d.get("time", [])):
        out.append({"date": date,
                    "high_f": d["temperature_2m_max"][i],
                    "low_f": d["temperature_2m_min"][i],
                    "precip_pct": d["precipitation_probability_max"][i],
                    "conditions": WMO.get(d["weather_code"][i], "unknown")})
    return {"location": name, "forecast": out}


HANDLERS = {
    "weather.current": current,
    "weather.forecast": forecast,
}
