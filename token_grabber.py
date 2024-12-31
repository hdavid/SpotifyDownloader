# copied from https://github.com/afkarxyz/SpotifyDown-GUI

import asyncio
import zendriver as zd

async def wait_for_turnstile_token(page):
    max_attempts = 20
    attempts = 0
    
    while attempts < max_attempts:
        element = await page.query_selector('input[name="cf-turnstile-response"]')
        
        if element:
            attrs = element.attrs
            if attrs and 'value' in attrs:
                return attrs['value']
        
        await asyncio.sleep(0.5)
        attempts += 1
    
    raise TimeoutError("Turnstile element not found within timeout period")

async def main():
    browser = await zd.start()
    try:
        page = await browser.get("https://spotifydown.com/")
        token = await wait_for_turnstile_token(page)
        return token
    finally:
        await browser.stop()

if __name__ == "__main__":
    asyncio.run(main())


