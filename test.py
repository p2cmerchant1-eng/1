import asyncio
import json
import re
from playwright.async_api import async_playwright

# ─── АНТИДЕТЕКТ ───────────────────────────────────────────────
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

async def apply_antidetect(page):
    for script in ANTIDETECT_SCRIPTS:
        try:
            await page.evaluate(script)
        except:
            pass

async def wait_for_input(page, timeout=30):
    """Ожидание поля ввода"""
    selectors = ['#addCard', 'input[name="pan"]', 'input[data-tid="addCard"]', 'input[maxlength="19"]']
    for _ in range(timeout * 2):
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.is_visible():
                    return loc
            except:
                continue
        await asyncio.sleep(0.5)
    return None

async def test_add_card():
    phone = '79779489934'
    password = 'Sobaka123'
    test_card = '2200701726865485'
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=ANTIDETECT_ARGS
        )
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36',
            locale='ru-RU',
            timezone_id='Europe/Moscow'
        )
        page = await context.new_page()
        await apply_antidetect(page)
        
        # Переменные для перехвата
        captured_uuid = None
        initial_sn_map = {}
        add_card_response = None
        cards_after_response = None
        cards_after_uuid = None
        
        # Флаг, что мы уже перехватили начальные карты
        initial_cards_captured = False
        
        async def intercept_response(response):
            nonlocal captured_uuid, initial_sn_map, add_card_response
            nonlocal cards_after_response, cards_after_uuid, initial_cards_captured
            
            url = response.url
            
            # ✅ ТОЛЬКО ПЕРЕХВАТЫВАЕМ ОТВЕТЫ, НИЧЕГО НЕ ДЕЛАЕМ САМИ!
            if 'api' in url and response.status == 200:
                try:
                    body = await response.body()
                    text = body.decode('utf-8', errors='ignore')
                    data = json.loads(text) if text else {}
                    
                    # 1. Перехватываем /api/cards/ ДО добавления (для SN)
                    if '/api/cards/' in url and 'get-tokens=false' in url and not initial_cards_captured:
                        match = re.search(r'/api/cards/([a-f0-9]+)\?', url)
                        if match:
                            captured_uuid = match.group(1)
                            print(f"\n✅ UUID из URL: {captured_uuid}")
                        
                        cards_data = data.get('data', {}).get('cards', {})
                        if isinstance(cards_data, dict) and 'items' in cards_data:
                            for card in cards_data.get('items', []):
                                number = card.get('number', '')
                                sn = card.get('sn')
                                if len(number) >= 4:
                                    suffix = number[-4:]
                                    initial_sn_map[suffix] = sn
                                    print(f"  [ДО] Карта: {number} | SN: {sn} | Суффикс: {suffix}")
                        initial_cards_captured = True
                    
                    # 2. Перехватываем /api/v3/add_card/ (ответ на добавление)
                    if '/api/v3/add_card/' in url:
                        add_card_response = data
                        print(f"\n🔥 [ОТВЕТ /api/v3/add_card/]")
                        print(json.dumps(data, indent=2, ensure_ascii=False)[:500])
                    
                    # 3. ✅ ПЕРЕХВАТЫВАЕМ /api/cards/ ПОСЛЕ ДОБАВЛЕНИЯ (автоматический запрос браузера!)
                    if '/api/cards/' in url and 'get-tokens=false' not in url and initial_cards_captured:
                        match = re.search(r'/api/cards/([a-f0-9]+)/?', url)
                        if match:
                            cards_after_uuid = match.group(1)
                            print(f"\n📋 [ОТВЕТ /api/cards/ ПОСЛЕ ДОБАВЛЕНИЯ - АВТОМАТИЧЕСКИЙ ЗАПРОС БРАУЗЕРА]")
                            print(f"   UUID: {cards_after_uuid}")
                        
                        cards_data = data.get('data', {}).get('cards', {})
                        if isinstance(cards_data, dict) and 'items' in cards_data:
                            after_sn_map = {}
                            for card in cards_data.get('items', []):
                                number = card.get('number', '')
                                sn = card.get('sn')
                                if len(number) >= 4:
                                    suffix = number[-4:]
                                    after_sn_map[suffix] = sn
                                    print(f"  [ПОСЛЕ] Карта: {number} | SN: {sn} | Суффикс: {suffix}")
                            
                            # ✅ СРАВНИВАЕМ SN!
                            print(f"\n🔍 СРАВНЕНИЕ SN для суффикса 5485:")
                            print(f"  До:   {initial_sn_map.get('5485', 'НЕТ')}")
                            print(f"  После: {after_sn_map.get('5485', 'НЕТ')}")
                            
                            if initial_sn_map.get('5485') == after_sn_map.get('5485'):
                                print(f"\n🎉🎉🎉 СН НЕ ИЗМЕНИЛСЯ - ЭТО ИСКОМАЯ КАРТА! УСПЕХ!")
                            elif after_sn_map.get('5485') and initial_sn_map.get('5485') != after_sn_map.get('5485'):
                                print(f"\n✅ СН ИЗМЕНИЛСЯ - ДОБАВЛЕНА НОВАЯ КАРТА!")
                            else:
                                print(f"\n⚠️ Карта с суффиксом 5485 не найдена!")
                        
                        cards_after_response = data
                    
                except Exception as e:
                    pass
        
        page.on('response', intercept_response)
        
        print("=" * 60)
        print("🚀 ТЕСТ: Перехват ответов при добавлении карты")
        print("=" * 60)
        
        try:
            # 1. Авторизация
            print("\n📋 1. Авторизация...")
            await page.goto('https://vamprivet.ru/auth/', wait_until='domcontentloaded', timeout=60000)
            await asyncio.sleep(2)
            
            await page.wait_for_selector('#login', timeout=20000)
            await page.fill('#login', phone)
            await page.fill('#pass', password)
            await page.evaluate("document.querySelector('#rememberMe')?.click()")
            
            await page.click('button[type="submit"]:has-text("Войти")')
            
            for i in range(30):
                await asyncio.sleep(1)
                if '/personal' in page.url or '/add-card' in page.url:
                    print(f"✅ Редирект на: {page.url}")
                    break
            
            await asyncio.sleep(2)
            
            # 2. Переходим на add-card
            print("\n📋 2. Переход на add-card...")
            await page.goto('https://vamprivet.ru/personal/add-card/', wait_until='domcontentloaded', timeout=60000)
            await asyncio.sleep(3)
            
            print(f"✅ Текущий URL: {page.url}")
            
            # 3. Добавляем карту
            print(f"\n📋 3. Добавление карты: {test_card}")
            
            # Находим поле
            inp = await wait_for_input(page, timeout=30)
            if not inp:
                print("❌ Поле не найдено!")
                await browser.close()
                return
            
            print("✅ Поле найдено")
            
            # Заполняем
            await page.fill('#addCard', '')
            await page.fill('#addCard', test_card)
            print(f"✅ Карта {test_card} заполнена")
            await asyncio.sleep(1)
            
            # ✅ НАЖИМАЕМ КНОПКУ - БРАУЗЕР САМ ОТПРАВИТ ВСЕ ЗАПРОСЫ!
            btn = page.locator('button:has-text("Продолжить")')
            if await btn.count() > 0:
                await btn.click()
                print("✅ Кнопка нажата, ждем автоматических запросов...")
            
            # ✅ ПРОСТО ЖДЕМ, КОГДА БРАУЗЕР СДЕЛАЕТ ВСЕ ЗАПРОСЫ САМ
            await asyncio.sleep(8)
            
            print("\n" + "=" * 60)
            print("📋 ИТОГ:")
            print("=" * 60)
            print(f"UUID: {captured_uuid}")
            print(f"Начальные SN: {initial_sn_map}")
            
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()
        
        print("\n" + "=" * 60)
        print("✅ ТЕСТ ЗАВЕРШЕН")
        print("=" * 60)
        
        await asyncio.sleep(2)
        await browser.close()

if __name__ == '__main__':
    asyncio.run(test_add_card())