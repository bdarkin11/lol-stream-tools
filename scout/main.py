"""
main.py – Punto de entrada del proyecto Scout.

Arranca todos los módulos en hilos:
  1. Hilo de datos: consulta la API de Riot cada 30 s y escribe estado.json.
  2. Hilo de overlay: regenera overlay.html tras cada actualización de estado.
  3. Hilo de live_game: detecta multikills y dispara clips/OBS.
  4. Hilo de chat_commands: lee !rank / !rango / !elo vía EventSub WebSocket.
"""
import json
import logging
import os
import sys
import threading
import time

from config import (
    RIOT_API_KEY, RIOT_GAME_NAME, RIOT_TAG_LINE,
    ENABLE_TWITCH, DATA_POLL_INTERVAL, ESTADO_FILE, OVERLAY_PATH,
)

log = logging.getLogger("main")


def data_loop(riot_api):
    """Hilo que consulta la API de Riot cada DATA_POLL_INTERVAL segundos y
    escribe estado.json.  Es el único consumidor de la API de Riot
    (corrección #8)."""
    prev_lp = None
    indice_inicial = None
    wins_iniciales = None
    losses_iniciales = None

    while True:
        try:
            state = riot_api.build_full_state(
                RIOT_GAME_NAME, RIOT_TAG_LINE,
                prev_lp, indice_inicial,
                wins_iniciales, losses_iniciales,
            )
            if state:
                # Persistir para overlay y chat_commands
                tmp = ESTADO_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False)
                os.replace(tmp, ESTADO_FILE)

                # Actualizar variables de sesión para la próxima iteración
                prev_lp = state["lp"]
                indice_inicial = state["indice_inicial"]
                wins_iniciales = state["wins_iniciales"]
                losses_iniciales = state["losses_iniciales"]

                log.info(
                    "Estado actualizado → %s %s %d LP (Hoy: %dW-%dL)",
                    state["tier"].capitalize(), state["rank"], state["lp"],
                    state["wins_hoy"], state["losses_hoy"],
                )

                # Regenerar overlay
                from overlay import generate_overlay
                if generate_overlay():
                    log.info("overlay.html regenerado en %s", OVERLAY_PATH)
            else:
                log.warning("No se pudieron obtener datos de Riot – se reintentará.")

        except Exception as exc:
            log.error("Error en data_loop: %s", exc)

        time.sleep(DATA_POLL_INTERVAL)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Validaciones ──────────────────────────────────────────────────
    if not RIOT_API_KEY:
        log.error("❌ Falta RIOT_API_KEY en el .env. Abortando.")
        sys.exit(1)
    if not RIOT_GAME_NAME:
        log.error("❌ Falta RIOT_GAME_NAME en el .env. Abortando.")
        sys.exit(1)
    if not RIOT_TAG_LINE:
        log.error("❌ Falta RIOT_TAG_LINE en el .env. Abortando.")
        sys.exit(1)

    # ── API de Riot (consumidor único – corrección #8) ────────────────
    from riot_api import RiotAPI
    riot_api = RiotAPI()

    # ── Twitch ────────────────────────────────────────────────────────
    clip_client = None
    chat_bot = None

    if ENABLE_TWITCH:
        from twitch_api import build_clip_client, build_chat_bot
        clip_client = build_clip_client()
        if clip_client and clip_client.broadcaster_id:
            chat_bot = build_chat_bot(clip_client.broadcaster_id)
        if not clip_client:
            log.warning("⚠️  No se pudo conectar a Twitch – clips deshabilitados.")
        if not chat_bot:
            log.warning("⚠️  No se pudo inicializar el bot de chat – comandos deshabilitados.")
    else:
        log.info("Twitch deshabilitado (ENABLE_TWITCH=0).")

    # ── OBS ───────────────────────────────────────────────────────────
    from live_game import build_obs
    obs_client = build_obs()

    # ── Hilo 1: Datos de Riot → estado.json ───────────────────────────
    t_data = threading.Thread(target=data_loop, args=(riot_api,), daemon=True, name="data-loop")
    t_data.start()

    # ── Hilo 2: Live Game (multikills) ────────────────────────────────
    from live_game import live_game_loop
    t_live = threading.Thread(
        target=live_game_loop,
        args=(obs_client, clip_client, chat_bot),
        daemon=True,
        name="live-game",
    )
    t_live.start()

    # ── Hilo 3: Chat Commands (EventSub WebSocket) ────────────────────
    if chat_bot:
        from chat_commands import chat_commands_loop
        t_chat = threading.Thread(
            target=chat_commands_loop,
            args=(chat_bot,),
            daemon=True,
            name="chat-commands",
        )
        t_chat.start()

    # ── Mantener vivo el proceso principal ────────────────────────────
    log.info("="*50)
    log.info("🚀 Scout iniciado correctamente.")
    log.info("   Datos de Riot: cada %d s → estado.json", DATA_POLL_INTERVAL)
    log.info("   Overlay: %s", OVERLAY_PATH)
    log.info("   Live Game: detectando multikills...")
    if chat_bot:
        log.info("   Chat: escuchando !rank, !rango, !elo (EventSub WebSocket)")
    log.info("="*50)

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Detenido por el usuario.")


if __name__ == "__main__":
    main()

