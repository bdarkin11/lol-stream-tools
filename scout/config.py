"""
config.py – Carga centralizada de variables de entorno y constantes.

Todas las claves se leen del archivo .env mediante python-dotenv.
Nunca se imprimen valores de claves en logs ni consola.

El .env es unico y compartido con los trackers: vive en la carpeta
"launcher" (hermana de scout/), asi la Riot API key y el resto de
credenciales se actualizan en un solo lugar.
"""
import os
from dotenv import load_dotenv

# Ruta al .env unico y compartido en ../launcher/.env
# (launcher/ es hermana de scout/, de ahi el "..")
_ENV_COMPARTIDO = os.path.join(os.path.dirname(__file__), "..", "launcher", ".env")
load_dotenv(_ENV_COMPARTIDO)

# ── Twitch ────────────────────────────────────────────────────────────
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "")
TWITCH_REFRESH_TOKEN = os.getenv("TWITCH_REFRESH_TOKEN", "")
TWITCH_CHANNEL = os.getenv("TWITCH_CHANNEL", "")
BOT_REFRESH_TOKEN = os.getenv("BOT_REFRESH_TOKEN", "")
BOT_USER_ID = os.getenv("BOT_USER_ID", "")
ENABLE_TWITCH = os.getenv("ENABLE_TWITCH", "1") == "1"

STREAMER_TOKEN_FILE = "twitch_tokens.json"
BOT_TOKEN_FILE = "bot_tokens.json"

# ── OBS ───────────────────────────────────────────────────────────────
OBS_HOST = os.getenv("OBS_HOST", "localhost")
OBS_PORT = int(os.getenv("OBS_PORT", "4455"))
OBS_PASSWORD = os.getenv("OBS_PASSWORD", "")
ENABLE_OBS = os.getenv("ENABLE_OBS", "1") == "1"

# ── Riot ──────────────────────────────────────────────────────────────
RIOT_API_KEY = os.getenv("RIOT_API_KEY", "")
RIOT_GAME_NAME = os.getenv("RIOT_GAME_NAME", "")
RIOT_TAG_LINE = os.getenv("RIOT_TAG_LINE", "")

# Regiones (se pueden añadir como variables de entorno si el usuario cambia de servidor)
RIOT_REGION_ACCOUNT = os.getenv("RIOT_REGION_ACCOUNT", "americas")
RIOT_REGION_LOL = os.getenv("RIOT_REGION_LOL", "la2")

# ── Live Client Data API ──────────────────────────────────────────────
LIVE_BASE = "https://127.0.0.1:2999/liveclientdata"

# ── Multikill detection ──────────────────────────────────────────────
TRIGGER_STREAKS = {3: "Triple Kill", 4: "Quadra Kill", 5: "Penta Kill"}
POLL_INTERVAL = 0.5          # segundos entre cada poll de eventos
MULTIKILL_WINDOW = 10        # ventana en segundos para acumular kills
SETTLE_DELAY = 8             # debounce en segundos antes de confirmar la racha

# ── Overlay / Tracker ────────────────────────────────────────────────
# Ruta de salida del overlay HTML.  Se puede sobreescribir con la variable
# OVERLAY_PATH en el .env; por defecto se genera dentro del proyecto.
OVERLAY_PATH = os.getenv("OVERLAY_PATH") or os.path.join(os.path.dirname(__file__), "overlay.html")

# Archivo de estado compartido entre el módulo de datos y el overlay / chat.
ESTADO_FILE = os.path.join(os.path.dirname(__file__), "estado.json")

# Intervalo en segundos entre consultas a la API de Riot (módulo de datos).
DATA_POLL_INTERVAL = 30

# Intervalo de rotación de slides en el overlay (en segundos).
OVERLAY_ROTATION_SECS = 7

# DEBUG_ANIMATION: cuando es True simula un cambio de LP para probar la
# animación del overlay sin necesidad de jugar una partida.  Desactivado
# por defecto para uso en stream.
DEBUG_ANIMATION = os.getenv("DEBUG_ANIMATION", "0") == "1"

# ── Tabla de estimación de índice (antes "MMR") ──────────────────────
# NOTA: Este "Índice" es una estimación propia basada en tier/division + resultados
# recientes. NO es el MMR interno de Riot Games, que no se expone públicamente.
TABLA_INDICE_BASE = {
    "IRON": 400, "BRONZE": 800, "SILVER": 1200,
    "GOLD": 1600, "PLATINUM": 2000, "EMERALD": 2400,
    "DIAMOND": 2800, "MASTER": 3300, "GRANDMASTER": 3800, "CHALLENGER": 4300,
}
TABLA_DIVISION = {"IV": 0, "III": 100, "II": 200, "I": 300}

# ── Límite de caché de partidas ──────────────────────────────────────
MAX_MATCH_CACHE = 50

