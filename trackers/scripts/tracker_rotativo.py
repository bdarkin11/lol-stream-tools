import requests
import time
import threading
import os
import json
from dotenv import load_dotenv

# ================= CONFIGURACIÓN =================
# Lee la Riot API key del .env unico y compartido (launcher/.env),
# el mismo que usa scout, para cambiarla en un solo lugar.
_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ENV_COMPARTIDO = os.path.join(_RAIZ, "launcher", ".env")
load_dotenv(_ENV_COMPARTIDO)

API_KEY = os.getenv("RIOT_API_KEY", "")
REGION_ACCOUNT = os.getenv("RIOT_REGION_ACCOUNT", "americas")
REGION_LOL = os.getenv("RIOT_REGION_LOL", "la2")
RIOT_ID = os.getenv("RIOT_GAME_NAME", "")
RIOT_TAG = os.getenv("RIOT_TAG_LINE", "")

CARPETA_OVERLAYS = os.getenv("TRACKER_OVERLAYS_DIR", os.path.join(_RAIZ, "trackers", "overlays"))
ARCHIVO_HTML = os.path.join(CARPETA_OVERLAYS, "overlay.html")
ARCHIVO_JSON = os.path.join(CARPETA_OVERLAYS, "overlay_rotativo_data.json")

TIEMPO_API = 30
TIEMPO_ROTACION = 7

# ================= VARIABLES GLOBALES =================
lp_anterior = None
mmr_inicial = None
wins_iniciales = None
losses_iniciales = None
latest_patch = "14.8.1"
match_cache = {}

datos_actuales = {
    "tier": "DIAMOND", "rank": "IV", "lp_actual": 47, "lp_viejo": 47,
    "wr": 54, "wins": 164, "losses": 139, "mmr": 2805, "mmr_diff": 0,
    "wins_hoy": 0, "losses_hoy": 0,
    "top3_html": "<span>-</span>",
    "partidas_html": "<span>-</span>",
    "kda_promedio": "0.0 / 0.0 / 0.0"
}

TABLA_MMR_BASE = {
    "IRON": 400, "BRONZE": 800, "SILVER": 1200,
    "GOLD": 1600, "PLATINUM": 2000, "EMERALD": 2400,
    "DIAMOND": 2800, "MASTER": 3300, "GRANDMASTER": 3800, "CHALLENGER": 4300
}
TABLA_DIVISION = {"IV": 0, "III": 100, "II": 200, "I": 300}

# Carga inicial de versión de Data Dragon
try:
    latest_patch = requests.get("https://ddragon.leagueoflegends.com/api/versions.json").json()[0]
except Exception as e:
    print(f"Error cargando Data Dragon: {e}")

# ================= 1. CÁLCULO DE MMR =================
def calcular_mmr_aproximado(tier, rank, puuid, headers):
    base = TABLA_MMR_BASE.get(tier, 2400) + TABLA_DIVISION.get(rank, 0)
    try:
        url_matches = f"https://{REGION_ACCOUNT}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&start=0&count=5"
        res = requests.get(url_matches, headers=headers)
        if res.status_code != 200:
            return base
        match_ids = res.json()
        if not isinstance(match_ids, list) or len(match_ids) == 0:
            return base
        ajuste_total = 0
        partidas_analizadas = 0
        for m_id in match_ids:
            if m_id not in match_cache:
                time.sleep(0.1)  # Pequeño delay para respetar Rate Limit
                res_m = requests.get(f"https://{REGION_ACCOUNT}.api.riotgames.com/lol/match/v5/matches/{m_id}", headers=headers)
                if res_m.status_code == 200:
                    match_cache[m_id] = res_m.json()
            
            if m_id in match_cache:
                match_data = match_cache[m_id]
                if 'info' in match_data:
                    for p in match_data['info']['participants']:
                        if p['puuid'] == puuid:
                            if p.get('win'): ajuste_total += 15
                            else: ajuste_total -= 10
                            partidas_analizadas += 1
        if partidas_analizadas > 0:
            base += int(ajuste_total / partidas_analizadas)
    except:
        pass
    return base

# ================= 2. GUARDADO DE DATOS JSON =================
def guardar_datos_json():
    tmp_json = ARCHIVO_JSON + ".tmp"
    try:
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(datos_actuales, f, ensure_ascii=False)
        os.replace(tmp_json, ARCHIVO_JSON)
    except Exception as e:
        pass

# ================= 3. GENERADOR DE BASE HTML =================
def generar_html_base():
    html = f"""<!DOCTYPE html>
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
                        <img id="rank-icon-img" class="rank-icon" src="" />
                        <div class="profile-info">
                            <span class="player-name" id="player-name-anim">{RIOT_ID} <span style="font-size:9px; background:#444; padding:1px 4px; border-radius:3px;">LAS</span></span>
                            <span class="rank-title" id="rank-title-anim">-</span>
                            <span class="lp-text" id="lp-text-anim">
                                <span id="lp-counter">0</span> LP
                                <span id="daily-record-text" class="daily-record-box">Hoy: 0W - 0L</span>
                            </span>
                        </div>
                    </div>
                    <div class="stats-right" id="stats-block">
                        <span class="season-title">V/D de Temporada</span>
                        <span class="record-box">
                            <span id="wins-season" class="win-color">0</span>-<span id="losses-season" class="loss-color">0</span>
                        </span>
                        <span id="wr-season" class="wr-text">0%</span>
                    </div>
                </div>
            </div>
           
            <div class="carousel-item" id="slide-2">
                <div class="slide2-container">
                    <div class="bottom-col">
                        <span class="bottom-label" style="margin-bottom: 0;">MMR</span>
                        <div style="display: flex; align-items: baseline; gap: 8px;">
                            <span id="mmr-value" class="highlight" style="font-size: 26px; font-weight: 900;">0</span>
                            <span id="mmr-trend">-</span>
                        </div>
                    </div>
                    <div class="bottom-col" style="border-left: 1px solid rgba(255,255,255,0.1); padding-left: 20px;">
                        <span class="bottom-label" style="font-size: 11px;">Top Campeones</span>
                        <div id="top3-container" style="display:flex; gap: 8px; margin-top:2px;">-</div>
                    </div>
                </div>
            </div>

            <div class="carousel-item" id="slide-3">
                <div class="slide3-container">
                    <div id="partidas-container" class="matches-row">
                        -
                    </div>
                    <div class="stats-right" style="min-width: 110px;">
                        <span class="season-title">KDA Promedio</span>
                        <span id="kda-value" class="record-box" style="font-size: 17px; color: #3498db;">0.0 / 0.0 / 0.0</span>
                    </div>
                </div>
            </div>

        </div>
    </div>
   
    <script>
        const items = document.querySelectorAll('.carousel-item');
        let current = 0;
        let carouselInterval = null;
        let currentLp = null;
        let isAnimating = false;

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
            if (carouselInterval) clearInterval(carouselInterval);
            carouselInterval = setInterval(() => {{
                if (isAnimating) return;
                items[current].classList.remove('active');
                current = (current + 1) % items.length;
                items[current].classList.add('active');
            }}, {TIEMPO_ROTACION * 1000});
        }}

        function ejecutarAnimacionNexo(lpViejo, lpNuevo) {{
            isAnimating = true;
            items[current].classList.remove('active');
            current = 0;
            items[current].classList.add('active');

            const profileBlock = document.getElementById('profile-block');
            const statsBlock = document.getElementById('stats-block');
            const playerName = document.getElementById('player-name-anim');
            const rankTitle = document.getElementById('rank-title-anim');
            const lpText = document.getElementById('lp-text-anim');
            const lpCounter = document.getElementById('lp-counter');
            const flashOverlay = document.getElementById('flash-overlay');

            const isWin = lpNuevo > lpViejo;
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
                animateValue(lpCounter, lpViejo, lpNuevo, 2500);
            }}, 2000);

            setTimeout(() => {{
                lpCounter.classList.remove(colorClass);
                flashOverlay.classList.remove(flashClass);
               
                playerName.style.opacity = '1';
                rankTitle.style.opacity = '1';
               
                profileBlock.style.transform = '';
                lpText.style.transform = '';
               
                statsBlock.classList.remove('hidden-for-anim');
                isAnimating = false;
            }}, 6000);
        }}

        async function actualizarOverlay() {{
            try {{
                const res = await fetch('./overlay_rotativo_data.json?v=' + Date.now());
                if (!res.ok) return;
                const data = await res.json();

                const tier = data.tier.toLowerCase();
                document.getElementById('rank-icon-img').src = `https://opgg-static.akamaized.net/images/medals_new/${{tier}}.png`;
                
                let textoRango = data.tier.charAt(0) + data.tier.slice(1).toLowerCase();
                if (!["MASTER", "GRANDMASTER", "CHALLENGER"].includes(data.tier)) {{
                    textoRango += " " + data.rank;
                }}
                document.getElementById('rank-title-anim').textContent = textoRango;
                document.getElementById('daily-record-text').textContent = `Hoy: ${{data.wins_hoy}}W - ${{data.losses_hoy}}L`;
                document.getElementById('wins-season').textContent = data.wins;
                document.getElementById('losses-season').textContent = data.losses;
                document.getElementById('wr-season').textContent = `${{data.wr}}%`;

                document.getElementById('mmr-value').textContent = data.mmr;
                
                let trendHtml = '<span style="color: #aaa; font-size: 14px; font-weight: bold;">-</span>';
                if (data.mmr_diff > 0) {{
                    trendHtml = `<span style="color: #2ecc71; font-size: 14px; font-weight: bold;">▲ +${{data.mmr_diff}}</span>`;
                }} else if (data.mmr_diff < 0) {{
                    trendHtml = `<span style="color: #e74c3c; font-size: 14px; font-weight: bold;">▼ ${{Math.abs(data.mmr_diff)}}</span>`;
                }}
                document.getElementById('mmr-trend').innerHTML = trendHtml;

                document.getElementById('top3-container').innerHTML = data.top3_html;
                document.getElementById('partidas-container').innerHTML = data.partidas_html;
                document.getElementById('kda-value').textContent = data.kda_promedio;

                if (currentLp === null) {{
                    currentLp = data.lp_actual;
                    document.getElementById('lp-counter').textContent = currentLp;
                    startCarousel();
                }} else if (currentLp !== data.lp_actual) {{
                    const lpViejo = currentLp;
                    currentLp = data.lp_actual;
                    ejecutarAnimacionNexo(lpViejo, currentLp);
                }}
            }} catch (e) {{
                console.error("Error leyendo datos del overlay:", e);
            }}
        }}

        actualizarOverlay();
        setInterval(actualizarOverlay, 3000);
    </script>
</body>
</html>"""
    try:
        with open(ARCHIVO_HTML, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        pass

# ================= 4. CONSULTA A LA API DE RIOT =================
def obtener_datos_riot():
    global lp_anterior, mmr_inicial, wins_iniciales, losses_iniciales, datos_actuales
    headers = {"X-Riot-Token": API_KEY}
   
    while True:
        try:
            url_account = f"https://{REGION_ACCOUNT}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{RIOT_ID}/{RIOT_TAG}"
            res_acc = requests.get(url_account, headers=headers)
           
            if res_acc.status_code in [401, 403]:
                print(f"[{time.strftime('%X')}] ❌ ERROR: API Key vencida o inválida (Status {res_acc.status_code}). Renovala en developer.riotgames.com")
                time.sleep(TIEMPO_API)
                continue
            elif res_acc.status_code != 200:
                print(f"[{time.strftime('%X')}] Error API Account, status {res_acc.status_code}")
                time.sleep(TIEMPO_API)
                continue
               
            puuid = res_acc.json()['puuid']
           
            url_league = f"https://{REGION_LOL}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
            ligas = requests.get(url_league, headers=headers).json()
            datos_ranked = next((liga for liga in ligas if type(liga) is dict and liga.get('queueType') == 'RANKED_SOLO_5x5'), None)
           
            if not datos_ranked:
                time.sleep(TIEMPO_API)
                continue

            tier = datos_ranked['tier']
            rank = datos_ranked['rank']
            lp_actual = datos_ranked['leaguePoints']
            wins = datos_ranked['wins']
            losses = datos_ranked['losses']
            wr = round((wins / (wins + losses)) * 100) if (wins + losses) > 0 else 0
           
            if wins_iniciales is None:
                wins_iniciales = wins
                losses_iniciales = losses
               
            wins_hoy = wins - wins_iniciales
            losses_hoy = losses - losses_iniciales

            if lp_anterior is None:
                lp_anterior = lp_actual
               
            mmr_estimado = calcular_mmr_aproximado(tier, rank, puuid, headers)
           
            if mmr_inicial is None:
                mmr_inicial = mmr_estimado
               
            mmr_diff = mmr_estimado - mmr_inicial
           
            url_matches = f"https://{REGION_ACCOUNT}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&start=0&count=10"
            match_ids = requests.get(url_matches, headers=headers).json()
           
            champ_counts = {}
            partidas_html = ""
            total_k, total_d, total_a = 0, 0, 0
            partidas_contadas = 0

            if isinstance(match_ids, list) and len(match_ids) > 0:
                for i, match_id in enumerate(match_ids):
                    if match_id not in match_cache:
                        time.sleep(0.1)
                        res_detalle = requests.get(f"https://{REGION_ACCOUNT}.api.riotgames.com/lol/match/v5/matches/{match_id}", headers=headers)
                        if res_detalle.status_code == 200:
                            match_cache[match_id] = res_detalle.json()
                        else:
                            continue
                           
                    match_data = match_cache[match_id]
                   
                    for participant in match_data['info']['participants']:
                        if participant['puuid'] == puuid:
                            campeon = participant.get('championName', 'Desconocido')
                            if campeon == "FiddleSticks": campeon = "Fiddlesticks"
                           
                            champ_counts[campeon] = champ_counts.get(campeon, 0) + 1
                           
                            if i < 5:
                                k = participant.get('kills', 0)
                                d = participant.get('deaths', 0)
                                a = participant.get('assists', 0)
                                total_k += k
                                total_d += d
                                total_a += a
                                partidas_contadas += 1
                               
                                victoria = participant.get('win')
                                color_borde = "#2ecc71" if victoria else "#e74c3c"
                                img_url = f"https://ddragon.leagueoflegends.com/cdn/{latest_patch}/img/champion/{campeon}.png"
                               
                                partidas_html += f"""
                                <div style="display: flex; flex-direction: column; align-items: center;">
                                    <img style="width:36px; height:36px; border-radius:50%; border: 2px solid {color_borde};" src="{img_url}" />
                                    <span style="margin-top:2px; font-size: 11px; font-weight: bold; color: white;">{k}/{d}/{a}</span>
                                </div>
                                """
                            break

            top_champs = sorted(champ_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            top3_html = ""
            for champ, count in top_champs:
                img_url = f"https://ddragon.leagueoflegends.com/cdn/{latest_patch}/img/champion/{champ}.png"
                top3_html += f'<img style="width:40px; height:40px; border-radius:50%;" src="{img_url}" title="{champ} ({count} partidas recientes)"/>'
           
            if top3_html == "": top3_html = "<span>-</span>"

            if partidas_contadas > 0:
                avg_k = round(total_k / partidas_contadas, 1)
                avg_d = round(total_d / partidas_contadas, 1)
                avg_a = round(total_a / partidas_contadas, 1)
                kda_promedio = f"{avg_k} / {avg_d} / {avg_a}"
            else:
                partidas_html = '<div style="width:100%; text-align:center; color:#888; font-size:12px;">Sin partidas recientes</div>'
                kda_promedio = "0.0 / 0.0 / 0.0"

            datos_actuales = {
                "tier": tier, "rank": rank, "lp_actual": lp_actual, "lp_viejo": lp_anterior,
                "wr": wr, "wins": wins, "losses": losses, "mmr": mmr_estimado, "mmr_diff": mmr_diff,
                "wins_hoy": wins_hoy, "losses_hoy": losses_hoy,
                "top3_html": top3_html, "partidas_html": partidas_html, "kda_promedio": kda_promedio
            }

            guardar_datos_json()
            print(f"[{time.strftime('%X')}] Datos actualizados. -> {lp_actual} LP (Hoy: {wins_hoy}W - {losses_hoy}L)")
           
            lp_anterior = lp_actual
           
        except Exception as e:
            print(f"[{time.strftime('%X')}] Error en ciclo API: {str(e)[:50]}")
       
        time.sleep(TIEMPO_API)

# ================= 5. INICIALIZACIÓN =================
os.makedirs(CARPETA_OVERLAYS, exist_ok=True)
generar_html_base()
guardar_datos_json()

thread_api = threading.Thread(target=obtener_datos_riot, daemon=True)
thread_api.start()

print("Tracker rotativo inicializado correctamente.")
while True:
    time.sleep(TIEMPO_ROTACION)