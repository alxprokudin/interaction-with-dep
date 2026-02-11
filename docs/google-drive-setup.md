# Настройка Google Drive

## 1. Создание сервисного аккаунта

1. Откройте [Google Cloud Console](https://console.cloud.google.com/)
2. Создайте проект или выберите существующий
3. Включите **Google Drive API** (API и сервисы → Библиотека → Google Drive API)
4. Создайте учётные данные:
   - Учётные данные → Создать учётные данные → Сервисный аккаунт
   - Скачайте JSON-ключ

## 2. Папка на Google Drive

1. Создайте папку на Google Drive (или используйте существующую)
2. Откройте доступ для **email сервисного аккаунта** (напр. `xxx@project.iam.gserviceaccount.com`) с правами **Редактор**
3. Скопируйте ID папки из URL: `https://drive.google.com/drive/folders/XXXXX` → `XXXXX`

## 3. Переменные окружения

```
GOOGLE_DRIVE_CREDENTIALS_FILE=credentials.json
GOOGLE_DRIVE_FOLDER_ID=ваш_id_папки
```

Поместите `credentials.json` в корень проекта.

## Структура папок

При заведении продукта создаётся:

```
{корневая папка}/
  {поставщик}/
    {номенклатура}/
      Сертификаты и декларации/
      Фото продукта/
      Фото этикетки/
```
