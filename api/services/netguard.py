"""Guards for server-side fetches of user- or search-supplied URLs."""
import asyncio
import ipaddress
import socket

from errors import ApiError


async def check_public_host(host: str) -> None:
    """Refuse hosts that resolve to this machine or a private network (server-side request forgery)."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        raise ApiError(502, f'Could not resolve "{host}"', "fetch_failed") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise ApiError(422, f'Links to "{host}" are not allowed (private or local address)', "blocked_address")
