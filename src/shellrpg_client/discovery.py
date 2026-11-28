"""Pick a reachable ShellRPG service from the operator's known endpoints."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DEFAULT_HOSTS = ("http://127.0.0.1:8765", "https://shellrpg.fluffy-bushmaster.ts.net")


def known_hosts(previous: str = "") -> tuple[str, ...]:
    paths = (Path.home() / ".config/shellrpg/servers", Path("/etc/shellrpg/known-servers"))
    candidates = [previous]
    candidates.extend(os.environ.get("SHELLRPG_SERVERS", "").split(","))
    for path in paths:
        try:
            candidates.extend(path.read_text(encoding="utf-8").splitlines()[:32])
        except OSError:
            pass
    candidates.extend(DEFAULT_HOSTS)
    clean = {}
    for raw in candidates:
        endpoint = raw.strip().rstrip("/")
        if not endpoint or len(endpoint) > 512:
            continue
        try:
            authority = urlsplit(endpoint)
            if (authority.scheme in {"http", "https"} and authority.hostname and not authority.path
                    and not authority.username and not authority.password and not authority.query and not authority.fragment):
                clean[endpoint] = None
        except ValueError:
            continue
    return tuple(clean)[:12]


def is_shellrpg(endpoint: str) -> bool:
    for path in ("/health", "/api/server/health"):
        url = endpoint + path
        try:
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "ShellRPG-client"})
            with urlopen(request, timeout=1.4) as response:
                if urlsplit(response.url).netloc != urlsplit(url).netloc:
                    continue
                payload = json.loads(response.read(8192))
            if isinstance(payload, dict) and payload.get("ok") is True and payload.get("service") == "shellrpg-server":
                return True
        except (OSError, URLError, ValueError):
            continue
    return False


def discover(previous: str = "") -> str:
    hosts = known_hosts(previous)
    if not hosts:
        raise ConnectionError("In der Serverliste steht kein gültiger ShellRPG-Endpunkt.")
    if hosts[0].startswith("http://127.0.0.1:") and is_shellrpg(hosts[0]):
        return hosts[0]
    with ThreadPoolExecutor(max_workers=min(len(hosts), 8)) as pool:
        futures = {host: pool.submit(is_shellrpg, host) for host in hosts}
        for host, result in futures.items():
            if result.result():
                for job in futures.values():
                    job.cancel()
                return host
    raise ConnectionError("Keiner der bekannten ShellRPG-Server ist erreichbar.")
