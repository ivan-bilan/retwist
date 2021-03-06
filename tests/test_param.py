import pytest
from twisted.web.error import Error
from twisted.web.test.requesthelper import DummyRequest

from retwist.param import BoolParam, EnumParam, IntParam, LangParam, Param, VersionParam


@pytest.fixture
def dummy_request():
    """
    Dummy request with some arguments used by tests in this module.
    """
    request = DummyRequest(b"/")
    # Mimic the way Twisted parses URL parameters:
    request.args = {
        b"id": [b"1234"],
        b"count": [b"20"],
        b"parent_id": [], # happens when you pass "parent_id="
        b"child_id": [b"a", b"b"], # happens when you pass "child_id=a&child_id=b". Not supported by retwist
        b"debug": [b"true"],
        b"verbose": [b"false"],
        b"type": [b"int"],
        b"lang": [b"de"],
        b"v": [b"1.0"]
    }
    return request


def test_param(dummy_request):
    """
    Test basic parsing behavior. 
    """

    param = Param()

    val = param.parse_from_request("id", dummy_request)
    assert val == "1234"

    with pytest.raises(Error) as exc_info:
        param.parse_from_request("parent_id", dummy_request)
    assert exc_info.value.status == b"400"

    with pytest.raises(Error) as exc_info:
        param.parse_from_request("child_id", dummy_request)
    assert exc_info.value.status == b"400"

    with pytest.raises(ValueError):
        Param(required=True, default="required params shouldn't have a default")


def test_missing_params(dummy_request):
    """
    Test behavior when parameters are missing.
    """

    # Return default if parameter is missing

    param_with_default = Param(default="default")

    val = param_with_default.parse_from_request("missing_key", dummy_request)
    assert val == "default"

    # Return error if required parameter is missing

    required_param = Param(required=True)

    with pytest.raises(Error) as exc_info:
        required_param.parse_from_request("missing_key", dummy_request)
    assert exc_info.value.status == b"400"


def test_bool_param(dummy_request):

    bool_param = BoolParam(default=False)

    val = bool_param.parse_from_request("debug", dummy_request)
    assert val is True

    val = bool_param.parse_from_request("enabled", dummy_request)
    assert val is False

    val = bool_param.parse_from_request("missing_key", dummy_request)
    assert val is False

    with pytest.raises(Error) as exc_info:
        bool_param.parse_from_request("id", dummy_request)
    assert exc_info.value.status == b"400"


def test_int_param(dummy_request):

    int_param = IntParam()

    val = int_param.parse_from_request("count", dummy_request)
    assert val == 20

    # Test error on malformed input

    with pytest.raises(Error) as exc_info:
        val = int_param.parse_from_request("lang", dummy_request)
    assert exc_info.value.status == b"400"

    # Test minimum and maximum boundaries

    int_param = IntParam(min_val=0, max_val=20)

    val = int_param.parse_from_request("count", dummy_request)
    assert val == 20

    dummy_request.addArg(b"count", b"-1")
    with pytest.raises(Error) as exc_info:
        val = int_param.parse_from_request("count", dummy_request)
    assert exc_info.value.status == b"400"

    dummy_request.addArg(b"count", b"21")
    with pytest.raises(Error) as exc_info:
        val = int_param.parse_from_request("count", dummy_request)
    assert exc_info.value.status == b"400"


def test_enum_param(dummy_request):

    enum_param = EnumParam(["int", "float"])

    val = enum_param.parse_from_request("type", dummy_request)
    assert val == "int"

    with pytest.raises(Error) as exc_info:
        enum_param.parse_from_request("debug", dummy_request)
    assert exc_info.value.status == b"400"


def test_lang_param(dummy_request):

    lang_param = LangParam(default="en")

    val = lang_param.parse_from_request("lang", dummy_request)
    assert val == "de"

    # Fall back to default

    del dummy_request.args[b"lang"]

    val = lang_param.parse_from_request("lang", dummy_request)
    assert val == "en"

    # ... or parse from HTTP header

    dummy_request.requestHeaders.setRawHeaders("Accept-Language", ["fr-CH, fr;q=0.9, en;q=0.8, de;q=0.7, *;q=0.5"])

    val = lang_param.parse_from_request("lang", dummy_request)
    assert val == "fr-CH"

    # Don't crap out on malformed HTTP header

    dummy_request.requestHeaders.setRawHeaders("Accept-Language", ["fr-CH;q=;"])
    lang_param.parse_from_request("lang", dummy_request)


def test_version_param(dummy_request):

    version_param = VersionParam()

    val = version_param.parse_from_request("v", dummy_request)
    assert val == (1, 0)

    # Handle malformed version

    dummy_request.args[b"v"] = [b"derp"]

    with pytest.raises(Error) as exc_info:
        version_param.parse_from_request("v", dummy_request)
    assert exc_info.value.status == b"400"