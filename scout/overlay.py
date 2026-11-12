"""
overlay.py – Genera overlay.html para OBS (fuente de navegador).

Lee los datos de ``estado.json`` (escrito por el módulo de datos en main.py)
y genera el HTML con la misma animación de LP y diseño del tracker original.

Correcciones aplicadas:
  #3  – Versión de Data Dragon dinámica (viene de estado.json).
  #4  – La etiqueta del jugador muestra RIOT_TAG_LINE del .env, no "LAS" fijo.
  #5  – "MMR" renombrado a "Índice" (ver nota en config.py).
  #9  – Recarga suave: el HTML se genera atómicamente (escritura a .tmp + os.replace)
        y el JS hace reload solo cada 20 s, sin cortar la animación de LP.
  #10 – DEBUG_ANIMATION como opción en config.py, desactivada por defecto.
  #11 – La ruta de overlay.html se toma de config.py.
"""
import json
import logging
import os

from config import (
    OVERLAY_PATH, ESTADO_FILE, DEBUG_ANIMATION,
    OVERLAY_ROTATION_SECS, RIOT_TAG_LINE,
)

log = logging.getLogger("overlay")


def _build_html(state: dict) -> str:
    """Genera el HTML completo del overlay a partir del estado."""
    tier = state.get("tier", "UNRANKED")
    rank = state.get("rank", "")
    lp_actual = state.get("lp", 0)
    lp_viejo = state.get("lp_prev", lp_actual)
    wr = state.get("wr", 0)
    wins = state.get("wins", 0)
    losses = state.get("losses", 0)

    # "Índice" (estimación propia, NO el MMR de Riot – ver config.py)
    indice = state.get("indice", 0)
    indice_diff = state.get("indice_diff", 0)

    wins_hoy = state.get("wins_hoy", 0)
    losses_hoy = state.get("losses_hoy", 0)

    game_name = state.get("game_name", "Jugador")
    tag_line = state.get("tag_line", RIOT_TAG_LINE)
    ddragon = state.get("ddragon_version", "14.8.1")
    top_champs = state.get("top_champs", [])
    partidas = state.get("partidas", [])
    kda_promedio = state.get("kda_promedio", "0.0 / 0.0 / 0.0")

    rank_img = f"https://opgg-static.akamaized.net/images/medals_new/{tier.lower()}.png"
    tiempo_ms = OVERLAY_ROTATION_SECS * 1000
    test_mode_str = "true" if DEBUG_ANIMATION else "false"

    # Trend del índice
    if indice_diff > 0:
        trend_html = f'<span style="color: #2ecc71; font-size: 14px; font-weight: bold;">▲ +{indice_diff}</span>'
    elif indice_diff < 0:
        trend_html = f'<span style="color: #e74c3c; font-size: 14px; font-weight: bold;">▼ {abs(indice_diff)}</span>'
    else:
        trend_html = '<span style="color: #aaa; font-size: 14px; font-weight: bold;">-</span>'

    # Top campeones HTML
    top3_html = ""
    for champ in top_champs:
        cname = champ["name"]
        ccount = champ["count"]
        img = f"https://ddragon.leagueoflegends.com/cdn/{ddragon}/img/champion/{cname}.png"
        top3_html += f'<img style="width:40px; height:40px; border-radius:50%;" src="{img}" title="{cname} ({ccount} partidas recientes)"/>'
    if not top3_html:
        top3_html = "<span>-</span>"

    # Partidas recientes HTML
    partidas_html = ""
    for p in partidas:
        color_borde = "#2ecc71" if p["win"] else "#e74c3c"
        img = f"https://ddragon.leagueoflegends.com/cdn/{ddragon}/img/champion/{p['champion']}.png"
        partidas_html += f"""
        <div style="display: flex; flex-direction: column; align-items: center;">
            <img style="width:36px; height:36px; border-radius:50%; border: 2px solid {color_borde};" src="{img}" />
            <span style="margin-top:2px; font-size: 11px; font-weight: bold; color: white;">{p['kills']}/{p['deaths']}/{p['assists']}</span>
        </div>
        """
    if not partidas_html:
        partidas_html = '<div style="width:100%; text-align:center; color:#888; font-size:12px;">Sin partidas recientes</div>'

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@700;900&display=swap');
            body {{
                margin: 0; padding: 0;
                font-family: 'Montserrat', sans-serif;
                color: white; text-shadow: 2px 2px 4px rgba(0,0,0,0.8);
                overflow: hidden; display: flex; align-items: center; height: 100vh;
                background: transparent;
            }}

            .container-box {{
                position: relative;
                width: 440px;
                height: 95px;
                display: flex;
                flex-direction: column;
                justify-content: center;
                padding: 10px 16px;
                box-sizing: border-box;
                background: rgba(5, 5, 12, 0.75);
                border: 2px solid rgba(255, 255, 255, 0.4);
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 0 25px rgba(255, 255, 255, 0.15), inset 0 0 15px rgba(52, 152, 219, 0.1);
            }}

            #flash-overlay {{
                position: absolute;
                top: 0; left: 0; right: 0; bottom: 0;
                z-index: 5;
                opacity: 0;
                pointer-events: none;
                mix-blend-mode: screen;
            }}

            @keyframes nexusFlashWin {{
                0% {{ opacity: 0; background: radial-gradient(circle, rgba(52, 152, 219, 0) 0%, transparent 100%); }}
                10% {{ opacity: 1; background: radial-gradient(circle, rgba(52, 152, 219, 0.85) 0%, transparent 80%); box-shadow: inset 0 0 100px rgba(52, 152, 219, 0.9); }}
                100% {{ opacity: 0; background: radial-gradient(circle, rgba(52, 152, 219, 0) 0%, transparent 100%); }}
            }}

            @keyframes nexusFlashLoss {{
                0% {{ opacity: 0; background: radial-gradient(circle, rgba(231, 76, 60, 0) 0%, transparent 100%); }}
                10% {{ opacity: 1; background: radial-gradient(circle, rgba(231, 76, 60, 0.85) 0%, transparent 80%); box-shadow: inset 0 0 100px rgba(231, 76, 60, 0.9); }}
                100% {{ opacity: 0; background: radial-gradient(circle, rgba(231, 76, 60, 0) 0%, transparent 100%); }}
            }}

            .flash-win-anim {{ animation: nexusFlashWin 2s ease-out forwards; }}
            .flash-loss-anim {{ animation: nexusFlashLoss 2s ease-out forwards; }}

            .container-box::after {{
                content: '';
                position: absolute;
                top: -50%; left: -75%;
                width: 50%; height: 200%;
                background: linear-gradient(
                    to right,
                    rgba(255, 255, 255, 0) 0%,
                    rgba(255, 255, 255, 0.4) 50%,
                    rgba(255, 255, 255, 0) 100%
                );
                transform: rotate(25deg);
                animation: shine 10s infinite;
            }}

            @keyframes shine {{
                0% {{ left: -75%; top: -50%; }}
                20% {{ left: 125%; top: 50%; }}
                100% {{ left: 125%; top: 50%; }}
            }}

            .carousel-item {{
                display: none;
                flex-direction: column;
                justify-content: center;
                width: 100%;
                height: 100%;
                animation: fadeIn 0.4s forwards;
            }}
            .carousel-item.active {{ display: flex; }}
            @keyframes fadeIn {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}

            .top-section {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                width: 100%;
                height: 100%;
            }}

            .profile-left {{
                display: flex;
                align-items: center;
                gap: 12px;
                transition: transform 1s cubic-bezier(0.25, 0.8, 0.25, 1);
                transform-origin: center left;
                z-index: 10;
            }}

            .stats-right {{
                transition: opacity 0.5s ease;
                text-align: right;
                display: flex;
                flex-direction: column;
                align-items: flex-end;
                justify-content: center;
            }}

            .hidden-for-anim {{
                opacity: 0 !important;
                pointer-events: none;
            }}

            .rank-icon {{ height: 68px; filter: drop-shadow(0 0 8px rgba(52, 152, 219, 0.6)); }}

            .profile-info {{ display: flex; flex-direction: column; position: relative; }}

            #lp-text-anim {{
                display: flex;
                align-items: center;
                transform-origin: left center;
                transition: transform 1s cubic-bezier(0.25, 0.8, 0.25, 1);
            }}

            .player-name {{ font-size: 12px; color: #aaa; font-weight: 700; transition: opacity 0.5s ease; }}
            .rank-title {{ font-size: 22px; font-weight: 900; color: #ffffff; line-height: 1.1; transition: opacity 0.5s ease; }}

            .lp-text {{
                font-size: 18px;
                color: #bdc3c7;
                font-weight: 900;
                margin-top: 4px;
            }}

            #lp-counter {{
                font-size: 18px;
                display: inline-block;
                color: #3498db;
                transition: color 0.3s ease, text-shadow 0.3s ease;
                margin-right: 4px;
            }}

            .daily-record-box {{
                background-color: rgba(40, 40, 40, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 3px 10px;
                font-size: 16px;
                color: #ecf0f1;
                margin-left: 12px;
                font-weight: bold;
                text-shadow: none;
            }}

            .color-win {{
                color: #2ecc71 !important;
                text-shadow: 0 0 20px rgba(46, 204, 113, 1) !important;
            }}
            .color-loss {{
                color: #e74c3c !important;
                text-shadow: 0 0 20px rgba(231, 76, 60, 1) !important;
            }}

            .season-title {{ font-size: 12px; color: #aaa; }}
            .record-box {{ font-size: 18px; font-weight: 900; line-height: 1.1; }}
            .win-color {{ color: #2ecc71; }}
            .loss-color {{ color: #e74c3c; }}
            .wr-text {{ font-size: 13px; color: #3498db; font-weight: 700; }}

            .slide2-container {{ display: flex; width: 100%; height: 100%; align-items: center; justify-content: space-around; }}
            .bottom-col {{ display: flex; flex-direction: column; align-items: center; text-align: center; }}
            .bottom-label {{ color: #aaa; font-size: 12px; margin-bottom: 2px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; }}
            .highlight {{ color: #3498db; }}

            .slide3-container {{ display: flex; width: 100%; height: 100%; align-items: center; justify-content: space-between; }}
            .matches-row {{ display: flex; gap: 12px; align-items: center; }}
        </style>
    </head>
    <body>
        <div class="container-box">
            <div id="flash-overlay"></div>

            <div id="carousel" style="width: 100%; height: 100%;">

                <div class="carousel-item active" id="slide-1">
                    <div class="top-section">
                        <div class="profile-left" id="profile-block">
                            <img class="rank-icon" src="{rank_img}" />
                            <div class="profile-info">
                                <span class="player-name" id="player-name-anim">{game_name} <span style="font-size:9px; background:#444; padding:1px 4px; border-radius:3px;">{tag_line}</span></span>
                                <span class="rank-title" id="rank-title-anim">{tier.capitalize()} {rank}</span>
                                <span class="lp-text" id="lp-text-anim">
                                    <span id="lp-counter">{lp_viejo}</span> LP
                                    <span class="daily-record-box">Hoy: {wins_hoy}W - {losses_hoy}L</span>
                                </span>
                            </div>
                        </div>
                        <div class="stats-right" id="stats-block">
                            <span class="season-title">V/D de Temporada</span>
                            <span class="record-box">
                                <span class="win-color">{wins}</span>-<span class="loss-color">{losses}</span>
                            </span>
                            <span class="wr-text">{wr}%</span>
                        </div>
                    </div>
                </div>

                <!-- Slide 2: Índice + Top Campeones -->
                <!-- NOTA: "Índice" es una estimación propia, NO el MMR interno de Riot Games. -->
                <div class="carousel-item" id="slide-2">
                    <div class="slide2-container">
                        <div class="bottom-col">
                            <span class="bottom-label" style="margin-bottom: 0;">Índice</span>
                            <div style="display: flex; align-items: baseline; gap: 8px;">
                                <span class="highlight" style="font-size: 26px; font-weight: 900;">{indice}</span>
                                {trend_html}
                            </div>
                        </div>
                        <div class="bottom-col" style="border-left: 1px solid rgba(255,255,255,0.1); padding-left: 20px;">
                            <span class="bottom-label" style="font-size: 11px;">Top Campeones</span>
                            <div style="display:flex; gap: 8px; margin-top:2px;">{top3_html}</div>
                        </div>
                    </div>
                </div>

                <!-- Slide 3: Últimas partidas + KDA -->
                <div class="carousel-item" id="slide-3">
                    <div class="slide3-container">
                        <div class="matches-row">
                            {partidas_html}
                        </div>
                        <div class="stats-right" style="min-width: 110px;">
                            <span class="season-title">KDA Promedio</span>
                            <span class="record-box" style="font-size: 17px; color: #3498db;">{kda_promedio}</span>
                        </div>
                    </div>
                </div>

            </div>
        </div>

        <script>
            // Recarga suave: 20 s es suficiente para que la animación LP termine
            // antes de que el browser source refresque el HTML.
            setTimeout(() => location.reload(), 20000);

            const items = document.querySelectorAll('.carousel-item');
            let current = 0;

            let lpViejo = {lp_viejo};
            let lpActual = {lp_actual};
            const testMode = {test_mode_str};

            if (testMode) {{
                lpViejo = Math.max(0, lpActual - 21);
            }}

            function animateValue(obj, start, end, duration) {{
                let startTimestamp = null;
                const step = (timestamp) => {{
                    if (!startTimestamp) startTimestamp = timestamp;
                    const progress = Math.min((timestamp - startTimestamp) / duration, 1);
                    obj.innerHTML = Math.floor(progress * (end - start) + start);
                    if (progress < 1) window.requestAnimationFrame(step);
                }};
                window.requestAnimationFrame(step);
            }}

            function startCarousel() {{
                setInterval(() => {{
                    items[current].classList.remove('active');
                    current = (current + 1) % items.length;
                    items[current].classList.add('active');
                }}, {tiempo_ms});
            }}

            if (lpViejo !== lpActual) {{
                const profileBlock = document.getElementById('profile-block');
                const statsBlock = document.getElementById('stats-block');
                const playerName = document.getElementById('player-name-anim');
                const rankTitle = document.getElementById('rank-title-anim');
                const lpText = document.getElementById('lp-text-anim');
                const lpCounter = document.getElementById('lp-counter');
                const flashOverlay = document.getElementById('flash-overlay');

                const isWin = lpActual > lpViejo;
                const colorClass = isWin ? 'color-win' : 'color-loss';
                const flashClass = isWin ? 'flash-win-anim' : 'flash-loss-anim';

                setTimeout(() => {{
                    statsBlock.classList.add('hidden-for-anim');
                    playerName.style.opacity = '0';
                    rankTitle.style.opacity = '0';
                    profileBlock.style.transform = 'translateX(50px) scale(1.6)';
                    lpText.style.transform = 'translateY(-22px) scale(1.1)';
                }}, 500);

                setTimeout(() => {{
                    flashOverlay.classList.add(flashClass);
                    lpCounter.classList.add(colorClass);
                    animateValue(lpCounter, lpViejo, lpActual, 2500);
                }}, 2000);

                setTimeout(() => {{
                    lpCounter.classList.remove(colorClass);
                    flashOverlay.classList.remove(flashClass);
                    playerName.style.opacity = '1';
                    rankTitle.style.opacity = '1';
                    profileBlock.style.transform = '';
                    lpText.style.transform = '';
                    statsBlock.classList.remove('hidden-for-anim');
                }}, 6000);

                setTimeout(() => {{
                    startCarousel();
                }}, 7000);

            }} else {{
                document.getElementById("lp-counter").innerHTML = lpActual;
                startCarousel();
            }}
        </script>
    </body>
    </html>
    """
    return html


def generate_overlay():
    """Lee estado.json y genera overlay.html de forma atómica."""
    if not os.path.exists(ESTADO_FILE):
        log.debug("estado.json no existe todavía – overlay no generado.")
        return False

    try:
        with open(ESTADO_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except Exception as exc:
        log.error("No se pudo leer estado.json para el overlay: %s", exc)
        return False

    html = _build_html(state)

    # Escritura atómica: .tmp → replace (corrección #9)
    tmp_path = OVERLAY_PATH + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp_path, OVERLAY_PATH)
        return True
    except Exception as exc:
        log.error("No se pudo escribir overlay.html: %s", exc)
        return False

