# Configuration

Единый список «что идёт через VPN» для роутеров (xray) и Clash/Mihomo-клиентов.
Через VPN — только то, что в списке, остальное напрямую.

## Задачи

- [x] ~~**Научить build.py вливать категории blackmatrix7 в роутерный вывод.**~~
      СДЕЛАНО (21.11): categories.list + слияние в build.py; выходы
      proxy.list (клиенты+роутеры), reject.list (реклама), xray-routing.json
      (роутеры, реклама -> block). Дубли ручного списка и категорий съедаются
      merge'ем — ручная чистка proxy.list не обязательна, но приветствуется.
      Сейчас Clash-клиенты получают категории (Telegram/YouTube/Whatsapp/Instagram)
      отдельными rule-provider'ами из профиля vpnAsCode, а роутеры едят только
      `generated/xray-routing.json` — поэтому дубли четырёх сервисов пока
      ОСТАВЛЕНЫ в proxy.list (роутерам больше неоткуда их взять).
      Сделать:
      - список категорий (имя + URL raw yaml blackmatrix7) — в конфиге build.py;
      - при сборке скачивать их и конвертировать в правила routing
        (DOMAIN-SUFFIX → domain:, DOMAIN-KEYWORD → keyword:, IP-CIDR → ip:[…]);
      - валидация как сейчас: сбой загрузки/парсинга = красный Action,
        generated/ не меняется;
      - после этого дедуплицировать proxy.list: убрать дубли четырёх сервисов,
        оставив TON-экосистему (fragment/ton/mbrx), nicegram/swiftgram,
        Meta IP-блоки и WhatsApp voice (57.141.x) — их в категориях нет/не уверен.
      Бонус-цель: тот же механизм даст роутерам будущие категории без ручной работы.

## Как поменять правила

1. Отредактировать `proxy.list` (точечное) или `categories.list` (целые сервисы), запушить.
2. Action проверит, соберёт `generated/` (скачав свежие категории), прогонит `xray -test`, закоммитит.
   Ошибка → Action красный, `generated/` не меняется, у всех остаётся последняя рабочая версия.
3. Роутеры подтянут ночью, Clash-клиенты — в течение суток.
4. Раз в сутки (cron 04:17) Action сам обновляет категории и пересобирает.

Проверить локально до push: `python3 build.py` (кеш `.cache/`; `--refresh` — свежие категории).

## Файлы

| Путь | Что |
|---|---|
| `proxy.list` | **источник** — ручные правила «через VPN» |
| `categories.list` | **источник** — категории blackmatrix7 (секция [reject] = реклама) |
| `build.py` | проверка + сборка |
| `generated/proxy.list` | rule-provider «через VPN» (ручные + категории) |
| `generated/reject.list` | rule-provider «реклама в блок» |
| `generated/xray-routing.json` | `routing` для xray на роутерах |
| `clash/routing.yaml` | ссылка на него — в 3x-ui `subClashRules` |
| `router/00-base.json` | шаблон xray роутера (inbounds, outbounds); `__UUID__`/`__WS_PATH__` заполняются на роутере |
| `router/nftables.nft` | заворот TCP в xray; без IP сервера — туннель пропускается по mark 255 |
| `router/xray-rules-update` | cron-скрипт роутера: скачать → `xray -test` → применить |

## Клиенты

- **Clash / Mihomo** (Clash Verge, CMFA, Clash Mi): Clash-подписка 3x-ui. Панель кладёт в профиль серверы клиента и правила из `clash/routing.yaml`.
- **v2rayNG**: обычная подписка 3x-ui; маршрутизация — встроенными geosite/geoip на телефоне, этот репозиторий не используется.
- **Роутеры** (оба одинаковые, отличается только UUID): xray запускается своим `/etc/init.d/xray`
  (он у каждого роутера свой и в репо не лежит) с `-confdir /etc/xray/conf.d`:
  - `00-base.json` — из `router/00-base.json`, с UUID и ws-path. Только на роутере.
    В нём `observatory` раз в минуту пингует туннель: если сервер мёртв, список идёт в `direct`
    (balancer `proxy-or-direct` в правилах) — интернет дома не пропадает. Без `observatory`
    новые правила не пройдут `xray -test`, и роутер останется на старых.
  - `10-routing.json` — `generated/xray-routing.json`, кладёт и обновляет `xray-rules-update` (cron, раз в сутки).
  - `/etc/xray/nftables.nft` — из `router/nftables.nft`, грузится init-скриптом.

Установка на роутер (один раз):

```sh
RAW=https://raw.githubusercontent.com/RobertMaz/Configuration/main
mkdir -p /etc/xray/conf.d
uclient-fetch -O /etc/xray/nftables.nft       $RAW/router/nftables.nft
uclient-fetch -O /etc/xray/conf.d/00-base.json $RAW/router/00-base.json
sed -i -e 's/__UUID__/<uuid>/' -e 's|__WS_PATH__|<ws-path>|' /etc/xray/conf.d/00-base.json
uclient-fetch -O /usr/bin/xray-rules-update   $RAW/router/xray-rules-update
chmod +x /usr/bin/xray-rules-update
# в /etc/init.d/xray: -config /etc/xray/config.json → -confdir /etc/xray/conf.d
echo '30 1 * * * /usr/bin/xray-rules-update' >> /etc/crontabs/root
/etc/init.d/cron restart
xray-rules-update && logread -e xray-rules
```
