#!/usr/bin/env python3
"""Собирает единую конфигурацию правил для Clash/Mihomo-клиентов и xray-роутеров.

    proxy.list     ─┐
    categories.list─┼─> generated/proxy.list        всё через VPN (rule-provider)
                    │   generated/reject.list       реклама в блок (rule-provider)
                    └─> generated/xray-routing.json  routing для xray на роутерах

Категории (blackmatrix7) скачиваются в .cache/ при первой сборке; флаг --refresh
обновляет. Офлайн-сборка работает из кеша. Любая ошибка роняет сборку:
generated/ не меняется, клиенты продолжают тянуть последнюю валидную версию.
Только stdlib.
"""
import ipaddress
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "proxy.list"
CATEGORIES = ROOT / "categories.list"
CACHE = ROOT / ".cache"
# raw.githubusercontent из РФ душит большие файлы (обрыв ~15-20КБ) — jsDelivr зеркало
BASE_URLS = [
    "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/Clash",
    "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash",
]

XRAY_DOMAIN_PREFIX = {"DOMAIN": "full:", "DOMAIN-SUFFIX": "domain:", "DOMAIN-KEYWORD": "keyword:"}
HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)*[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?$")
KEYWORD = re.compile(r"^[a-z0-9._-]+$")
# строки payload категорий: "- DOMAIN-SUFFIX,x.com", "- +.x.com", "- x.com", "- IP-CIDR,..."
CAT_LINE = re.compile(r"^\s*-\s+(.+?)\s*$")
# заголовок blackmatrix7: "# DOMAIN: 23753", "# DOMAIN-SUFFIX: 13939", ...
META = re.compile(r"^#\s*((?:DOMAIN|DOMAIN-SUFFIX|DOMAIN-KEYWORD|IP-CIDR|IP-CIDR6)):\s*(\d+)")


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


def parse_categories():
    """-> ([имя=Папка], [имя=Папка]) — секции [proxy] и [reject]."""
    proxy, reject, current = [], [], None
    for n, raw in enumerate(CATEGORIES.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower() == "[proxy]":
            current = proxy
            continue
        if line.lower() == "[reject]":
            current = reject
            continue
        if current is None or "=" not in line:
            raise ValueError(f"categories.list:{n}: ожидается [proxy]/[reject] и строки имя=Папка — {raw!r}")
        name, _, folder = (p.strip() for p in line.partition("="))
        if not name or not folder:
            raise ValueError(f"categories.list:{n}: кривая строка {raw!r}")
        current.append((name, folder))
    return proxy, reject


def _complete(body):
    """Число строк payload >= суммы счётчиков заголовка (обрыв ловится до кеша)."""
    expected = sum(int(m.group(2)) for m in map(META.match, body.splitlines()) if m)
    lines = sum(1 for l in body.splitlines() if CAT_LINE.match(l))
    return not expected or lines >= expected


def fetch(name, folder, refresh):
    """Скачивает yaml категории в .cache/ (или берёт кеш) -> текст payload.

    Пробует оба зеркала; в кеш ложится только ПОЛНЫЙ файл (обрывы больших
    файлов на raw.githubusercontent из РФ — не редкость, поймано 22.11)."""
    cache = CACHE / f"{name}.yaml"
    if cache.exists() and not refresh:
        return cache.read_text(encoding="utf-8")
    body, errors = None, []
    for base in BASE_URLS:
        # у рекламных списков главный .yaml урезан (374 строки из 38066) —
        # полный классический формат живёт в <Имя>_Classical.yaml
        for suffix in ("_Classical.yaml", ".yaml"):
            url = f"{base}/{folder}/{folder}{suffix}"
            for attempt in range(2):
                try:
                    with urllib.request.urlopen(url, timeout=300) as r:
                        got = r.read().decode("utf-8")
                    if _complete(got):
                        body = got
                        break
                    errors.append(f"{url}: неполный ({len(got)} байт)")
                except Exception as e:
                    errors.append(f"{url}: {e}")
            if body is not None:
                break
        if body is not None:
            break
    if body is None:
        where = "кеш отсутствует" if not cache.exists() else "кеш есть, но --refresh"
        raise ValueError(f"категория {name} не скачалась полностью; {'; '.join(errors)}; {where}")
    CACHE.mkdir(exist_ok=True)
    cache.write_text(body, encoding="utf-8")
    return body


def parse_category(name, body):
    """payload категории -> [(TYPE, value)]; доменный формат (+.x.com) -> DOMAIN-SUFFIX.

    Полная проверка: сумма счётчиков в заголовке файла обязана совпадать с
    числом распознанных правил — обрыв загрузки роняет сборку, а не молча
    отдаёт 1% списка (поймано 22.11: ads — 374 строки из ~38000)."""
    expected = sum(int(m.group(2)) for m in map(META.match, body.splitlines()) if m)
    rules = []
    for line in body.splitlines():
        m = CAT_LINE.match(line)
        if not m:
            continue
        item = m.group(1)
        if "," in item:
            kind, _, value = (p.strip() for p in item.partition(","))
            kind = kind.upper()
            value = value.lower().split(",")[0].strip().strip("'\"")  # срезать no-resolve и прочие опции
        else:
            kind, value = "DOMAIN-SUFFIX", item.lower().lstrip("+*").lstrip(".")
        if kind in ("IP-CIDR", "IP-CIDR6"):
            try:
                value = str(ipaddress.ip_network(value, strict=False))
            except ValueError:
                continue
        elif kind in XRAY_DOMAIN_PREFIX:
            if not (HOSTNAME.match(value) or (kind == "DOMAIN-SUFFIX" and value)):
                continue
        else:  # PROCESS-NAME и прочее — не для сетевых правил
            continue
        rules.append((kind, value))
    if not rules:
        raise ValueError(f"категория {name}: распознано 0 правил — формат источника изменился?")
    if expected and len(rules) != expected:
        raise ValueError(
            f"категория {name}: неполная — распознано {len(rules)} правил, "
            f"заголовок обещает {expected} (обрыв загрузки? перезапусти с --refresh)"
        )
    return rules


def merge(*batches):
    out, seen = [], set()
    for batch in batches:
        for rule in batch:
            if rule not in seen:
                seen.add(rule)
                out.append(rule)
    return out


def render_clash(rules, header):
    lines = [f"# Сгенерировано build.py — руками не править. {header}"]
    for kind, value in rules:
        # no-resolve: Clash не резолвит каждый домен ради проверки IP-правила
        lines.append(f"{kind},{value},no-resolve" if kind == "IP-CIDR" else f"{kind},{value}")
    return "\n".join(lines) + "\n"


def render_xray(proxy_rules, reject_rules):
    domains = [XRAY_DOMAIN_PREFIX[k] + v for k, v in proxy_rules if k not in ("IP-CIDR", "IP-CIDR6")]
    ips = [v for k, v in proxy_rules if k in ("IP-CIDR", "IP-CIDR6")]
    ads = [XRAY_DOMAIN_PREFIX[k] + v for k, v in reject_rules if k not in ("IP-CIDR", "IP-CIDR6")]
    # Контракт с роутером: в его 00-base.json есть outbounds proxy, direct и block
    # и observatory, который пингует proxy. Список идёт через balancer:
    # proxy жив — в proxy, мёртв — fallback в direct (интернет не пропадает).
    balancer = {
        "tag": "proxy-or-direct",
        "selector": ["proxy"],
        "strategy": {"type": "leastPing"},
        "fallbackTag": "direct",
    }
    xray_rules = []
    if ads:  # реклама режется до всего прочего
        xray_rules.append({"type": "field", "domain": ads, "outboundTag": "block"})
    if domains:
        xray_rules.append({"type": "field", "domain": domains, "balancerTag": balancer["tag"]})
    if ips:
        xray_rules.append({"type": "field", "ip": ips, "balancerTag": balancer["tag"]})
    xray_rules.append({"type": "field", "network": "tcp,udp", "outboundTag": "direct"})
    routing = {"domainStrategy": "AsIs", "balancers": [balancer], "rules": xray_rules}
    return json.dumps({"routing": routing}, indent=2) + "\n"


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        print(f"без изменений: {path.relative_to(ROOT)}")
        return
    path.write_text(content, encoding="utf-8")
    print(f"обновлён: {path.relative_to(ROOT)}")


def main():
    refresh = "--refresh" in sys.argv
    manual = parse(SOURCE.read_text(encoding="utf-8"))
    cat_defs, reject_defs = parse_categories()

    cat_rules, reject_rules = [], []
    for name, folder in cat_defs + reject_defs:
        got = parse_category(name, fetch(name, folder, refresh))
        target = cat_rules if (name, folder) in cat_defs else reject_rules
        target.extend(got)
        print(f"категория {name}: {len(got)} правил")

    proxy_all = merge(manual, cat_rules)
    print(f"итого через VPN: {len(proxy_all)} правил (минуя {len(manual) + len(cat_rules) - len(proxy_all)} дублей), "
          f"в блок: {len(reject_rules)}")
    write(ROOT / "generated/proxy.list", render_clash(proxy_all, "Что через VPN: proxy.list + categories.list"))
    write(ROOT / "generated/reject.list", render_clash(reject_rules, "Реклама в блок"))
    write(ROOT / "generated/xray-routing.json", render_xray(proxy_all, reject_rules))


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"ОШИБКА:\n{e}", file=sys.stderr)
        sys.exit(1)
