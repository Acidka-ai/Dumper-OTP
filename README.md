# DumperOTP

Небольшой дампер `STM32WB55` через `USB DFU` бутлоадер.

Проверялось на:
- `Flipper Zero` в режиме `DFU`
- платах на `STM32WB55CGU6`, если МК тоже загружен в `DFU`

`STM32CubeProgrammer` для работы не нужен. Чтение идёт напрямую через `PyUSB`.

## Что лежит в проекте

- `dfu_otp_dumper.py` — основной скрипт, читает OTP
- `otp_parse.py` — разбирает уже снятый дамп
- `dump_otp.sh` — shell-обёртка для Linux/macOS
- `parse_otp.sh` — shell-обёртка для парсера
- `requirements.txt` — зависимости Python

## Что читается

По умолчанию читается OTP-блок `STM32WB55`:

- начало: `0x1FFF7000`
- размер: `0x400` байт

## Что нужно для запуска

Нужно:

1. `Python 3`
2. `PyUSB`
3. доступ к USB-устройству
4. устройство должно быть именно в `STM32 USB DFU`, а не просто подключено кабелем

Установка зависимостей:

```bash
python3 -m pip install --user -r requirements.txt
```

На Fedora можно поставить пакет так:

```bash
sudo dnf install python3-pyusb
```
## Важно!!!
Для корректной работы необходимо запускать через sudo или от имени администратора ( на Windows )

## Проверка DFU

Сначала смотри, видит ли скрипт DFU-интерфейс:

```bash
./dump_otp.sh --list
```

Если всё нормально, вывод будет примерно такой:

```text
[0] vid=0x0483 pid=0xDF11 ...
```

`0483:DF11` — это обычный `STM32 USB DFU`.

## Как снять OTP

Обычный запуск:

```bash
sudo ./dump_otp.sh --vid 0x0483 --pid 0xDF11 --alt 0 -o otp_dump.bin
```

Если непон, какой `alt` использовать:

```bash
sudo ./dump_otp.sh --vid 0x0483 --pid 0xDF11 --probe-alts
```

Если устройство отвечает на всех `alt`, можно использовать `alt 0`.

## Как разобрать дамп

После чтения:

```bash
./parse_otp.sh otp_dump.bin
```

Если нужен JSON:

```bash
./parse_otp.sh otp_dump.bin --json
```

Парсер вытаскивает такие поля:

- `magic`
- `otp_version`
- `build_date_utc`
- `version`
- `firmware`
- `body`
- `connect`
- `display_id`
- `color`
- `region`
- `name`

## Пример вывода

```text
Parsed OTP:
  magic: 0xBABE
  otp_version: 2
  build_date_utc: 2025-05-09T12:58:47+00:00
  version: 12
  firmware: 7
  body: 9
  connect: 6
  display_id: 2 (mgg)
  color: black (code 1)
  region: world (code 4)
  name: Acidka
```

## Частые проблемы

`No DFU devices found`

Устройство не в режиме `STM32 DFU`. Просто подключённый по USB Flipper сюда не подходит, нужен именно DFU.

`access denied to USB DFU device`

Система видит устройство, но не даёт его открыть. Быстрый вариант:

```bash
sudo ./dump_otp.sh --vid 0x0483 --pid 0xDF11 --alt 0 -o otp_dump.bin
```

## Изначально не планировал заливать но окда
