import asyncio
import json
import logging
import os
import random
import re
import shutil
from playwright.async_api import async_playwright

# ─── ЛОГИРОВАНИЕ ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('auth.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ─── АНТИДЕТЕКТ ФЛАГИ ────────────────────────────────────────
ANTIDETECT_ARGS = [
    '--disable-blink-features=AutomationControlled',
    '--disable-automation',
    '--disable-infobars',
    '--disable-dev-shm-usage',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--no-sandbox',
    '--disable-notifications',
    '--disable-popup-blocking',
    '--disable-extensions',
    '--disable-background-networking',
    '--disable-background-timer-throttling',
    '--disable-sync',
    '--disable-logging',
    '--mute-audio',
    '--window-size=1920,1080',
    '--start-maximized',
    '--lang=ru-RU',
    '--disable-reading-from-canvas',
    '--disable-webgl',
    '--disable-webrtc',
]

ANTIDETECT_SCRIPTS = [
    """
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
    });
    """,
    """
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5]
    });
    """,
    """
    Object.defineProperty(navigator, 'languages', {
        get: () => ['ru-RU', 'ru', 'en-US', 'en']
    });
    """,
    """
    window.chrome = {
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
        app: {}
    };
    """,
]

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
]

AUTH_URL = 'https://vamprivet.ru/auth/'
ACCOUNTS_FILE = 'accounts.txt'
MAX_WAIT_REDIRECT = 60
BATCH_SIZE = 5
MAX_RETRIES = 3  # количество попыток при ошибках


def load_accounts():
    accounts = []
    if not os.path.exists(ACCOUNTS_FILE):
        logger.error(f"❌ Файл {ACCOUNTS_FILE} не найден")
        return accounts
    
    with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
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


async def wait_for_redirect(page, timeout=MAX_WAIT_REDIRECT):
    for _ in range(timeout):
        current_url = page.url.lower()
        if '/personal' in current_url or '/add-card' in current_url:
            return True
        if '/auth' in current_url:
            error = page.locator('.auth__master-error:not(.is-hidden)')
            if await error.count() > 0:
                error_text = await error.inner_text()
                if error_text.strip():
                    return False
        await asyncio.sleep(1)
    return False


async def auth_worker(phone, password, worker_id):
    profile_dir = f'./profiles/worker{worker_id}'
    
    # Удаляем старый профиль перед авторизацией
    if os.path.exists(profile_dir):
        logger.info(f"🗑 Удаляю старый профиль worker{worker_id}...")
        shutil.rmtree(profile_dir, ignore_errors=True)
    
    os.makedirs(profile_dir, exist_ok=True)
    os.makedirs('screenshots', exist_ok=True)
    
    logger.info(f"🔐 Авторизация воркера {worker_id} (тел: {phone})...")

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(f"🔄 Попытка {attempt}/{MAX_RETRIES}")
        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=True,
                    viewport={'width': 1920, 'height': 1080},
                    user_agent=random.choice(USER_AGENTS),
                    locale='ru-RU',
                    timezone_id='Europe/Moscow',
                    args=ANTIDETECT_ARGS
                )
                
                page = await context.new_page()
                await apply_antidetect(page)
                
                try:
                    await page.goto(AUTH_URL, wait_until='domcontentloaded', timeout=30000)
                    await asyncio.sleep(5)
                    
                    if '/personal' in page.url or '/add-card' in page.url:
                        logger.info(f"✅ Воркер {worker_id} уже авторизован")
                        return True
                    
                    login_input = page.locator('#login')
                    if await login_input.count() == 0:
                        logger.warning(f"⚠️ Поле телефона не найдено (попытка {attempt})")
                        await asyncio.sleep(3)
                        # Пробуем перезагрузить
                        await page.reload(wait_until='domcontentloaded', timeout=30000)
                        await asyncio.sleep(5)
                        login_input = page.locator('#login')
                        if await login_input.count() == 0:
                            logger.error(f"❌ Поле телефона так и не появилось")
                            continue  # следующая попытка
                    
                    await login_input.click()
                    await asyncio.sleep(1)
                    await login_input.fill(normalize_phone(phone))
                    await asyncio.sleep(1)
                    
                    pass_input = page.locator('#pass')
                    if await pass_input.count() == 0:
                        logger.error(f"❌ Поле пароля не найдено")
                        continue
                    
                    await pass_input.click()
                    await asyncio.sleep(1)
                    await pass_input.fill(password)
                    await asyncio.sleep(1)
                    
                    await page.evaluate("document.querySelector('#rememberMe').click()")
                    
                    submit_btn = page.locator('button[type="submit"]:has-text("Войти")')
                    if await submit_btn.count() == 0:
                        submit_btn = page.get_by_role("button", name="Войти")
                    
                    if await submit_btn.count() == 0:
                        logger.error(f"❌ Кнопка не найдена")
                        continue
                    
                    await submit_btn.click()
                    logger.info(f"🔄 Жду редирект...")
                    
                    success = await wait_for_redirect(page)
                    
                    if success:
                        cookies = await context.cookies()
                        important_cookies = [
                            c for c in cookies 
                            if c['name'] in ['PHPSESSID', 'accessToken', 'hashQuery', 'refreshToken']
                        ]
                        cookie_file = f'{profile_dir}/cookies.json'
                        with open(cookie_file, 'w', encoding='utf-8') as f:
                            json.dump(important_cookies, f, indent=2, ensure_ascii=False)
                        logger.info(f"✅ Воркер {worker_id}: куки сохранены")
                        return True
                    else:
                        logger.warning(f"⚠️ Редирект не произошёл (попытка {attempt})")
                        continue
                        
                except Exception as e:
                    logger.error(f"❌ Ошибка: {e}")
                    continue
                finally:
                    await context.close()
        except Exception as e:
            logger.error(f"❌ Критическая ошибка: {e}")
            continue
    
    # Если все попытки не удались — удаляем профиль
    if not os.path.exists(os.path.join(profile_dir, 'cookies.json')):
        logger.info(f"🗑 Удаляю неудачный профиль worker{worker_id}...")
        shutil.rmtree(profile_dir, ignore_errors=True)
    return False


async def main():
    logger.info("=" * 60)
    logger.info("🔐 АВТОРИЗАЦИЯ ВОРКЕРОВ (по 5 параллельно, с ретраями)")
    logger.info("=" * 60)
    
    accounts = load_accounts()
    if not accounts:
        logger.error("❌ Нет аккаунтов в accounts.txt")
        return
    
    logger.info(f"📋 Найдено аккаунтов: {len(accounts)}")
    
    os.makedirs('profiles', exist_ok=True)
    
    success_count = 0
    fail_count = 0
    
    for batch_start in range(0, len(accounts), BATCH_SIZE):
        batch = accounts[batch_start:batch_start + BATCH_SIZE]
        logger.info(f"\n{'='*60}")
        logger.info(f"Батч {batch_start//BATCH_SIZE + 1}: {len(batch)} аккаунтов")
        logger.info(f"{'='*60}")
        
        tasks = []
        for j, account in enumerate(batch):
            worker_id = batch_start + j + 1
            task = asyncio.create_task(auth_worker(account['phone'], account['password'], worker_id))
            tasks.append(task)
        
        results = await asyncio.gather(*tasks)
        
        for result in results:
            if result:
                success_count += 1
            else:
                fail_count += 1
        
        if batch_start + BATCH_SIZE < len(accounts):
            await asyncio.sleep(random.uniform(5, 10))
    
    logger.info("\n" + "=" * 60)
    logger.info("📊 ИТОГИ АВТОРИЗАЦИИ")
    logger.info("=" * 60)
    logger.info(f"✅ Успешно: {success_count}")
    logger.info(f"❌ Не удалось: {fail_count}")
    logger.info("=" * 60)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⚠️ Остановлено пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")