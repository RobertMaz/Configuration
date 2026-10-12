#!/usr/bin/env python3
"""Проверяет proxy.list и собирает из него готовые файлы для клиентов.

    proxy.list -> generated/proxy.list         rule-provider для Clash/Mihomo
               -> generated/xray-routing.json  объект routing для xray на роутерах

Любая ошибка в proxy.list роняет сборку: generated/ не меняется, клиенты
продолжают тянуть последнюю валидную версию. Только stdlib.
"""
import ipaddress
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "proxy.list"

XRAY_DOMAIN_PREFIX = {"DOMAIN": "full:", "DOMAIN-SUFFIX": "domain:", "DOMAIN-KEYWORD": "keyword:"}
HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)*[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?$")
KEYWORD = re.compile(r"^[a-z0-9._-]+$")


def parse(text):
    """Возвращает [(TYPE, value)] без дублей; на первой же кривой строке — ValueError."""
    rules, seen, errors = [], set(), []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 2:
            errors.append(f"proxy.list:{n}: ожидается TYPE,value — {raw!r}")
            continue
        kind, value = parts[0].upper(), parts[1].lower()
        if kind == "IP-CIDR":
            try:
                net = ipaddress.ip_network(value)
            except ValueError:
                errors.append(f"proxy.list:{n}: кривая подсеть {value!r} (host-биты должны быть нулями)")
                continue
            value = str(net)
        elif kind == "DOMAIN-KEYWORD":
            if not KEYWORD.match(value):
                errors.append(f"proxy.list:{n}: кривой keyword {value!r}")
                continue
        elif kind in XRAY_DOMAIN_PREFIX:
            if not HOSTNAME.match(value):
                errors.append(f"proxy.list:{n}: кривой домен {value!r}")
                continue
        else:
            errors.append(f"proxy.list:{n}: неизвестный тип {kind!r}, допустимы: {', '.join([*XRAY_DOMAIN_PREFIX, 'IP-CIDR'])}")
            continue
        if (kind, value) in seen:
            print(f"дубль, пропущен: proxy.list:{n}: {kind},{value}")
            continue
        seen.add((kind, value))
        rules.append((kind, value))
    if errors:
        raise ValueError("\n".join(errors))
    if not rules:
        raise ValueError("proxy.list пустой")
    return rules


def render_clash(rules):
    lines = ["# Сгенерировано build.py из proxy.list — руками не править."]
    for kind, value in rules:
        # no-resolve: Clash не резолвит каждый домен ради проверки IP-правила
        lines.append(f"{kind},{value},no-resolve" if kind == "IP-CIDR" else f"{kind},{value}")
    return "\n".join(lines) + "\n"


def render_xray(rules):
    domains = [XRAY_DOMAIN_PREFIX[k] + v for k, v in rules if k != "IP-CIDR"]
    ips = [v for k, v in rules if k == "IP-CIDR"]
    # Контракт с роутером: в его 00-base.json есть outbounds с тегами proxy и direct.
    xray_rules = []
    if domains:
        xray_rules.append({"type": "field", "domain": domains, "outboundTag": "proxy"})
    if ips:
        xray_rules.append({"type": "field", "ip": ips, "outboundTag": "proxy"})
    xray_rules.append({"type": "field", "network": "tcp,udp", "outboundTag": "direct"})
    return json.dumps({"routing": {"domainStrategy": "AsIs", "rules": xray_rules}}, indent=2) + "\n"


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        print(f"без изменений: {path.relative_to(ROOT)}")
        return
    path.write_text(content, encoding="utf-8")
    print(f"обновлён: {path.relative_to(ROOT)}")


def main():
    rules = parse(SOURCE.read_text(encoding="utf-8"))
    print(f"правил: {len(rules)}")
    write(ROOT / "generated/proxy.list", render_clash(rules))
    write(ROOT / "generated/xray-routing.json", render_xray(rules))


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"ОШИБКА:\n{e}", file=sys.stderr)
        sys.exit(1)
