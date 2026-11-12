import requests
import time
import threading
import os
import json
from dotenv import load_dotenv

# ================= CONFIGURACIÓN =================
# Lee las credenciales del .env unico y compartido (launcher/.env).
_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ENV_COMPARTIDO = os.path.join(_RAIZ, "launcher", ".env")
load_dotenv(_ENV_COMPARTIDO)

API_KEY = os.getenv("RIOT_API_KEY", "")
REGION_ACCOUNT = os.getenv("RIOT_REGION_ACCOUNT", "americas")
REGION_TFT = os.getenv("RIOT_REGION_TFT", "la2")   # Codigo de plataforma en minuscula (na1, euw1, kr, br1, la1, la2...)
RIOT_ID = os.getenv("TFT_GAME_NAME") or os.getenv("RIOT_GAME_NAME", "")
RIOT_TAG = os.getenv("TFT_TAG_LINE") or os.getenv("RIOT_TAG_LINE", "")

CARPETA_OVERLAYS = os.getenv("TRACKER_OVERLAYS_DIR", os.path.join(_RAIZ, "trackers", "overlays"))
ARCHIVO_HTML = os.path.join(CARPETA_OVERLAYS, "overlay_tft_ingame.html")
ARCHIVO_JSON = os.path.join(CARPETA_OVERLAYS, "overlay_tft_data.json")
TIEMPO_API = 30

# ================= VARIABLES GLOBALES =================
wins_iniciales = None   # top4 al iniciar la sesión
losses_iniciales = None # bottom4 al iniciar la sesión
match_cache = {}
latest_patch = "14.8.1"
profile_icon_id = 1

datos_actuales = {
    "tier": "DIAMOND", "rank": "IV", "lp": 47,
    "wins_hoy": 0, "losses_hoy": 0,
    "historial_html": "<span>Buscando partidas...</span>",
    "profile_icon_id": 1
}

# ================= 1. CARGA INICIAL =================
try:
    latest_patch = requests.get("https://ddragon.leagueoflegends.com/api/versions.json").json()[0]
except Exception as e:
    print(f"Error cargando Data Dragon: {e}")

# ================= 2. MOTOR DE CALIFICACIÓN POR POSICIÓN =================
def obtener_calificacion_jugador(placement):
    """TFT ya entrega la posición final (1-8) por jugador, así que la calificación
    se arma directamente sobre ese valor en lugar de un score compuesto."""
    if placement == 1:
        return {"texto": "1st", "bg": "linear-gradient(0deg, rgba(255, 148, 25, 0.3), rgba(255, 148, 25, 0.3)), linear-gradient(0deg, rgba(12, 14, 18, 0.6), rgba(12, 14, 18, 0.6))", "color": "rgb(255, 148, 25)"}
    if placement == 8:
        return {"texto": "8th", "bg": "linear-gradient(0deg, rgba(255, 27, 72, 0.3), rgba(255, 27, 72, 0.3)), linear-gradient(0deg, rgba(12, 14, 18, 0.6), rgba(12, 14, 18, 0.6))", "color": "rgb(255, 27, 72)"}

    sufijos = {1: "st", 2: "nd", 3: "rd"}
    sufijo = sufijos.get(placement, "th")
    return {"texto": f"{placement}{sufijo}", "bg": "linear-gradient(0deg, rgba(255, 255, 255, 0.3), rgba(255, 255, 255, 0.3)), linear-gradient(0deg, rgba(12, 14, 18, 0.6), rgba(12, 14, 18, 0.6))", "color": "rgb(255, 255, 255)"}

# ================= 3. ACTUALIZACIÓN DE DATOS =================
def obtener_datos_riot():
    global wins_iniciales, losses_iniciales, datos_actuales, profile_icon_id
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
                print(f"[{time.strftime('%X')}] Error API Account, status {res_acc.status_code}: {res_acc.text[:150]}")
                time.sleep(TIEMPO_API)
                continue

            puuid = res_acc.json()['puuid']

            url_summoner = f"https://{REGION_TFT}.api.riotgames.com/tft/summoner/v1/summoners/by-puuid/{puuid}"
            res_sum = requests.get(url_summoner, headers=headers)
            if res_sum.status_code == 200:
                profile_icon_id = res_sum.json()['profileIconId']
                datos_actuales['profile_icon_id'] = profile_icon_id
            else:
                print(f"[{time.strftime('%X')}] Aviso: no se pudo leer el summoner TFT (status {res_sum.status_code}).")

            url_league = f"https://{REGION_TFT}.api.riotgames.com/tft/league/v1/by-puuid/{puuid}"
            res_league = requests.get(url_league, headers=headers)

            if res_league.status_code in [401, 403]:
                print(f"[{time.strftime('%X')}] ❌ ERROR: API Key vencida o inválida (Status {res_league.status_code}) consultando liga TFT.")
                time.sleep(TIEMPO_API)
                continue
            elif res_league.status_code != 200:
                print(f"[{time.strftime('%X')}] Error API League TFT, status {res_league.status_code}: {res_league.text}")
                time.sleep(TIEMPO_API)
                continue

            ligas = res_league.json()
            datos_ranked = next((l for l in ligas if type(l) is dict and l.get('queueType') == 'RANKED_TFT'), None)

            if datos_ranked:
                if wins_iniciales is None:
                    wins_iniciales, losses_iniciales = datos_ranked['wins'], datos_ranked['losses']

                datos_actuales['tier'] = datos_ranked['tier']
                datos_actuales['rank'] = datos_ranked['rank']
                datos_actuales['lp'] = datos_ranked['leaguePoints']
                datos_actuales['wins_hoy'] = datos_ranked['wins'] - wins_iniciales
                datos_actuales['losses_hoy'] = datos_ranked['losses'] - losses_iniciales
            else:
                print(f"[{time.strftime('%X')}] Aviso: la cuenta no tiene entrada RANKED_TFT todavía. Respuesta cruda: {ligas}")

            url_matches = f"https://{REGION_ACCOUNT}.api.riotgames.com/tft/match/v1/matches/by-puuid/{puuid}/ids?count=10"
            res_matches = requests.get(url_matches, headers=headers)
            if res_matches.status_code != 200:
                print(f"[{time.strftime('%X')}] Error API Match TFT, status {res_matches.status_code}: {res_matches.text[:150]}")
                match_ids = []
            else:
                match_ids = res_matches.json()

            historial_html = ""
            if isinstance(match_ids, list):
                indice_mostrado = 0
                for match_id in match_ids:
                    if match_id not in match_cache:
                        res_detalle = requests.get(f"https://{REGION_ACCOUNT}.api.riotgames.com/tft/match/v1/matches/{match_id}", headers=headers)
                        if res_detalle.status_code == 200:
                            match_cache[match_id] = res_detalle.json()

                    if match_id in match_cache:
                        m_data = match_cache[match_id]
                        if m_data.get('info', {}).get('queue_id') != 1100:
                            continue

                        for p in m_data['info']['participants']:
                            if p['puuid'] == puuid:
                                placement = p.get('placement', 8)

                                calif = obtener_calificacion_jugador(placement)
                                border_shadow = "rgb(0, 163, 255) 0px 0px 0px 2px inset" if placement <= 4 else "rgb(255, 27, 72) 0px 0px 0px 2px inset"
                                num_color = "rgb(255, 148, 25)" if placement == 1 else ("rgb(255, 27, 72)" if placement == 8 else "rgb(255, 255, 255)")
                                anim_delay = round(indice_mostrado * 0.05, 2)
                                indice_mostrado += 1

                                historial_html += f"""
                                <div class="flex flex-col items-center gap-[2px]" style="animation: 0.3s ease-out {anim_delay}s 1 normal both running championIconIn;">
                                    <div class="relative rounded-[5px]" style="border: 1px solid transparent;">
                                        <div class="overflow-hidden flex items-center justify-center rounded-[4px] h-[28px] w-[28px]" style="background: rgba(12, 14, 18, 0.6);">
                                            <span class="font-barlow font-black" style="font-size: 16px; color: {num_color};">{placement}</span>
                                        </div>
                                        <div class="pointer-events-none absolute inset-[-1px] rounded-[5px]" style="box-shadow: {border_shadow};"></div>
                                    </div>
                                    <span class="flex h-[14px] w-[30px] items-center justify-center rounded-[3px] pb-[1px] text-center font-barlow font-bold text-[10px]" style="background: {calif['bg']}; color: {calif['color']};">{calif['texto']}</span>
                                </div>
                                """
                            break
            datos_actuales['historial_html'] = historial_html

            guardar_datos_json()
            print(f"[{time.strftime('%X')}] Tracker TFT In-Game actualizado.")

        except Exception as e:
            print(f"[{time.strftime('%X')}] Error: {str(e)[:50]}")

        time.sleep(TIEMPO_API)

# ================= 4. GENERADOR Y GUARDADO DE ARCHIVOS =================
def guardar_datos_json():
    payload = {
        "riot_id": RIOT_ID,
        "riot_tag": RIOT_TAG,
        "tier": datos_actuales['tier'].upper(),
        "rank": datos_actuales['rank'],
        "lp": datos_actuales['lp'],
        "wins_hoy": datos_actuales['wins_hoy'],
        "losses_hoy": datos_actuales['losses_hoy'],
        "historial_html": datos_actuales['historial_html'],
        "profile_icon_id": datos_actuales.get('profile_icon_id', 1),
        "latest_patch": latest_patch
    }
    tmp_json = ARCHIVO_JSON + ".tmp"
    try:
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_json, ARCHIVO_JSON)
    except Exception as e:
        pass

def generar_html_base():
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800;900&display=swap">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            theme: {{
                extend: {{
                    fontFamily: {{
                        barlow: ['Barlow', 'sans-serif'],
                    }}
                }}
            }}
        }}
    </script>
    <style>
        body {{ background: transparent !important; margin: 0; overflow: hidden; }}
        html {{ background: transparent !important; overflow: hidden; }}

        @keyframes rankGlow {{
            0%, 100% {{ filter: drop-shadow(0 0 6px var(--glow-color-25)) drop-shadow(0 0 12px var(--glow-color-15)); }}
            50% {{ filter: drop-shadow(0 0 10px var(--glow-color-40)) drop-shadow(0 0 20px var(--glow-color-25)); }}
        }}

        .rank-border-glow {{
            animation: 3s ease-in-out 0s infinite normal none running rankGlow;
        }}

        @keyframes championIconIn {{
            0% {{ opacity: 0; transform: scale(0.8) translateY(4px); }}
            100% {{ opacity: 1; transform: scale(1) translateY(0); }}
        }}
    </style>
</head>
<body class="font-barlow text-white">
    <div class="flex items-start justify-start">
        <div class="relative w-fit pr-6 pl-[91.5px]" style="padding-top: 134px;">
            <div class="relative isolate flex flex-col gap-[9px]">

                <!-- TOP CONTAINER: Perfil + Info + Session -->
                <div class="relative flex min-h-[84px] items-stretch gap-[2px] rounded-lg border-2 border-transparent" style="background: linear-gradient(90deg, rgba(255, 255, 255, 0) 15%, rgba(255, 255, 255, 0.1) 73%) border-box border-box;">

                    <!-- Columna Izquierda: Medalla y Perfil -->
                    <!-- Proporciones calibradas contra el PNG real de wings_<tier>.png: el hueco circular
                         del medallón está centrado en 49.8% / 59.8% del lienzo y mide ~33% del ancho. -->
                    <div class="absolute top-[-110px] left-[-92.5px] z-10 h-[240px] w-[185px] flex-shrink-0">
                        <img id="rank-badge-img" alt="Rank Border" class="absolute inset-0 h-full w-full object-fill rank-border-glow" src="">
                        <img id="profile-icon-img" alt="Profile Icon" class="absolute h-[61px] w-[61px] rounded-full shadow-lg" style="left: 49.8%; top: 59.8%; transform: translate(-50%, -50%);" src="">
                    </div>

                    <!-- Columna Central: Nombre y Elo -->
                    <div class="flex flex-col font-bold rounded-l-[6px] pr-5 pl-[82px] justify-start" style="background: linear-gradient(90deg, rgba(14, 16, 21, 0) 0%, rgba(14, 16, 21, 0.8) 40%); min-width: 250px;">
                        <div class="flex flex-col gap-[8px] pt-[14px]">
                            <span class="whitespace-nowrap font-barlow font-bold leading-none" style="color: rgb(255, 255, 255); font-size: 18px;">
                                <span id="riot-id-text">{RIOT_ID}</span><span id="riot-tag-text" class="ml-1 font-barlow font-bold" style="color: rgba(255, 255, 255, 0.7); font-size: 14px;">#{RIOT_TAG}</span>
                            </span>

                            <div class="flex items-center gap-1 whitespace-nowrap mt-[-2px]">
                                <span id="rango-texto" class="font-barlow font-bold leading-none" style="font-size: 18px;">-</span>
                                <span id="lp-texto" class="font-barlow font-bold leading-none" style="color: rgb(255, 255, 255); font-size: 18px;">0LP</span>
                            </div>
                        </div>
                    </div>

                    <!-- Columna Derecha: Sesión -->
                    <div class="flex h-[80px] w-[115px] flex-shrink-0 flex-col items-center justify-center gap-1 px-[20px] py-[18px] rounded-r-[6px]" style="background-color: rgba(14, 16, 21, 0.8);">
                        <div class="text-center font-barlow font-bold text-[20px] leading-none" style="color: rgb(255, 255, 255);">
                            <span id="wins-texto">0</span><span class="mx-[2px]">-</span><span id="losses-texto">0</span>
                        </div>
                        <div class="font-barlow font-semibold text-[14px] leading-none" style="color: rgba(255, 255, 255, 0.7);">Session</div>
                    </div>
                </div>

                <!-- BOTTOM CONTAINER: Historial de Partidas -->
                <div class="relative z-10 ml-[-35px] flex min-h-[30px] items-start">
                    <div id="historial-container" class="flex gap-[3px]">
                        <span>Cargando datos...</span>
                    </div>
                </div>

            </div>
        </div>
    </div>

    <script>
        const coloresLiga = {{
            "IRON": "rgba(161, 157, 148",
            "BRONZE": "rgba(205, 127, 50",
            "SILVER": "rgba(189, 195, 199",
            "GOLD": "rgba(241, 196, 15",
            "PLATINUM": "rgba(26, 188, 156",
            "EMERALD": "rgba(46, 204, 113",
            "DIAMOND": "rgba(52, 152, 219",
            "MASTER": "rgba(224, 64, 251",
            "GRANDMASTER": "rgba(231, 76, 60",
            "CHALLENGER": "rgba(243, 156, 18"
        }};

        let ultimoTier = "";
        let ultimoHistorial = "";

        async function actualizarOverlay() {{
            try {{
                const res = await fetch('./overlay_tft_data.json?v=' + Date.now());
                if (!res.ok) return;
                const data = await res.json();

                const tier = data.tier || "DIAMOND";
                const rgbBase = coloresLiga[tier] || "rgba(224, 64, 251";

                document.documentElement.style.setProperty('--glow-color-25', rgbBase + ", 0.25)");
                document.documentElement.style.setProperty('--glow-color-15', rgbBase + ", 0.15)");
                document.documentElement.style.setProperty('--glow-color-40', rgbBase + ", 0.40)");

                const badgeImg = document.getElementById('rank-badge-img');
                if (ultimoTier !== tier) {{
                    badgeImg.src = `https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/wings/wings_${{tier.toLowerCase()}}.png`;
                    ultimoTier = tier;
                }}

                document.getElementById('profile-icon-img').src = `https://ddragon.leagueoflegends.com/cdn/${{data.latest_patch}}/img/profileicon/${{data.profile_icon_id}}.png`;

                const eloColor = rgbBase + ", 1)";
                const rangoElem = document.getElementById('rango-texto');
                rangoElem.style.color = eloColor;

                let textoRango = tier.charAt(0) + tier.slice(1).toLowerCase();
                if (!["MASTER", "GRANDMASTER", "CHALLENGER"].includes(tier)) {{
                    textoRango += " " + data.rank;
                }}
                rangoElem.textContent = textoRango;

                document.getElementById('lp-texto').textContent = `${{data.lp}}LP`;
                document.getElementById('wins-texto').textContent = `${{data.wins_hoy}}`;
                document.getElementById('losses-texto').textContent = `${{data.losses_hoy}}`;

                if (ultimoHistorial !== data.historial_html) {{
                    document.getElementById('historial-container').innerHTML = data.historial_html;
                    ultimoHistorial = data.historial_html;
                }}

            }} catch (e) {{
                console.error("Error leyendo datos:", e);
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

# ================= 5. INICIALIZADOR =================
os.makedirs(CARPETA_OVERLAYS, exist_ok=True)
generar_html_base()
guardar_datos_json()
threading.Thread(target=obtener_datos_riot, daemon=True).start()

print("Tracker TFT IN-GAME inicializado (Sincronización fluida sin reinicio de animaciones).")
while True:
    time.sleep(10)
