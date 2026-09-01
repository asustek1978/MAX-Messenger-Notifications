<p align="center">
  <img src="images/max-messenger-notifications.svg" width="300" alt="MAX Messenger Notifications">
</p>

# MAX Messenger Notifications for Home Assistant

Неофициальная интеграция Home Assistant для ботов **MAX Messenger**.

> Текущая совместимая версия: **0.6.3**

## Важно о совместимости

Начиная с 0.6.2 проект называется **MAX Messenger Notifications**, но внутренний домен Home Assistant намеренно сохранён:

```text
slava_max
```

Это сделано для полной обратной совместимости. Существующие автоматизации и действия `slava_max.*` продолжат работать без изменений.

## Возможности

- текстовые уведомления Home Assistant → MAX;
- отправка фото и видео;
- несколько пользователей и ACL-права;
- callback-кнопки;
- фоновый Long Polling;
- отдельный аварийный канал MAX;
- аварийные сообщения и изображения без указания `chat_id` в каждой автоматизации;
- локальная иконка интеграции для Home Assistant;
- blueprint камеры с режимами Фото / Видео / Фото + видео.

## Установка через HACS

Добавьте этот репозиторий как **Custom repository → Integration**:

```text
https://github.com/asustek1978/MAX-Messenger-Notifications
```

Установите **MAX Messenger Notifications** и перезапустите Home Assistant.

## Ручная установка

Скопируйте:

```text
custom_components/slava_max/
```

в:

```text
/config/custom_components/slava_max/
```

После этого полностью перезапустите Home Assistant.

## Основные действия

```text
slava_max.send_message
slava_max.broadcast
slava_max.send_image
slava_max.broadcast_image
slava_max.send_video
slava_max.broadcast_video
slava_max.send_emergency
slava_max.send_emergency_image
slava_max.answer_callback
```

### Аварийный канал

В Home Assistant откройте:

**Настройки → Устройства и службы → MAX Messenger Notifications → Настроить → Основные настройки**

и укажите **ID аварийного канала MAX**.

После этого аварийное сообщение отправляется без `chat_id`:

```yaml
action: slava_max.send_emergency
data:
  message: "🚨 Аварийное уведомление"
```

Аварийное изображение:

```yaml
action: slava_max.send_emergency_image
data:
  file_path: /media/emergency.jpg
  message: "🚨 Авария — снимок камеры"
```

## Blueprint камеры

Совместимый blueprint находится здесь:

```text
blueprints/automation/slava_max/camera_snapshot_max.yaml
```

Он использует действия `slava_max.broadcast_image` и `slava_max.broadcast_video`.

## Иконка интеграции

Начиная с 0.6.3 в интеграции присутствуют локальные brand-файлы:

```text
custom_components/slava_max/brand/icon.png
custom_components/slava_max/brand/icon@2x.png
```

## События

Для обратной совместимости сохранены события:

```text
slava_max_event
slava_max_access_request
```

## Безопасность

Не публикуйте токен MAX-бота, пароли, секреты Home Assistant и приватные URL в публичном репозитории.

## License

MIT License. See [LICENSE](LICENSE).
