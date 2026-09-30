import os
import socket

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from fastmcp.tools import ToolResult

app = FastMCP("Weather MCP Server")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MOCK = os.getenv("WEATHER_MOCK", "0") == "1"

# WMO weather interpretation codes used by Open-Meteo.
WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog", 51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 80: "rain showers", 81: "heavy rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


def _hop_meta() -> dict:
    """What this server actually received from the MCP Gateway (shown in the chatbot's traffic panel)."""
    headers = get_http_headers(include_all=True)
    return {
        "served_by": socket.gethostname(),
        "user_id": headers.get("x-mcp-userid"),
        "roles": [r for r in headers.get("x-mcp-roles", "").split(",") if r],
        "authorization_header_received": "authorization" in headers,
    }


def _result(data: dict) -> ToolResult:
    return ToolResult(structured_content=data, meta={"hop": _hop_meta()})


def _geocode(city: str) -> dict:
    resp = httpx.get(GEOCODE_URL, params={"name": city, "count": 1}, timeout=10)
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        raise ValueError(f"City not found: {city}")
    return results[0]


@app.tool()
def get_current_weather(city: str) -> ToolResult:
    """Get the current weather for a city (temperature in °C, wind in km/h)."""
    print(f"[weather] get_current_weather({city!r})")
    if MOCK:
        return _result({"city": city, "temperature_c": 31.0, "conditions": "partly cloudy", "wind_kmh": 9.0, "mock": True})

    place = _geocode(city)
    resp = httpx.get(
        FORECAST_URL,
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "timezone": "auto",
        },
        timeout=10,
    )
    resp.raise_for_status()
    current = resp.json()["current"]
    return _result({
        "city": place["name"],
        "country": place.get("country"),
        "time": current["time"],
        "temperature_c": current["temperature_2m"],
        "humidity_pct": current["relative_humidity_2m"],
        "conditions": WMO_CODES.get(current["weather_code"], f"code {current['weather_code']}"),
        "wind_kmh": current["wind_speed_10m"],
    })


@app.tool()
def get_forecast(city: str, days: int = 3) -> ToolResult:
    """Get a daily forecast for a city for the next 1-7 days."""
    print(f"[weather] get_forecast({city!r}, {days})")
    days = max(1, min(days, 7))
    if MOCK:
        return _result({"city": city, "days": [{"date": f"day+{i}", "min_c": 26, "max_c": 33, "precip_mm": 2.0} for i in range(days)], "mock": True})

    place = _geocode(city)
    resp = httpx.get(
        FORECAST_URL,
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "daily": "weather_code,temperature_2m_min,temperature_2m_max,precipitation_sum",
            "forecast_days": days,
            "timezone": "auto",
        },
        timeout=10,
    )
    resp.raise_for_status()
    daily = resp.json()["daily"]
    return _result({
        "city": place["name"],
        "country": place.get("country"),
        "days": [
            {
                "date": daily["time"][i],
                "conditions": WMO_CODES.get(daily["weather_code"][i], f"code {daily['weather_code'][i]}"),
                "min_c": daily["temperature_2m_min"][i],
                "max_c": daily["temperature_2m_max"][i],
                "precip_mm": daily["precipitation_sum"][i],
            }
            for i in range(len(daily["time"]))
        ],
    })
