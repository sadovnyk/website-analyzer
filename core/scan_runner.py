import asyncio
from core.database import save_scan, save_links
from core.logger import get_logger
from core.security import check_host
from main import analyze

logger = get_logger(__name__)

def dns_failed_scan(url):
    return {
        "status": {"title": None, "description": None, "duration_ms": None,
                   "status_code": None, "error": "dns_failed"},
        "ip": None,
        "geo": {"country": None, "city": None, "org": None, "error": "invalid_ip"},
        "cert": {"ssl_days_left": None, "valid": None, "error": "dns_failed"},
        "links": {"url": url, "broken_links": [], "error": "dns_failed", "total_links": None},
        "number_of_links": None,
    }

async def scan_one_site(url, site_id):
    try:
        host_state = await asyncio.to_thread(check_host, url)
        if host_state in ("private", "invalid"):
            logger.warning(f"Blocked scan of unsafe/private host: {url}")
            return

        if host_state == "unresolved":
            scan_data = dns_failed_scan(url)
        else:
            scan_data = await analyze(url)

        save_result = await asyncio.to_thread(save_scan, url, scan_data, site_id)

        if save_result["success"] is True:
            links_result = await asyncio.to_thread(save_links, save_result["scan_id"], scan_data["links"])
            if links_result["success"] is not True:
                logger.error(f"Error (Mongo): {links_result['error']}")
        else:
            logger.error(f"Error (MySQL): {save_result['error']}")
    except Exception as e:
        logger.error(f"FATAL ERROR scanning {url}: {str(e)}")


