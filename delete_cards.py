import asyncio
import json
import logging
import os
import random
import re
import time
from playwright.async_api import async_playwright

# ─── ЛОГИРОВАНИЕ ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,  # Включаем DEBUG для детального логирования
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('delete_cards.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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

AUTH_URL = 'https://vamprivet.ru/auth/'
CARDS_URL = 'https://vamprivet.ru/personal/cards/'
KEEP_CARDS = 1
DELETE_DELAY = 2
BATCH_SIZE = 3
MAX_RETRIES = 3
MAX_LOGIN_RETRIES = 3

# ─── ЗАГРУЗКА АККАУНТОВ ──────────────────────────────────────
def load_accounts():
    accounts = []
    accounts_file = 'accounts.txt'
    if not os.path.exists(accounts_file):
        logger.error(f"❌ Файл {accounts_file} не найден")
        return accounts
    
    with open(accounts_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(':')
            if len(parts) >= 2:
                phone = parts[0].strip()
                password = ':'.join(parts[1:]).strip()
                accounts.append({'phone': phone, 'password': password})
    
    return accounts

def normalize_phone(phone):
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 11 and digits[0] in ['7', '8']:
        digits = digits[1:]
    if len(digits) == 10:
        return digits
    return phone

async def apply_antidetect(page):
    for script in ANTIDETECT_SCRIPTS:
        try:
            await page.evaluate(script)
        except:
            pass

async def perform_login(page, phone, password):
    """Выполняет авторизацию"""
    logger.info(f"🔐 Авторизация {phone}...")
    
    for attempt in range(MAX_LOGIN_RETRIES):
        try:
            logger.debug(f"🌐 Переход на {AUTH_URL} (попытка {attempt+1})")
            await page.goto(AUTH_URL, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(random.uniform(2, 4))
            
            logger.debug(f"📍 Текущий URL после перехода: {page.url}")
            
            # Если уже авторизован
            if '/personal' in page.url or '/add-card' in page.url or '/cards' in page.url:
                logger.info(f"✅ Уже авторизован")
                return True
            
            # Ждем форму
            try:
                logger.debug("⏳ Ожидание формы входа...")
                await page.wait_for_selector('#login', timeout=10000)
                logger.debug("✅ Форма найдена")
            except Exception as e:
                logger.warning(f"⏳ Форма не загрузилась: {e}")
                continue
            
            # Заполняем форму
            logger.debug(f"📝 Ввод телефона: {normalize_phone(phone)}")
            await page.fill('#login', normalize_phone(phone))
            await asyncio.sleep(random.uniform(0.5, 1))
            
            logger.debug("📝 Ввод пароля")
            await page.fill('#pass', password)
            await asyncio.sleep(random.uniform(0.5, 1))
            
            # Кнопка входа
            btn = page.locator('button[type="submit"]:has-text("Войти")')
            if await btn.count() == 0:
                btn = page.locator('button:has-text("Войти")')
            
            if await btn.count() > 0:
                logger.debug("🔘 Нажатие кнопки входа")
                await btn.click()
            else:
                logger.error(f"❌ Кнопка не найдена")
                continue
            
            # Ждем редирект
            logger.debug("⏳ Ожидание редиректа...")
            for i in range(30):
                await asyncio.sleep(1)
                current_url = page.url
                
                # Проверяем ошибку
                try:
                    error_elem = page.locator('.auth__master-error:not(.is-hidden)')
                    if await error_elem.count() > 0:
                        error_text = await error_elem.inner_text()
                        if error_text.strip():
                            logger.error(f"❌ Ошибка: {error_text}")
                            return False
                except:
                    pass
                
                if '/personal' in current_url or '/add-card' in current_url or '/cards' in current_url:
                    logger.info(f"✅ Авторизация успешна для {phone}")
                    return True
                
                if i % 5 == 0:
                    logger.debug(f"⏳ Ожидание... {i+1}/30, URL: {current_url}")
            
            logger.warning(f"⏳ Таймаут, попытка {attempt+1}/{MAX_LOGIN_RETRIES}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка авторизации: {e}")
            import traceback
            traceback.print_exc()
            if attempt < MAX_LOGIN_RETRIES - 1:
                await asyncio.sleep(3)
    
    logger.error(f"❌ Не удалось авторизовать {phone}")
    return False

async def get_tokens(page):
    """Получает токены из cookies"""
    cookies = await page.context.cookies()
    logger.debug(f"🍪 Получено {len(cookies)} cookies")
    
    access_token = None
    hash_query = None
    
    for cookie in cookies:
        if cookie['name'] == 'accessToken':
            access_token = cookie['value']
            logger.debug(f"  ✅ Найден accessToken: {access_token[:30]}...")
        elif cookie['name'] == 'hashQuery':
            hash_query = cookie['value']
            logger.debug(f"  ✅ Найден hashQuery: {hash_query[:30]}...")
    
    return access_token, hash_query

async def get_csrf_token(page):
    """Получает CSRF токен со страницы /personal/cards/ с повторными попытками"""
    logger.debug("🔍 Начинаем поиск CSRF токена...")
    
    # Переходим на страницу карт
    logger.debug(f"🌐 Переход на {CARDS_URL}")
    await page.goto(CARDS_URL, wait_until='domcontentloaded', timeout=30000)
    
    # Ждем загрузки страницы с повторными проверками
    logger.debug("⏳ Ожидание загрузки страницы карт...")
    
    # Даем больше времени на загрузку (увеличил с 1 до 3 секунд)
    await asyncio.sleep(3)
    
    # Проверяем, что страница загрузилась
    for wait_attempt in range(15):  # Увеличил до 15 попыток
        logger.debug(f"⏳ Проверка загрузки... {wait_attempt+1}/15")
        
        try:
            # Проверяем наличие контента
            has_content = await page.evaluate("""
                () => {
                    return !!(
                        document.querySelector('.cards-list') || 
                        document.querySelector('.card-item') ||
                        document.querySelector('.personal-cards') ||
                        document.querySelector('[data-tid="cards"]') ||
                        document.querySelector('main') ||
                        document.querySelector('.container')
                    );
                }
            """)
            
            if has_content:
                logger.debug("✅ Страница карт загружена")
                break
        except:
            pass
        
        await asyncio.sleep(1)
    
    # Дебаг состояния страницы
    logger.debug(f"📍 Текущий URL после перехода: {page.url}")
    logger.debug(f"📄 Заголовок: {await page.title()}")
    
    # Пробуем получить CSRF с повторными попытками
    for csrf_attempt in range(5):
        logger.debug(f"🔍 Попытка получения CSRF #{csrf_attempt+1}/5")
        
        # Даем время на загрузку JS перед каждой попыткой
        if csrf_attempt > 0:
            logger.debug(f"⏳ Ждем 3 секунды перед попыткой {csrf_attempt+1}...")
            await asyncio.sleep(3)
        
        result = await page.evaluate("""
            () => {
                const debug = {};
                
                // 1. Из window.csrf
                try {
                    debug.windowCsrf = window.csrf || 'undefined';
                    if (window.csrf && String(window.csrf).length > 5) {
                        return { found: true, value: window.csrf, source: 'window.csrf', debug: debug };
                    }
                } catch(e) {
                    debug.windowCsrf = 'error: ' + e.message;
                }
                
                // 2. Из мета-тега
                try {
                    const meta = document.querySelector('meta[name="csrf-token"]');
                    debug.metaCsrf = meta ? meta.getAttribute('content') : 'not found';
                    if (meta) {
                        const val = meta.getAttribute('content');
                        if (val && val.length > 5) {
                            return { found: true, value: val, source: 'meta-tag', debug: debug };
                        }
                    }
                } catch(e) {
                    debug.metaCsrf = 'error: ' + e.message;
                }
                
                // 3. Из cookies
                try {
                    const cookieCsrf = document.cookie.split('; ').find(row => row.startsWith('csrf='));
                    debug.cookieCsrf = cookieCsrf ? cookieCsrf.split('=')[1] : 'not found';
                    if (cookieCsrf) {
                        const val = cookieCsrf.split('=')[1];
                        if (val && val.length > 5) {
                            return { found: true, value: val, source: 'cookie', debug: debug };
                        }
                    }
                } catch(e) {
                    debug.cookieCsrf = 'error: ' + e.message;
                }
                
                // 4. Из window.__INITIAL_STATE__
                try {
                    debug.initialState = window.__INITIAL_STATE__ ? 'exists' : 'undefined';
                    if (window.__INITIAL_STATE__ && window.__INITIAL_STATE__.csrf) {
                        const val = window.__INITIAL_STATE__.csrf;
                        if (val && val.length > 5) {
                            return { found: true, value: val, source: '__INITIAL_STATE__', debug: debug };
                        }
                    }
                } catch(e) {
                    debug.initialState = 'error: ' + e.message;
                }
                
                // 5. Из window.nspk
                try {
                    debug.nspk = window.nspk ? 'exists' : 'undefined';
                    if (window.nspk && window.nspk.csrf) {
                        const val = window.nspk.csrf;
                        if (val && val.length > 5) {
                            return { found: true, value: val, source: 'nspk.csrf', debug: debug };
                        }
                    }
                } catch(e) {
                    debug.nspk = 'error: ' + e.message;
                }
                
                // 6. Ищем в HTML
                try {
                    const html = document.documentElement.innerHTML;
                    const match = html.match(/csrf["']?\\s*[:=]\\s*["']([^"']+)["']/i);
                    debug.htmlMatch = match ? match[1] : 'not found';
                    if (match && match[1] && match[1].length > 5) {
                        return { found: true, value: match[1], source: 'html-regex', debug: debug };
                    }
                } catch(e) {
                    debug.htmlMatch = 'error: ' + e.message;
                }
                
                // 7. Из формы
                try {
                    const form = document.querySelector('input[name="csrf"]');
                    debug.formCsrf = form ? form.value : 'not found';
                    if (form && form.value && form.value.length > 5) {
                        return { found: true, value: form.value, source: 'form-input', debug: debug };
                    }
                } catch(e) {
                    debug.formCsrf = 'error: ' + e.message;
                }
                
                return { found: false, debug: debug };
            }
        """)
        
        # Логируем результат поиска
        logger.debug(f"🔍 Результат поиска CSRF (попытка {csrf_attempt+1}):")
        logger.debug(f"  found: {result.get('found')}")
        if result.get('found'):
            logger.debug(f"  source: {result.get('source')}")
            logger.debug(f"  value: {result.get('value', '')[:30]}...")
            logger.debug(f"  debug: {json.dumps(result.get('debug', {}), indent=2)}")
        
        if result.get('found') and result.get('value'):
            csrf = result.get('value')
            logger.info(f"✅ CSRF получен из {result.get('source')}: {csrf[:20]}...")
            return csrf
        
        if csrf_attempt < 4:
            logger.warning(f"⏳ CSRF не найден, перезагружаю страницу...")
            await page.reload(wait_until='domcontentloaded')
            # Даем время на загрузку после перезагрузки
            logger.debug("⏳ Ждем 5 секунд после перезагрузки...")
            await asyncio.sleep(5)
            
            # Проверяем что страница загрузилась после перезагрузки
            for check_attempt in range(5):
                try:
                    has_content = await page.evaluate("""
                        () => {
                            return !!(
                                document.querySelector('.cards-list') || 
                                document.querySelector('.card-item') ||
                                document.querySelector('.personal-cards') ||
                                document.querySelector('main')
                            );
                        }
                    """)
                    if has_content:
                        logger.debug("✅ Страница загружена после перезагрузки")
                        break
                except:
                    pass
                await asyncio.sleep(1)
    
    logger.warning(f"⚠️ CSRF не найден после всех попыток на {page.url}")
    
    # Сохраняем скриншот для отладки
    try:
        timestamp = int(time.time())
        screenshot_path = f'screenshot_no_csrf_{timestamp}.png'
        await page.screenshot(path=screenshot_path)
        logger.debug(f"📸 Скриншот сохранен: {screenshot_path}")
    except:
        pass
    
    return None

async def get_cards(page, hash_query, access_token):
    """Получает список карт через API"""
    cards_url = f'https://vamprivet.ru/api/cards/{hash_query}'
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json',
        'User-Agent': random.choice(USER_AGENTS),
        'Referer': 'https://vamprivet.ru/personal/cards/',
        'X-Requested-With': 'XMLHttpRequest'
    }
    
    logger.debug(f"📡 Запрос карт: {cards_url}")
    
    try:
        result = await page.evaluate(f"""
            async () => {{
                try {{
                    const response = await fetch('{cards_url}', {{
                        headers: {json.dumps(headers)}
                    }});
                    const data = await response.json();
                    return {{ status: response.status, data: data }};
                }} catch(e) {{
                    return {{ status: 0, error: e.message }};
                }}
            }}
        """)
        
        logger.debug(f"📡 Ответ API: статус {result.get('status')}")
        
        if result.get('status') == 200:
            data = result.get('data', {})
            cards = data.get('data', {}).get('cards', {}).get('items', [])
            logger.debug(f"📋 Получено карт: {len(cards)}")
            return cards
        else:
            logger.error(f"❌ Ошибка получения карт: {result.get('status')}")
            logger.debug(f"Ответ: {result}")
            return None
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return None

async def delete_card(page, hash_query, access_token, card_id, card_number, csrf):
    """Удаляет одну карту с CSRF токеном"""
    delete_url = f'https://vamprivet.ru/api/cards/{hash_query}/card/{card_id}'
    
    delete_headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': random.choice(USER_AGENTS),
        'Referer': 'https://vamprivet.ru/personal/cards/',
        'Origin': 'https://vamprivet.ru',
        'X-Requested-With': 'XMLHttpRequest'
    }
    
    logger.debug(f"🗑 Удаление карты {card_number} (ID: {card_id})")
    logger.debug(f"🗑 URL: {delete_url}")
    
    # Пробуем с CSRF
    if csrf:
        body = json.dumps({'csrf': csrf})
        logger.debug(f"  С CSRF: {csrf[:20]}...")
        
        result = await page.evaluate(f"""
            async () => {{
                try {{
                    const response = await fetch('{delete_url}', {{
                        method: 'DELETE',
                        headers: {json.dumps(delete_headers)},
                        body: {body}
                    }});
                    
                    let data = null;
                    try {{
                        data = await response.json();
                    }} catch(e) {{
                        data = {{ error: 'Cannot parse response' }};
                    }}
                    
                    return {{ 
                        success: response.ok, 
                        status: response.status, 
                        data: data 
                    }};
                }} catch(e) {{
                    return {{ success: false, error: e.message }};
                }}
            }}
        """)
        
        logger.debug(f"  Результат с CSRF: статус {result.get('status')}, success: {result.get('success')}")
        
        if result.get('success'):
            logger.info(f"✅ Карта {card_number} удалена (с CSRF)")
            return True
        
        if result.get('status') == 404:
            logger.info(f"✅ Карта {card_number} уже удалена")
            return True
        
        if result.get('status') == 403:
            logger.warning(f"⚠️ 403 с CSRF, пробую без...")
    
    # Пробуем без CSRF
    logger.debug(f"🗑 Удаление {card_number} без CSRF")
    result = await page.evaluate(f"""
        async () => {{
            try {{
                const response = await fetch('{delete_url}', {{
                    method: 'DELETE',
                    headers: {json.dumps(delete_headers)}
                }});
                
                let data = null;
                try {{
                    data = await response.json();
                }} catch(e) {{
                    data = {{ error: 'Cannot parse response' }};
                }}
                
                return {{ 
                    success: response.ok, 
                    status: response.status, 
                    data: data 
                }};
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }}
    """)
    
    logger.debug(f"  Результат без CSRF: статус {result.get('status')}, success: {result.get('success')}")
    
    if result.get('success'):
        logger.info(f"✅ Карта {card_number} удалена (без CSRF)")
        return True
    
    if result.get('status') == 404:
        logger.info(f"✅ Карта {card_number} уже удалена")
        return True
    
    logger.error(f"❌ Ошибка удаления {card_number}: {result}")
    return False

async def process_account(account, account_index):
    """Обрабатывает один аккаунт"""
    phone = account['phone']
    password = account['password']
    
    logger.info(f"\n{'='*60}")
    logger.info(f"🔧 Аккаунт #{account_index} (тел: {phone})")
    logger.info(f"{'='*60}")
    
    for attempt in range(MAX_RETRIES):
        try:
            logger.debug(f"🚀 Запуск браузера для {phone} (попытка {attempt+1}/{MAX_RETRIES})")
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=ANTIDETECT_ARGS
                )
                
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent=random.choice(USER_AGENTS),
                    locale='ru-RU',
                    timezone_id='Europe/Moscow'
                )
                
                page = await context.new_page()
                await apply_antidetect(page)
                
                try:
                    # 1. Авторизация
                    if not await perform_login(page, phone, password):
                        if attempt < MAX_RETRIES - 1:
                            logger.info(f"🔄 Повтор {attempt + 2}/{MAX_RETRIES}...")
                            await asyncio.sleep(3)
                            continue
                        return False
                    
                    # 2. Получаем CSRF токен со страницы /personal/cards/
                    csrf = await get_csrf_token(page)
                    
                    # 3. Получаем токены
                    access_token, hash_query = await get_tokens(page)
                    if not access_token or not hash_query:
                        logger.error(f"❌ Нет токенов")
                        logger.debug(f"  access_token: {access_token}")
                        logger.debug(f"  hash_query: {hash_query}")
                        return False
                    
                    logger.info(f"✅ Токены получены")
                    logger.debug(f"  hash_query: {hash_query}")
                    
                    # 4. Получаем карты
                    cards = await get_cards(page, hash_query, access_token)
                    if cards is None:
                        if attempt < MAX_RETRIES - 1:
                            logger.info(f"🔄 Повтор {attempt + 2}/{MAX_RETRIES}...")
                            await asyncio.sleep(3)
                            continue
                        return False
                    
                    if not cards:
                        logger.info(f"📋 Нет карт")
                        return True
                    
                    logger.info(f"📋 Найдено карт: {len(cards)}")
                    for idx, card in enumerate(cards[:3]):
                        logger.debug(f"  Карта {idx+1}: {card.get('number', '')} (ID: {card.get('id')})")
                    
                    cards_to_delete = len(cards) - KEEP_CARDS
                    if cards_to_delete <= 0:
                        logger.info(f"📋 Карт {len(cards)}, оставляем {KEEP_CARDS}")
                        return True
                    
                    logger.info(f"🗑 Удаляем {cards_to_delete} карт (останется {KEEP_CARDS})")
                    
                    deleted = 0
                    for i, card in enumerate(cards):
                        if i >= len(cards) - KEEP_CARDS:
                            logger.info(f"⏭️ Пропускаем {card.get('number', '')} (оставляем)")
                            continue
                        
                        card_id = card.get('id')
                        card_number = card.get('number', '')
                        
                        if not card_id:
                            logger.warning(f"⚠️ Нет ID для карты {card_number}")
                            continue
                        
                        if await delete_card(page, hash_query, access_token, card_id, card_number, csrf):
                            deleted += 1
                            await asyncio.sleep(DELETE_DELAY)
                    
                    logger.info(f"✅ Аккаунт #{account_index}: удалено {deleted} карт")
                    return True
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка: {e}")
                    import traceback
                    traceback.print_exc()
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(3)
                        continue
                    return False
                finally:
                    await browser.close()
                    
        except Exception as e:
            logger.error(f"❌ Критическая ошибка: {e}")
            import traceback
            traceback.print_exc()
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(3)
                continue
            return False
    
    return False

async def delete_cards_from_accounts():
    logger.info("=" * 60)
    logger.info("🗑 УДАЛЕНИЕ КАРТ")
    logger.info("=" * 60)
    logger.info(f"📋 Оставляем: {KEEP_CARDS} карт")
    logger.info(f"⏳ Задержка: {DELETE_DELAY} сек")
    logger.info(f"📦 Батч: {BATCH_SIZE} аккаунтов")
    
    accounts = load_accounts()
    if not accounts:
        logger.error("❌ Нет аккаунтов")
        return
    
    # Удаляем дубликаты
    seen = set()
    unique_accounts = []
    for acc in accounts:
        if acc['phone'] not in seen:
            seen.add(acc['phone'])
            unique_accounts.append(acc)
    
    logger.info(f"📋 Аккаунтов: {len(unique_accounts)}")
    
    success_count = 0
    fail_count = 0
    
    for batch_start in range(0, len(unique_accounts), BATCH_SIZE):
        batch = unique_accounts[batch_start:batch_start + BATCH_SIZE]
        logger.info(f"\n📦 Батч {batch_start//BATCH_SIZE + 1}: {len(batch)} аккаунтов")
        
        tasks = []
        for j, account in enumerate(batch):
            account_index = batch_start + j + 1
            tasks.append(asyncio.create_task(process_account(account, account_index)))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            account_index = batch_start + i + 1
            if isinstance(result, Exception):
                logger.error(f"❌ Аккаунт #{account_index}: {result}")
                fail_count += 1
            elif result:
                success_count += 1
                logger.info(f"✅ Аккаунт #{account_index}: УСПЕХ")
            else:
                fail_count += 1
                logger.error(f"❌ Аккаунт #{account_index}: НЕУДАЧА")
        
        if batch_start + BATCH_SIZE < len(unique_accounts):
            wait_time = random.uniform(10, 20)
            logger.info(f"⏳ Пауза {wait_time:.1f} сек...")
            await asyncio.sleep(wait_time)
    
    logger.info(f"\n📊 ИТОГИ: ✅ {success_count} | ❌ {fail_count}")

async def scheduled_delete():
    logger.info("🕐 Планировщик запущен (каждый день в 3:00 MSK)")
    
    while True:
        now = time.localtime()
        target_hour = 3
        target_minute = 0
        
        seconds_until = (target_hour - now.tm_hour) * 3600 + (target_minute - now.tm_min) * 60 - now.tm_sec
        if seconds_until <= 0:
            seconds_until += 86400
        
        logger.info(f"⏳ До запуска: {seconds_until // 3600}ч {(seconds_until % 3600) // 60}м")
        await asyncio.sleep(seconds_until)
        
        logger.info("\n🔄 ЗАПУСК ПЛАНОВОГО УДАЛЕНИЯ")
        await delete_cards_from_accounts()

def main():
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == '--schedule':
        asyncio.run(scheduled_delete())
    else:
        asyncio.run(delete_cards_from_accounts())

if __name__ == '__main__':
    main()