# Configuration

Единый список «что идёт через VPN» для роутеров (xray) и Clash/Mihomo-клиентов.
Через VPN — только то, что в списке, остальное напрямую.

## Как поменять правила

1. Отредактировать `proxy.list`, запушить в `main`.
2. Action проверит список, соберёт `generated/`, прогонит через `xray -test` и закоммитит.
   Ошибка в списке → Action красный, `generated/` не меняется, у всех остаётся последняя рабочая версия.
3. Роутеры подтянут ночью, Clash-клиенты — в течение суток.

Проверить локально до push: `python3 build.py`.

## Файлы

| Путь | Что |
|---|---|
| `proxy.list` | **источник** — единственное, что правится руками |
| `build.py` | проверка + сборка |
| `generated/proxy.list` | rule-provider для Clash/Mihomo |
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
  - `10-routing.json` — `generated/xray-routing.json`, кладёт и обновляет `xray-rules-update` (cron, раз в сутки).
  - `/etc/xray/nftables.nft` — из `router/nftables.nft`, грузится init-скриптом.
