import requests
import time
import threading
import os
import json
from dotenv import load_dotenv

# =====================================================================
#  TRACKER IN-GAME
#  Overlay para OBS con el rango, LP y partidas de la sesion.
#  Tema "Iris" con video warp de fondo y efecto glitch en las rotaciones.
#  Necesita fondo_warp.webm / fondo_warp.mp4 en trackers/overlays.
#  Escribe overlay_ingame.html y overlay_data.json en trackers/overlays.
# =====================================================================

# ================= CONFIGURACIÓN =================
# Lee la Riot API key del .env unico y compartido (launcher/.env).
_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ENV_COMPARTIDO = os.path.join(_RAIZ, "launcher", ".env")
load_dotenv(_ENV_COMPARTIDO)

API_KEY = os.getenv("RIOT_API_KEY", "")
REGION_ACCOUNT = os.getenv("RIOT_REGION_ACCOUNT", "americas")
REGION_LOL = os.getenv("RIOT_REGION_LOL", "la2")
RIOT_ID = os.getenv("RIOT_GAME_NAME", "")
RIOT_TAG = os.getenv("RIOT_TAG_LINE", "")

CARPETA_OVERLAYS = os.getenv("TRACKER_OVERLAYS_DIR", os.path.join(_RAIZ, "trackers", "overlays"))
ARCHIVO_HTML = os.path.join(CARPETA_OVERLAYS, "overlay_ingame.html")
ARCHIVO_JSON = os.path.join(CARPETA_OVERLAYS, "overlay_data.json")
TIEMPO_API = 30
CANTIDAD_PARTIDAS = 10

# Base de LP por liga y division, para contar ascensos/descensos en los LP de la sesion
LP_BASE_LIGA = {"IRON": 0, "BRONZE": 400, "SILVER": 800, "GOLD": 1200, "PLATINUM": 1600,
                "EMERALD": 2000, "DIAMOND": 2400, "MASTER": 2800, "GRANDMASTER": 2800, "CHALLENGER": 2800}
LP_BASE_DIVISION = {"IV": 0, "III": 100, "II": 200, "I": 300}

# ================= VARIABLES GLOBALES =================
wins_iniciales = None
losses_iniciales = None
lp_inicial_abs = None
match_cache = {}
latest_patch = "14.8.1"

datos_actuales = {
    "tier": "DIAMOND", "rank": "IV", "lp": 47,
    "wins_hoy": 0, "losses_hoy": 0, "lp_hoy": 0,
    "wins_temporada": 0, "losses_temporada": 0,
    "historial": [],
    "historial_html": "<span>Buscando partidas...</span>",
    "profile_icon_id": 1
}

# ================= 1. CARGA INICIAL =================
try:
    latest_patch = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=10).json()[0]
except Exception as e:
    print(f"Error cargando Data Dragon: {e}")

# ================= 2. MOTOR DE CÁLCULO MVP (igual que la v2) =================
def obtener_calificacion_jugador(match_data, target_puuid):
    participantes = match_data['info']['participants']
    puntajes = []

    for p in participantes:
        kills, deaths, assists = p.get('kills', 0), p.get('deaths', 0), p.get('assists', 0)
        oro = p.get('goldEarned', 0) / 1000
        dano = p.get('totalDamageDealtToChampions', 0) / 1000
        mitigacion = p.get('damageSelfMitigated', 0) / 1500
        vision = p.get('visionScore', 0) * 1.2
        farm = (p.get('totalMinionsKilled', 0) + p.get('neutralMinionsKilled', 0)) * 0.1

        score = (kills * 2) + (assists * 1.5) - (deaths * 2) + oro + dano + mitigacion + vision + farm
        puntajes.append({'puuid': p['puuid'], 'win': p['win'], 'score': score})

    puntajes.sort(key=lambda x: x['score'], reverse=True)

    mvp_puuid = next((p['puuid'] for p in puntajes if p['win']), None)
    ace_puuid = next((p['puuid'] for p in puntajes if not p['win']), None)

    posicion_global = 1
    for p in puntajes:
        if p['puuid'] == target_puuid:
            if p['puuid'] == mvp_puuid:
                return "MVP"
            if p['puuid'] == ace_puuid:
                return "ACE"
            sufijos = {1: "st", 2: "nd", 3: "rd"}
            return f"{posicion_global}{sufijos.get(posicion_global, 'th')}"
        posicion_global += 1
    return "-"

def lp_absoluto(tier, rank, lp):
    return LP_BASE_LIGA.get(tier, 0) + LP_BASE_DIVISION.get(rank, 0) + (lp or 0)

# ================= 3. ACTUALIZACIÓN DE DATOS =================
def obtener_datos_riot():
    global wins_iniciales, losses_iniciales, lp_inicial_abs
    headers = {"X-Riot-Token": API_KEY}

    while True:
        try:
            url_account = f"https://{REGION_ACCOUNT}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{RIOT_ID}/{RIOT_TAG}"
            res_acc = requests.get(url_account, headers=headers, timeout=15)
            if res_acc.status_code != 200:
                print(f"[{time.strftime('%X')}] Riot respondio {res_acc.status_code} (revisa la API key en launcher\\.env)")
                time.sleep(TIEMPO_API); continue
            puuid = res_acc.json()['puuid']

            url_summoner = f"https://{REGION_LOL}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
            res_sum = requests.get(url_summoner, headers=headers, timeout=15)
            if res_sum.status_code == 200:
                datos_actuales['profile_icon_id'] = res_sum.json()['profileIconId']

            url_league = f"https://{REGION_LOL}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
            ligas = requests.get(url_league, headers=headers, timeout=15).json()
            datos_ranked = None
            if isinstance(ligas, list):
                datos_ranked = next((l for l in ligas if isinstance(l, dict) and l.get('queueType') == 'RANKED_SOLO_5x5'), None)

            if datos_ranked:
                tier, rank, lp = datos_ranked['tier'], datos_ranked['rank'], datos_ranked['leaguePoints']
                if wins_iniciales is None:
                    wins_iniciales, losses_iniciales = datos_ranked['wins'], datos_ranked['losses']
                    lp_inicial_abs = lp_absoluto(tier, rank, lp)

                datos_actuales['tier'] = tier
                datos_actuales['rank'] = rank
                datos_actuales['lp'] = lp
                datos_actuales['wins_hoy'] = datos_ranked['wins'] - wins_iniciales
                datos_actuales['losses_hoy'] = datos_ranked['losses'] - losses_iniciales
                datos_actuales['lp_hoy'] = lp_absoluto(tier, rank, lp) - lp_inicial_abs
                datos_actuales['wins_temporada'] = datos_ranked['wins']
                datos_actuales['losses_temporada'] = datos_ranked['losses']

            url_matches = f"https://{REGION_ACCOUNT}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&start=0&count={CANTIDAD_PARTIDAS}"
            match_ids = requests.get(url_matches, headers=headers, timeout=15).json()

            if isinstance(match_ids, list):
                historial = []
                for match_id in match_ids:
                    if match_id not in match_cache:
                        res_detalle = requests.get(f"https://{REGION_ACCOUNT}.api.riotgames.com/lol/match/v5/matches/{match_id}", headers=headers, timeout=15)
                        if res_detalle.status_code == 200:
                            match_cache[match_id] = res_detalle.json()

                    if match_id in match_cache:
                        m_data = match_cache[match_id]
                        for p in m_data['info']['participants']:
                            if p['puuid'] == puuid:
                                campeon = p.get('championName', 'Desconocido')
                                if campeon == "FiddleSticks": campeon = "Fiddlesticks"
                                historial.append({
                                    "img": f"https://ddragon.leagueoflegends.com/cdn/{latest_patch}/img/champion/{campeon}.png",
                                    "win": bool(p.get('win')),
                                    "etiqueta": obtener_calificacion_jugador(m_data, puuid)
                                })
                datos_actuales['historial'] = historial
                datos_actuales['historial_html'] = "".join(
                    f'<div><img src="{h["img"]}"><span>{h["etiqueta"]}</span></div>' for h in historial)

            guardar_datos_json()
            print(f"[{time.strftime('%X')}] Tracker In-Game v4 actualizado.")

        except Exception as e:
            print(f"[{time.strftime('%X')}] Error: {str(e)[:80]}")

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
        "lp_hoy": datos_actuales['lp_hoy'],
        "wins_temporada": datos_actuales['wins_temporada'],
        "losses_temporada": datos_actuales['losses_temporada'],
        "historial": datos_actuales['historial'],
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
        print(f"No se pudo guardar el JSON: {e}")

# Plantilla del overlay (no usa f-string: todos los datos los lee el navegador del JSON)
HTML_OVERLAY = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Tracker In-Game v4</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,400..900&display=swap">
<style>
  /* Tracker In-Game v3 - generado por tracker_ingame_v3.py. Lee ./overlay_data.json
     Fijo: escudo con alas + icono, nombre + tag, ultimas 10 partidas.
     Rota cada 7 s: elo + LP | W-L + Session   <->   winrate + partidas | W-L + LP de la sesion */
  html, body { margin: 0; background: transparent; overflow: hidden; }
  body { font-family: 'Archivo', sans-serif; color: #f1efe9; padding: 60px 28px; }
  :root { --tier: 46, 204, 113; --win: 0, 163, 255; --loss: 255, 27, 72; --ink: 241, 239, 233; }
  @keyframes entrar { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
  @keyframes latido { 0%,100% { box-shadow: 0 0 0 0 rgba(var(--tier), .55); } 50% { box-shadow: 0 0 0 5px rgba(var(--tier), 0); } }

  .wrap { position: relative; display: inline-block; padding-left: 0; }
  .crest { position: absolute; left: -6px; top: 50%; margin-top: -168px; width: 208px; height: 270px; z-index: 10; pointer-events: none; animation: entrar .5s ease-out both; }
  .crest .wings { width: 208px; height: 270px; object-fit: contain; filter: drop-shadow(0 0 8px rgba(var(--tier), .35)) drop-shadow(0 0 16px rgba(var(--tier), .2)); }
  .crest .icon { position: absolute; left: 50%; top: 126px; width: 69px; height: 69px; margin-left: -34.5px; border-radius: 50%; box-shadow: 0 2px 8px rgba(0,0,0,.6); }
  .card {
    position: relative; width: 603px; padding: 12px 20px 11px 195px; box-sizing: border-box;
    background: linear-gradient(90deg, rgba(40, 40, 42, 0.96) 30%, rgba(40, 40, 42, 0.88) 55%, rgba(40, 40, 42, 0.55) 78%, rgba(40, 40, 42, 0) 100%);
    clip-path: polygon(14px 0, 100% 0, 100% calc(100% - 14px), calc(100% - 14px) 100%, 0 100%, 0 14px);
    animation: entrar .5s ease-out both;
  }
  .card::before { content: ""; position: absolute; inset: 0; pointer-events: none; padding: 1px; z-index: 5;
    background: linear-gradient(90deg, rgba(var(--tier), .9) 0%, rgba(var(--ink), .16) 12%, rgba(var(--ink), .16) 40%, rgba(var(--ink), .06) 75%, rgba(var(--ink), 0) 100%);
    clip-path: inherit;
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask-composite: exclude; }
  .indic { position: absolute; top: 9px; right: 20px; display: flex; gap: 4px; z-index: 6; }
  .indic i { width: 14px; height: 3px; background: rgba(var(--ink), .2); transition: background .4s; }
  .indic i.on { background: rgb(var(--tier)); }

  .head { display: flex; align-items: stretch; gap: 14px; margin-top: 4px; }
  .mid { flex: 1; min-width: 0; padding-left: 6px; }
  .name { font-size: 32px; font-weight: 900; font-stretch: 112%; letter-spacing: -0.02em; line-height: .95; white-space: nowrap; overflow: hidden; }
  .name .tag { font-size: 16px; font-weight: 500; font-stretch: 100%; letter-spacing: 0; color: rgba(var(--ink), .55); margin-left: 6px; }

  .rot { position: relative; height: 34px; margin-top: 7px; }
  .rot > div { position: absolute; inset: 0; transition: opacity .5s ease, transform .5s ease; }
  .oculta { opacity: 0; transform: translateY(8px); pointer-events: none; }
  .line { display: flex; align-items: baseline; gap: 8px; font-size: 17px; font-weight: 800; line-height: 1; white-space: nowrap; }
  .line .tier { color: rgb(var(--tier)); }
  .line .muted { font-weight: 500; color: rgba(var(--ink), .6); font-size: 14px; }
  .line .sep { width: 1px; height: 14px; background: rgba(var(--ink), .3); align-self: center; }
  .dot { width: 11px; height: 11px; border-radius: 50%; background: rgb(var(--tier)); animation: latido 2.6s ease-in-out infinite; align-self: center; }
  .seg { display: flex; gap: 3px; margin-top: 8px; }
  .seg i { width: 14px; height: 6px; background: rgba(var(--ink), .12); transform: skewX(-20deg); }
  .seg i.on { background: rgb(var(--tier)); box-shadow: 0 0 5px rgba(var(--tier), .6); }
  .wrbar { display: flex; height: 6px; margin-top: 8px; width: 186px; background: rgba(var(--ink), .12); transform: skewX(-20deg); }
  .wrbar .bw { background: rgb(var(--win)); } .wrbar .bl { background: rgb(var(--loss)); }

  .ses { width: 98px; flex-shrink: 0; border-left: 1px solid rgba(var(--ink), .25); padding-left: 14px;
    display: flex; flex-direction: column; justify-content: center; }
  .ses .wl { font-size: 26px; font-weight: 900; font-stretch: 112%; letter-spacing: -0.01em; line-height: 1; white-space: nowrap; }
  .ses .wl .w { color: rgb(var(--win)); } .ses .wl .l { color: rgb(var(--loss)); } .ses .wl .d { color: rgba(var(--ink), .35); margin: 0 2px; font-weight: 400; }
  .ses .sub { position: relative; height: 16px; margin-top: 6px; }
  .ses .sub > div { position: absolute; inset: 0; font-size: 13px; font-weight: 700; white-space: nowrap; transition: opacity .5s ease, transform .5s ease; }
  .ses .sub .lbl { color: rgba(var(--ink), .55); font-weight: 500; }
  .pos { color: #3ddc84; } .neg { color: rgb(var(--loss)); } .zero { color: rgba(var(--ink), .55); }

  .rule { height: 1px; background: linear-gradient(90deg, rgba(var(--ink), .25), rgba(var(--ink), .08)); margin: 11px 0 9px; }
  .hist { display: flex; gap: 4px; min-height: 57px; }
  .m { width: 35px; text-align: center; animation: entrar .35s ease-out both; }
  .m img { width: 35px; height: 35px; display: block; object-fit: cover;
    clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px); }
  .m .bar { height: 3px; margin-top: 3px; }
  .m.win .bar { background: rgb(var(--win)); } .m.loss .bar { background: rgb(var(--loss)); }
  .m.loss img { filter: grayscale(.55) brightness(.8); }
  .m span { display: block; margin-top: 3px; font-size: 10px; font-weight: 600; color: rgba(var(--ink), .6); }
  .m span.mvp { color: #ffc93c; } .m span.ace { color: #b79cff; }

  /* ================= EFECTO GLITCH (intenso) ================= */
  /* Cortes secos, como en el video: sin fundidos */
  .rot > div, .ses .sub > div { transition: none !important; }
  .oculta { transform: none !important; }

  /* Grano de impresion que "salta" a 10 cuadros por segundo */
  .grain { position: absolute; inset: 0; pointer-events: none; z-index: 4; mix-blend-mode: overlay;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>"); opacity: .13; animation: grano 1s steps(10) infinite; }
  @keyframes grano { 0%{background-position:0 0} 10%{background-position:-37px 21px} 20%{background-position:53px -14px}
    30%{background-position:-12px 47px} 40%{background-position:29px 8px} 50%{background-position:-44px -33px}
    60%{background-position:17px 39px} 70%{background-position:-25px -6px} 80%{background-position:41px 26px} 90%{background-position:-8px -45px} }
  .scan { position: absolute; inset: 0; pointer-events: none; z-index: 4; display: block;
    background: repeating-linear-gradient(0deg, rgba(0,0,0,.22) 0 1px, transparent 1px 3px); opacity: .55; }

  /* Separacion rojo/azul en el texto durante el glitch */
  @keyframes rgb {
    0%   { text-shadow: -4px 0 rgba(255,27,72,.95), 4px 0 rgba(0,163,255,.95); transform: translateX(2px); }
    20%  { text-shadow: 2px 0 rgba(255,27,72,.9), -2px 0 rgba(0,163,255,.9); transform: translateX(-4px); }
    40%  { text-shadow: -4px 1px rgba(255,27,72,.9), 2px -1px rgba(0,163,255,.9); transform: translateX(1px); }
    60%  { text-shadow: 1px 0 rgba(255,27,72,.7), -1px 0 rgba(0,163,255,.7); transform: translateX(-1px); }
    80%  { text-shadow: none; transform: translateX(1px); }
    100% { text-shadow: none; transform: none; }
  }
  .wrap.glitch .name, .wrap.glitch .rot, .wrap.glitch .ses { animation: rgb .65s steps(1) both; }
  .wrap.mini .name { animation: rgb .3s steps(1) both; }

  /* Bandas de "rotura" que cruzan la tarjeta */
  .tears { position: absolute; inset: 0; pointer-events: none; z-index: 7; }
  .tears i { position: absolute; left: 0; right: 0; height: 4px; opacity: 0; mix-blend-mode: screen; }
  .wrap.glitch .tears i { animation: tear .65s steps(1) both; }
  .tears i:nth-child(1) { background: rgba(255,27,72,.75); }
  .tears i:nth-child(2) { background: rgba(0,163,255,.75); animation-delay: .07s !important; }
  .tears i:nth-child(3) { background: rgba(var(--tier),.6); animation-delay: .14s !important; }
  .tears i:nth-child(4) { background: rgba(241,239,233,.35); animation-delay: .04s !important; display: block; }
  .tears i:nth-child(5) { background: rgba(255,27,72,.5); animation-delay: .2s !important; display: block; }
  @keyframes tear { 0%{opacity:1; transform:translateX(-10px)} 25%{opacity:0} 40%{opacity:1; transform:translateX(14px) scaleY(1.8)} 60%{opacity:0} 75%{opacity:.8; transform:translateX(-6px)} 100%{opacity:0} }

  /* Intenso: la tarjeta se "corta" en franjas y salta */
  @keyframes cortes {
    0%   { clip-path: inset(0 0 62% 0); transform: translateX(-5px); }
    15%  { clip-path: inset(35% 0 20% 0); transform: translateX(6px); }
    30%  { clip-path: inset(0 0 0 0); transform: translateX(-2px); }
    45%  { clip-path: inset(55% 0 0 0); transform: translateX(4px); }
    60%  { clip-path: inset(0 0 0 0); transform: translateX(0); }
    100% { clip-path: inset(0 0 0 0); transform: none; }
  }
  .wrap.glitch.fuerte .head, .wrap.glitch.fuerte .hist { animation: cortes .65s steps(1) both; }
  @keyframes escudo { 0%{filter: drop-shadow(-4px 0 0 rgba(255,27,72,.9)) drop-shadow(4px 0 0 rgba(0,163,255,.9)); transform: translateX(3px)}
    30%{filter: none; transform: translateX(-3px)} 50%{filter: drop-shadow(3px 0 0 rgba(255,27,72,.9)) drop-shadow(-3px 0 0 rgba(0,163,255,.9)); transform:none} 70%,100%{filter:none; transform:none} }
  .wrap.glitch.fuerte .crest .wings, .wrap.glitch.fuerte .crest .icon { animation: escudo .65s steps(1) both; }

  /* Historial: aparece parpadeando, entrecortado */
  .m { animation-name: parpadeo !important; animation-duration: .6s !important; animation-timing-function: steps(1) !important; }
  @keyframes parpadeo { 0%{opacity:0} 20%{opacity:1} 35%{opacity:0} 50%{opacity:1; transform:translateX(-2px)} 65%{opacity:.3} 80%,100%{opacity:1; transform:none} }

  /* ================= TEMA: IRIS (estetica del video) ================= */
  :root { --win: 31, 107, 255; --loss: 214, 31, 44; --ink: 236, 244, 255; --cian: 111, 227, 255; --verde: 61, 255, 110; }
  .card {
    background:
      radial-gradient(circle at 98px 50%, rgba(var(--win), .55) 0, rgba(var(--win), .22) 55px, rgba(var(--win), .06) 95px, transparent 125px),
      linear-gradient(90deg, rgba(4, 6, 15, .97) 30%, rgba(4, 6, 15, .9) 55%, rgba(4, 6, 15, .55) 78%, rgba(4, 6, 15, 0) 100%);
  }
  .card::before { background: linear-gradient(90deg, rgba(var(--cian), .9) 0%, rgba(var(--win), .45) 20%, rgba(var(--ink), .1) 50%, rgba(var(--ink), 0) 100%); }
  .crest .wings { filter: drop-shadow(0 0 12px rgba(var(--cian), .55)) drop-shadow(0 0 3px rgba(var(--cian), .9)); }
  .crest .icon { box-shadow: 0 0 0 2px rgba(var(--cian), .8), 0 0 16px rgba(var(--win), .9); }

  .name { text-shadow: 0 0 14px rgba(var(--win), .55); }
  .name .tag { color: rgb(var(--cian)); }
  .line .tier { color: rgb(var(--cian)); }
  .dot { background: rgb(var(--loss)); box-shadow: 0 0 8px rgba(var(--loss), .9); animation: latidoRojo 1.2s steps(2) infinite; }
  @keyframes latidoRojo { 50% { opacity: .35; } }
  .seg i { background: rgba(var(--cian), .14); }
  .seg i.on { background: rgb(var(--cian)); box-shadow: 0 0 6px rgba(var(--cian), .9); }
  .wrbar .bw { background: rgb(var(--win)); }
  .ses { border-left: 1px solid rgba(var(--cian), .3); }
  .ses .sub .lbl { color: rgba(var(--ink), .55); }
  .pos { color: rgb(var(--verde)); }
  .indic i { width: 10px; background: rgba(var(--verde), .2); } .indic i.on { background: rgb(var(--verde)); box-shadow: 0 0 5px rgba(var(--verde), .9); }
  .rule { background: linear-gradient(90deg, rgba(var(--cian), .45), rgba(var(--cian), 0)); }

  /* campeones dentro de marcos azules, como las tarjetas del video */
  .m img { filter: saturate(.75) contrast(1.1); box-shadow: inset 0 0 0 2px rgb(var(--win)); outline: 2px solid rgba(var(--win), .85); outline-offset: -2px; }
  .m.loss img { filter: grayscale(.7) sepia(.4) hue-rotate(-30deg) saturate(2.4) brightness(.75); outline-color: rgba(var(--loss), .85); }
  .m.win .bar { background: rgb(var(--cian)); }
  .m span.mvp { color: #ffd400; } .m span.ace { color: rgb(var(--cian)); }

  /* manchas rojas y lente */
  .deco { position: absolute; inset: 0; z-index: -1; pointer-events: none; }
  .deco svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
  .grain { opacity: .15; }

  /* ================= FONDO WARP (video) ================= */
  .card {
    background:
      linear-gradient(90deg, rgba(1, 2, 6, .97) 30%, rgba(1, 2, 6, .9) 55%, rgba(1, 2, 6, .55) 78%, rgba(1, 2, 6, 0) 100%);
  }
  /* el video se desvanece igual que la tarjeta: solido a la izquierda, transparente a la derecha */
  .deco .fondo { position: absolute; inset: 0; overflow: hidden;
    -webkit-mask: linear-gradient(90deg, #000 40%, rgba(0,0,0,.65) 72%, transparent 100%);
            mask: linear-gradient(90deg, #000 40%, rgba(0,0,0,.65) 72%, transparent 100%); }
  /* centro del warp (centro del video) justo sobre el escudo: x 98, y 83 */
  .deco .fondo video { position: absolute; width: 1040px; height: 585px; left: -422px; top: -209px; object-fit: cover;
    opacity: .85; }
  /* resplandor azul del iris por encima del video */
  .deco .glow { position: absolute; inset: 0; background: radial-gradient(circle at 98px 50%, rgba(var(--win), .35) 0, rgba(var(--win), .1) 50px, transparent 95px); }

  /* ================= FONDO DESDE ARRIBA A LA IZQUIERDA =================
     Solido en la esquina superior izquierda y se vuelve transparente hacia
     las otras tres esquinas (fondo, borde fino y video warp). */
  .card {
    background: radial-gradient(ellipse farthest-corner at 0 0, rgba(1, 2, 6, .97) 0%, rgba(1, 2, 6, .9) 25%, rgba(1, 2, 6, .6) 50%, rgba(1, 2, 6, .25) 72%, rgba(1, 2, 6, 0) 92%);
  }
  .card::before {
    background: radial-gradient(ellipse farthest-corner at 0 0, rgba(var(--cian), .9) 0%, rgba(var(--win), .45) 22%, rgba(var(--ink), .12) 48%, rgba(var(--ink), 0) 75%);
  }
  .deco .fondo {
    -webkit-mask: radial-gradient(ellipse farthest-corner at 0 0, #000 0%, #000 20%, rgba(0,0,0,.45) 45%, transparent 70%);
            mask: radial-gradient(ellipse farthest-corner at 0 0, #000 0%, #000 20%, rgba(0,0,0,.45) 45%, transparent 70%);
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="crest"><img id="emb" class="wings" alt=""><img id="icon" class="icon" alt=""></div>
  <div class="card">
  <div class="deco">
    <div class="fondo">
      <video autoplay muted loop playsinline>
        <source src="fondo_warp.webm" type="video/webm">
        <source src="fondo_warp.mp4" type="video/mp4">
      </video>
    </div>
    <div class="glow"></div>
    <svg viewBox="0 0 603 181" preserveAspectRatio="none">
      <!-- marcas de mira -->
      <path d="M186 10 L186 170" stroke="rgba(111,227,255,.25)" stroke-width="1" stroke-dasharray="1 4"/>
      <path d="M8 83 L14 83 M182 83 L190 83" stroke="rgba(111,227,255,.6)" stroke-width="1"/>
      <circle cx="568" cy="150" r="3" fill="rgba(214,31,44,.9)"/><circle cx="580" cy="150" r="3" fill="none" stroke="rgba(214,31,44,.9)"/>
    </svg>
  </div>

    <div class="indic"><i id="i1" class="on"></i><i id="i2"></i></div>
    <div class="head">
      <div class="mid">
        <div class="name" id="nameBox"><span id="name">-</span><span class="tag" id="tag"></span></div>
        <div class="rot">
          <div id="rA">
            <div class="line"><span class="tier" id="tier">-</span><span><span id="lp">0</span>LP</span><span class="dot"></span></div>
            <div class="seg" id="seg"></div>
          </div>
          <div id="rB" class="oculta">
            <div class="line"><span><span id="wr">—</span> Winrate</span><span class="sep"></span><span class="muted"><span id="games">-</span> Games</span></div>
            <div class="wrbar"><div class="bw" id="bw" style="width:50%"></div><div class="bl" id="bl" style="width:50%"></div></div>
          </div>
        </div>
      </div>
      <div class="ses">
        <div class="wl"><span class="w" id="w">0W</span><span class="d">-</span><span class="l" id="l">0L</span></div>
        <div class="sub">
          <div id="sA" class="lbl">Session</div>
          <div id="sB" class="oculta"><span id="lpdiff" class="zero">±0 LP</span></div>
        </div>
      </div>
    </div>
    <div class="rule"></div>
    <div class="hist" id="hist"></div>
  </div>
</div>
<script>
  const PARTIDAS = 10;   // partidas que se muestran abajo
  const SEGUNDOS = parseFloat(new URLSearchParams(location.search).get("seg")) || 10;   // segundos de cada rotacion; el glitch va con cada rotacion (?seg=N para probar)
  const COLORES = {
    IRON:"161,157,148", BRONZE:"205,127,50", SILVER:"189,195,199", GOLD:"241,196,15",
    PLATINUM:"26,188,156", EMERALD:"46,204,113", DIAMOND:"52,152,219",
    MASTER:"224,64,251", GRANDMASTER:"231,76,60", CHALLENGER:"243,156,18"
  };
  const SIN_DIVISION = ["MASTER", "GRANDMASTER", "CHALLENGER"];
  const $ = id => document.getElementById(id);
  const setText = (id, v) => { const el = $(id); if (el && el.textContent !== String(v)) el.textContent = v; };
  const setSrc = (id, v) => { const el = $(id); if (el && el.getAttribute("src") !== v) el.src = v; };

  function ajustarNombre() {  // achica el nombre si es muy largo
    const box = $("nameBox"); let size = 32; box.style.fontSize = size + "px";
    while (box.scrollWidth > box.clientWidth && size > 20) { size -= 2; box.style.fontSize = size + "px"; }
  }

  let ultimoHist = null, ultimoNombre = null;
  async function actualizar() {
    try {
      const res = await fetch("./overlay_data.json?v=" + Date.now());
      if (!res.ok) return;
      const d = await res.json();
      const tier = (d.tier || "EMERALD").toUpperCase();
      const sinDiv = SIN_DIVISION.includes(tier);
      document.documentElement.style.setProperty("--tier", COLORES[tier] || "255,255,255");

      setSrc("emb", `https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/wings/wings_${tier.toLowerCase()}.png`);
      setSrc("icon", `https://ddragon.leagueoflegends.com/cdn/${d.latest_patch}/img/profileicon/${d.profile_icon_id}.png`);

      const nombre = (d.riot_id || "-") + "|" + (d.riot_tag || "");
      if (nombre !== ultimoNombre) {
        ultimoNombre = nombre;
        setText("name", d.riot_id || "-");
        setText("tag", d.riot_tag ? "#" + d.riot_tag : "");
        ajustarNombre();
      }

      // Rotacion A: elo
      setText("tier", tier.charAt(0) + tier.slice(1).toLowerCase() + (sinDiv ? "" : " " + d.rank));
      setText("lp", d.lp);
      const on = sinDiv ? 10 : Math.round(Math.max(0, Math.min(100, d.lp)) / 10);
      const seg = Array.from({length: 10}, (_, k) => `<i class="${k < on ? "on" : ""}"></i>`).join("");
      if ($("seg").innerHTML !== seg) $("seg").innerHTML = seg;

      // Sesion
      setText("w", (d.wins_hoy || 0) + "W");
      setText("l", (d.losses_hoy || 0) + "L");
      const diff = d.lp_hoy || 0, el = $("lpdiff");
      el.textContent = (diff > 0 ? "+" : diff < 0 ? "" : "±") + diff + " LP";
      el.className = diff > 0 ? "pos" : diff < 0 ? "neg" : "zero";

      // Rotacion B: winrate de la temporada
      const tw = d.wins_temporada || 0, tl = d.losses_temporada || 0, total = tw + tl;
      setText("wr", total ? Math.round(tw * 100 / total) + "%" : "—");
      setText("games", total || "-");
      $("bw").style.width = total ? (tw * 100 / total) + "%" : "50%";
      $("bl").style.width = total ? (tl * 100 / total) + "%" : "50%";

      // Historial (solo se redibuja si cambio, para no repetir la animacion)
      const hist = JSON.stringify(d.historial || []);
      if (hist !== ultimoHist) {
        ultimoHist = hist;
        $("hist").innerHTML = (d.historial || []).slice(0, PARTIDAS).map((m, k) => {
          const cls = m.etiqueta === "MVP" ? "mvp" : m.etiqueta === "ACE" ? "ace" : "";
          return `<div class="m ${m.win ? "win" : "loss"}" style="animation-delay:${k * .05}s">
            <img src="${m.img}" alt=""><div class="bar"></div><span class="${cls}">${m.etiqueta}</span></div>`;
        }).join("");
      }
    } catch (e) { console.error(e); }
  }
  actualizar();
  setInterval(actualizar, 3000);

  // Rotacion de la parte de arriba. Con ?slide=1 o ?slide=2 en la ruta queda fija.
  function mostrar(n) {
    ["rA", "sA"].forEach(id => $(id).classList.toggle("oculta", n !== 1));
    ["rB", "sB"].forEach(id => $(id).classList.toggle("oculta", n !== 2));
    $("i1").classList.toggle("on", n === 1); $("i2").classList.toggle("on", n === 2);
  }
  const fija = parseInt(new URLSearchParams(location.search).get("slide"));
  if (fija === 1 || fija === 2) mostrar(fija);
  else { let n = 1; mostrar(n); setInterval(() => { n = n === 1 ? 2 : 1; mostrar(n); }, SEGUNDOS * 1000); }
</script>

<script>
  /* ================= EFECTO GLITCH (intenso) ================= */
  const MODO = "intenso";
  const DURACION = 650;                      // ms que dura el glitch de cada rotacion
  const PAUSA_MINI = [5, 9];     // cada cuantos segundos hay un mini glitch en el nombre
  const SIMBOLOS = "█▓▒░#%&@$*+=/<>_-";
  const wrap = document.querySelector(".wrap"), card = document.querySelector(".card");
  if (MODO === "intenso") wrap.classList.add("fuerte");

  // capas extra: grano, lineas de barrido y bandas
  card.insertAdjacentHTML("beforeend", '<div class="grain"></div><div class="scan"></div><div class="tears"><i></i><i></i><i></i><i></i><i></i></div>');

  // Las letras aparecen de a una, con simbolos que se van "acomodando" (10 cuadros por segundo)
  function revelar(el, ms) {
    if (!el || el.children.length) return;
    const final = el._final !== undefined && el._sc ? el._final : el.textContent;
    el._final = final; clearInterval(el._sc);
    const pasos = Math.max(3, Math.round(ms / 100)); let k = 0;
    el._sc = setInterval(() => {
      k++;
      const fijas = Math.floor(final.length * k / pasos);
      el.textContent = final.slice(0, fijas) + [...final.slice(fijas)]
        .map(c => c === " " ? " " : (Math.random() < .55 ? SIMBOLOS[Math.random() * SIMBOLOS.length | 0] : "")).join("");
      if (k >= pasos) { clearInterval(el._sc); el._sc = null; el.textContent = final; }
    }, 100);
  }
  function hojas(id) { return [...document.getElementById(id).querySelectorAll("span")].filter(s => !s.children.length); }

  function lanzarGlitch() {
    wrap.classList.remove("glitch"); void wrap.offsetWidth; wrap.classList.add("glitch");
    card.querySelectorAll(".tears i").forEach(t => { t.style.top = (8 + Math.random() * 80) + "%"; t.style.height = (2 + Math.random() * (MODO === "intenso" ? 9 : 4)) + "px"; });
    clearTimeout(wrap._t); wrap._t = setTimeout(() => wrap.classList.remove("glitch"), DURACION);
  }

  // Reemplaza la rotacion: corte seco en medio del glitch y el texto nuevo se "escribe"
  function mostrar(n) {
    lanzarGlitch();
    setTimeout(() => {
      ["rA", "sA"].forEach(id => $(id).classList.toggle("oculta", n !== 1));
      ["rB", "sB"].forEach(id => $(id).classList.toggle("oculta", n !== 2));
      $("i1").classList.toggle("on", n === 1); $("i2").classList.toggle("on", n === 2);
      const entra = n === 1 ? ["rA", "sA"] : ["rB", "sB"];
      entra.forEach(id => hojas(id).forEach(el => revelar(el, MODO === "intenso" ? 500 : 350)));
    }, 110);
  }

  // Mini glitch de vez en cuando sobre el nombre
  const idle = parseFloat(new URLSearchParams(location.search).get("idle"));
  function programarMini() {
    const s = idle || (PAUSA_MINI[0] + Math.random() * (PAUSA_MINI[1] - PAUSA_MINI[0]));
    setTimeout(() => {
      wrap.classList.remove("mini"); void wrap.offsetWidth; wrap.classList.add("mini");
      setTimeout(() => wrap.classList.remove("mini"), 320);
      programarMini();
    }, s * 1000);
  }
  // Sin mini glitch suelto: el glitch ocurre solo con cada rotacion (cada 10 s). Con ?idle=N se puede volver a activar.
  if (idle) programarMini();

  // Al aparecer, el nombre se escribe letra por letra
  setTimeout(() => { revelar($("name"), 700); lanzarGlitch(); }, 400);
</script>
</body>
</html>
"""

def generar_html_base():
    try:
        with open(ARCHIVO_HTML, "w", encoding="utf-8") as f:
            f.write(HTML_OVERLAY)
    except Exception as e:
        print(f"No se pudo guardar el HTML: {e}")

# ================= 5. INICIALIZADOR =================
if __name__ == "__main__":
    os.makedirs(CARPETA_OVERLAYS, exist_ok=True)
    generar_html_base()
    if not any(os.path.exists(os.path.join(CARPETA_OVERLAYS, f)) for f in ("fondo_warp.webm", "fondo_warp.mp4")):
        print("AVISO: no encontre fondo_warp.webm ni fondo_warp.mp4 en la carpeta de overlays; el fondo warp no se va a ver.")
    guardar_datos_json()
    threading.Thread(target=obtener_datos_riot, daemon=True).start()

    print("Tracker IN-GAME v4 inicializado.")
    while True:
        time.sleep(10)
