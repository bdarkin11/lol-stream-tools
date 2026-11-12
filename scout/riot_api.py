"""
riot_api.py – Cliente único de la API de Riot Games.

Centraliza todas las llamadas a la API de Riot con:
  - Caché de respuestas (Data Dragon, partidas, datos de invocador).
  - Manejo de rate-limit 429 con espera según el encabezado Retry-After.
  - Logs detallados en caso de error (sin imprimir claves).
  - match_cache limitado a MAX_MATCH_CACHE partidas.
"""
import logging
import time
import urllib.parse
import threading
from collections import OrderedDict

import requests

from config import (
    RIOT_API_KEY, RIOT_REGION_ACCOUNT, RIOT_REGION_LOL,
    TABLA_INDICE_BASE, TABLA_DIVISION, MAX_MATCH_CACHE,
)

log = logging.getLogger("riot_api")


class RiotAPI:
    """Consumidor único de la API de Riot.

    Todas las llamadas HTTP pasan por ``_get`` que gestiona 429 y logging.
    """

    def __init__(self):
        self.api_key = RIOT_API_KEY
        self.headers = {"X-Riot-Token": self.api_key}
        self.session = requests.Session()

        # Caché ----------------------------------------------------------
        self._ddragon_version: str | None = None
        self._champion_map: dict[str, str] = {}     # key (id numérico str) → nombre interno
        self._champion_names: dict[str, str] = {}   # nombre lower → key
        self._match_cache: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

        # Datos cacheados del jugador local
        self._puuid: str | None = None

        # Inicializar Data Dragon
        self._init_ddragon()

    # ── Data Dragon ────────────────────────────────────────────────────
    def _init_ddragon(self):
        """Obtiene la versión actual de Data Dragon y carga el mapa de campeones."""
        try:
            ver_resp = self._get("https://ddragon.leagueoflegends.com/api/versions.json", use_riot_key=False)
            if ver_resp is not None:
                self._ddragon_version = ver_resp[0]
                log.info("Data Dragon versión: %s", self._ddragon_version)
        except Exception as exc:
            log.error("No se pudo obtener la versión de Data Dragon: %s", exc)

        if not self._ddragon_version:
            self._ddragon_version = "14.8.1"  # fallback razonable
            log.warning("Usando versión de Data Dragon de respaldo: %s", self._ddragon_version)

        try:
            url = f"https://ddragon.leagueoflegends.com/cdn/{self._ddragon_version}/data/es_AR/champion.json"
            data = self._get(url, use_riot_key=False)
            if data and "data" in data:
                for champ_info in data["data"].values():
                    key = champ_info["key"]
                    name = champ_info["id"]          # nombre interno (Aatrox, Ahri…)
                    self._champion_map[key] = name
                    self._champion_names[champ_info["name"].lower()] = key
                log.info("Mapa de campeones cargado (%d campeones).", len(self._champion_map))
        except Exception as exc:
            log.error("No se pudo cargar el mapa de campeones: %s", exc)

    @property
    def ddragon_version(self) -> str:
        return self._ddragon_version or "14.8.1"

    @property
    def champion_map(self) -> dict[str, str]:
        """key (id numérico como str) → nombre interno del campeón."""
        return self._champion_map

    # ── HTTP con rate-limit ────────────────────────────────────────────
    def _get(self, url: str, *, use_riot_key: bool = True, retries: int = 3):
        """GET con reintentos y espera automática ante 429."""
        headers = self.headers if use_riot_key else {}
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(url, headers=headers, timeout=10)
            except requests.RequestException as exc:
                log.warning("Intento %d/%d – Error de red al consultar %s: %s", attempt, retries, url, exc)
                time.sleep(2)
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "5"))
                log.warning("Rate-limit 429 en %s – esperando %.1f s (intento %d/%d)", url, retry_after, attempt, retries)
                time.sleep(retry_after)
                continue

            if resp.status_code == 404:
                log.debug("404 en %s", url)
                return None

            log.error("Error %d en %s: %s (intento %d/%d)", resp.status_code, url, resp.text[:200], attempt, retries)
            time.sleep(2)

        log.error("Se agotaron los reintentos para %s", url)
        return None

    # ── Cuenta / Invocador ─────────────────────────────────────────────
    def get_puuid(self, game_name: str, tag_line: str) -> str | None:
        """Obtiene el PUUID a partir del Riot ID."""
        gn = urllib.parse.quote(game_name.strip())
        tg = urllib.parse.quote(tag_line.strip())
        url = f"https://{RIOT_REGION_ACCOUNT}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{gn}/{tg}"
        data = self._get(url)
        if data:
            self._puuid = data.get("puuid")
            return self._puuid
        return None

    def get_summoner_level(self, puuid: str) -> int:
        url = f"https://{RIOT_REGION_LOL}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
        data = self._get(url)
        if data:
            return data.get("summonerLevel", 0)
        return 0

    # ── Ranked (usa PUUID, sin fallback a summonerId) ──────────────────
    def get_ranked_stats(self, puuid: str) -> dict:
        """Consulta solo por PUUID (corrección #2: sin fallback por summonerId)."""
        url = f"https://{RIOT_REGION_LOL}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
        data = self._get(url)

        stats = {
            "tier": "UNRANKED", "rank": "", "lp": 0,
            "wins": 0, "losses": 0, "wr": 0.0, "total_games": 0,
        }

        if not data or not isinstance(data, list):
            return stats

        queue_data = None
        for entry in data:
            if isinstance(entry, dict) and entry.get("queueType") == "RANKED_SOLO_5x5":
                queue_data = entry
                break

        if queue_data is None:
            return stats

        wins = queue_data.get("wins", 0)
        losses = queue_data.get("losses", 0)
        total = wins + losses
        stats["tier"] = queue_data.get("tier", "UNRANKED")
        stats["rank"] = queue_data.get("rank", "")
        stats["lp"] = queue_data.get("leaguePoints", 0)
        stats["wins"] = wins
        stats["losses"] = losses
        stats["wr"] = round((wins / total) * 100, 1) if total > 0 else 0.0
        stats["total_games"] = total
        return stats

    # ── Partidas ───────────────────────────────────────────────────────
    def get_match_ids(self, puuid: str, count: int = 20) -> list[str]:
        url = (
            f"https://{RIOT_REGION_ACCOUNT}.api.riotgames.com"
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&start=0&count={count}"
        )
        data = self._get(url)
        return data if isinstance(data, list) else []

    def get_match(self, match_id: str) -> dict | None:
        """Obtiene los datos de una partida, usando caché limitada a MAX_MATCH_CACHE."""
        with self._lock:
            if match_id in self._match_cache:
                self._match_cache.move_to_end(match_id)
                return self._match_cache[match_id]

        url = f"https://{RIOT_REGION_ACCOUNT}.api.riotgames.com/lol/match/v5/matches/{match_id}"
        data = self._get(url)
        if data:
            with self._lock:
                self._match_cache[match_id] = data
                # Limitar a MAX_MATCH_CACHE entradas (corrección #6)
                while len(self._match_cache) > MAX_MATCH_CACHE:
                    self._match_cache.popitem(last=False)
        return data

    # ── Estimación de Índice (antes "MMR") ─────────────────────────────
    # NOTA: Este valor NO es el MMR real de Riot Games.  Es una estimación
    # propia basada en el tier, la división y los resultados de las últimas
    # partidas del jugador.
    def calcular_indice(self, tier: str, rank: str, puuid: str) -> int:
        base = TABLA_INDICE_BASE.get(tier, 2400) + TABLA_DIVISION.get(rank, 0)
        match_ids = self.get_match_ids(puuid, count=5)
        if not match_ids:
            return base

        ajuste_total = 0
        analizadas = 0
        for m_id in match_ids:
            match_data = self.get_match(m_id)
            if not match_data or "info" not in match_data:
                continue
            for p in match_data["info"]["participants"]:
                if p["puuid"] == puuid:
                    ajuste_total += 15 if p.get("win") else -10
                    analizadas += 1
                    break

        if analizadas > 0:
            base += int(ajuste_total / analizadas)
        return base

    # ── Datos completos para overlay / chat ────────────────────────────
    def build_full_state(self, game_name: str, tag_line: str,
                         prev_lp: int | None, indice_inicial: int | None,
                         wins_iniciales: int | None, losses_iniciales: int | None):
        """Consulta todo y devuelve un dict con la información para estado.json.

        Retorna ``None`` si no se pudo obtener datos del jugador.
        """
        puuid = self.get_puuid(game_name, tag_line)
        if not puuid:
            return None

        ranked = self.get_ranked_stats(puuid)
        tier = ranked["tier"]
        rank = ranked["rank"]
        lp = ranked["lp"]
        wins = ranked["wins"]
        losses = ranked["losses"]
        wr = ranked["wr"]

        # Índice estimado
        indice = self.calcular_indice(tier, rank, puuid)

        # Sesión de hoy
        if wins_iniciales is None:
            wins_iniciales = wins
        if losses_iniciales is None:
            losses_iniciales = losses

        wins_hoy = max(0, wins - wins_iniciales)
        losses_hoy = max(0, losses - losses_iniciales)

        if prev_lp is None:
            prev_lp = lp
        if indice_inicial is None:
            indice_inicial = indice

        indice_diff = indice - indice_inicial

        # Partidas recientes, top campeones, KDA
        match_ids = self.get_match_ids(puuid, count=20)
        champ_counts: dict[str, int] = {}
        partidas_info: list[dict] = []
        total_k, total_d, total_a = 0, 0, 0
        partidas_contadas = 0

        for i, mid in enumerate(match_ids):
            mdata = self.get_match(mid)
            if not mdata or "info" not in mdata:
                continue
            for p in mdata["info"]["participants"]:
                if p["puuid"] == puuid:
                    campeon = p.get("championName", "Desconocido")
                    if campeon == "FiddleSticks":
                        campeon = "Fiddlesticks"
                    champ_counts[campeon] = champ_counts.get(campeon, 0) + 1

                    if i < 5:
                        k = p.get("kills", 0)
                        d = p.get("deaths", 0)
                        a = p.get("assists", 0)
                        total_k += k
                        total_d += d
                        total_a += a
                        partidas_contadas += 1
                        partidas_info.append({
                            "champion": campeon,
                            "win": bool(p.get("win")),
                            "kills": k,
                            "deaths": d,
                            "assists": a,
                        })
                    break

        top_champs = sorted(champ_counts.items(), key=lambda x: x[1], reverse=True)[:3]

        if partidas_contadas > 0:
            avg_k = round(total_k / partidas_contadas, 1)
            avg_d = round(total_d / partidas_contadas, 1)
            avg_a = round(total_a / partidas_contadas, 1)
            kda_str = f"{avg_k} / {avg_d} / {avg_a}"
        else:
            kda_str = "0.0 / 0.0 / 0.0"

        state = {
            "puuid": puuid,
            "game_name": game_name,
            "tag_line": tag_line,
            "tier": tier,
            "rank": rank,
            "lp": lp,
            "lp_prev": prev_lp,
            "wins": wins,
            "losses": losses,
            "wr": wr,
            "indice": indice,
            "indice_diff": indice_diff,
            "wins_hoy": wins_hoy,
            "losses_hoy": losses_hoy,
            "top_champs": [{"name": c, "count": n} for c, n in top_champs],
            "partidas": partidas_info,
            "kda_promedio": kda_str,
            "ddragon_version": self.ddragon_version,
            "wins_iniciales": wins_iniciales,
            "losses_iniciales": losses_iniciales,
            "indice_inicial": indice_inicial if indice_inicial is not None else indice,
            "timestamp": time.time(),
        }
        return state

    # ── Texto de rank para el chat ─────────────────────────────────────
    @staticmethod
    def format_rank_chat(state: dict) -> str:
        """Genera la línea de texto que se envía al chat ante !rank."""
        tier = state["tier"].capitalize()
        rank = state["rank"]
        lp = state["lp"]
        wins = state["wins"]
        losses = state["losses"]
        wr = state["wr"]
        w_hoy = state["wins_hoy"]
        l_hoy = state["losses_hoy"]
        return (
            f"{tier} {rank} {lp} LP – Sesión: {w_hoy}W / {l_hoy}L – "
            f"Totales: {wins}W / {losses}L – {wr}% WR"
        )

