import os
import sys
import subprocess
import threading


def _auto_install_deps():
    def _install():
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        req = os.path.join(plugin_dir, "requirements.txt")
        if not os.path.exists(req):
            return
        try:
            with open(req, "r", encoding="utf-8") as f:
                pkgs = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        except Exception:
            return
        if not pkgs:
            return
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", req, "--no-cache-dir", "-q"],
                capture_output=True, text=True, timeout=120,
            )
        except Exception:
            pass

    threading.Thread(target=_install, daemon=True).start()


_auto_install_deps()

import asyncio
import aiohttp
import json
import time
from datetime import datetime
from typing import Any

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

KV_KEY_STATES = "server_states"

DEFAULT_ACTIVE_QUERY_PROMPT = (
    "判断用户消息意图，只返回一个标签：\n"
    "BOT_QUERY — 明确提到 BOT/机器人/astrbot/QQ机器人 查询状态\n"
    "MC_QUERY  — 明确提到 MC/Minecraft/我的世界/游戏服务器 查询状态\n"
    "ASK_CLARIFY — 提到服务器/服务状态但未指明 BOT 或 MC\n"
    "OTHER — 与以上无关\n\n"
    "只输出标签，不要其他文字。\n"
    "消息: {user_message}"
)


@register(
    "astrbot_plugin_astrbot_llbot_server",
    "YCHDDZZ",
    "MC 与 BOT 服务器状态监控——定时监控 astrbot/QQ 存活 + MC 游戏服务器状态，支持自然语言主动查询",
    "2.0.0",
)
class ServerMonitorPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self.session: aiohttp.ClientSession = None
        self._monitor_task: asyncio.Task = None
        self._qqofficial_platform = None
        self._server_states: dict[str, dict[str, Any]] = {}
        self._last_notify_time: dict[str, float] = {}
        self._recovery_counters: dict[str, int] = {}
        self._check_count = 0
        self._last_active_check: float = 0
        self._pending_clarify: dict[str, float] = {}

    # ==================== 生命周期 ====================

    async def initialize(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        self._find_qqofficial_platform()
        await self._load_persisted_states()
        self._init_server_states()
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        servers = self._get_servers()
        mc = self._get_mc_servers()
        interval = self.config.get("check_interval", 60) if self.config else 60
        logger.info(f"服务监控已加载: {len(servers)} 台 BOT + {len(mc)} 台 MC, 间隔 {interval}s")
        if not self._qqofficial_platform:
            logger.warning("未找到 QQ 官方平台适配器，通知功能不可用")

    async def terminate(self):
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        if self.session:
            await self.session.close()
        logger.info("服务监控已卸载")

    def _find_qqofficial_platform(self):
        for p in self.context.platform_manager.platform_insts:
            if p.meta().name == "qq_official":
                self._qqofficial_platform = p
                return

    # ==================== 配置读取 ====================

    def _get_servers(self) -> list:
        if not self.config:
            return []
        raw = self.config.get("servers", [])
        return raw if isinstance(raw, list) else []

    def _get_mc_servers(self) -> list:
        if not self.config:
            return []
        raw = self.config.get("mc_servers", [])
        return raw if isinstance(raw, list) else []

    def _get_notification_groups(self) -> list[str]:
        if not self.config:
            return []
        raw = (self.config.get("notification_groups", "") or "").strip()
        return [g.strip() for g in raw.split("\n") if g.strip()]

    def _get_at_users(self) -> list[str]:
        if not self.config:
            return []
        raw = (self.config.get("at_users", "") or "").strip()
        return [u.strip() for u in raw.split("\n") if u.strip()]

    # ==================== 状态持久化 ====================

    def _init_server_states(self):
        for s in self._get_servers():
            name = s.get("name", "").strip()
            if name and name not in self._server_states:
                self._server_states[name] = {"astrbot": None, "llbot": None}

    async def _save_persisted_states(self):
        try:
            clean = {
                n: st
                for n, st in self._server_states.items()
                if st.get("astrbot") is not None or st.get("llbot") is not None
            }
            if clean:
                await self.put_kv_data(KV_KEY_STATES, clean)
        except Exception as e:
            logger.error(f"保存状态失败: {e}")

    async def _load_persisted_states(self):
        try:
            data = await self.get_kv_data(KV_KEY_STATES, None)
            if isinstance(data, dict):
                self._server_states = data
                logger.info("已恢复持久化的服务器状态")
        except Exception as e:
            logger.error(f"加载状态失败: {e}")

    # ==================== BOT 监控主循环 ====================

    async def _monitor_loop(self):
        while True:
            try:
                interval = self.config.get("check_interval", 60) if self.config else 60
                await self._run_checks()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"监控循环异常: {e}")
                await asyncio.sleep(30)

    async def _run_checks(self):
        servers = self._get_servers()
        if not servers:
            return
        tasks = [self._check_server(s) for s in servers]
        await asyncio.gather(*tasks)
        await self._save_persisted_states()
        self._check_count += 1
        if self._check_count % 60 == 0:
            online = 0
            total = 0
            for s in servers:
                name = s.get("name", "").strip()
                st = self._server_states.get(name, {})
                for svc in ("astrbot", "llbot"):
                    if s.get(f"enable_{svc}", True):
                        total += 1
                        if st.get(svc) is True:
                            online += 1
            logger.info(f"[心跳] 第 {self._check_count} 次, {online}/{total} 在线")

    async def _check_server(self, s: dict):
        name = s.get("name", "").strip()
        if not name:
            return
        enable_a = s.get("enable_astrbot", True)
        enable_l = s.get("enable_llbot", True)
        old_states = self._server_states.get(name, {"astrbot": None, "llbot": None})
        changes = []
        confirm = self.config.get("recovery_confirm_count", 1) if self.config else 1

        # astrbot
        if enable_a:
            alive = await self._check_astrbot(s)
            cur = old_states.get("astrbot")
            if cur is not None and cur != alive:
                if alive:
                    rk = f"{name}|astrbot"
                    c = self._recovery_counters.get(rk, 0) + 1
                    if c >= confirm:
                        self._recovery_counters.pop(rk, None)
                        self._server_states.setdefault(name, {})["astrbot"] = True
                        changes.append(("astrbot", cur, True))
                    else:
                        self._recovery_counters[rk] = c
                else:
                    self._recovery_counters.pop(f"{name}|astrbot", None)
                    self._server_states.setdefault(name, {})["astrbot"] = False
                    changes.append(("astrbot", cur, False))
            elif cur is None:
                self._server_states.setdefault(name, {})["astrbot"] = alive
        else:
            self._server_states.setdefault(name, {})["astrbot"] = None

        # llbot — 纯 HTTP 存活性检查（不解析 JSON）
        if enable_l:
            alive = await self._check_llbot(s)
            cur = old_states.get("llbot")
            if cur is not None and cur != alive:
                if alive:
                    rk = f"{name}|llbot"
                    c = self._recovery_counters.get(rk, 0) + 1
                    if c >= confirm:
                        self._recovery_counters.pop(rk, None)
                        self._server_states.setdefault(name, {})["llbot"] = True
                        changes.append(("llbot", cur, True))
                    else:
                        self._recovery_counters[rk] = c
                else:
                    self._recovery_counters.pop(f"{name}|llbot", None)
                    self._server_states.setdefault(name, {})["llbot"] = False
                    changes.append(("llbot", cur, False))
            elif cur is None:
                self._server_states.setdefault(name, {})["llbot"] = alive
        else:
            self._server_states.setdefault(name, {})["llbot"] = None

        for service, old, new in changes:
            logger.info(f"[状态变更] {name} {service}: {'恢复' if new else '离线'}")
            await self._send_notification(name, service, old, new)

    async def _check_astrbot(self, s: dict) -> bool:
        url = s.get("astrbot_url", "").strip()
        if not url:
            return False
        if not url.startswith("http"):
            url = "http://" + url
        url = url.rstrip("/")
        timeout = self.config.get("http_timeout", 10) if self.config else 10
        headers = {}
        key = s.get("astrbot_api_key", "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            async with self.session.get(url, headers=headers, timeout=timeout) as resp:
                return resp.status < 500
        except Exception:
            return False

    async def _check_llbot(self, s: dict) -> bool:
        hp = s.get("llbot_url", "").strip()
        if not hp:
            return False
        if "://" not in hp:
            hp = "http://" + hp
        hp = hp.rstrip("/")
        timeout = self.config.get("http_timeout", 10) if self.config else 10
        path = s.get("llbot_health_path", "/get_login_info").strip()
        if not path.startswith("/"):
            path = "/" + path

        # 依次尝试：配置路径 → Milky路径 → OneBot11路径
        paths = [path]
        if path != "/api/get_login_info":
            paths.append("/api/get_login_info")
        if path != "/get_login_info":
            paths.append("/get_login_info")

        for p in paths:
            try:
                async with self.session.get(hp + p, timeout=timeout) as resp:
                    if resp.status < 500:
                        try:
                            data = await resp.json(content_type=None)
                        except Exception:
                            continue
                        if self._verify_llbot_login(data):
                            return True
            except Exception:
                continue

        # 兜底：根路径
        try:
            async with self.session.get(hp, timeout=timeout) as resp:
                return resp.status < 500
        except Exception:
            return False

    def _verify_llbot_login(self, data: dict) -> bool:
        if data.get("data", {}).get("online", False):
            return True
        if isinstance(data.get("data"), dict) and data["data"].get("user_id"):
            return True
        if data.get("status") == "ok" and data.get("retcode") == 0:
            return True
        return False

    # ==================== 通知 ====================

    async def _send_notification(self, server_name: str, service: str, old: bool, new: bool):
        enable = self.config.get("enable_notification", True) if self.config else True
        if not enable:
            return
        groups = self._get_notification_groups()
        if not groups:
            return

        if not new:
            cd = self.config.get("notification_cooldown", 300) if self.config else 300
            key = f"{server_name}|{service}"
            last = self._last_notify_time.get(key, 0)
            if time.time() - last < cd:
                return
            self._last_notify_time[key] = time.time()

        label = "astrbot" if service == "astrbot" else "QQ"
        if new:
            line = f"{label} 已恢复"
        else:
            line = f"{label} 掉线" if service == "llbot" else f"{label} 无法连接"

        at_text = ""
        if self.config.get("enable_at_users", False) if self.config else False:
            users = self._get_at_users()
            if users:
                at_text = "\n" + " ".join(f"@{u}" for u in users)

        msg = f"[服务监控]\n服务器：{server_name}\n状态：{line}{at_text}"

        if self._qqofficial_platform:
            try:
                client = self._qqofficial_platform.get_client()
                for gid in groups:
                    gid = gid.strip()
                    if not gid:
                        continue
                    if gid.isdigit():
                        logger.warning(f"群 {gid} 是 QQ 号而非 openid，请检查")
                    try:
                        await client.api.post_group_message(
                            group_openid=gid, content=msg, msg_type=0, msg_seq=1,
                        )
                        logger.info(f"已向群 {gid} 发送通知")
                    except Exception as e:
                        logger.error(f"发送通知失败({gid}): {e}")
            except Exception as e:
                logger.error(f"通知发送异常: {e}")
        else:
            logger.warning("无可用平台，无法发送通知")

    # ==================== BOT 实时查询 ====================

    async def _query_astrbot(self, s: dict) -> dict:
        url = s.get("astrbot_url", "").strip()
        if not url:
            return {"alive": False, "detail": "未配置", "time_ms": 0, "code": None}
        if not url.startswith("http"):
            url = "http://" + url
        url = url.rstrip("/")
        timeout = self.config.get("http_timeout", 10) if self.config else 10
        headers = {}
        key = s.get("astrbot_api_key", "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        start = time.time()
        try:
            async with self.session.get(url, headers=headers, timeout=timeout) as resp:
                ms = int((time.time() - start) * 1000)
                return {"alive": resp.status < 500, "detail": f"HTTP {resp.status}", "time_ms": ms, "code": resp.status}
        except asyncio.TimeoutError:
            ms = int((time.time() - start) * 1000)
            return {"alive": False, "detail": "超时", "time_ms": ms, "code": None}
        except Exception as e:
            ms = int((time.time() - start) * 1000)
            return {"alive": False, "detail": str(e)[:40], "time_ms": ms, "code": None}

    async def _query_llbot(self, s: dict) -> dict:
        hp = s.get("llbot_url", "").strip()
        if not hp:
            return {"alive": False, "detail": "未配置", "time_ms": 0, "code": None}
        if "://" not in hp:
            hp = "http://" + hp
        hp = hp.rstrip("/")
        path = s.get("llbot_health_path", "/get_login_info").strip()
        if not path.startswith("/"):
            path = "/" + path

        paths = [path]
        if path != "/api/get_login_info":
            paths.append("/api/get_login_info")
        if path != "/get_login_info":
            paths.append("/get_login_info")

        for p in paths:
            start = time.time()
            try:
                async with self.session.get(hp + p, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    ms = int((time.time() - start) * 1000)
                    if resp.status < 500:
                        try:
                            data = await resp.json(content_type=None)
                        except Exception:
                            continue
                        if self._verify_llbot_login(data):
                            return {"alive": True, "detail": "LLBOT在线，QQ已登录", "time_ms": ms, "code": resp.status}
            except Exception:
                continue

        # 兜底：根路径
        start = time.time()
        try:
            async with self.session.get(hp, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                ms = int((time.time() - start) * 1000)
                if resp.status < 500:
                    return {"alive": True, "detail": "LLBOT在线，通信正常，但无法验证QQ是否已登录", "time_ms": ms, "code": resp.status}
                return {"alive": False, "detail": f"HTTP {resp.status}", "time_ms": ms, "code": resp.status}
        except asyncio.TimeoutError:
            ms = int((time.time() - start) * 1000)
            return {"alive": False, "detail": "超时", "time_ms": ms, "code": None}
        except Exception as e:
            ms = int((time.time() - start) * 1000)
            return {"alive": False, "detail": str(e)[:40], "time_ms": ms, "code": None}

    async def _query_bot_servers(self, server_name: str = None) -> list:
        servers = self._get_servers()
        target = [s for s in servers if not server_name or s.get("name", "").strip() == server_name]
        if not target:
            return []

        async def _one(s):
            name = s.get("name", "").strip()
            a_en = s.get("enable_astrbot", True)
            l_en = s.get("enable_llbot", True)
            if a_en and l_en:
                ar, lr = await asyncio.gather(self._query_astrbot(s), self._query_llbot(s))
            elif a_en:
                ar, lr = await self._query_astrbot(s), {"alive": None, "detail": "未启用", "time_ms": 0, "code": None}
            elif l_en:
                ar, lr = {"alive": None, "detail": "未启用", "time_ms": 0, "code": None}, await self._query_llbot(s)
            else:
                ar = lr = {"alive": None, "detail": "未启用", "time_ms": 0, "code": None}
            return {"name": name, "astrbot": ar, "QQ": lr}

        results = await asyncio.gather(*[_one(s) for s in target])
        return list(results)

    def _format_bot_results(self, results: list, realtime: bool = True) -> str:
        if not results:
            return "暂无 BOT 服务器配置。"
        lines = []
        for r in results:
            name = r["name"]
            lines.append(f"━━━━ {name} ━━━━")

            def _row(label, info):
                if info is None:
                    lines.append(f"  {label}  [-] 未配置")
                    return
                a = info.get("alive")
                if a is None:
                    lines.append(f"  {label}  [-] 未启用")
                    return
                t = info.get("time_ms", 0)
                d = info.get("detail", "")
                icon = "🟢" if a else "🔴"
                status = "在线" if a else "离线"
                lines.append(f"  {label}  {icon} {status}")
                parts = []
                if d:
                    parts.append(d)
                parts.append(f"{t}ms")
                lines.append(f"         {chr(9472)}  " + "  ·  ".join(parts))

            _row("astrbot", r.get("astrbot"))
            _row("QQ", r.get("QQ"))
            lines.append(f"  ⏱ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append("")
        return "\n".join(lines)

    # ==================== MC 服务器查询（严格遵循 MineBBSClient 实现） ====================

    async def _query_mc_server(self, s: dict) -> dict:
        ip = (s.get("mc_ip", "") or "").strip()
        if not ip:
            return {"online": False, "error": "未配置", "time_ms": 0}
        port = s.get("mc_port", 25565)
        stype = "je" if str(s.get("mc_server_type", "java")).lower() in ("java", "je") else "be"
        params = {"ip": ip, "port": str(port), "stype": stype, "_ts": str(int(time.time() * 1000))}
        start = time.time()
        try:
            logger.info(f"[MC查询] ip={ip} port={port} stype={stype}")
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://motd.minebbs.com/api/status",
                    params=params,
                    headers={
                        "User-Agent": "MCBotMonitor/2.0",
                        "Cache-Control": "no-cache",
                        "Pragma": "no-cache",
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    raw_text = await response.text()
                    ms = int((time.time() - start) * 1000)
                    if response.status != 200:
                        logger.warning(f"[MC查询] HTTP {response.status}, body={raw_text[:300]}")
                        return {"online": False, "error": f"HTTP {response.status}", "time_ms": ms}
                    try:
                        data = await response.json(content_type=None)
                    except Exception as exc:
                        logger.warning(f"[MC查询] JSON解析失败: {exc}, body={raw_text[:300]}")
                        return {"online": False, "error": "JSON解析失败", "time_ms": ms}

                    result = self._parse_mc(s, data, ms)

                    # MineBBS 有时玩家列表不稳定，在线玩家 >0 时再查一次
                    if result.get("online") and result.get("online_players", 0) > 0:
                        await asyncio.sleep(0.3)
                        retry_params = dict(params)
                        retry_params["_ts"] = str(int(time.time() * 1000))
                        try:
                            async with session.get(
                                "https://motd.minebbs.com/api/status",
                                params=retry_params,
                                headers={
                                    "User-Agent": "MCBotMonitor/2.0",
                                    "Cache-Control": "no-cache",
                                    "Pragma": "no-cache",
                                },
                                timeout=aiohttp.ClientTimeout(total=15),
                            ) as resp2:
                                if resp2.status == 200:
                                    data2 = await resp2.json(content_type=None)
                                    ms2 = int((time.time() - start) * 1000)
                                    result2 = self._parse_mc(s, data2, ms2)
                                    logger.info(f"[MC查询] 重查结果: online={result2.get('online_players')} players={result2.get('player_list')}")
                                    return result2
                        except Exception:
                            pass

                    return result
        except asyncio.TimeoutError:
            ms = int((time.time() - start) * 1000)
            return {"online": False, "error": "超时", "time_ms": ms}
        except Exception as exc:
            ms = int((time.time() - start) * 1000)
            logger.error(f"[MC查询] 异常: {exc}")
            return {"online": False, "error": str(exc)[:60], "time_ms": ms}

    def _parse_mc(self, s: dict, data: dict, elapsed: int) -> dict:
        raw_status = str(data.get("status", "offline")).strip().lower()
        online = raw_status == "online"

        players_info = data.get("players", {})
        if isinstance(players_info, dict):
            online_players = int(players_info.get("online", 0) or 0)
            max_players = int(players_info.get("max", 0) or 0)
            sample = players_info.get("sample") or []
            player_list = []
            if isinstance(sample, str):
                player_list = [n.strip() for n in sample.split(",") if n.strip()]
            elif isinstance(sample, list):
                for p in sample:
                    if isinstance(p, dict):
                        name = p.get("name_clean") or p.get("name") or p.get("username", "")
                    elif isinstance(p, str):
                        name = p.strip()
                    else:
                        name = str(p) if p else ""
                    if name:
                        player_list.append(name.strip())

            logger.info(
                f"[MC解析] online={online_players} sample_type={type(sample).__name__} "
                f"sample_len={len(sample) if isinstance(sample, (list, str)) else 0} "
                f"players={player_list}"
            )
        else:
            online_players = 0
            max_players = 0
            player_list = []

        error = str(data.get("error") or "").strip() or None
        if error and online:
            error = None

        motd_info = data.get("motd", {})
        if isinstance(motd_info, dict):
            motd = str(data.get("pureMotd") or motd_info.get("text") or "")
        else:
            motd = str(data.get("pureMotd") or motd_info or "")

        host = str(data.get("host") or f"{s.get('mc_ip', '')}:{s.get('mc_port', '')}")
        delay = int(data.get("delay", 0) or 0)

        logger.info(
            f"[MC查询] {s.get('mc_ip', '')}:{s.get('mc_port', '')} "
            f"status={raw_status}, online={online_players}/{max_players}, players={player_list}"
        )
        return {
            "online": online,
            "version": str(data.get("version", "")),
            "server_type": str(data.get("type", "")),
            "online_players": online_players,
            "max_players": max_players,
            "player_list": player_list,
            "motd": motd[:200],
            "host": host,
            "delay": delay if delay else elapsed,
            "error": error,
            "time_ms": delay if delay else elapsed,
            "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    async def _query_mc_servers(self, server_name: str = None) -> list:
        servers = self._get_mc_servers()
        target = [s for s in servers if not server_name or s.get("name", "").strip() == server_name]
        if not target:
            return []
        tasks = [self._query_mc_server(s) for s in target]
        snaps = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for s, snap in zip(target, snaps):
            name = s.get("name", "").strip()
            if isinstance(snap, Exception):
                results.append({"name": name, "online": False, "error": str(snap)[:50], "time_ms": 0})
            else:
                snap["name"] = name
                results.append(snap)
        return results

    def _format_mc_results(self, results: list) -> str:
        if not results:
            return "暂无 MC 服务器配置。"
        lines = []
        for i, r in enumerate(results, 1):
            name = r.get("name", "?")
            online = r.get("online", False)
            icon = "🟢" if online else "🔴"

            lines.append(f"===== {i}. {name} =====")
            lines.append(f"{icon} 服务器: {name}")
            host = r.get("host", "")
            if host:
                lines.append(f"🌐 地址: {host}")
            st = r.get("server_type", "")
            if st:
                lines.append(f"🔧 类型: {'Java版' if 'java' in str(st).lower() else '基岩版'}")
            ver = r.get("version", "")
            if ver:
                lines.append(f"🎮 版本: {ver}")
            lines.append(f"👥 在线玩家: {r.get('online_players', 0)}/{r.get('max_players', 0)}")
            motd = (r.get("motd") or "").strip()
            if motd:
                lines.append(f"📝 MOTD: {motd[:120]}{'...' if len(motd) > 120 else ''}")
            plist = r.get("player_list", [])
            if plist:
                lines.append(f"📋 玩家列表: {', '.join(plist)}")
            lines.append(f"🕒 更新时间: {r.get('query_time', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}")
            if not online:
                err = r.get("error", "")
                if err:
                    lines.append(f"⚠️ 错误: {err}")
            lines.append(f"⏱ 延迟: {r.get('time_ms', 0)}ms")
            lines.append("")
        return "\n".join(lines)

    # ==================== LLM 工具 ====================

    async def _get_llm_tools(self):
        from astrbot.core.agent.tool import FunctionTool

        return [
            FunctionTool(
                name="transfer_to_bot_server",
                description="查询 BOT 服务器（astrbot + QQ 机器人）实时运行状态。用户询问 BOT/机器人/astrbot 服务器状态时使用。",
                parameters={"server_name": {"type": "str", "description": "可选服务器名"}},
                func=self._tool_bot_status,
            ),
            FunctionTool(
                name="transfer_to_mc_server",
                description="查询 Minecraft 游戏服务器实时状态（在线玩家、版本、MOTD 等）。用户询问 MC/Minecraft/我的世界 服务器时使用。",
                parameters={"server_name": {"type": "str", "description": "可选服务器名"}},
                func=self._tool_mc_status,
            ),
        ]

    async def _tool_bot_status(self, server_name: str = "", **kwargs):
        results = await self._query_bot_servers(server_name if server_name else None)
        return self._format_bot_results(results, realtime=True)

    async def _tool_mc_status(self, server_name: str = "", **kwargs):
        results = await self._query_mc_servers(server_name if server_name else None)
        return self._format_mc_results(results)

    # ==================== 意图识别 ====================

    async def _detect_intent(self, message: str) -> str:
        pid = (self.config.get("active_query_provider", "") or "").strip() if self.config else ""
        if not pid:
            try:
                pm = self.context.provider_manager
                if hasattr(pm, 'curr_provider_name'):
                    pid = pm.curr_provider_name
                elif hasattr(pm, 'get_using_provider_name'):
                    pid = pm.get_using_provider_name()
            except Exception:
                pass
        if not pid:
            return "OTHER"
        prompt = (self.config.get("active_query_prompt", "") or DEFAULT_ACTIVE_QUERY_PROMPT) if self.config else DEFAULT_ACTIVE_QUERY_PROMPT
        prompt = prompt.replace("{user_message}", message)
        try:
            resp = await asyncio.wait_for(
                self.context.llm_generate(chat_provider_id=pid, prompt=prompt, temperature=0),
                timeout=10,
            )
            return resp.completion_text.strip()
        except asyncio.TimeoutError:
            logger.warning("意图识别超时")
        except Exception as e:
            logger.warning(f"意图识别异常: {e}")
        return "OTHER"

    # ==================== 主动查询入口 ====================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_active_query(self, event: AstrMessageEvent):
        if not (self.config.get("active_query_enabled", False) if self.config else False):
            return

        try:
            sid = getattr(event.message_obj, "sender_id", None)
            if sid is None and hasattr(event.message_obj, "sender"):
                s = event.message_obj.sender
                sid = getattr(s, "user_id", None) if hasattr(s, "user_id") else None
            bid = getattr(event.message_obj, "self_id", None)
            if sid and bid and str(sid) == str(bid):
                return
        except Exception:
            pass

        msg = (event.message_str or "").strip()
        if not msg:
            return

        origin = getattr(event, "unified_msg_origin", "") or ""

        # 检查待定歧义消解
        pending = self._pending_clarify.pop(origin, None)
        if pending and time.time() - pending < 120:
            low = msg.lower().strip()
            if low in ("bot", "b", "机器人") or "bot" in low:
                logger.info("[主动查询] 歧义消解: BOT")
                event.stop_event()
                r = await self._query_bot_servers()
                yield event.plain_result(self._format_bot_results(r, realtime=True))
                return
            if low in ("mc", "m", "我的世界") or "mc" in low:
                logger.info("[主动查询] 歧义消解: MC")
                event.stop_event()
                r = await self._query_mc_servers()
                yield event.plain_result(self._format_mc_results(r))
                return
            self._pending_clarify[origin] = time.time()
            event.stop_event()
            yield event.plain_result("请回复 BOT 或 MC。")
            return

        # 冷却（防止刷屏）
        now = time.time()
        if now - self._last_active_check < (self.config.get("active_query_cooldown", 30) if self.config else 30):
            return
        self._last_active_check = now

        logger.info(f"[主动查询] {msg[:40]}")
        intent = await self._detect_intent(msg)

        if "BOT_QUERY" in intent and "MC" not in intent:
            logger.info("[主动查询] -> BOT_QUERY")
            event.stop_event()
            r = await self._query_bot_servers()
            yield event.plain_result(self._format_bot_results(r, realtime=True))
        elif "MC_QUERY" in intent:
            logger.info("[主动查询] -> MC_QUERY")
            event.stop_event()
            r = await self._query_mc_servers()
            yield event.plain_result(self._format_mc_results(r))
        elif "ASK_CLARIFY" in intent:
            logger.info("[主动查询] -> ASK_CLARIFY")
            event.stop_event()
            self._pending_clarify[origin] = time.time()
            yield event.plain_result("请问您要查询哪种服务器？\n  BOT — BOT 服务器\n  MC  — MC 游戏服务器\n请回复 BOT 或 MC。")
        else:
            logger.info(f"[主动查询] -> {intent} 放行")

    # ==================== 命令 ====================

    @filter.command("bm")
    async def cmd_bm(self, event: AstrMessageEvent):
        yield event.plain_result(self._help())

    @filter.command("server_query")
    async def cmd_server_query(self, event: AstrMessageEvent, server_type: str = ""):
        st = server_type.strip().lower()
        if st in ("bot", "b", "astrbot", "qq", "机器人"):
            async for r in self.cmd_bot_status(event, ""):
                yield r
            return
        if st in ("mc", "m", "minecraft", "我的世界"):
            async for r in self.cmd_mc_status(event, ""):
                yield r
            return
        if st:
            yield event.plain_result(f"未知类型: {server_type}。支持: bot, mc")
            return
        yield event.plain_result("请问查询哪种服务器?\n  /server_query bot — BOT 服务器\n  /server_query mc  — MC 服务器")

    @filter.command("bot_status")
    @filter.command("bm_check")
    async def cmd_bot_status(self, event: AstrMessageEvent, name: str = ""):
        sn = name.strip() if name else None
        results = await self._query_bot_servers(sn)
        if not results:
            yield event.plain_result(f"未找到: {name}" if sn else "暂无 BOT 服务器配置。")
            return
        yield event.plain_result(self._format_bot_results(results, realtime=True))

    @filter.command("mc_status")
    async def cmd_mc_status(self, event: AstrMessageEvent, name: str = ""):
        sn = name.strip() if name else None
        results = await self._query_mc_servers(sn)
        if not results:
            yield event.plain_result(f"未找到: {name}" if sn else "暂无 MC 服务器配置。")
            return
        yield event.plain_result(self._format_mc_results(results))

    @filter.command("bm_list")
    async def cmd_bm_list(self, event: AstrMessageEvent):
        servers = self._get_servers()
        if not servers:
            yield event.plain_result("暂无 BOT 服务器配置。")
            return
        results = []
        for s in servers:
            name = s.get("name", "?").strip()
            st = self._server_states.get(name, {})

            def _info(alive, enabled):
                if not enabled:
                    return {"alive": None, "detail": "未启用", "time_ms": 0, "code": None}
                if alive is True:
                    return {"alive": True, "detail": "缓存", "time_ms": 0, "code": None}
                if alive is False:
                    return {"alive": False, "detail": "上次离线", "time_ms": 0, "code": None}
                return {"alive": False, "detail": "未检查", "time_ms": 0, "code": None}

            results.append({
                "name": name,
                "astrbot": _info(st.get("astrbot"), s.get("enable_astrbot", True)),
                "QQ": _info(st.get("llbot"), s.get("enable_llbot", True)),
            })
        yield event.plain_result(self._format_bot_results(results, realtime=False))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bm_add")
    async def cmd_bm_add(self, event: AstrMessageEvent, name: str = "", astrbot_url: str = "",
                         astrbot_api_key: str = "", llbot_url: str = "", llbot_api_token: str = ""):
        if not name:
            yield event.plain_result("用法: /bm_add [名称] [astrbot_url] [astrbot_api_key] [llbot_url] [llbot_api_token]")
            return
        servers = self._get_servers()
        if any(s.get("name") == name for s in servers):
            yield event.plain_result(f"已存在: {name}")
            return
        servers.append({
            "name": name,
            "astrbot_url": astrbot_url,
            "astrbot_api_key": astrbot_api_key,
            "enable_astrbot": bool(astrbot_url),
            "llbot_url": llbot_url,
            "llbot_api_token": llbot_api_token,
            "enable_llbot": bool(llbot_url),
        })
        self.config["servers"] = servers
        self.config.save_config()
        self._server_states[name] = {"astrbot": None, "llbot": None}
        yield event.plain_result(f"已添加: {name}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bm_remove")
    async def cmd_bm_remove(self, event: AstrMessageEvent, name: str = ""):
        if not name:
            yield event.plain_result("用法: /bm_remove [名称]")
            return
        servers = self._get_servers()
        new_s = [s for s in servers if s.get("name") != name]
        if len(new_s) == len(servers):
            yield event.plain_result(f"未找到: {name}")
            return
        self.config["servers"] = new_s
        self.config.save_config()
        self._server_states.pop(name, None)
        yield event.plain_result(f"已删除: {name}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bm_interval")
    async def cmd_bm_interval(self, event: AstrMessageEvent, seconds: str = ""):
        if not seconds:
            cur = self.config.get("check_interval", 60) if self.config else 60
            yield event.plain_result(f"间隔: {cur}s\n/bm_interval [秒]")
            return
        try:
            v = int(seconds)
            if v < 5:
                yield event.plain_result("最少 5 秒。")
                return
            self.config["check_interval"] = v
            self.config.save_config()
            yield event.plain_result(f"已设为 {v}s")
        except ValueError:
            yield event.plain_result("请输入整数秒数。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bm_notif")
    async def cmd_bm_notif(self, event: AstrMessageEvent, action: str = "", target: str = ""):
        if not action:
            en = self.config.get("enable_notification", True) if self.config else True
            gr = self._get_notification_groups()
            yield event.plain_result(
                f"通知: {'开' if en else '关'}\n群: {', '.join(gr) if gr else '(空)'}\n"
                "/bm_notif on|off|add|del [群ID]"
            )
            return
        if action == "on":
            self.config["enable_notification"] = True
        elif action == "off":
            self.config["enable_notification"] = False
        elif action == "add" and target:
            gr = self._get_notification_groups()
            if target not in gr:
                gr.append(target)
                self.config["notification_groups"] = "\n".join(gr)
            else:
                yield event.plain_result(f"已存在: {target}")
                return
        elif action == "del" and target:
            gr = self._get_notification_groups()
            if target in gr:
                gr.remove(target)
                self.config["notification_groups"] = "\n".join(gr)
            else:
                yield event.plain_result(f"未找到: {target}")
                return
        else:
            yield event.plain_result("/bm_notif on|off|add|del [群ID]")
            return
        self.config.save_config()
        yield event.plain_result("完成。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bm_at")
    async def cmd_bm_at(self, event: AstrMessageEvent, action: str = "", target: str = ""):
        if not action:
            en = self.config.get("enable_at_users", False) if self.config else False
            us = self._get_at_users()
            yield event.plain_result(
                f"@用户: {'开' if en else '关'}\n用户: {', '.join(us) if us else '(空)'}\n"
                "/bm_at on|off|add|del [QQ号]"
            )
            return
        if action == "on":
            self.config["enable_at_users"] = True
        elif action == "off":
            self.config["enable_at_users"] = False
        elif action == "add" and target:
            us = self._get_at_users()
            if target not in us:
                us.append(target)
                self.config["at_users"] = "\n".join(us)
            else:
                yield event.plain_result(f"已存在: {target}")
                return
        elif action == "del" and target:
            us = self._get_at_users()
            if target in us:
                us.remove(target)
                self.config["at_users"] = "\n".join(us)
            else:
                yield event.plain_result(f"未找到: {target}")
                return
        else:
            yield event.plain_result("/bm_at on|off|add|del [QQ号]")
            return
        self.config.save_config()
        yield event.plain_result("完成。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("bm_edit")
    async def cmd_bm_edit(self, event: AstrMessageEvent, name: str = "", field: str = "", value: str = ""):
        if not name or not field or not value:
            yield event.plain_result("用法: /bm_edit [名称] [字段] [值]\n字段: name,astrbot_url,astrbot_api_key,llbot_url,llbot_api_token,enable_astrbot,enable_llbot")
            return
        servers = self._get_servers()
        for s in servers:
            if s.get("name") == name:
                if field in ("enable_astrbot", "enable_llbot"):
                    s[field] = value.lower() in ("true", "1", "yes")
                else:
                    s[field] = value
                self.config["servers"] = servers
                self.config.save_config()
                yield event.plain_result(f"已更新 {name}.{field}")
                return
        yield event.plain_result(f"未找到: {name}")

    def _help(self) -> str:
        return (
            "**MC 与 BOT 服务监控**\n"
            "\n查询（所有人）:\n"
            "  /server_query [bot|mc] — 统一查询入口\n"
            "  /bot_status [/bm_check] — BOT 服务器\n"
            "  /mc_status — MC 服务器\n"
            "  /bm_list — BOT 缓存状态\n"
            "\n管理（管理员）:\n"
            "  /bm_add|remove|edit|interval|notif|at — BOT 服务器管理\n"
            "  MC 服务器请在 WebUI 中配置\n"
            "\n开启主动查询后支持自然语言。"
        )
