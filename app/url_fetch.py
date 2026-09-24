import ipaddress
import socket
import urllib.request
from urllib.parse import urlparse


class PublicFetchError(Exception):
    """Raised for anything that must never turn into a raw 500 (validation
    failures, DNS failures, disallowed hosts, network errors)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # refuse silently rather than follow: the URL we validated and the
        # URL we'd actually fetch must always be the same one
        return None


def _is_public_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    # unwrap IPv4-mapped IPv6 (::ffff:127.0.0.1) — the private/loopback
    # checks below don't recognize the mapped form on its own
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def fetch_public_url(url: str, max_bytes: int, timeout: float = 8.0) -> bytes:
    """Fetch `url` and return up to `max_bytes` of its body.

    Rejects anything that isn't a plain http(s) URL resolving only to
    public IP addresses — meant to stop this server being used to probe or
    reach internal/private network resources (SSRF) from a URL supplied by
    a client. Does not follow redirects, for the same reason.

    This is a pragmatic defense, not a hardened proxy: it re-resolves DNS
    once for the validation check and once (via urlopen) for the actual
    connection, so a DNS answer that changes between those two lookups
    (DNS rebinding) could still slip through. Acceptable for this app's
    threat model (small deployment, authenticated board access already
    required to reach this at all) — a hardened setup would pin the
    validated IP for the actual connection instead.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise PublicFetchError("URL non supportée (http/https uniquement)")
    if not parsed.hostname:
        raise PublicFetchError("URL invalide")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addrs = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as err:
        raise PublicFetchError("Nom d'hôte introuvable") from err
    if not addrs or not all(_is_public_ip(addr[4][0]) for addr in addrs):
        raise PublicFetchError("Cette adresse n'est pas autorisée")

    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": "ProjectMgr-image-import/1.0"})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.read(max_bytes + 1)
    except (OSError, ValueError) as err:
        # OSError covers urllib.error.URLError/HTTPError (both subclass it),
        # socket.timeout, and connection failures
        raise PublicFetchError("Impossible de récupérer cette image") from err
