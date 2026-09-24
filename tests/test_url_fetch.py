import pytest

from app.url_fetch import PublicFetchError, _is_public_ip, fetch_public_url


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("8.8.8.8", True),
        ("1.1.1.1", True),
        ("2001:4860:4860::8888", True),  # public IPv6 (google dns)
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("192.168.1.1", False),
        ("172.16.0.1", False),
        ("169.254.169.254", False),  # cloud metadata endpoint
        ("0.0.0.0", False),
        ("::1", False),
        ("::ffff:127.0.0.1", False),  # IPv4-mapped loopback bypass attempt
        ("fe80::1", False),  # link-local IPv6
    ],
)
def test_is_public_ip(ip, expected):
    assert _is_public_ip(ip) is expected


def test_rejects_non_http_scheme():
    with pytest.raises(PublicFetchError):
        fetch_public_url("ftp://example.com/image.png", 1000)


def test_rejects_file_scheme():
    with pytest.raises(PublicFetchError):
        fetch_public_url("file:///etc/passwd", 1000)


def test_rejects_url_without_hostname():
    with pytest.raises(PublicFetchError):
        fetch_public_url("http:///no-host", 1000)


def test_rejects_localhost():
    with pytest.raises(PublicFetchError):
        fetch_public_url("http://localhost/", 1000)


def test_rejects_loopback_ip_literal():
    with pytest.raises(PublicFetchError):
        fetch_public_url("http://127.0.0.1/", 1000)


def test_rejects_ipv6_loopback_literal():
    with pytest.raises(PublicFetchError):
        fetch_public_url("http://[::1]/", 1000)


def test_rejects_private_ip_literal():
    with pytest.raises(PublicFetchError):
        fetch_public_url("http://10.0.0.1/", 1000)


def test_rejects_link_local_metadata_ip():
    with pytest.raises(PublicFetchError):
        fetch_public_url("http://169.254.169.254/latest/meta-data/", 1000)


def test_rejects_unresolvable_hostname():
    with pytest.raises(PublicFetchError):
        fetch_public_url("http://this-domain-should-not-exist-xyz123.invalid/", 1000)
