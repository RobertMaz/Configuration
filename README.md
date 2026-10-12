# Configuration

Единый список правил «что идёт через VPN» для всех потребителей: Clash-клиентов и xray-роутеров.
Логика везде одна — **whitelist Proxy**: через VPN только то, что в списке, остальное напрямую.

## Где что

| Путь | Что | Кто правит |
|---|---|---|
| `rules/upstream.txt` | сервисы из [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script) | руками |
| `rules/custom.list` | ручные правила (`DOMAIN-SUFFIX,x.com`, `IP-CIDR,…`) | руками |
| `generated/proxy.list` | Surge-list для subconverter | Action |
| `generated/xray-routing.json` | объект `routing` для xray | Action |
| `clash/subconverter.ini` | политика Clash, подключается через `&config=` в ссылке subconverter | руками, редко |
| `router/xray-rules-update` | скрипт обновления на роутере | руками |
| `scripts/build.py` | генератор | руками |

## Как добавить домен

1. Дописать строку в `rules/custom.list`, закоммитить в `main`.
2. Action `Build rules` пересоберёт `generated/`, проверит xray-ядром и закоммитит.
3. Роутеры подтянут в 04:30 UTC, Clash-клиенты — при обновлении профиля (раз в сутки).

Локально: `python3 scripts/build.py` (только stdlib).

Сборка падает, а не публикует урезанный список, если не скачался upstream, встретилось кривое правило или правил меньше порога.
Красный Action = старый список продолжает работать, письмо от GitHub — сигнал разобраться.

## Роутер (OpenWrt + xray)

Контракт: xray запущен с `-confdir /etc/xray/conf.d`:

- `00-base.json` — inbounds, outbounds (обязательно теги `proxy` и `direct`), секреты. **Без `routing`.** Живёт только на роутере.
- `10-routing.json` — скачивается из `generated/xray-routing.json`.

Установка:

```sh
uclient-fetch -O /usr/bin/xray-rules-update \
  https://raw.githubusercontent.com/RobertMaz/Configuration/main/router/xray-rules-update
chmod +x /usr/bin/xray-rules-update
echo '30 4 * * * /usr/bin/xray-rules-update' >> /etc/crontabs/root
/etc/init.d/cron restart
xray-rules-update        # первый прогон
logread -e xray-rules    # что произошло
```

Скрипт применяет новый файл, только если он изменился и `xray run -test` принял полный конфиг; иначе оставляет текущий.
