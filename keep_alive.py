import asyncio
import json
import logging
import os
import random
from playwright.async_api import async_playwright

# ─── ЛОГИРОВАНИЕ ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('keep_alive.log', encoding='utf-8'),
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

ADD_CARD_URL = 'https://vamprivet.ru/personal/add-card/'
REFRESH_INTERVAL = 15 * 60  # 15 минут в секундах


def get_worker_profiles():
    """Возвращает список профилей воркеров"""
    profiles = []
    profiles_dir = 'profiles'
    
    if not os.path.exists(profiles_dir):
        return profiles
    
    for item in os.listdir(profiles_dir):
        if item.startswith('worker'):
            profile_path = os.path.join(profiles_dir, item)
            if os.path.isdir(profile_path):
                profiles.append(profile_path)
    
    return sorted(profiles)


async def apply_antidetect(page):
    """Применяет антидетект скрипты"""
    for script in ANTIDETECT_SCRIPTS:
        try:
            await page.evaluate(script)
        except:
            pass


async def check_auth(page):
    """Проверяет авторизацию"""
    current_url = page.url.lower()
    
    if '/auth' in current_url or 'login' in current_url:
        return False
    
    # Проверяем наличие поля ввода
    selectors = ['#addCard', 'input[name="pan"]', 'input[maxlength="19"]']
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.is_visible():
                return True
        except:
            continue
    
    return True


async def keep_alive_worker(profile_dir, worker_name):
    """Поддерживает сессию одного воркера"""
    logger.info(f"🔄 [{worker_name}] Запуск keep-alive...")
    
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
            # Первичная загрузка
            await page.goto(ADD_CARD_URL, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(3)
            
            is_auth = await check_auth(page)
            if is_auth:
                logger.info(f"✅ [{worker_name}] Авторизован, сессия активна")
            else:
                logger.warning(f"⚠️ [{worker_name}] Не авторизован! Нужна повторная авторизация")
            
            # Основной цикл
            while True:
                await asyncio.sleep(REFRESH_INTERVAL)
                
                logger.info(f"🔄 [{worker_name}] Обновление страницы...")
                
                # Перезагружаем страницу
                await page.reload(wait_until='domcontentloaded', timeout=30000)
                await asyncio.sleep(3)
                
                # Проверяем авторизацию
                is_auth = await check_auth(page)
                
                if is_auth:
                    logger.info(f"✅ [{worker_name}] Сессия активна после обновления")
                    
                    # Имитация активности (клики)
                    try:
                        # Кликаем по полю ввода
                        inp = page.locator('#addCard')
                        if await inp.count() > 0 and await inp.is_visible():
                            await inp.click()
                            await asyncio.sleep(1)
                            # Кликаем в другое место
                            await page.mouse.click(100, 100)
                            await asyncio.sleep(1)
                    except:
                        pass
                else:
                    logger.warning(f"⚠️ [{worker_name}] Сессия протухла! Требуется повторная авторизация")
                    
                    # Сохраняем куки для диагностики
                    cookies = await context.cookies()
                    cookie_file = os.path.join(profile_dir, 'cookies_expired.json')
                    with open(cookie_file, 'w', encoding='utf-8') as f:
                        json.dump(cookies, f, indent=2, ensure_ascii=False)
                    
                    logger.info(f"💾 [{worker_name}] Куки сохранены в {cookie_file}")
                
        except Exception as e:
            logger.error(f"❌ [{worker_name}] Ошибка: {e}")
        finally:
            await context.close()


async def main():
    """Основная функция"""
    logger.info("=" * 60)
    logger.info("🔄 KEEP-ALIVE СИСТЕМА")
    logger.info("=" * 60)
    
    profiles = get_worker_profiles()
    
    if not profiles:
        logger.error("❌ Нет профилей воркеров. Запустите auth_workers.py")
        return
    
    logger.info(f"📋 Найдено профилей: {len(profiles)}")
    logger.info(f"⏱️ Интервал обновления: {REFRESH_INTERVAL // 60} минут")
    
    # Запускаем keep-alive для каждого профиля
    tasks = []
    for i, profile_dir in enumerate(profiles, 1):
        worker_name = os.path.basename(profile_dir)
        task = asyncio.create_task(keep_alive_worker(profile_dir, worker_name))
        tasks.append(task)
    
    logger.info(f"🚀 Запущено {len(tasks)} keep-alive задач")
    
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("⚠️ Остановлено пользователем")
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⚠️ Остановлено пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")