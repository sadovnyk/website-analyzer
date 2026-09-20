MAX_BODY_BYTES = 5 * 1024 * 1024


async def read_limited(response, limit=MAX_BODY_BYTES):
    chunks, size = [], 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        chunks.append(chunk)
        size += len(chunk)
        if size >= limit:
            break
    return b"".join(chunks)[:limit]