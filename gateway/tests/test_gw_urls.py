import pytest

from artie_gateway.errors import GatewayError
from artie_gateway.urls import normalize_profile_url


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.linkedin.com/in/jane-example",
        "https://linkedin.com/in/jane-example/",
        "http://www.linkedin.com/in/Jane-Example?trk=public_profile#top",
        "https://de.linkedin.com/in/jane-example",
        "linkedin.com/in/jane-example",
        "  www.linkedin.com/in/jane-example  ",
        "https://WWW.LINKEDIN.COM/in/jane-example",
    ],
)
def test_profile_urls_normalize_to_one_form(raw):
    assert normalize_profile_url(raw) == "https://www.linkedin.com/in/jane-example"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        42,
        ["https://www.linkedin.com/in/jane"],
        "",
        "   ",
        "jane-example",                                         # a bare handle is not a URL
        "https://www.linkedin.com/company/acme",                # not a person
        "https://www.linkedin.com/pub/jane/1/2/3",
        "https://www.linkedin.com/in/",
        "https://www.linkedin.com/in/a",                        # too short
        "https://www.linkedin.com/in/jane/details/experience",  # sub-pages are not profiles
        "https://www.linkedin.com/feed/",
        "https://example.com/in/jane-example",
        "https://linkedin.com.evil.test/in/jane-example",
        "https://evil.test/linkedin.com/in/jane-example",
        "https://www.linkedin.com@evil.test/in/jane-example",   # userinfo trick
        "https://user:pw@www.linkedin.com/in/jane-example",
        "https://www.linkedin.com:8443/in/jane-example",
        "ftp://www.linkedin.com/in/jane-example",
        "javascript:alert(1)",
        "https://www.linkedin.com/in/jane example",
        "https://www.linkedin.com/in/" + "a" * 101,
        "https://www.linkedin.com/in/jane-example/" + "x" * 400,
        "https://notlinkedin.com/in/jane-example",
        "https://www.linkedin.com.evil.test/in/jane-example",
    ],
)
def test_everything_else_is_a_400(raw):
    with pytest.raises(GatewayError) as caught:
        normalize_profile_url(raw)
    assert caught.value.status == 400
    assert caught.value.code == "invalid_url"
