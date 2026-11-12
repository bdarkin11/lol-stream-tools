"""
live_game.py – Detección de multikills del jugador local.

Usa la LoL Live Client Data API (https://127.0.0.1:2999) para leer
eventos de partida en tiempo real.  Detecta ChampionKill con KillerName
igual al jugador activo, con una ventana de 10 s y debounce de 8 s.

Dispara:
  - Guardado de replay en OBS (si está habilitado).
  - Creación de clip en Twitch + envío del enlace al chat.
  - Scouting automático al inicio de la partida.
"""
import json
import logging
import os
import threading
import time

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

from config import (
    LIVE_BASE, TRIGGER_STREAKS, POLL_INTERVAL,
    MULTIKILL_WINDOW, SETTLE_DELAY, ENABLE_OBS,
    OBS_HOST, OBS_PORT, OBS_PASSWORD, ESTADO_FILE,
)

urllib3.disable_warnings(InsecureRequestWarning)

log = logging.getLogger("live_game")

try:
    import obsws_python as obs
except ImportError:
    obs = None


def normalize_name(name: str) -> str:
    return (name or "").split("#")[0].strip().lower()


# ── Live Client ───────────────────────────────────────────────────────
class LiveClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False

    def events(self):
        try:
            r = self.session.get(f"{LIVE_BASE}/eventdata", timeout=2)
            r.raise_for_status()
            return r.json().get("Events", [])
        except requests.exceptions.ConnectionError:
            return None
        except requests.exceptions.RequestException as exc:
            log.debug("Live Client API no disponible: %s", exc)
            return None

    def active_player_name(self) -> str | None:
        try:
            r = self.session.get(f"{LIVE_BASE}/activeplayername", timeout=2)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            log.debug("No se pudo obtener el nombre del jugador activo: %s", exc)
            return None

    def player_list(self) -> list | None:
        try:
            r = self.session.get(f"{LIVE_BASE}/playerlist", timeout=2)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException as exc:
            log.debug("No se pudo obtener la lista de jugadores: %s", exc)
        return None


# ── Kill Tracker ──────────────────────────────────────────────────────
class KillTracker:
    def __init__(self):
        self.times: list[float] = []

    def add(self, game_time: float) -> int:
        self.times = [t for t in self.times if game_time - t <= MULTIKILL_WINDOW]
        self.times.append(game_time)
        return len(self.times)

    def reset(self):
        self.times.clear()


# ── OBS Replay ────────────────────────────────────────────────────────
class ObsReplay:
    def __init__(self):
        self.cl = obs.ReqClient(host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD, timeout=5)
        if not self.cl.get_replay_buffer_status().output_active:
            self.cl.start_replay_buffer()
        log.info("OBS Replay Buffer conectado.")

    def save(self):
        self.cl.save_replay_buffer()
        log.info("OBS: Replay guardado.")


# ── Debouncer ─────────────────────────────────────────────────────────
class MultikillDebouncer:
    def __init__(self, delay: float, callback):
        self.delay = delay
        self.callback = callback
        self.max_streak = 0
        self.timer: threading.Timer | None = None
        self.lock = threading.Lock()

    def trigger(self, streak: int):
        with self.lock:
            self.max_streak = max(self.max_streak, streak)
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(self.delay, self._fire)
            self.timer.daemon = True
            self.timer.start()

    def _fire(self):
        with self.lock:
            streak = self.max_streak
            self.max_streak = 0
            self.timer = None
        self.callback(streak)


# ── Scouting al inicio de partida ─────────────────────────────────────
def ejecutar_scouting(chat_bot, local_player_name: str):
    """Lee estado.json y envía al chat las stats del jugador local."""
    try:
        if not os.path.exists(ESTADO_FILE):
            log.debug("Scouting: estado.json aún no existe.")
            return False

        with open(ESTADO_FILE, encoding="utf-8") as f:
            state = json.load(f)

        from riot_api import RiotAPI
        msg = RiotAPI.format_rank_chat(state)
        chat_bot.send(msg)
        log.info("Scouting enviado al chat.")
        return True
    except Exception as exc:
        log.error("Error en scouting de sesión: %s", exc)
        return False


# ── Acción post-multikill ─────────────────────────────────────────────
ACTION_LOCK = threading.Lock()


def on_multikill_settled(streak: int, obs_client, clip_client, chat_bot):
    label = TRIGGER_STREAKS.get(streak, f"{streak}-Kill")
    log.info("🔥 %s confirmado. Ejecutando acciones...", label)

    with ACTION_LOCK:
        if obs_client:
            try:
                obs_client.save()
            except Exception as exc:
                log.error("OBS: error al guardar replay: %s", exc)

        if clip_client:
            try:
                url = clip_client.create_clip()
                if chat_bot:
                    chat_bot.send(f"🔥 {label}! Clip: {url}")
            except Exception as exc:
                log.error("Twitch clip: %s", exc)


# ── Build helpers ─────────────────────────────────────────────────────
def build_obs() -> ObsReplay | None:
    if not ENABLE_OBS or obs is None:
        if not ENABLE_OBS:
            log.info("OBS deshabilitado en config.")
        elif obs is None:
            log.warning("obsws-python no está instalado; OBS deshabilitado.")
        return None
    try:
        return ObsReplay()
    except Exception as exc:
        log.error("No se pudo conectar a OBS WebSocket: %s", exc)
        return None


# ── Bucle principal ───────────────────────────────────────────────────
def live_game_loop(obs_client, clip_client, chat_bot):
    """Hilo que monitorea la partida en curso y dispara clips ante multikills."""
    live = LiveClient()
    tracker = KillTracker()
    me = ""
    debouncer = MultikillDebouncer(
        SETTLE_DELAY,
        lambda streak: on_multikill_settled(streak, obs_client, clip_client, chat_bot),
    )

    log.info("Esperando partida de League of Legends...")
    in_game = False
    scouting_done = False
    seen_ids: set[int] = set()

    while True:
        try:
            events = live.events()

            if events is None:
                if in_game:
                    log.info("Partida finalizada. Esperando la siguiente...")
                    in_game = False
                    scouting_done = False
                    seen_ids = set()
                    tracker.reset()
                time.sleep(3)
                continue

            if not in_game:
                in_game = True
                seen_ids = {e["EventID"] for e in events}
                tracker.reset()
                me = normalize_name(live.active_player_name())
                if me:
                    log.info("Partida detectada. Jugador local: %s", me)

            # Scouting al inicio
            if in_game and not scouting_done and chat_bot and me:
                scouting_done = ejecutar_scouting(chat_bot, me)

            for ev in events:
                if ev["EventID"] in seen_ids:
                    continue
                seen_ids.add(ev["EventID"])

                if ev.get("EventName") != "ChampionKill":
                    continue
                if not me or normalize_name(ev.get("KillerName")) != me:
                    continue

                streak = tracker.add(ev["EventTime"])
                if streak in TRIGGER_STREAKS:
                    debouncer.trigger(streak)

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            break
        except Exception as exc:
            log.error("Error en el bucle de live_game: %s", exc)
            time.sleep(2)

