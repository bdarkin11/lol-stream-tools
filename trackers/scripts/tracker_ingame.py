import requests
import time
import threading
import os
import json
from dotenv import load_dotenv

# =====================================================================
#  TRACKER IN-GAME
#  Overlay para OBS con el rango, LP y partidas de la sesion.
#  Tema "collage": papel oscuro rasgado, marco holografico, cinta y
#  nombre escrito a mano, a juego con las escenas del stream.
#  El escudo de rango se descarga solo de CommunityDragon a
#  trackers/overlays/emblemas la primera vez.
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

# El escudo de rango (las alas) se guarda en la carpeta de overlays. El servidor del que sale
# (communitydragon) a veces tarda mucho o no responde, y sin copia local el escudo no aparecia.
URL_EMBLEMA = "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/wings/wings_{}.png"

def asegurar_emblema(tier):
    nombre = f"wings_{tier.lower()}.png"
    carpeta = os.path.join(CARPETA_OVERLAYS, "emblemas")
    destino = os.path.join(carpeta, nombre)
    if os.path.exists(destino) and os.path.getsize(destino) > 1000:
        return
    try:
        os.makedirs(carpeta, exist_ok=True)
        res = requests.get(URL_EMBLEMA.format(tier.lower()), timeout=90)
        if res.status_code == 200 and len(res.content) > 1000:
            with open(destino + ".tmp", "wb") as f:
                f.write(res.content)
            os.replace(destino + ".tmp", destino)
            print(f"[{time.strftime('%X')}] Escudo de {tier} guardado en emblemas\\{nombre}")
        else:
            print(f"[{time.strftime('%X')}] No pude bajar el escudo de {tier} (respuesta {res.status_code}); reintento en la proxima vuelta.")
    except Exception as e:
        print(f"[{time.strftime('%X')}] No pude bajar el escudo de {tier}: {str(e)[:60]}; reintento en la proxima vuelta.")

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
            print(f"[{time.strftime('%X')}] Tracker In-Game actualizado.")
            # Va despues de guardar los datos: si la descarga del escudo tarda, no frena el resto.
            asegurar_emblema(datos_actuales['tier'].upper())

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
<title>Tracker In-Game</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,400..900&display=swap">
<style>
  /* Tracker In-Game - generado por tracker_ingame.py. Lee ./overlay_data.json
     Tema collage, a juego con las escenas del stream (papel oscuro + holografico).
     ?op=1  papel rasgado, nombre a mano, linea holografica arriba y a la izquierda (por defecto)
     ?op=2  marco holografico completo sobre papel rasgado, nombre en letra gruesa, sesion en talon claro
     ?slide=1|2 deja fija una de las dos rotaciones. ?fondo pinta un fondo de prueba detras. */
  html, body { margin: 0; background: transparent; overflow: hidden; }
  body { font-family: 'Archivo', sans-serif; color: rgb(var(--ink)); padding: 60px 28px; }
  body.fondo { background: linear-gradient(160deg, #24382c, #1c2a33); min-height: 100vh; box-sizing: border-box; }
  :root {
    --tier: 46, 204, 113; --ink: 211, 206, 198; --gris: 111, 111, 115;
    --papel: #1a1a1c; --claro: #e6e2da; --cinta: #a8935a;
    --holo: #8ef6ff, #a9b8ff, #ffa6dc, #fff7b0, #9dffd0, #c7a6ff, #8ef6ff;
    --grano: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/></filter><rect width='100%' height='100%' filter='url(%23n)' opacity='.22'/></svg>");
  }
  @keyframes entrar { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
  @keyframes correr { to { background-position-x: 700px; } }
  .holo-fondo { background: linear-gradient(90deg, var(--holo)) 0 0 / 700px 100%; animation: correr 6s linear infinite; }
  .holo-texto { background: linear-gradient(90deg, var(--holo)) 0 0 / 700px 100%; -webkit-background-clip: text; background-clip: text; color: transparent; animation: correr 6s linear infinite; }

  /* ---------- medidas: iguales al tracker actual ---------- */
  .wrap { position: relative; display: inline-block; padding-left: 0; }
  .crest { position: absolute; left: -6px; top: 50%; margin-top: -168px; width: 208px; height: 270px; z-index: 10; pointer-events: none; animation: entrar .5s ease-out both; }
  .crest .wings { width: 208px; height: 270px; object-fit: contain; filter: drop-shadow(0 3px 6px rgba(0, 0, 0, .8)) drop-shadow(0 0 10px rgba(169, 184, 255, .3)); }
  .crest .icon { position: absolute; left: 50%; top: 126px; width: 69px; height: 69px; margin-left: -34.5px; border-radius: 50%;
    box-shadow: 0 0 0 2px #0b0b0c, 0 0 0 4px rgb(var(--ink)), 0 2px 8px rgba(0, 0, 0, .7); }
  .card { position: relative; z-index: 1; width: 603px; padding: 12px 20px 11px 195px; box-sizing: border-box;
    background: var(--grano), radial-gradient(120% 90% at 20% 10%, rgba(255, 255, 255, .06), transparent 60%), var(--papel);
    animation: entrar .5s ease-out both; }
  .indic { position: absolute; top: 9px; right: 20px; display: flex; gap: 4px; z-index: 6; }
  .indic i { width: 10px; height: 3px; background: rgba(var(--gris), .6); }
  .indic i.on { background: linear-gradient(90deg, #8ef6ff, #ffa6dc); }

  .head { display: flex; align-items: stretch; gap: 14px; margin-top: 4px; }
  .mid { flex: 1; min-width: 0; padding-left: 6px; }
  .name { font-size: 32px; line-height: .95; white-space: nowrap; overflow: hidden; }
  .name .tag { font-family: ui-monospace, Consolas, monospace; font-size: 14px; font-weight: 600; letter-spacing: .04em; color: rgb(var(--gris)); margin-left: 8px; -webkit-text-stroke: 0; }

  .rot { position: relative; height: 34px; margin-top: 7px; }
  .rot > div { position: absolute; inset: 0; }
  .oculta { opacity: 0; pointer-events: none; }
  .line { display: flex; align-items: baseline; gap: 8px; font-size: 17px; font-weight: 800; line-height: 1; white-space: nowrap; }
  .line .muted { font-weight: 500; color: rgba(var(--ink), .6); font-size: 14px; }
  .line .sep { width: 1px; height: 14px; background: rgba(var(--ink), .3); align-self: center; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: rgb(var(--ink)); align-self: center; }
  .seg { display: flex; gap: 3px; margin-top: 8px; }
  .seg i { width: 14px; height: 6px; background: rgba(var(--gris), .45); }
  .seg i.on { background: #8ef6ff; }
  .seg i.on:nth-child(2) { background: #9cd9ff; } .seg i.on:nth-child(3) { background: #a9b8ff; } .seg i.on:nth-child(4) { background: #d4afee; }
  .seg i.on:nth-child(5) { background: #ffa6dc; } .seg i.on:nth-child(6) { background: #ffcec6; } .seg i.on:nth-child(7) { background: #fff7b0; }
  .seg i.on:nth-child(8) { background: #cefbc0; } .seg i.on:nth-child(9) { background: #9dffd0; } .seg i.on:nth-child(10) { background: #c7a6ff; }
  .wrbar { display: flex; height: 6px; margin-top: 8px; width: 186px; background: rgba(var(--gris), .45); }
  .wrbar .bl { background: rgba(var(--gris), .55); }

  .ses { position: relative; width: 98px; flex-shrink: 0; border-left: 1px solid rgba(var(--ink), .25); padding-left: 14px;
    display: flex; flex-direction: column; justify-content: center; }
  .ses .wl { font-family: Impact, 'Arial Black', sans-serif; font-size: 27px; letter-spacing: .02em; line-height: 1; white-space: nowrap; }
  .ses .wl .l { color: rgb(var(--gris)); } .ses .wl .d { color: rgba(var(--ink), .35); margin: 0 3px; }
  .ses .sub { position: relative; height: 16px; margin-top: 6px; }
  .ses .sub > div { position: absolute; inset: 0; font-size: 13px; font-weight: 700; white-space: nowrap; }
  .ses .sub .lbl { color: rgba(var(--ink), .55); font-weight: 500; }
  .pos { color: #9dffd0; } .neg { color: #ff8096; } .zero { color: rgba(var(--ink), .55); }

  .rule { height: 1px; background: rgba(var(--ink), .22); margin: 11px 0 9px; }
  .hist { display: flex; gap: 4px; min-height: 57px; }
  .m { width: 35px; text-align: center; }
  .m img { width: 35px; height: 35px; display: block; object-fit: cover; box-sizing: border-box; border: 1px solid rgba(var(--ink), .55); }
  .m .bar { height: 3px; margin-top: 3px; background: rgba(var(--gris), .6); }
  .m.loss img { filter: grayscale(1) brightness(.6) contrast(1.1); border-color: rgba(var(--gris), .7); }
  .m span { display: block; margin-top: 3px; font-family: ui-monospace, Consolas, monospace; font-size: 10px; font-weight: 600; color: rgba(var(--ink), .6); }
  .m span.mvp { color: #0b0b0c; }
  .m span.ace { color: rgb(var(--ink)); box-shadow: inset 0 0 0 1px rgba(var(--ink), .7); }

  /* ---------- papel rasgado, linea holografica, cinta y tiza ---------- */
  .fibra, .base { position: absolute; z-index: 0; pointer-events: none; animation: entrar .5s ease-out both; }
  /* El borde rasgado tiene tres variantes que se turnan cada 0,6 s: se mueve, pero despacio. */
  .fibra { animation: entrar .5s ease-out both, turno 1.8s steps(1) infinite; }
  .fibra.f2 { animation-delay: 0s, -1.2s; }
  .fibra.f3 { animation-delay: 0s, -.6s; }
  @keyframes turno { 0% { opacity: 1; } 33.34%, 100% { opacity: 0; } }
  .fibra { background: var(--grano), #8f8d89; filter: drop-shadow(0 6px 10px rgba(0, 0, 0, .7)); }
  .base { background: var(--grano), #2b2b2e; display: none; }
  .hb { position: absolute; z-index: 5; pointer-events: none; }
  .hb.t, .hb.b { left: 0; right: 0; height: 3px; background: linear-gradient(90deg, var(--holo)) 0 0 / 700px 100%; animation: correr 6s linear infinite; }
  .hb.l, .hb.r { top: 0; bottom: 0; width: 3px; background: linear-gradient(180deg, var(--holo)) 0 0 / 100% 700px; animation: correr-y 6s linear infinite; }
  @keyframes correr-y { to { background-position-y: 700px; } }
  .hb.t { top: 0; } .hb.b { bottom: 0; animation-direction: reverse; } .hb.l { left: 0; } .hb.r { right: 0; animation-direction: reverse; }
  .cinta { position: absolute; z-index: 11; width: 96px; height: 28px; background: var(--grano), var(--cinta); opacity: .92;
    filter: drop-shadow(0 2px 3px rgba(0, 0, 0, .5)); clip-path: polygon(3px 0, 100% 2px, calc(100% - 3px) 50%, 100% 100%, 2px 100%, 0 55%); }
  .tiza { position: absolute; z-index: 2; pointer-events: none; opacity: .75; }

  /* ---------- OPCION 1: papel rasgado y letra a mano ---------- */
  body.op1 .name { font-family: 'Ink Free', 'Segoe Print', cursive; font-weight: 700; -webkit-text-stroke: .7px currentColor; color: #e6e2da; }
  body.op1 .line, body.op1 .ses .sub > div { font-family: ui-monospace, Consolas, monospace; font-size: 14px; letter-spacing: .02em; }
  body.op1 .hb.r, body.op1 .hb.b { display: none; }
  body.op1 .cinta { left: 524px; top: -11px; rotate: 9deg; }
  body.op1 .tiza { left: 566px; top: 104px; width: 22px; height: 22px; }

  /* ---------- OPCION 2: marco holografico completo, letra gruesa y talon claro ---------- */
  body.op2 .base { display: block; }
  body.op2 .card { background: var(--grano), #121214; }
  body.op2 .name { font-family: 'Ink Free', 'Segoe Print', cursive; font-weight: 700; -webkit-text-stroke: .7px currentColor; color: #e6e2da; }
  body.op2 .line { font-size: 15px; text-transform: uppercase; letter-spacing: .04em; }
  body.op2 .indic { top: 11px; }
  body.op2 .m:nth-child(odd) { rotate: -2.5deg; } body.op2 .m:nth-child(even) { rotate: 2deg; }
  body.op2 .m img { border: 2px solid #e6e2da; }
  body.op2 .m.loss img { border-color: #77747a; }
  body.op2 .cinta { left: 250px; top: -22px; rotate: -4deg; }
  body.op2 .tiza { display: none; }

  /* ---------- cambio de rotacion: el glitch de siempre, con la paleta nueva ---------- */
  .grain { position: absolute; inset: 0; pointer-events: none; z-index: 4; mix-blend-mode: overlay; background-image: var(--grano); opacity: .5; }
  @keyframes rgb {
    0%   { text-shadow: -4px 0 rgba(255, 166, 220, .95), 4px 0 rgba(142, 246, 255, .95); transform: translateX(2px); }
    20%  { text-shadow: 2px 0 rgba(255, 166, 220, .9), -2px 0 rgba(142, 246, 255, .9); transform: translateX(-4px); }
    40%  { text-shadow: -4px 1px rgba(255, 247, 176, .9), 2px -1px rgba(169, 184, 255, .9); transform: translateX(1px); }
    60%  { text-shadow: 1px 0 rgba(255, 166, 220, .7), -1px 0 rgba(142, 246, 255, .7); transform: translateX(-1px); }
    80%  { text-shadow: none; transform: translateX(1px); }
    100% { text-shadow: none; transform: none; }
  }
  .wrap.glitch .name, .wrap.glitch .rot, .wrap.glitch .ses .wl, .wrap.glitch .ses .sub { animation: rgb .65s steps(1) both; }
  .wrap.mini .name { animation: rgb .3s steps(1) both; }
  .tears { position: absolute; inset: 0; pointer-events: none; z-index: 7; }
  .tears i { position: absolute; left: 0; right: 0; height: 4px; opacity: 0; mix-blend-mode: screen; }
  .wrap.glitch .tears i { animation: tear .65s steps(1) both; }
  .tears i:nth-child(1) { background: rgba(255, 166, 220, .75); }
  .tears i:nth-child(2) { background: rgba(142, 246, 255, .75); animation-delay: .07s !important; }
  .tears i:nth-child(3) { background: rgba(255, 247, 176, .6); animation-delay: .14s !important; }
  .tears i:nth-child(4) { background: rgba(211, 206, 198, .35); animation-delay: .04s !important; }
  .tears i:nth-child(5) { background: rgba(199, 166, 255, .55); animation-delay: .2s !important; }
  @keyframes tear { 0%{opacity:1; transform:translateX(-10px)} 25%{opacity:0} 40%{opacity:1; transform:translateX(14px) scaleY(1.8)} 60%{opacity:0} 75%{opacity:.8; transform:translateX(-6px)} 100%{opacity:0} }
  .m { animation: parpadeo .6s steps(1) both; }
  @keyframes parpadeo { 0%{opacity:0} 20%{opacity:1} 35%{opacity:0} 50%{opacity:1} 65%{opacity:.3} 80%,100%{opacity:1} }
</style>
</head>
<body>
<div class="wrap">
  <div class="fibra"></div><div class="fibra f2"></div><div class="fibra f3"></div>
  <div class="base"></div>
  <div class="crest"><img id="emb" class="wings" alt=""><img id="icon" class="icon" alt=""></div>
  <div class="cinta"></div>
  <div class="card">
    <i class="hb t"></i><i class="hb l"></i><i class="hb r"></i><i class="hb b"></i>
    <svg class="tiza" viewBox="-20 -20 40 40"><path d="M0,-18 C1.5,-5 5,-1.5 12,0 C5,1.5 1.5,5 0,18 C-1.5,5 -5,1.5 -12,0 C-5,-1.5 -1.5,-5 0,-18 Z" fill="#d3cec6"/></svg>

    <div class="indic"><i id="i1" class="on"></i><i id="i2"></i></div>
    <div class="head">
      <div class="mid">
        <div class="name" id="nameBox"><span id="name">-</span><span class="tag" id="tag"></span></div>
        <div class="rot">
          <div id="rA">
            <div class="line"><span class="tier holo-texto" id="tier">-</span><span><span id="lp">0</span>LP</span><span class="dot"></span></div>
            <div class="seg" id="seg"></div>
          </div>
          <div id="rB" class="oculta">
            <div class="line"><span><span id="wr">—</span> Winrate</span><span class="sep"></span><span class="muted"><span id="games">-</span> Games</span></div>
            <div class="wrbar"><div class="bw holo-fondo" id="bw" style="width:50%"></div><div class="bl" id="bl" style="width:50%"></div></div>
          </div>
        </div>
      </div>
      <div class="ses">
        <div class="wl"><span class="w holo-texto" id="w">0W</span><span class="d">-</span><span class="l" id="l">0L</span></div>
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
  const Q = new URLSearchParams(location.search);
  const OPCION = Q.get("op") === "1" ? 1 : 2;   // la elegida es la 2
  document.body.classList.add("op" + OPCION);
  if (Q.has("fondo")) document.body.classList.add("fondo");

  const PARTIDAS = 10;   // partidas que se muestran abajo
  const SEGUNDOS = parseFloat(Q.get("seg")) || 10;   // segundos de cada rotacion (?seg=N para probar)
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

  // Borde rasgado (abajo y a la derecha). Opcion 1: la tarjeta misma es el papel.
  // Opcion 2: la tarjeta queda recta con su marco y el papel rasgado asoma 10 px por detras.
  const wrap = document.querySelector(".wrap"), card = document.querySelector(".card");
  function rasgar() {
    const m = OPCION === 2 ? 10 : 0, w = card.offsetWidth + 2 * m, h = card.offsetHeight + 2 * m;
    let s = 7;
    const r = () => { s = (s + 0x6d2b79f5) | 0; let t = Math.imul(s ^ (s >>> 15), 1 | s); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
    const fuera = [[0, 0]], dentro = [[0, 0]], f2 = [[0, 0]], f3 = [[0, 0]];
    const j = () => (r() - .5) * 5;   // cuanto se corre el borde en las otras dos variantes
    let p = 3;
    for (let y = 0; y < h; y += 11) { p = Math.max(0, Math.min(8, p + (r() - .5) * 6)); const e = 1.5 + r() * 4;
      fuera.push([w - p, y]); dentro.push([w - p - e, y]); f2.push([w - p + j(), y]); f3.push([w - p + j(), y]); }
    p = 2;
    for (let x = w; x > 0; x -= 11) { p = Math.max(0, Math.min(5, p + (r() - .5) * 4)); const e = 1.5 + r() * 3;
      fuera.push([x, h - p]); dentro.push([x, h - p - e]); f2.push([x, h - p + j()]); f3.push([x, h - p + j()]); }
    [fuera, dentro, f2, f3].forEach(a => a.push([0, h]));
    const poli = pts => "polygon(" + pts.map(q => q[0].toFixed(1) + "px " + q[1].toFixed(1) + "px").join(",") + ")";
    // Las fibras tienen 4 px de aire para que el borde pueda moverse hacia afuera.
    const caja = `left:${-m}px;top:${card.offsetTop - m}px;width:${w}px;height:${h}px;`;
    document.querySelectorAll(".fibra").forEach((el, k) => {
      el.style.cssText = `left:${-m}px;top:${card.offsetTop - m}px;width:${w + 4}px;height:${h + 4}px;clip-path:` + poli([fuera, f2, f3][k]);
    });
    document.querySelector(".base").style.cssText = caja + "clip-path:" + poli(dentro);
    card.style.clipPath = OPCION === 1 ? poli(dentro) : "none";
  }
  new ResizeObserver(rasgar).observe(card);

  // Escudo de rango: primero la copia local (./emblemas, la baja el script) y, si todavia no esta,
  // la de internet. Si ninguna cargo, se vuelve a probar en cada actualizacion hasta que aparezca.
  function ponerEscudo(tier) {
    const el = $("emb");
    const local = `./emblemas/wings_${tier}.png?v=` + Date.now();
    const remoto = `https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/wings/wings_${tier}.png`;
    if (el.dataset.tier === tier && (el.naturalWidth || !el.complete)) return;   // ya se ve, o todavia esta cargando
    el.dataset.tier = tier;
    el.onerror = () => { if (el.dataset.paso === "local") { el.dataset.paso = "remoto"; el.src = remoto; } };
    el.dataset.paso = "local";
    el.src = local;
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

      ponerEscudo(tier.toLowerCase());
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
          const cls = m.etiqueta === "MVP" ? "mvp holo-fondo" : m.etiqueta === "ACE" ? "ace" : "";
          return `<div class="m ${m.win ? "win" : "loss"}" style="animation-delay:${k * .05}s">
            <img src="${m.img}" alt=""><div class="bar ${m.win ? "holo-fondo" : ""}"></div><span class="${cls}">${m.etiqueta}</span></div>`;
        }).join("");
      }
    } catch (e) { console.error(e); }
  }
  actualizar();
  setInterval(actualizar, 3000);

  /* ================= cambio de rotacion con glitch ================= */
  const DURACION = 650;                      // ms que dura el glitch de cada rotacion
  const SIMBOLOS = "█▓▒░#%&@$*+=/<>_-";
  card.insertAdjacentHTML("beforeend", '<div class="grain"></div><div class="tears"><i></i><i></i><i></i><i></i><i></i></div>');

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
    card.querySelectorAll(".tears i").forEach(t => { t.style.top = (8 + Math.random() * 80) + "%"; t.style.height = (2 + Math.random() * 9) + "px"; });
    clearTimeout(wrap._t); wrap._t = setTimeout(() => wrap.classList.remove("glitch"), DURACION);
  }

  // Rotacion de la parte de arriba. Con ?slide=1 o ?slide=2 en la ruta queda fija.
  function poner(n) {
    ["rA", "sA"].forEach(id => $(id).classList.toggle("oculta", n !== 1));
    ["rB", "sB"].forEach(id => $(id).classList.toggle("oculta", n !== 2));
    $("i1").classList.toggle("on", n === 1); $("i2").classList.toggle("on", n === 2);
  }
  function mostrar(n) {
    lanzarGlitch();
    setTimeout(() => {
      poner(n);
      (n === 1 ? ["rA", "sA"] : ["rB", "sB"]).forEach(id => hojas(id).forEach(el => revelar(el, 500)));
    }, 110);
  }
  const fija = parseInt(Q.get("slide"));
  if (fija === 1 || fija === 2) poner(fija);
  else { let n = 1; poner(n); setInterval(() => { n = n === 1 ? 2 : 1; mostrar(n); }, SEGUNDOS * 1000); }

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
    guardar_datos_json()
    threading.Thread(target=obtener_datos_riot, daemon=True).start()

    print("Tracker IN-GAME inicializado.")
    while True:
        time.sleep(10)
