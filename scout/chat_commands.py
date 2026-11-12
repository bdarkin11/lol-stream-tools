"""
chat_commands.py – Lectura de comandos !rank, !rango, !elo desde el chat de Twitch.

Intenta primero Twitch EventSub por WebSocket. Si el token de la cuenta bot
no posee el scope específico de EventSub (user:read:chat, arrojando 403),
hace fallback transparente a Twitch IRC autenticado usando el token OAuth
guardado y el nombre de usuario real del bot (con scope chat:read).

Se eliminó definitivamente el usuario anónimo 'justinfan' y el pass falso.
"""
import json
import logging
import os
import socket
import time

import requests

try:
    import websocket
except ImportError:
    websocket = None

from config import ESTADO_FILE, TWITCH_CHANNEL

log = logging.getLogger("chat_commands")

EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws"
EVENTSUB_API_URL = "https://api.twitch.tv/helix/eventsub/subscriptions"
RANK_COMMANDS = {"!rank", "!rango", "!elo"}


def _read_state() -> dict | None:
    """Lee estado.json – la fuente de verdad para datos de rank."""
    try:
        if os.path.exists(ESTADO_FILE):
            with open(ESTADO_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        log.error("No se pudo leer estado.json: %s", exc)
    return None


def _format_rank(state: dict) -> str:
    from riot_api import RiotAPI
    return RiotAPI.format_rank_chat(state)


def _responder_comando(bot, palabra_comando: str, usuario: str = ""):
    log.info("Comando '%s' detectado de %s.", palabra_comando, usuario or "chat")
    state = _read_state()
    if state:
        try:
            bot.send(_format_rank(state))
            log.info("Comando respondido en chat con éxito.")
        except Exception as exc:
            log.error("No se pudo responder el comando en chat: %s", exc)
    else:
        log.warning("estado.json aún no contiene datos – no se puede responder.")


def _subscribe_chat_read(session_id: str, bot) -> bool:
    """Crea la suscripción EventSub para channel.chat.message."""
    bot.ensure_token()
    headers = {
        "Client-Id": bot.client_id,
        "Authorization": f"Bearer {bot.access_token}",
        "Content-Type": "application/json",
    }
    body = {
        "type": "channel.chat.message",
        "version": "1",
        "condition": {
            "broadcaster_user_id": bot.broadcaster_id,
            "user_id": bot.bot_user_id,
        },
        "transport": {
            "method": "websocket",
            "session_id": session_id,
        },
    }
    try:
        r = requests.post(EVENTSUB_API_URL, headers=headers, json=body, timeout=10)
        if r.status_code in (200, 202):
            log.info("Suscripción EventSub channel.chat.message creada correctamente.")
            return True
        elif r.status_code in (401, 403):
            log.warning(
                "EventSub no autorizado (status %d). Falta scope 'user:read:chat'. "
                "Cambiando a IRC autenticado con la cuenta bot...",
                r.status_code,
            )
            return False
        else:
            log.error("No se pudo crear suscripción EventSub (%d): %s", r.status_code, r.text[:300])
            return False
    except Exception as exc:
        log.error("Error al crear suscripción EventSub: %s", exc)
        return False


def _irc_authenticated_loop(bot):
    """Lector de chat vía IRC estándar autenticado con el token real de la cuenta bot."""
    channel_clean = TWITCH_CHANNEL.lower().lstrip("#")
    log.info("Iniciando lector de chat vía IRC autenticado para #%s...", channel_clean)

    while True:
        try:
            bot.ensure_token()
            nick = bot.bot_login or "bot"

            sock = socket.socket()
            sock.settimeout(300)
            sock.connect(("irc.chat.twitch.tv", 6667))

            # Autenticación con token real del bot (scope chat:read)
            sock.send(f"PASS oauth:{bot.access_token}\r\n".encode("utf-8"))
            sock.send(f"NICK {nick}\r\n".encode("utf-8"))
            sock.send(f"JOIN #{channel_clean}\r\n".encode("utf-8"))

            log.info("🎧 Lector IRC conectado como '%s' a #%s. Escuchando !rank, !rango, !elo...", nick, channel_clean)

            buffer = ""
            while True:
                try:
                    data = sock.recv(2048)
                except socket.timeout:
                    # Enviar PING para mantener viva la conexión
                    sock.send(b"PING :tmi.twitch.tv\r\n")
                    continue

                if not data:
                    log.warning("Conexión IRC de Twitch cerrada por el servidor. Reconectando en 5s...")
                    break

                buffer += data.decode("utf-8", errors="ignore")
                lines = buffer.split("\r\n")
                buffer = lines.pop()

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue

                    if line.startswith("PING"):
                        sock.send(b"PONG :tmi.twitch.tv\r\n")
                        continue

                    if " PRIVMSG " in line:
                        # Extraer remitente
                        user = line.split("!", 1)[0].lstrip(":") if "!" in line else ""
                        parts = line.split(" PRIVMSG ", 1)
                        if len(parts) > 1 and ":" in parts[1]:
                            msg = parts[1].split(":", 1)[1].strip().lower()
                            palabras = msg.split()
                            if palabras and palabras[0] in RANK_COMMANDS:
                                _responder_comando(bot, palabras[0], user)

            sock.close()

        except Exception as exc:
            log.error("Error en conexión IRC de chat: %s. Reintentando en 5s...", exc)
            time.sleep(5)


def chat_commands_loop(bot):
    """Hilo principal: conecta primero con EventSub WebSocket.
    Si el token no tiene autorización para EventSub, usa IRC autenticado con la cuenta bot.
    """
    if websocket is None:
        log.warning("websocket-client no disponible. Usando IRC autenticado...")
        _irc_authenticated_loop(bot)
        return

    while True:
        try:
            log.info("Conectando a EventSub WebSocket...")
            ws = websocket.WebSocket()
            ws.connect(EVENTSUB_WS_URL)

            # 1. Recibir bienvenida con session_id
            raw = ws.recv()
            welcome = json.loads(raw)
            session_id = welcome["payload"]["session"]["id"]
            keepalive_s = welcome["payload"]["session"].get("keepalive_timeout_seconds", 10)
            log.info("EventSub WebSocket conectado (session_id=%s).", session_id)

            # 2. Suscribirse a channel.chat.message
            exito = _subscribe_chat_read(session_id, bot)
            if not exito:
                ws.close()
                log.info("EventSub no disponible con los scopes actuales. Pasando a IRC autenticado...")
                _irc_authenticated_loop(bot)
                return

            # 3. Bucle de lectura de EventSub
            ws.settimeout(keepalive_s + 5)
            while True:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    log.warning("EventSub keepalive expirado – reconectando.")
                    break

                if not raw:
                    log.warning("EventSub WebSocket cerrado por el servidor – reconectando.")
                    break

                msg = json.loads(raw)
                msg_type = msg.get("metadata", {}).get("message_type", "")

                if msg_type == "session_keepalive":
                    continue

                if msg_type == "session_reconnect":
                    new_url = msg["payload"]["session"].get("reconnect_url")
                    if new_url:
                        log.info("EventSub solicitó reconexión a %s", new_url)
                        ws.close()
                        ws = websocket.WebSocket()
                        ws.connect(new_url)
                        raw2 = ws.recv()
                        welcome2 = json.loads(raw2)
                        session_id = welcome2["payload"]["session"]["id"]
                        keepalive_s = welcome2["payload"]["session"].get("keepalive_timeout_seconds", 10)
                        ws.settimeout(keepalive_s + 5)
                        _subscribe_chat_read(session_id, bot)
                    continue

                if msg_type == "notification":
                    sub_type = msg.get("payload", {}).get("subscription", {}).get("type", "")
                    if sub_type == "channel.chat.message":
                        event = msg["payload"]["event"]
                        text = event.get("message", {}).get("text", "").strip().lower()
                        words = text.split()
                        if words and words[0] in RANK_COMMANDS:
                            user = event.get("chatter_user_login", "")
                            _responder_comando(bot, words[0], user)

            ws.close()

        except Exception as exc:
            log.error("Error en EventSub WebSocket: %s. Reintentando en 5 s...", exc)

        time.sleep(5)
