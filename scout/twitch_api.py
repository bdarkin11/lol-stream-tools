"""
twitch_api.py – Cliente de Twitch Helix: clips y chat.

Gestiona dos pares de tokens OAuth (streamer y bot) en archivos separados
que se actualizan automáticamente cuando Twitch rota el refresh_token.

Nunca imprime valores de claves en logs.
"""
import json
import logging
import os
import time

import requests

from config import (
    TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, TWITCH_REFRESH_TOKEN,
    TWITCH_CHANNEL, BOT_REFRESH_TOKEN, BOT_USER_ID,
    STREAMER_TOKEN_FILE, BOT_TOKEN_FILE,
)

log = logging.getLogger("twitch_api")

AUTH_URL = "https://id.twitch.tv/oauth2/token"
USERS_URL = "https://api.twitch.tv/helix/users"
CLIPS_URL = "https://api.twitch.tv/helix/clips"
CHAT_URL = "https://api.twitch.tv/helix/chat/messages"


class ClipError(Exception):
    pass


# ── Helpers de tokens ─────────────────────────────────────────────────
def _load_refresh(path: str) -> str | None:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f).get("refresh_token")
        except Exception as exc:
            log.warning("No se pudo leer %s: %s", path, exc)
    return None


def _save_refresh(path: str, token: str):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"refresh_token": token}, f)
    except Exception as exc:
        log.error("No se pudo guardar %s: %s", path, exc)


# ── Cliente de clips (cuenta del streamer) ────────────────────────────
class TwitchClipClient:
    """Crea clips en Twitch usando la cuenta del streamer."""

    def __init__(self):
        self.client_id = TWITCH_CLIENT_ID
        self.client_secret = TWITCH_CLIENT_SECRET
        self.refresh_token = _load_refresh(STREAMER_TOKEN_FILE) or TWITCH_REFRESH_TOKEN
        self.channel = TWITCH_CHANNEL
        self.access_token: str | None = None
        self.expires_at = 0.0
        self.broadcaster_id: str | None = None
        self.session = requests.Session()

    def _refresh(self):
        r = self.session.post(
            AUTH_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=10,
        )
        if r.status_code != 200:
            raise ClipError(f"Streamer: no se pudo renovar el token ({r.status_code})")
        data = r.json()
        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]
        self.expires_at = time.time() + data["expires_in"] - 60
        _save_refresh(STREAMER_TOKEN_FILE, self.refresh_token)

    def _ensure_token(self):
        if not self.access_token or time.time() >= self.expires_at:
            self._refresh()

    def _headers(self):
        return {
            "Client-Id": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }

    def resolve_broadcaster(self):
        self._ensure_token()
        r = self.session.get(
            USERS_URL,
            params={"login": self.channel},
            headers=self._headers(),
            timeout=10,
        )
        r.raise_for_status()
        users = r.json().get("data", [])
        if not users:
            raise ClipError(f"Canal '{self.channel}' no encontrado")
        self.broadcaster_id = users[0]["id"]
        log.info("Broadcaster resuelto: %s (ID: %s)", self.channel, self.broadcaster_id)

    def create_clip(self, retries: int = 3) -> str:
        if self.broadcaster_id is None:
            self.resolve_broadcaster()

        for attempt in range(1, retries + 1):
            self._ensure_token()
            r = self.session.post(
                CLIPS_URL,
                params={"broadcaster_id": self.broadcaster_id},
                headers=self._headers(),
                timeout=10,
            )
            if r.status_code == 202:
                return r.json()["data"][0]["edit_url"]
            if r.status_code == 401:
                log.warning("Token de streamer expirado, renovando (intento %d).", attempt)
                self.access_token = None
                continue
            if r.status_code == 429:
                reset = float(r.headers.get("Ratelimit-Reset", time.time() + 10))
                wait = min(max(reset - time.time(), 1), 60)
                log.warning("Rate-limit de clips – esperando %.1f s", wait)
                time.sleep(wait)
                continue
            if r.status_code == 404:
                raise ClipError("El canal no está en vivo o los clips están deshabilitados")
            if r.status_code == 503:
                log.warning("Twitch 503 – reintentando en 2 s")
                time.sleep(2)
                continue
            raise ClipError(f"Error de Twitch {r.status_code}: {r.text[:200]}")
        raise ClipError("Se agotaron los reintentos creando el clip")


# ── Bot de chat (cuenta bot) ──────────────────────────────────────────
class TwitchChatBot:
    """Envía mensajes al chat y provee datos de autenticación para EventSub."""

    def __init__(self, broadcaster_id: str):
        self.client_id = TWITCH_CLIENT_ID
        self.client_secret = TWITCH_CLIENT_SECRET
        self.refresh_token = _load_refresh(BOT_TOKEN_FILE) or BOT_REFRESH_TOKEN or TWITCH_REFRESH_TOKEN
        self.bot_user_id = BOT_USER_ID
        self.bot_login: str | None = None
        self.broadcaster_id = broadcaster_id
        self.access_token: str | None = None
        self.expires_at = 0.0
        self.session = requests.Session()

    def _refresh(self):
        r = self.session.post(
            AUTH_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=10,
        )
        if r.status_code != 200:
            raise ClipError(f"Bot: no se pudo renovar el token ({r.status_code})")
        data = r.json()
        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]
        self.expires_at = time.time() + data["expires_in"] - 60
        _save_refresh(BOT_TOKEN_FILE, self.refresh_token)

        # Auto-detectar el user ID del token
        try:
            u_res = self.session.get(
                USERS_URL,
                headers={
                    "Client-Id": self.client_id,
                    "Authorization": f"Bearer {self.access_token}",
                },
                timeout=10,
            )
            if u_res.status_code == 200:
                u_data = u_res.json().get("data", [])
                if u_data:
                    self.bot_user_id = u_data[0]["id"]
                    self.bot_login = u_data[0].get("login")
                    log.info(
                        "🤖 Bot autenticado como '%s' (ID: %s)",
                        self.bot_login, self.bot_user_id,
                    )
        except Exception as exc:
            log.warning("No se pudo auto-verificar bot_user_id: %s", exc)

    def ensure_token(self):
        """Renueva el token si es necesario.  Público para que EventSub lo use."""
        if not self.access_token or time.time() >= self.expires_at:
            self._refresh()

    def send(self, message: str):
        self.ensure_token()
        r = self.session.post(
            CHAT_URL,
            headers={
                "Client-Id": self.client_id,
                "Authorization": f"Bearer {self.access_token}",
            },
            json={
                "broadcaster_id": self.broadcaster_id,
                "sender_id": self.bot_user_id,
                "message": message,
            },
            timeout=10,
        )
        if r.status_code != 200:
            log.error("Chat Helix error (%s): %s", r.status_code, r.text[:200])
            raise ClipError(f"Bot: no se pudo enviar ({r.status_code})")


# ── Constructores de conveniencia ─────────────────────────────────────
def build_clip_client() -> TwitchClipClient | None:
    """Crea el cliente de clips.  Retorna None si falla o Twitch está deshabilitado."""
    from config import ENABLE_TWITCH
    if not ENABLE_TWITCH:
        log.info("Twitch deshabilitado en config.")
        return None
    client = TwitchClipClient()
    try:
        client.resolve_broadcaster()
        return client
    except Exception as exc:
        log.error("No se pudo inicializar el cliente de clips: %s", exc)
        return None


def build_chat_bot(broadcaster_id: str) -> TwitchChatBot | None:
    """Crea el bot de chat.  Retorna None si no hay credenciales suficientes."""
    if not broadcaster_id:
        return None
    try:
        bot = TwitchChatBot(broadcaster_id)
        bot.ensure_token()
        return bot
    except Exception as exc:
        log.error("No se pudo inicializar el bot de chat: %s", exc)
        return None

