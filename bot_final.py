#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бот расписания TAG - БЕЗ АВТОРИЗАЦИИ!
Использует публичный API EduPage
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from datetime import datetime, date, time as dt_time
import pytz
import re
import requests
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============= КОНФИГУРАЦИЯ =============
TELEGRAM_TOKEN = "TELEGRAM TOKEN HERE"
EDUPAGE_BASE_URL = "https://tag.edupage.org"
TIMEZONE = pytz.timezone('Europe/Tallinn')

# Все классы TAG
ALL_CLASSES = [
    "7a", "7b", "7c", "7e",
    "8a", "8b", "8c", "8d", "8e", "8L",
    "9a", "9b", "9c", "9d", "9e",
    "G1a", "G1b", "G1c",
    "G2a", "G2b", "G2k",
    "G3a", "G3b", "G3c", "G3k"
]

# Расписание звонков
BELL_SCHEDULE = [
    {"number": 1, "start": "08:00", "end": "08:45"},
    {"number": 2, "start": "08:55", "end": "09:40"},
    {"number": 3, "start": "09:50", "end": "10:35"},
    {"number": 4, "start": "11:00", "end": "11:45"},
    {"number": 5, "start": "12:10", "end": "12:55"},
    {"number": 6, "start": "13:20", "end": "14:05"},
    {"number": 7, "start": "14:15", "end": "15:00"},
    {"number": 8, "start": "15:10", "end": "15:55"},
    {"number": 9, "start": "16:05", "end": "16:50"},
    {"number": 10, "start": "16:55", "end": "17:40"},
]

# Триггеры для групповых чатов
TRIGGER_PATTERNS = {
    'current_lesson': [
        r'какой\s+(сейчас\s+)?урок',
        r'где\s+урок',
        r'куда\s+идти',
        r'в\s+какой\s+кабинет',
        r'урок\s+где',
        r'куда\s+идти',
        r'в\s+каком\s+кабинете',
        r'какой\s+кабинет',
        r'какой\s+класс',
    ],
    'today_schedule': [
        r'расписание\s+на\s+сегодня',
        r'что\s+сегодня',
        r'покажи\s+расписание',
        r'расписание',
    ],
    'bell_schedule': [
        r'когда\s+звонок',
        r'расписание\s+звонков',
    ],
}

class TAGTimetableAPI:
    """Класс для работы с публичным API TAG EduPage"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Referer': f'{EDUPAGE_BASE_URL}/timetable'
        })
        self.cache = {}
        self.cache_time = None

    def get_timetable_data(self, tt_num="442"):
        """
        Получить данные расписания из публичного API
        tt_num = номер расписания (442 = текущее)
        """
        # Кэширование на 5 минут
        now = datetime.now()
        if self.cache_time and (now - self.cache_time).seconds < 300:
            if tt_num in self.cache:
                return self.cache[tt_num]

        try:
            url = f"{EDUPAGE_BASE_URL}/timetable/server/regulartt.js?__func=regularttGetData"

            payload = {
                "__args": [None, tt_num],
                "__gsh": "00000000"
            }

            response = self.session.post(url, json=payload, timeout=10)
            response.raise_for_status()

            data = response.json()

            if 'r' in data and 'dbiAccessorRes' in data['r']:
                result = self._parse_timetable_data(data['r']['dbiAccessorRes'])
                self.cache[tt_num] = result
                self.cache_time = now
                logger.info(f"✓ Получено расписание TAG (tt_num={tt_num})")
                return result
            else:
                logger.error("Неожиданная структура ответа API")
                return None

        except Exception as e:
            logger.error(f"Ошибка получения расписания: {e}")
            return None

    def _parse_timetable_data(self, dbi_data):
        """Парсинг данных DBI в удобную структуру"""
        tables = {t['id']: t for t in dbi_data['tables']}

        # Создаём словари для быстрого доступа
        result = {
            'classes': {},
            'subjects': {},
            'teachers': {},
            'classrooms': {},
            'periods': {},
            'lessons': [],
            'cards': []
        }

        # Парсим классы
        for cls in tables['classes']['data_rows']:
            result['classes'][cls['id']] = {
                'id': cls['id'],
                'name': cls['name'],
                'short': cls['short']
            }

        # Парсим предметы
        for subj in tables['subjects']['data_rows']:
            result['subjects'][subj['id']] = {
                'id': subj['id'],
                'name': subj['name'],
                'short': subj['short']
            }

        # Парсим учителей
        for teacher in tables['teachers']['data_rows']:
            result['teachers'][teacher['id']] = {
                'id': teacher['id'],
                'short': teacher['short'],
                'firstname': teacher.get('firstname', ''),
                'lastname': teacher.get('lastname', '')
            }

        # Парсим кабинеты
        for room in tables['classrooms']['data_rows']:
            result['classrooms'][room['id']] = {
                'id': room['id'],
                'name': room['name'],
                'short': room['short']
            }

        # Парсим периоды (уроки по времени)
        for period in tables['periods']['data_rows']:
            result['periods'][period['id']] = {
                'id': period['id'],
                'number': int(period['period']),
                'start': period['starttime'],
                'end': period['endtime']
            }

        # Парсим уроки
        result['lessons'] = tables['lessons']['data_rows']

        # Парсим карточки (расписание)
        result['cards'] = tables['cards']['data_rows']

        return result

    def get_class_schedule_for_day(self, class_name, target_date=None):
        """Получить расписание класса на конкретный день"""
        if target_date is None:
            target_date = date.today()

        timetable = self.get_timetable_data()
        if not timetable:
            return None

        # Находим ID класса
        class_id = None
        for cid, cdata in timetable['classes'].items():
            if cdata['short'].lower() == class_name.lower():
                class_id = cid
                break

        if not class_id:
            logger.warning(f"Класс {class_name} не найден")
            return None

        # Находим уроки для этого класса
        lesson_ids = []
        for lesson in timetable['lessons']:
            if class_id in lesson.get('classids', []):
                lesson_ids.append(lesson['id'])

        # Определяем день недели (0=понедельник)
        weekday = target_date.weekday()
        day_mask_pos = weekday  # 0-6

        # Собираем расписание
        schedule_raw = []
        for card in timetable['cards']:
            if card['lessonid'] in lesson_ids:
                # Проверяем день недели
                days_string = card['days']  # например "10000" = понедельник
                if len(days_string) > day_mask_pos and days_string[day_mask_pos] == '1':
                    # Находим урок
                    lesson = next((l for l in timetable['lessons'] if l['id'] == card['lessonid']), None)
                    if not lesson:
                        continue

                    period = timetable['periods'].get(card['period'], {})
                    subject = timetable['subjects'].get(lesson['subjectid'], {})

                    # Собираем ВСЕ кабинеты
                    classroom_ids = card.get('classroomids', [])
                    classrooms = []
                    for cid in classroom_ids:
                        room = timetable['classrooms'].get(cid, {})
                        if room:
                            # Убираем всё после первого пробела
                            room_name = room.get('name', '?').split()[0]
                            classrooms.append(room_name)

                    # Собираем ВСЕХ учителей
                    teacher_ids = lesson.get('teacherids', [])
                    teachers = []
                    for tid in teacher_ids:
                        teacher = timetable['teachers'].get(tid, {})
                        if teacher:
                            teachers.append(teacher.get('short', '?'))

                    schedule_raw.append({
                        'period_number': period.get('number', 0),
                        'time_start': period.get('start', '?'),
                        'time_end': period.get('end', '?'),
                        'subject': subject.get('name', 'Неизвестно'),
                        'subject_short': subject.get('short', '?'),
                        'classrooms': classrooms,
                        'teachers': teachers
                    })

        # Группируем уроки по (номер урока + предмет)
        grouped = {}
        for item in schedule_raw:
            key = (item['period_number'], item['subject'])
            if key not in grouped:
                grouped[key] = {
                    'period_number': item['period_number'],
                    'time_start': item['time_start'],
                    'time_end': item['time_end'],
                    'subject': item['subject'],
                    'subject_short': item['subject_short'],
                    'classrooms': [],
                    'teachers': []
                }
            # Собираем все кабинеты и учителей
            grouped[key]['classrooms'].extend(item['classrooms'])
            grouped[key]['teachers'].extend(item['teachers'])

        # Убираем дубликаты и сортируем
        schedule = []
        for key in sorted(grouped.keys()):
            lesson = grouped[key]
            lesson['classrooms'] = sorted(list(set(lesson['classrooms'])))
            lesson['teachers'] = sorted(list(set(lesson['teachers'])))
            schedule.append(lesson)

        return schedule

class TimetableBot:
    """Telegram бот расписания TAG"""

    def __init__(self, settings_file="bot_settings.json"):
        self.api = TAGTimetableAPI()
        self.settings_file = settings_file
        self.user_settings = {}
        self.group_settings = {}
        self.load_settings()

    def load_settings(self):
        """Загрузить настройки из файла"""
        try:
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

                # Конвертируем user_settings
                self.user_settings = {int(k): v for k, v in data.get('user_settings', {}).items()}

                # Конвертируем group_settings с вложенными user_classes
                self.group_settings = {}
                for chat_id_str, settings in data.get('group_settings', {}).items():
                    chat_id = int(chat_id_str)

                    # Миграция со старого формата
                    if 'class' in settings and 'global_class' not in settings:
                        # Старый формат: {"class": "G3a"}
                        # Конвертируем в новый: {"global_class": "G3a", "global_enabled": True}
                        self.group_settings[chat_id] = {
                            'global_class': settings['class'],
                            'global_enabled': True,
                            'user_classes': {}
                        }
                        logger.info(f"Мигрировали чат {chat_id} со старого формата")
                    else:
                        # Новый формат или пустой
                        self.group_settings[chat_id] = {
                            'global_class': settings.get('global_class'),
                            'global_enabled': settings.get('global_enabled', False),
                            'user_classes': {}
                        }

                    # Конвертируем user_classes (строковые user_id → int)
                    if 'user_classes' in settings:
                        self.group_settings[chat_id]['user_classes'] = {
                            int(user_id_str): class_name
                            for user_id_str, class_name in settings['user_classes'].items()
                        }

                logger.info(f"✓ Загружено настроек: {len(self.user_settings)} пользователей, {len(self.group_settings)} групп")

                # Детальное логирование для отладки
                for chat_id, settings in self.group_settings.items():
                    logger.info(f"  Чат {chat_id}:")
                    logger.info(f"    - Глобальный класс: {settings.get('global_class')} (включён: {settings.get('global_enabled')})")
                    logger.info(f"    - Личных классов: {len(settings.get('user_classes', {}))}")
                    for user_id, user_class in settings.get('user_classes', {}).items():
                        logger.info(f"      • User {user_id}: {user_class}")

        except FileNotFoundError:
            logger.info("⚠️ Файл настроек не найден, создаём новый")
            self.user_settings = {}
            self.group_settings = {}
        except Exception as e:
            logger.error(f"Ошибка загрузки настроек: {e}")
            import traceback
            traceback.print_exc()
            self.user_settings = {}
            self.group_settings = {}

    def save_settings(self):
        """Сохранить настройки в файл"""
        try:
            data = {
                'user_settings': self.user_settings,
                'group_settings': self.group_settings
            }
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("✓ Настройки сохранены")
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек: {e}")

    def get_user_class_in_group(self, user_id, chat_id):
        """Получить класс пользователя в группе (с учётом личного и глобального)"""
        logger.debug(f"get_user_class_in_group: user_id={user_id}, chat_id={chat_id}")

        # Проверяем личный класс пользователя в этой группе
        if chat_id in self.group_settings:
            user_classes = self.group_settings[chat_id].get('user_classes', {})
            logger.debug(f"user_classes в группе {chat_id}: {user_classes}")

            if user_id in user_classes:
                personal_class = user_classes[user_id]
                logger.info(f"✓ Найден личный класс для user {user_id}: {personal_class}")
                return personal_class

        # Проверяем глобальный класс группы (если включён)
        if chat_id in self.group_settings:
            group_data = self.group_settings[chat_id]
            global_enabled = group_data.get('global_enabled', False)
            global_class = group_data.get('global_class', None)

            logger.debug(f"Глобальный класс: {global_class}, включён: {global_enabled}")

            if global_enabled and global_class:
                logger.info(f"✓ Используется глобальный класс: {global_class}")
                return global_class

        logger.warning(f"⚠️ Класс не найден для user {user_id} в чате {chat_id}")
        return None

    def set_user_class_in_group(self, user_id, chat_id, class_name):
        """Установить личный класс пользователя в группе"""
        if chat_id not in self.group_settings:
            self.group_settings[chat_id] = {}

        if 'user_classes' not in self.group_settings[chat_id]:
            self.group_settings[chat_id]['user_classes'] = {}

        self.group_settings[chat_id]['user_classes'][user_id] = class_name
        self.save_settings()

    def set_global_class(self, chat_id, class_name):
        """Установить глобальный класс для группы"""
        if chat_id not in self.group_settings:
            self.group_settings[chat_id] = {}

        self.group_settings[chat_id]['global_class'] = class_name
        self.group_settings[chat_id]['global_enabled'] = True
        self.save_settings()

    def toggle_global_class(self, chat_id):
        """Включить/выключить глобальный класс"""
        if chat_id not in self.group_settings:
            self.group_settings[chat_id] = {'global_enabled': False}

        current = self.group_settings[chat_id].get('global_enabled', False)
        self.group_settings[chat_id]['global_enabled'] = not current
        self.save_settings()

        return self.group_settings[chat_id]['global_enabled']

    def get_global_class_status(self, chat_id):
        """Получить статус глобального класса"""
        if chat_id not in self.group_settings:
            return False, None

        enabled = self.group_settings[chat_id].get('global_enabled', False)
        global_class = self.group_settings[chat_id].get('global_class', None)

        return enabled, global_class

    def get_current_time(self):
        return datetime.now(TIMEZONE)

    def get_current_lesson_number(self):
        """Определить номер текущего урока"""
        current_time = self.get_current_time().time()

        for bell in BELL_SCHEDULE:
            start = datetime.strptime(bell["start"], "%H:%M").time()
            end = datetime.strptime(bell["end"], "%H:%M").time()

            if start <= current_time <= end:
                return bell["number"], "lesson", bell

            if bell != BELL_SCHEDULE[-1]:
                next_bell = BELL_SCHEDULE[BELL_SCHEDULE.index(bell) + 1]
                next_start = datetime.strptime(next_bell["start"], "%H:%M").time()

                if end < current_time < next_start:
                    return bell["number"], "break", next_bell

        return None, "no_lessons", None

    def get_user_class(self, user_id):
        """Получить класс пользователя (только для личных чатов)"""
        return self.user_settings.get(user_id, {}).get("class", None)

    def set_user_class(self, user_id, class_name):
        """Установить класс пользователя (только для личных чатов)"""
        if user_id not in self.user_settings:
            self.user_settings[user_id] = {}
        self.user_settings[user_id]["class"] = class_name
        self.save_settings()

    def get_effective_class(self, user_id, chat_type, chat_id):
        """Получить эффективный класс для пользователя (с учётом типа чата)"""
        if chat_type == "private":
            return self.get_user_class(user_id)
        else:
            # В группе: сначала личный, потом глобальный
            return self.get_user_class_in_group(user_id, chat_id)

    def check_trigger(self, text, trigger_type):
        text_lower = text.lower()
        patterns = TRIGGER_PATTERNS.get(trigger_type, [])
        return any(re.search(pattern, text_lower) for pattern in patterns)

    # ========== КОМАНДЫ ==========

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        chat = update.effective_chat
        user = update.effective_user

        if chat.type == "private":
            keyboard = [
                [InlineKeyboardButton("📚 Текущий урок", callback_data='current_lesson')],
                [InlineKeyboardButton("📅 Расписание на сегодня", callback_data='today_schedule')],
                [InlineKeyboardButton("🔔 Расписание звонков", callback_data='bell_schedule')],
                [InlineKeyboardButton("⚙️ Выбрать класс", callback_data='select_class')],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            user_class = self.get_user_class(user.id)
            msg = f"Привет, {user.first_name}! 👋\n\n"
            msg += "Я бот расписания TAG.\n"
            msg += "✅ Передаёт Ваши данные КАПО/ЦРУ/Киеву/Кремлю/Моссаду\n\n"

            if user_class:
                msg += f"Ваш класс: **{user_class}**\n\n"
            else:
                msg += "⚠️ Класс не выбран\n\n"

            msg += "Выберите действие:"

            await update.message.reply_text(msg, reply_markup=reply_markup)
        else:
            # Групповой чат
            user_class = self.get_user_class_in_group(user.id, chat.id)
            global_enabled, global_class = self.get_global_class_status(chat.id)

            msg = "👋 Привет! Я бот расписания TAG.\n"
            msg += "✅ Передаёт Ваши данные КАПО/ЦРУ/Киеву/Кремлю/Моссаду\n\n"

            # Показываем статус глобального класса
            if global_enabled and global_class:
                msg += f"🌐 Глобальный класс: **{global_class}** (включён)\n"
            elif global_class:
                msg += f"🌐 Глобальный класс: {global_class} (выключен)\n"
            else:
                msg += "🌐 Глобальный класс: не установлен\n"

            # Показываем личный класс пользователя
            if user_class:
                msg += f"👤 Ваш класс: **{user_class}**\n\n"
            else:
                msg += "👤 Ваш класс: не установлен\n\n"

            msg += "**Команды:**\n"
            msg += "• `/setclass <класс>` - установить свой класс\n"
            msg += "• `/setclassglobal <класс>` - установить глобальный (админы)\n"
            msg += "• `/toggleglobal` - вкл/выкл глобальный класс (админы)\n"
            msg += "• `/schedule` - расписание на сегодня\n"
            msg += "• `/bells` - расписание звонков\n"
            msg += "• `/current` - текущий урок\n\n"
            msg += "Или просто спросите: \"Какой сейчас урок?\""

            await update.message.reply_text(msg)

    async def set_class_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /setclass - устанавливает личный класс"""
        chat = update.effective_chat
        user = update.effective_user

        if not context.args:
            await update.message.reply_text(
                "⚠️ Укажите класс!\n\n"
                "Использование: `/setclass <класс>`\n"
                "Например: `/setclass G3k`\n\n"
                "Для очистки: `/setclass clear`"
            )
            return

        class_name = context.args[0]

        # Проверяем команду очистки
        if class_name.lower() == 'clear':
            if chat.type == "private":
                # Очищаем личный класс
                if user.id in self.user_settings and 'class' in self.user_settings[user.id]:
                    del self.user_settings[user.id]['class']
                    self.save_settings()
                    await update.message.reply_text("✅ Ваш класс удалён")
                else:
                    await update.message.reply_text("ℹ️ У вас не был установлен класс")
            else:
                # Очищаем личный класс в группе
                if (chat.id in self.group_settings and
                    'user_classes' in self.group_settings[chat.id] and
                    user.id in self.group_settings[chat.id]['user_classes']):
                    del self.group_settings[chat.id]['user_classes'][user.id]
                    self.save_settings()
                    await update.message.reply_text("✅ Ваш личный класс удалён")
                else:
                    await update.message.reply_text("ℹ️ У вас не был установлен личный класс")
            return

        correct_class = next(
            (c for c in ALL_CLASSES if c.lower() == class_name.lower()),
            None
        )

        if not correct_class:
            await update.message.reply_text(
                f"❌ Класс '{class_name}' не найден!\n\n"
                f"Доступные: {', '.join(ALL_CLASSES[:15])}...\n\n"
                f"Для очистки: `/setclass clear`"
            )
            return

        if chat.type == "private":
            # Личный чат - обычная установка
            self.set_user_class(user.id, correct_class)
            await update.message.reply_text(f"✓ Ваш класс установлен: **{correct_class}**")
        else:
            # Групповой чат - личный класс пользователя в группе
            self.set_user_class_in_group(user.id, chat.id, correct_class)

            # Проверяем глобальный класс
            global_enabled, global_class = self.get_global_class_status(chat.id)

            msg = f"✓ Ваш личный класс установлен: **{correct_class}**\n\n"

            if global_enabled and global_class:
                msg += f"ℹ️ Глобальный класс чата: {global_class} (включён)\n"
                msg += "Ваш личный класс имеет приоритет!"
            else:
                msg += "ℹ️ Глобальный класс чата: не установлен"

            await update.message.reply_text(msg)

    async def set_class_global_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /setclassglobal - устанавливает глобальный класс (только админы)"""
        chat = update.effective_chat
        user = update.effective_user

        # Работает только в группах
        if chat.type == "private":
            await update.message.reply_text(
                "⚠️ Эта команда работает только в групповых чатах!"
            )
            return

        # Проверка прав администратора
        try:
            member = await chat.get_member(user.id)
            if member.status not in ['creator', 'administrator']:
                await update.message.reply_text(
                    "⚠️ Только администраторы могут устанавливать глобальный класс!"
                )
                return
        except Exception as e:
            logger.error(f"Ошибка проверки прав: {e}")
            await update.message.reply_text("⚠️ Не удалось проверить права администратора")
            return

        if not context.args:
            await update.message.reply_text(
                "⚠️ Укажите класс!\n\n"
                "Использование: `/setclassglobal <класс>`\n"
                "Например: `/setclassglobal G3a`\n\n"
                "Для очистки: `/setclassglobal clear`"
            )
            return

        class_name = context.args[0]

        # Проверяем команду очистки
        if class_name.lower() == 'clear':
            if chat.id in self.group_settings:
                self.group_settings[chat.id]['global_class'] = None
                self.group_settings[chat.id]['global_enabled'] = False
                self.save_settings()

                msg = "✅ Глобальный класс чата удалён и выключен\n\n"
                msg += "ℹ️ Участники должны установить личные классы через `/setclass <класс>`"
                await update.message.reply_text(msg)
            else:
                await update.message.reply_text("ℹ️ Глобальный класс не был установлен")
            return

        correct_class = next(
            (c for c in ALL_CLASSES if c.lower() == class_name.lower()),
            None
        )

        if not correct_class:
            await update.message.reply_text(
                f"❌ Класс '{class_name}' не найден!\n\n"
                f"Доступные: {', '.join(ALL_CLASSES[:15])}...\n\n"
                f"Для очистки: `/setclassglobal clear`"
            )
            return

        # Устанавливаем глобальный класс
        self.set_global_class(chat.id, correct_class)

        msg = f"✅ Глобальный класс чата установлен: **{correct_class}**\n\n"
        msg += "ℹ️ Теперь все участники без личного класса будут использовать этот класс.\n"
        msg += "💡 Участники могут установить свой класс через `/setclass <класс>`"

        await update.message.reply_text(msg)

    async def toggle_global_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /toggleglobal - включить/выключить глобальный класс (только админы)"""
        chat = update.effective_chat
        user = update.effective_user

        # Работает только в группах
        if chat.type == "private":
            await update.message.reply_text(
                "⚠️ Эта команда работает только в групповых чатах!"
            )
            return

        # Проверка прав администратора
        try:
            member = await chat.get_member(user.id)
            if member.status not in ['creator', 'administrator']:
                await update.message.reply_text(
                    "⚠️ Только администраторы могут управлять глобальным классом!"
                )
                return
        except Exception as e:
            logger.error(f"Ошибка проверки прав: {e}")
            await update.message.reply_text("⚠️ Не удалось проверить права администратора")
            return

        # Переключаем статус
        new_status = self.toggle_global_class(chat.id)

        if new_status:
            _, global_class = self.get_global_class_status(chat.id)
            if global_class:
                msg = f"✅ Глобальный класс **включён**: {global_class}\n\n"
                msg += "Все участники без личного класса будут использовать его."
            else:
                msg = "✅ Глобальный класс включён, но не установлен.\n\n"
                msg += "Используйте `/setclassglobal <класс>` для установки."
        else:
            msg = "⏸️ Глобальный класс **выключен**\n\n"
            msg += "Участники должны установить личные классы через `/setclass <класс>`"

        await update.message.reply_text(msg)

    async def show_current_lesson(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать текущий урок"""
        # Определяем тип вызова
        if update.callback_query:
            chat = update.callback_query.message.chat
            user = update.callback_query.from_user
        else:
            chat = update.effective_chat
            user = update.effective_user

        class_name = self.get_effective_class(user.id, chat.type, chat.id)

        if not class_name:
            msg = "⚠️ Класс не установлен!\nИспользуйте: `/setclass <класс>`"
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.edit_message_text(msg)
            else:
                await update.message.reply_text(msg)
            return

        now = self.get_current_time()
        lesson_num, status, bell_info = self.get_current_lesson_number()

        if status == "lesson":
            # Получаем расписание на сегодня
            schedule = self.api.get_class_schedule_for_day(class_name, now.date())

            bell = BELL_SCHEDULE[lesson_num - 1]
            msg = f"📚 Сейчас {lesson_num} урок\n"
            msg += f"⏰ {bell['start']} - {bell['end']}\n\n"
            msg += f"👥 Класс: {class_name}\n"

            if schedule:
                # Находим текущий урок
                current_lesson = next((l for l in schedule if l['period_number'] == lesson_num), None)

                if current_lesson:
                    msg += f"📖 {current_lesson['subject']}\n"

                    # Отображаем все кабинеты через /
                    classrooms = current_lesson.get('classrooms', [])
                    if classrooms:
                        if len(classrooms) == 1:
                            msg += f"🚪 Кабинет: {classrooms[0]}\n"
                        else:
                            msg += f"🚪 Кабинеты: {'/'.join(classrooms)}\n"

                    # Отображаем всех учителей через /
                    teachers = current_lesson.get('teachers', [])
                    if teachers:
                        if len(teachers) == 1:
                            msg += f"👨‍🏫 {teachers[0]}"
                        else:
                            msg += f"👨‍🏫 {'/'.join(teachers)}"
                else:
                    msg += "⚠️ Урок не найден в расписании"
            else:
                msg += "⚠️ Не удалось загрузить расписание"

        elif status == "break":
            # Получаем расписание на сегодня
            schedule = self.api.get_class_schedule_for_day(class_name, now.date())

            msg = f"⏸️ Сейчас перемена\n"
            msg += f"Следующий {bell_info['number']} урок в {bell_info['start']}\n\n"

            if schedule:
                # Находим следующий урок
                next_lesson = next((l for l in schedule if l['period_number'] == bell_info['number']), None)

                if next_lesson:
                    msg += f"👥 Класс: {class_name}\n"
                    msg += f"📖 {next_lesson['subject']}\n"

                    # Отображаем кабинеты
                    classrooms = next_lesson.get('classrooms', [])
                    if classrooms:
                        if len(classrooms) == 1:
                            msg += f"🚪 Кабинет: {classrooms[0]}\n"
                        else:
                            msg += f"🚪 Кабинеты: {'/'.join(classrooms)}\n"

                    # Отображаем учителей
                    teachers = next_lesson.get('teachers', [])
                    if teachers:
                        if len(teachers) == 1:
                            msg += f"👨‍🏫 {teachers[0]}"
                        else:
                            msg += f"👨‍🏫 {'/'.join(teachers)}"
                else:
                    msg += "ℹ️ Следующий урок не найден в расписании"
            else:
                msg += "⚠️ Не удалось загрузить расписание"
        else:
            if now.time() < datetime.strptime("08:00", "%H:%M").time():
                # Получаем расписание на сегодня
                schedule = self.api.get_class_schedule_for_day(class_name, now.date())

                msg = "🌅 Уроки ещё не начались\nПервый урок в 08:00\n\n"

                if schedule:
                    # Находим первый урок (номер 1)
                    first_lesson = next((l for l in schedule if l['period_number'] == 1), None)

                    if first_lesson:
                        msg += f"👥 Класс: {class_name}\n"
                        msg += f"📖 {first_lesson['subject']}\n"

                        # Кабинеты
                        classrooms = first_lesson.get('classrooms', [])
                        if classrooms:
                            if len(classrooms) == 1:
                                msg += f"🚪 Кабинет: {classrooms[0]}\n"
                            else:
                                msg += f"🚪 Кабинеты: {'/'.join(classrooms)}\n"

                        # Учителя
                        teachers = first_lesson.get('teachers', [])
                        if teachers:
                            if len(teachers) == 1:
                                msg += f"👨‍🏫 {teachers[0]}"
                            else:
                                msg += f"👨‍🏫 {'/'.join(teachers)}"
                    else:
                        msg += "ℹ️ Первый урок не найден в расписании"
            else:
                msg = "🏠 Уроки закончились"

        msg += f"\n\n🕐 {now.strftime('%H:%M')}"

        if update.callback_query:
            await update.callback_query.answer()
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='back_to_menu')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
        else:
            await update.message.reply_text(msg)

    async def show_today_schedule(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать расписание на сегодня"""
        # Определяем тип вызова
        if update.callback_query:
            chat = update.callback_query.message.chat
            user = update.callback_query.from_user
        else:
            chat = update.effective_chat
            user = update.effective_user

        class_name = self.get_effective_class(user.id, chat.type, chat.id)

        if not class_name:
            msg = "⚠️ Класс не установлен!"
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.edit_message_text(msg)
            else:
                await update.message.reply_text(msg)
            return

        today = date.today()
        schedule = self.api.get_class_schedule_for_day(class_name, today)

        msg = f"📅 Расписание на {today.strftime('%d.%m.%Y')}\n"
        msg += f"👥 Класс: {class_name}\n\n"

        if schedule:
            for lesson in schedule:
                classroom_str = '/'.join(lesson['classrooms']) if lesson['classrooms'] else '?'

                msg += f"{lesson['period_number']}️⃣ {lesson['time_start']}-{lesson['time_end']} - "
                msg += f"{lesson['subject']} ({classroom_str})\n"
        else:
            msg += "⚠️ Расписание не найдено"

        if update.callback_query:
            await update.callback_query.answer()
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='back_to_menu')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
        else:
            await update.message.reply_text(msg)

    async def show_bells(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Расписание звонков"""
        now = self.get_current_time()
        lesson_num, status, _ = self.get_current_lesson_number()

        msg = "🔔 Расписание звонков:\n\n"

        for bell in BELL_SCHEDULE:
            if status == "lesson" and bell["number"] == lesson_num:
                msg += f"➡️ {bell['number']} урок: {bell['start']} - {bell['end']} ⬅️\n"
            else:
                msg += f"{bell['number']} урок: {bell['start']} - {bell['end']}\n"

            if bell != BELL_SCHEDULE[-1]:
                next_bell = BELL_SCHEDULE[BELL_SCHEDULE.index(bell) + 1]
                start_time = datetime.strptime(bell['end'], "%H:%M")
                end_time = datetime.strptime(next_bell['start'], "%H:%M")
                break_duration = int((end_time - start_time).seconds / 60)

                if status == "break" and bell["number"] == lesson_num:
                    msg += f"   ⏸️ Перемена: {break_duration} мин ⬅️\n"
                else:
                    msg += f"   ⏸️ Перемена: {break_duration} мин\n"

        msg += f"\n🕐 Сейчас: {now.strftime('%H:%M:%S')}"

        # Определяем тип вызова
        if update.callback_query:
            # Вызов через кнопку
            await update.callback_query.answer()
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data='back_to_menu')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
        else:
            # Вызов через команду
            await update.message.reply_text(msg)

    async def show_class_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Выбор класса"""
        query = update.callback_query
        await query.answer()

        classes_grouped = [ALL_CLASSES[i:i+3] for i in range(0, len(ALL_CLASSES), 3)]

        keyboard = []
        for row in classes_grouped[:15]:
            keyboard.append([
                InlineKeyboardButton(cls, callback_data=f'set_class_{cls}')
                for cls in row
            ])

        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data='back_to_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text("⚙️ Выберите ваш класс:", reply_markup=reply_markup)

    async def set_class_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка выбора класса"""
        query = update.callback_query
        await query.answer()

        class_name = query.data.replace('set_class_', '')
        self.set_user_class(query.from_user.id, class_name)

        keyboard = [[InlineKeyboardButton("◀️ Главное меню", callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"✓ Класс установлен: **{class_name}**",
            reply_markup=reply_markup
        )

    async def back_to_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Главное меню"""
        query = update.callback_query
        await query.answer()

        user_class = self.get_user_class(query.from_user.id)

        keyboard = [
            [InlineKeyboardButton("📚 Текущий урок", callback_data='current_lesson')],
            [InlineKeyboardButton("📅 Расписание на сегодня", callback_data='today_schedule')],
            [InlineKeyboardButton("🔔 Расписание звонков", callback_data='bell_schedule')],
            [InlineKeyboardButton("⚙️ Выбрать класс", callback_data='select_class')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        msg = "Выберите действие:"
        if user_class:
            msg = f"Ваш класс: {user_class}\n\n" + msg

        await query.edit_message_text(msg, reply_markup=reply_markup)

    async def handle_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка сообщений в группе"""
        chat = update.effective_chat
        if chat.type == "private":
            return

        text = update.message.text

        if self.check_trigger(text, 'current_lesson'):
            await self.show_current_lesson(update, context)
        elif self.check_trigger(text, 'today_schedule'):
            await self.show_today_schedule(update, context)
        elif self.check_trigger(text, 'bell_schedule'):
            await self.show_bells(update, context)

def main():
    print("🤖 Запуск бота расписания TAG...")
    print("✅ БЕЗ авторизации!")
    print("✅ Использует публичный API EduPage\n")

    bot = TimetableBot()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CommandHandler("setclass", bot.set_class_command))
    app.add_handler(CommandHandler("setclassglobal", bot.set_class_global_command))
    app.add_handler(CommandHandler("toggleglobal", bot.toggle_global_command))
    app.add_handler(CommandHandler("schedule", bot.show_today_schedule))
    app.add_handler(CommandHandler("bells", bot.show_bells))
    app.add_handler(CommandHandler("current", bot.show_current_lesson))

    # Callback кнопки
    app.add_handler(CallbackQueryHandler(bot.show_current_lesson, pattern='^current_lesson$'))
    app.add_handler(CallbackQueryHandler(bot.show_today_schedule, pattern='^today_schedule$'))
    app.add_handler(CallbackQueryHandler(bot.show_bells, pattern='^bell_schedule$'))
    app.add_handler(CallbackQueryHandler(bot.show_class_selection, pattern='^select_class$'))
    app.add_handler(CallbackQueryHandler(bot.set_class_callback, pattern='^set_class_'))
    app.add_handler(CallbackQueryHandler(bot.back_to_menu, pattern='^back_to_menu$'))

    # Сообщения в группах
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_group_message))

    print("✓ Бот запущен и готов к работе!")
    print("✓ Получает РЕАЛЬНОЕ расписание из TAG")
    print("✓ Работает в личных и групповых чатах\n")

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
