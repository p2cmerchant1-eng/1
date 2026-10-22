import asyncio
import json
from playwright.async_api import async_playwright

async def update_cookies_from_profiles():
    """Обновляет cookies.json из сохранённых профилей"""
    
    profiles = [
        {'name': 'monitor', 'label': 'Монитор'},
        {'name': 'worker1', 'label': 'Воркер-1'},
        {'name': 'worker2', 'label': 'Воркер-2'},
        
        {'name': 'worker4', 'label': 'Воркер-4'},
        {'name': 'worker5', 'label': 'Воркер-5'},
    ]
    
    data = {
        'monitor': {},
        'workers': []
    }
    
    async with async_playwright() as p:
        for profile in profiles:
            try:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=f'./profiles/{profile["name"]}',
                    headless=False
                )
                
                cookies = await context.cookies()
                await context.close()
                
                # Фильтруем только нужные куки
                important_cookies = [
                    c for c in cookies 
                    if c['name'] in ['PHPSESSID', 'accessToken', 'hashQuery', 'refreshToken']
                ]
                
                if profile['name'] == 'monitor':
                    data['monitor'] = {
                        'label': profile['label'],
                        'cookies': important_cookies
                    }
                else:
                    data['workers'].append({
                        'label': profile['label'],
                        'cookies': important_cookies
                    })
                
                print(f"✅ {profile['name']}: {len(important_cookies)} кук")
                
            except Exception as e:
                print(f"❌ {profile['name']}: {e}")
    
    # Сохраняем cookies.json
    with open('cookies.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print("\n✅ cookies.json обновлён из профилей!")
    print(f"   Монитор: 1")
    print(f"   Воркеры: {len(data['workers'])}")

if __name__ == '__main__':
    try:
        asyncio.run(update_cookies_from_profiles())
    except KeyboardInterrupt:
        print("⚠️ Остановлено")
    except Exception as e:
        print(f"❌ Ошибка: {e}")