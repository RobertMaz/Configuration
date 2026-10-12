#!/usr/bin/env python3
"""Собирает единый список Proxy-правил из rules/ и раскладывает его по форматам.

Источник:   rules/upstream.txt (сервисы blackmatrix7) + rules/custom.list (ручные правила)
Результат:  generated/proxy.list         — Surge-list для subconverter (Clash-клиенты)
            generated/xray-routing.json  — объект routing для xray на роутерах

Любая ошибка (не скачался upstream, кривое правило, подозрительно мало правил)
роняет сборку: лучше вчерашний рабочий список, чем сегодняшний урезанный.
Только stdlib — запускается и в Actions, и локально без зависимостей.
"""
import ipaddress
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_URL = "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/Clash/{0}/{0}.list"

# Типы, которые роутер/Clash по домену-или-IP не применят осмысленно — выкидываем молча.
IGNORED = {"PROCESS-NAME", "IP-ASN", "USER-AGENT", "IP-CIDR6", "URL-REGEX", "DOMAIN-WILDCARD", "DOMAIN-REGEX"}
XRAY_DOMAIN_PREFIX = {"DOMAIN": "full:", "DOMAIN-SUFFIX": "domain:", "DOMAIN-KEYWORD": "keyword:"}

MIN_RULES = 300  # сейчас ~500; резкое падение = что-то сломалось в upstream


def fetch(url, attempts=3):
    for i in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:
            if i == attempts:
                raise RuntimeError(f"не скачался {url}: {e}") from e
            time.sleep(5 * i)


def parse(text, origin):
    """Возвращает [(TYPE, value)], валидирует каждое правило."""
    rules = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[1]:
            raise ValueError(f"{origin}:{n}: не правило: {raw!r}")
        kind, value = parts[0].upper(), parts[1].lower()
        if kind in IGNORED:
            continue
        if kind == "IP-CIDR":
            try:
                value = str(ipaddress.ip_network(value, strict=False))
            except ValueError as e:
                raise ValueError(f"{origin}:{n}: кривая подсеть {value!r}") from e
        elif kind not in XRAY_DOMAIN_PREFIX:
            raise ValueError(f"{origin}:{n}: неизвестный тип {kind!r} — добавить в IGNORED или поддержать")
        elif " " in value or "/" in value:
            raise ValueError(f"{origin}:{n}: кривой домен {value!r}")
        rules.append((kind, value))
    return rules


def load_sections():
    """[(заголовок, правила)] в порядке: upstream-сервисы, затем custom."""
    sections = []
    services = parse_names(ROOT / "rules/upstream.txt")
    for svc in services:
        rules = parse(fetch(UPSTREAM_URL.format(svc)), f"upstream/{svc}")
        if not rules:
            raise RuntimeError(f"upstream/{svc}: пустой список")
        print(f"upstream {svc}: {len(rules)}")
        sections.append((svc, rules))
    custom = ROOT / "rules/custom.list"
    sections.append(("custom", parse(custom.read_text(encoding="utf-8"), "rules/custom.list")))
    return sections


def parse_names(path):
    names = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


def dedupe(sections):
    seen = set()
    result = []
    for title, rules in sections:
        unique = [r for r in rules if not (r in seen or seen.add(r))]
        if unique:
            result.append((title, unique))
    return result


def render_surge(sections):
    lines = ["# Сгенерировано scripts/build.py из rules/ — руками не править."]
    for title, rules in sections:
        lines.append(f"# --- {title} ---")
        for kind, value in rules:
            # no-resolve: Clash не резолвит каждый домен ради проверки IP-правила
            lines.append(f"{kind},{value},no-resolve" if kind == "IP-CIDR" else f"{kind},{value}")
    return "\n".join(lines) + "\n"


def render_xray(sections):
    domains = [XRAY_DOMAIN_PREFIX[k] + v for _, rules in sections for k, v in rules if k != "IP-CIDR"]
    ips = [v for _, rules in sections for k, v in rules if k == "IP-CIDR"]
    # Контракт с роутером: в его базовом конфиге есть outbounds с тегами proxy и direct.
    routing = {
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {"type": "field", "domain": domains, "outboundTag": "proxy"},
                {"type": "field", "ip": ips, "outboundTag": "proxy"},
                {"type": "field", "network": "tcp,udp", "outboundTag": "direct"},
            ],
        }
    }
    return json.dumps(routing, indent=2, ensure_ascii=False) + "\n"


def write_if_changed(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        print(f"без изменений: {path.relative_to(ROOT)}")
        return
    path.write_text(content, encoding="utf-8")
    print(f"обновлён: {path.relative_to(ROOT)}")


def main():
    sections = dedupe(load_sections())
    total = sum(len(r) for _, r in sections)
    print(f"итого правил: {total}")
    if total < MIN_RULES:
        raise RuntimeError(f"правил {total} < {MIN_RULES} — не публикую")
    write_if_changed(ROOT / "generated/proxy.list", render_surge(sections))
    write_if_changed(ROOT / "generated/xray-routing.json", render_xray(sections))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)
