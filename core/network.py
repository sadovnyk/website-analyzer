import aiohttp
import asyncio
import time
from bs4 import BeautifulSoup
from core.config import HEADERS
from core.security import is_safe_host
from urllib.parse import urljoin
from core.http_utils import read_limited

async def check_status(url):
    access = {
        "title": None,
        "description": None,
        "duration_ms": None,
        "status_code": None,
        "error": None
    }
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
            start = time.time()

            current_url = url
            for _ in range(5):
                if not await asyncio.to_thread(is_safe_host, current_url):
                    access["error"] = "unsafe_url"
                    return access

                async with session.get(current_url, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        current_url = urljoin(current_url, response.headers.get('Location', ''))
                        continue

                    end = time.time()
                    access["status_code"] = response.status
                    access["duration_ms"] = round((end - start) * 1000)

                    content_type = response.headers.get('Content-Type', '')
                    if 'text/html' not in content_type:

                        access["title"] = "Not HTML"
                        return access

                    content_bytes = await read_limited(response)
                    html_content = content_bytes.decode('utf-8', errors='ignore')

                    soup = BeautifulSoup(html_content, 'html.parser')
                    title = soup.title.text.strip() if soup.title else None
                    meta_data = soup.find('meta', attrs={'name': 'description'})

                    access["title"] = title
                    access["description"] = meta_data.get('content') if meta_data else None

                    return access

            access["error"] = "too_many_redirects"
            return access

    except (aiohttp.InvalidURL, ValueError):
        access["error"] = "invalid_url"
        return access
    except aiohttp.ClientConnectorError:
        access["error"] = "connection_failed"
        return access
    except asyncio.TimeoutError:
        access["error"] = "timeout"
        return access
    except aiohttp.ClientError:
        access["error"] = "client_error"
        return access