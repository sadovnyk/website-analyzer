import asyncio
from core.scan_runner import scan_one_site
from core.database import get_sites
from core.logger import get_logger

logger = get_logger(__name__)


async def run_schedule():
    logger.info("Launching the scheduler...")
    active_sites = await asyncio.to_thread(get_sites, True)

    if active_sites["success"] is True:
        if not active_sites["result"]:
            logger.warning("There are no active sites to scan.")
        for site in active_sites["result"]:
            logger.info(f"Website scan: {site['url']}")
            try:
                await scan_one_site(site["url"], site["id"])
            except Exception as e:
                logger.error(f"Failed to scan {site['url']}. Error: {e}")

    else:
        logger.error(f"Database error: {active_sites['error']}")


if __name__ == "__main__":
    asyncio.run(run_schedule())