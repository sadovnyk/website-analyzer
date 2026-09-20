import aiohttp
import asyncio
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from core.config import HEADERS
from core.security import is_safe_host
from core.http_utils import read_limited

MAX_LINKS_TO_CHECK = 200
MAX_CONCURRENT_LINK_CHECKS = 20


async def extract_links(base_url):
    access = {
        "links": None,
        "error": None,
        "status": None
    }
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:

            current_url = base_url
            for _ in range(5):
                if not await asyncio.to_thread(is_safe_host, current_url):
                    access["error"] = "unsafe_url"
                    return access

                async with session.get(current_url, allow_redirects=False) as response:

                    if response.status in (301, 302, 303, 307, 308):
                        current_url = urljoin(current_url, response.headers.get('Location', ''))
                        continue

                    if response.status != 200:
                        access["status"] = response.status
                        return access

                    content_type = response.headers.get('Content-Type', '')
                    if 'text/html' not in content_type:
                        access["status"] = 200
                        access["links"] = set()
                        return access

                    content_bytes = await read_limited(response)
                    html = content_bytes.decode('utf-8', errors='ignore')

                    soup = BeautifulSoup(html, 'html.parser')
                    un_links = set()
                    for tag in soup.find_all('a', href=True):
                        href = str(tag['href']).strip()
                        un_links.add(href)

                    access["links"] = un_links
                    access["status"] = 200
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


async def check_link(session, url, semaphore):
    access = {
        "url": url,
        "status": None,
        "error": None
    }
    async with semaphore:
        try:
            current_url = url
            for _ in range(3):
                if not await asyncio.to_thread(is_safe_host, current_url):
                    access["error"] = "unsafe_url"
                    return access
                async with session.head(current_url, timeout=aiohttp.ClientTimeout(total=3), allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        current_url = urljoin(current_url, response.headers.get('Location', ''))
                        continue

                    if response.status in (403, 405, 501):  # server may not support HEAD
                        async with session.get(current_url, timeout=aiohttp.ClientTimeout(total=5), allow_redirects=False) as get_response:
                            access["status"] = get_response.status
                            return access

                    access["status"] = response.status
                    return access

            access["error"] = "too_many_redirects"
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


async def check_links(urls):
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LINK_CHECKS)
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        links = [check_link(session, link, semaphore) for link in urls]
        results = await asyncio.gather(*links, return_exceptions=True)
        cleaned_results = []
        for url, result in zip(urls, results):
            if isinstance(result, Exception):
                result = {"url": url, "status": None, "error": str(result)}
            cleaned_results.append(result)

        return cleaned_results


async def broken_links(url):
    access = {
        "url": url,
        "broken_links": [],
        "error": None,
        "total_links": None
    }
    result = await extract_links(url)

    if result["error"] is not None:
        access["error"] = result["error"]
        return access

    if result["status"] != 200:
        access["error"] = f"Unable to check links — the page returned status code {result['status']}"
        return access

    absolute_links = set()
    for link in result["links"]:
        try:
            absolute = urljoin(url, link)
            if urlparse(absolute).scheme in ("http", "https"):
                absolute_links.add(absolute)
        except ValueError:
            continue
    absolute_links = sorted(absolute_links)[:MAX_LINKS_TO_CHECK]

    access["total_links"] = len(absolute_links)
    checked_links = await check_links(absolute_links)
    for link in checked_links:
        if link["error"] is not None or link["status"] is None or (link["status"] >= 400 and link["status"] != 429):
            access["broken_links"].append(link)
    return access
