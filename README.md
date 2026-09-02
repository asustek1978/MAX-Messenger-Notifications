<p align="center">
  <img src="https://raw.githubusercontent.com/asustek1978/MAX-Messenger-Notifications/main/images/max-messenger-notifications.svg" alt="MAX Messenger Notifications" width="100%">
</p>

<h1 align="center">MAX Messenger Notifications</h1>

<p align="center">
  Уведомления и управление Home Assistant через MAX Messenger
</p>

<p align="center">
  <img alt="Home Assistant" src="https://img.shields.io/badge/Home%20Assistant-Custom%20Integration-41BDF5?logo=homeassistant&logoColor=white">
  <img alt="HACS" src="https://img.shields.io/badge/HACS-Custom%20Repository-41BDF5">
  <img alt="Version" src="https://img.shields.io/badge/version-0.7.0-6f42c1">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
</p>

**MAX Messenger Notifications** — неофициальная пользовательская интеграция Home Assistant для ботов **MAX Messenger**. Она отправляет обычные и аварийные уведомления, фото и видео, поддерживает inline-кнопки, несколько пользователей, обновление уже отправленных сообщений и резервную доставку через Home Assistant Push и VK.

> [!IMPORTANT]
> Начиная с версии 0.6.2 проект называется **MAX Messenger Notifications**, но внутренний домен Home Assistant намеренно сохранён как `slava_max` для полной обратной совместимости. Существующие автоматизации `slava_max.*` продолжат работать.

## 🚀 Возможности

| Возможность | Что делает |
|---|---|
| 💬 **Текстовые сообщения** | Отправка обычных сообщений и рассылок в MAX |
| 🚨 **Аварийный канал** | Отдельный канал для дыма, газа, протечек, питания и других критических событий |
| 🖼️ **Фото и видео** | Загрузка локальных изображений и видео в MAX |
| 🔘 **Inline-кнопки** | Callback-кнопки для управления Home Assistant прямо из MAX |
| 👥 **Несколько пользователей** | ACL-права и фильтрация получателей по разрешениям |
| ♻️ **Обновление сообщений** | `send_or_update` обновляет существующее сообщение вместо создания нового |
| 🧠 **Сохранение message_id** | Связка `key → message_id` переживает перезапуск Home Assistant |
| 🛟 **Резервные уведомления** | HA Push и/или VK используются только при ошибке отправки через MAX |
| 📷 **Blueprint камеры** | Фото / видео / фото + видео по любому триггеру Home Assistant |
| ⚡ **Long Polling** | Приём сообщений и callback-событий без блокировки запуска Home Assistant |

## 🛟 Резервные уведомления

MAX остаётся **основным каналом**. Если MAX API не смог принять или отправить сообщение — например, из-за ошибки API, таймаута или недоступности сети — интеграция может автоматически использовать резервные каналы.

Откройте:

**Настройки → Устройства и службы → MAX Messenger Notifications → Настроить → Резервные уведомления**

Доступны настройки:

- **Использовать Home Assistant Push при ошибке MAX**;
- выбор устройства `notify.mobile_app_*`;
- **Использовать VK как резерв при ошибке MAX**;
- выбор `notify`-сущности VK;
- резервировать **обычные сообщения**;
- резервировать **аварийные сообщения**.

Если включены и HA Push, и VK, при ошибке MAX сообщение отправляется в оба резервных канала.

> [!NOTE]
> MAX Bot API подтверждает успешную обработку запроса сервером, но не предоставляет интеграции надёжный отдельный статус «push физически доставлен на телефон». Поэтому резерв срабатывает именно при ошибке MAX API/соединения.

## ♻️ Обновление сообщения вместо новых сообщений

Для статусов, которые постоянно меняются, используйте `slava_max.send_or_update`.

Первый вызов создаёт сообщение и сохраняет его `message_id`. Следующие вызовы с тем же `key` обновляют это сообщение.

```yaml
action: slava_max.send_or_update
data:
  key: electricity_status
  message: >-
    ⚡ Электричество: {{ 'есть' if is_state('binary_sensor.power', 'on') else 'нет' }}
  notify: true
```

Обновление выполняется **тихо**, без нового push MAX. Если старое сообщение больше нельзя редактировать, создаётся новое и запоминается новый `message_id`.

## 🚨 Аварийный канал

В настройках интеграции укажите **ID аварийного канала MAX**:

**Настройки → Устройства и службы → MAX Messenger Notifications → Настроить → Основные настройки**

После этого в автоматизациях не нужно указывать `chat_id`:

```yaml
action: slava_max.send_emergency
data:
  message: "🚨 Обнаружена протечка воды"
  format: markdown
  notify: true
```

Аварийное изображение:

```yaml
action: slava_max.send_emergency_image
data:
  file_path: /media/emergency.jpg
  message: "🚨 Авария — снимок камеры"
```

## 🔧 Действия Home Assistant

| Действие | Назначение |
|---|---|
| `slava_max.send_message` | Отправить обычное сообщение |
| `slava_max.broadcast` | Рассылка разрешённым пользователям |
| `slava_max.send_emergency` | Отправить в аварийный MAX-канал |
| `slava_max.edit_message` | Изменить сообщение по `message_id` |
| `slava_max.send_or_update` | Создать или обновить сообщение по `key` |
| `slava_max.send_image` | Отправить изображение |
| `slava_max.broadcast_image` | Рассылка изображения |
| `slava_max.send_emergency_image` | Аварийное изображение |
| `slava_max.send_video` | Отправить видео |
| `slava_max.broadcast_video` | Рассылка видео |
| `slava_max.answer_callback` | Ответить на callback кнопки |

## 📦 Установка через HACS

1. Откройте **HACS → Интеграции**.
2. Нажмите **⋮ → Пользовательские репозитории**.
3. Добавьте:

```text
https://github.com/asustek1978/MAX-Messenger-Notifications
```

4. Категория: **Integration**.
5. Найдите **MAX Messenger Notifications** и установите.
6. **Полностью перезапустите Home Assistant**.
7. Откройте **Настройки → Устройства и службы → Добавить интеграцию** и выберите **MAX Messenger Notifications**.

## 📁 Ручная установка

Скопируйте:

```text
custom_components/slava_max/
```

в:

```text
/config/custom_components/slava_max/
```

После копирования полностью перезапустите Home Assistant.

## 📷 Blueprint камеры

Совместимый blueprint:

```text
blueprints/automation/slava_max/camera_snapshot_max.yaml
```

Поддерживает:

- фото;
- видео;
- фото + видео;
- пользовательские триггеры и условия;
- фильтрацию получателей по ACL-правам;
- настраиваемую длительность записи.

## 🎨 Иконки интеграции

Brand-ассеты находятся непосредственно внутри custom integration:

```text
custom_components/slava_max/brand/icon.png
custom_components/slava_max/brand/icon@2x.png
custom_components/slava_max/brand/dark_icon.png
custom_components/slava_max/brand/dark_icon@2x.png
custom_components/slava_max/brand/logo.png
custom_components/slava_max/brand/logo@2x.png
custom_components/slava_max/brand/dark_logo.png
custom_components/slava_max/brand/dark_logo@2x.png
```

Home Assistant 2026.3+ использует эти локальные brand-файлы автоматически.

> В интерфейсе HACS у custom repositories иногда может оставаться системная заглушка `icon not available`, даже если локальная иконка корректно отображается в Home Assistant. Это ограничение текущего HACS и не влияет на работу интеграции.

## 🔐 Безопасность

Не публикуйте в GitHub:

- токен MAX-бота;
- пароли и secrets Home Assistant;
- приватные URL;
- персональные `user_id`/`chat_id`, если репозиторий публичный.

## 📡 События

Для обратной совместимости используются:

```text
slava_max_event
slava_max_access_request
```

## 📄 Лицензия

MIT License. См. [LICENSE](LICENSE).
