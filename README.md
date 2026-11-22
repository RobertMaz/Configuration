# Configuration

Единый список «что идёт через VPN» для роутеров (xray) и Clash/Mihomo-клиентов.
Через VPN — только то, что в списке, остальное напрямую.

## Как поменять правила

1. Отредактировать `proxy.list` (точечное), `categories.list` (целые сервисы)
   или `reject.list` (реклама), запушить.
2. Action проверит, соберёт `generated/` (скачав свежие категории), прогонит
   `xray -test` (две версии), закоммитит. Ошибка → Action красный,
   `generated/` не меняется, у всех остаётся последняя рабочая версия.
3. Нода подтянет в 04:40 (сразу после пересборки CI в 04:17), роутер — в 05:00,
   Clash-клиенты — в течение interval провайдера (до суток).
4. Раз в сутки (cron 04:17) Action сам обновляет категории и пересобирает.

Проверить локально до push: `python3 build.py` (кеш `.cache/`; `--refresh` — свежие категории).

Сборка также: вычищает из reject инфраструктуру Google/YouTube (SKIP_REJECT),
разрешает пересечения proxy∩reject в пользу proxy, режет reject по капу 50k
(бюджет RAM роутера; 80k деградировали матчинг — 22.11).

## Файлы

| Путь | Что |
|---|---|
| `proxy.list` | **источник** — ручные правила «через VPN» |
| `reject.list` | **источник** — ручные домены в блок (RU-видео: ad.mail.ru, r.mradx.net, adfox...) |
| `categories.list` | **источник** — категории blackmatrix7 (`имя=Папка`), geosite-вытяжки из .dat (`имя=geosite:категория@url`), easylist (`имя=easylist:URL`); теги geosite в нижнем регистре — runetfreedom хранит в верхнем |
| `build.py` | проверка + сборка |
| `generated/proxy.list` | rule-provider «через VPN» (ручные + категории) |
| `generated/reject.list` | rule-provider «реклама в блок» |
| `generated/xray-routing.json` | `routing` для xray на роутерах |
| `router/00-base.json` | шаблон xray роутера (inbounds/outbounds); плейсхолдеры `__UUID__`/`__WS_PATH__`/`__SERVER__`/`"__PORT__"` (порт с кавычками!) заполняются на роутере |
| `router/nftables.nft` | заворот TCP в xray; туннель пропускается по mark 255 |
| `router/xray-rules-update` | cron-скрипт роутера: скачать → `xray -test` → применить |

`clash/routing.yaml` удалён (22.11) — был ссылкой для панели 3x-ui (`subClashRules`), панель больше не живёт.

## Клиенты

- **Clash/Mihomo** (Clash Verge, CMFA, Clash Mi): подписка с ноды
  `https://<домен-ноды>/sub/<uuid>` (генерит и раскладывает vpnAsCode);
  правила приходят rule-provider'ом с той же ноды (`/rules/proxy.list`).
- **v2rayNG и прочие xray-клиенты**: `https://<домен>/sub/<uuid>.v2ray` — vless-ссылки.
- **Роутер** (OpenWrt): xray с `-confdir /etc/xray/conf.d`:
  - `00-base.json` — из `router/00-base.json`, с UUID/ws-path/сервером. Только на роутере.
    `observatory` раз в минуту пингует туннель: сервер мёртв — список идёт в
    `direct` (balancer `proxy-or-direct` в правилах), интернет дома не пропадает.
    Без `observatory` новые правила не пройдут `xray -test`, роутер останется на старых.
  - `10-routing.json` — `generated/xray-routing.json`, кладёт и обновляет
    `xray-rules-update` (cron `0 5 * * *` — после пересборки CI 04:17 и ноды 04:40).
  - `/etc/xray/nftables.nft` — из `router/nftables.nft`, грузится init-скриптом.

Установка на роутер (один раз):

```sh
RAW=https://raw.githubusercontent.com/RobertMaz/Configuration/main
mkdir -p /etc/xray/conf.d
uclient-fetch -O /etc/xray/nftables.nft        $RAW/router/nftables.nft
uclient-fetch -O /etc/xray/conf.d/00-base.json $RAW/router/00-base.json
sed -i -e 's/__UUID__/<uuid>/' -e 's|__WS_PATH__|<ws-path>|' \
       -e 's/__SERVER__/<домен>/' -e 's/"__PORT__"/<порт>/' /etc/xray/conf.d/00-base.json
grep -c '__' /etc/xray/conf.d/00-base.json    # обязано быть 0
uclient-fetch -O /usr/bin/xray-rules-update    $RAW/router/xray-rules-update
chmod +x /usr/bin/xray-rules-update
# в /etc/init.d/xray: -config /etc/xray/config.json → -confdir /etc/xray/conf.d
echo '0 5 * * * /usr/bin/xray-rules-update' >> /etc/crontabs/root
/etc/init.d/cron restart
xray-rules-update && logread -e xray-rules
```
