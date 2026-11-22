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
REJECT_EXTRA = ROOT / "reject.list"
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
            raise ValueError(f"categories.list:{n}: ожидается [proxy]/[reject] и строки "
                             f"имя=Папка или имя=geosite:категория@url — {raw!r}")
        name, _, folder = (p.strip() for p in line.partition("="))
        if not name or not folder:
            raise ValueError(f"categories.list:{n}: кривая строка {raw!r}")
        current.append((name, folder))
    return proxy, reject


EASYLIST_DOMAIN = re.compile(r"^\|\|([a-z0-9.-]+)\^")
# Инфраструктура Google/YouTube: блок поддоменов ЛОМАЕТ сервисы (s.youtube.com
# ронял воспроизведение — поймано 22.11). Рекламные же домены Google НЕ в этом
# списке и блокируются как обычно.
SKIP_REJECT = re.compile(
    r"(^|\.)((s[0-9]?|csi|footprints-pa|pagead2?)\.(youtube\.com|gstatic\.com|googleapis\.com)"
    r"|(www\.)?(youtube\.com|youtu\.be|ytimg\.com|googlevideo\.com|ggpht\.com|gstatic\.com|"
    r"googleapis\.com|googleusercontent\.com|google\.(com|ru)|gvt1\.com|gvt2\.com|goo\.gl)$)"
)


def parse_easylist(text):
    """easylist-формат -> [(TYPE, value)]: только чистые ||domain^ правила.
    Исключения (@@) и косметика (##) игнорируются — не доменные блоки."""
    out, skipped = [], 0
    for line in text.splitlines():
        m = EASYLIST_DOMAIN.match(line.strip())
        if m:
            if SKIP_REJECT.search(m.group(1)):
                skipped += 1
                continue
            out.append(("DOMAIN-SUFFIX", m.group(1)))
    if skipped:
        print(f"easylist: пропущено {skipped} доменов Google-инфраструктуры (SKIP_REJECT)")
    if not out:
        raise ValueError("easylist: 0 чистых доменных правил — формат источника изменился?")
    return out


def fetch_text(name, url, refresh):
    """Скачивает текстовый источник (easylist) в .cache/<name>.txt."""
    cache = CACHE / f"{name}.txt"
    if cache.exists() and not refresh:
        return cache.read_text(encoding="utf-8")
    try:
        with urllib.request.urlopen(url, timeout=180) as r:
            body = r.read().decode("utf-8")
    except Exception as e:
        raise ValueError(f"easylist-источник {name} ({url}) не скачался ({e})") from e
    CACHE.mkdir(exist_ok=True)
    cache.write_text(body, encoding="utf-8")
    return body


def fetch_dat(name, url, refresh):
    """Скачивает geosite .dat в .cache/<name>.dat (или берёт кеш)."""
    cache = CACHE / f"{name}.dat"
    if cache.exists() and not refresh:
        return cache.read_bytes()
    try:
        with urllib.request.urlopen(url, timeout=600) as r:
            body = r.read()
    except Exception as e:
        raise ValueError(f"geosite-источник {name} ({url}) не скачался ({e})") from e
    CACHE.mkdir(exist_ok=True)
    cache.write_bytes(body)
    return body


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


def _varint(buf, i):
    result = shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7


def _fields(buf):
    """Итератор полей protobuf-сообщения: yield (номер, wire, значение).
    Любой мусор/обрыв в конце падает с ошибкой — это наша проверка полноты .dat."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        f, w = key >> 3, key & 7
        if w == 2:
            ln, i = _varint(buf, i)
            if i + ln > len(buf):
                raise ValueError("geosite: сообщение обрезано")
            yield f, w, buf[i:i + ln]
            i += ln
        elif w == 0:
            v, i = _varint(buf, i)
            yield f, w, v
        else:
            raise ValueError(f"geosite: неожиданный wire-тип {w}")


# типы Domain из proto v2fly: 0=Plain, 1=Regex, 2=Domain(суффикс), 3=Full
GEOSITE_DOMAIN_TYPE = {0: "DOMAIN-KEYWORD", 2: "DOMAIN-SUFFIX", 3: "DOMAIN"}


def parse_geosite(data, category):
    """geosite.dat (protobuf GeositeList) -> [(TYPE, value)] одной категории."""
    out = []
    for f, w, entry in _fields(data):
        if f != 1:
            continue
        tag, doms = None, []
        for f2, w2, payload in _fields(entry):
            if f2 == 1:
                tag = payload.decode("utf-8", "replace").lower()  # runetfreedom держит теги в ВЕРХНЕМ регистре
            elif f2 == 2:
                dtype, val = None, None
                for f3, w3, p3 in _fields(payload):
                    if f3 == 1:
                        dtype = p3
                    elif f3 == 2:
                        val = p3.decode("utf-8", "replace")
                doms.append((dtype, val))
        if tag == category:
            for dtype, val in doms:
                kind = GEOSITE_DOMAIN_TYPE.get(dtype)
                if kind and val:
                    out.append((kind, val.lower()))
    if not out:
        raise ValueError(f"geosite: категория {category!r} не найдена в .dat")
    return out


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

    manual_reject = parse(REJECT_EXTRA.read_text(encoding="utf-8")) if REJECT_EXTRA.exists() else []
    cat_rules, reject_rules = [], []
    reject_rules.extend(manual_reject)  # ручной reject.list — первым (приоритет)
    for name, value in cat_defs + reject_defs:
        if value.startswith("easylist:"):
            got = parse_easylist(fetch_text(name, value.removeprefix("easylist:"), refresh))
        elif value.startswith("geosite:"):
            # формат: geosite:категория@url — вытаскиваем домены из .dat
            cat, _, url = value.partition("@")
            got = parse_geosite(fetch_dat(name, url, refresh), cat.removeprefix("geosite:"))
        else:
            got = parse_category(name, fetch(name, value, refresh))
        target = cat_rules if (name, value) in cat_defs else reject_rules
        target.extend(got)
        print(f"категория {name}: {len(got)} правил")

    # SKIP_REJECT применяется ко ВСЕМ reject-источникам: инфраструктура
    # Google/YouTube не блокируется независимо от того, кто её принёс
    before = len(reject_rules)
    reject_rules = [r for r in reject_rules if r[0] == "IP-CIDR" or not SKIP_REJECT.search(r[1])]
    if len(reject_rules) != before:
        print(f"SKIP_REJECT: вычищено {before - len(reject_rules)} доменов Google-инфраструктуры (из всех источников)")

    proxy_all = merge(manual, cat_rules)

    # пересечение с proxy: домен в обоих списках ведёт себя по-разному на
    # роутере (block-правило первое) и клиентах (через туннель) — молчаливый
    # источник багов (механика инцидента s.youtube, 22.11). Побеждает proxy.
    proxy_set = set(proxy_all)
    inter = [r for r in reject_rules if r in proxy_set]
    if inter:
        print(f"ПЕРЕСЕЧЕНИЕ proxy∩reject: {len(inter)} правил отдано proxy, из reject убраны "
              f"(примеры: {', '.join(v for _, v in inter[:5])})")
        reject_rules = [r for r in reject_rules if r not in proxy_set]

    # бюджет reject-списка: роутер (240МБ RAM) держит инлайн-домены.
    # Урок 22.11: 38k — работает, 80k — деградировал матчинг на соединение;
    # при возврате RU-источников сверх cap обрезаем (проверенные первыми)
    REJECT_CAP = 50_000
    if len(reject_rules) > REJECT_CAP:
        print(f"ВНИМАНИЕ: reject {len(reject_rules)} > cap {REJECT_CAP}, обрезано")
        reject_rules = reject_rules[:REJECT_CAP]

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
